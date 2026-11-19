package com.sdj.mobilecare.data

import java.math.BigDecimal
import java.math.MathContext
import java.math.RoundingMode

/** Money is kept as Long paise everywhere in the app (exact, like the PC). */
object Money {
    fun rupees(p: Long): BigDecimal = BigDecimal.valueOf(p, 2)
    fun paise(x: BigDecimal): Long = x.setScale(2, RoundingMode.HALF_UP).movePointRight(2).toLong()

    /** "₹1,25,050.50" (Indian grouping). */
    fun fmt(p: Long, symbol: Boolean = true): String {
        val neg = p < 0
        val a = kotlin.math.abs(p)
        val r = a / 100
        val ps = a % 100
        val s = r.toString()
        val grouped = if (s.length <= 3) s else {
            val head = s.dropLast(3)
            val tail = s.takeLast(3)
            head.reversed().chunked(2).joinToString(",").reversed() + "," + tail
        }
        return (if (neg) "-" else "") + (if (symbol) "₹" else "") + grouped + "." + ps.toString().padStart(2, '0')
    }

    fun short(p: Long): String = if (p % 100 == 0L) fmt(p).dropLast(3) else fmt(p)

    /** Parse what the user typed ("1,250.5") into paise; null if not a number. */
    fun parse(text: String): Long? {
        val t = text.replace(",", "").replace("₹", "").trim()
        if (t.isEmpty()) return 0
        return try { paise(BigDecimal(t)) } catch (e: NumberFormatException) { null }
    }

    fun plain(p: Long): String = if (p == 0L) "" else rupees(p).stripTrailingZeros().toPlainString()

    /** A tax rate given as a plain number (18, 18.0, "18") → rate in paise of percent (1800). */
    fun ratePaise(v: Any?): Long = try {
        when (v) {
            null -> 0
            is Number -> paise(BigDecimal(v.toString()))
            else -> paise(BigDecimal(v.toString()))
        }
    } catch (e: Exception) { 0 }
}

data class LineIn(val qty: Int, val price: Long, val discount: Long = 0, val rate: Long = 0)

data class LineOut(val gross: Long, val itemDisc: Long, val share: Long, val grossEx: Long, val discEx: Long,
                   val taxable: Long, val gst: Long, val total: Long)

data class BillOut(
    val lines: List<LineOut>, val gross: Long, val itemDisc: Long, val billDisc: Long, val grossEx: Long,
    val discEx: Long, val taxable: Long, val gst: Long, val beforeRound: Long, val roundOff: Long, val grandTotal: Long,
) {
    val cgst: Long get() = Money.paise(Money.rupees(gst).divide(BigDecimal(2), MathContext(28, RoundingMode.HALF_EVEN)))
    val sgst: Long get() = gst - cgst
}

/**
 * Exact port of sdj/core/calc.py – the PC recalculates every bill with the same rules, so what the phone shows
 * is what the PC saves. (Python Decimal: 28 digits, ROUND_HALF_EVEN for division, ROUND_HALF_UP for money.)
 */
object Calc {
    private val HUNDRED = BigDecimal(100)
    private val MC = MathContext(28, RoundingMode.HALF_EVEN)
    private val ZERO2 = BigDecimal("0.00")

    private fun q2(x: BigDecimal): BigDecimal = x.setScale(2, RoundingMode.HALF_UP)

    fun allocate(total: BigDecimal, weights: List<BigDecimal>): List<BigDecimal> {
        val t = q2(total)
        val n = weights.size
        if (n == 0) return emptyList()
        val wsum = weights.fold(BigDecimal.ZERO) { a, b -> a.add(b) }
        if (t.signum() == 0 || wsum.signum() <= 0) return List(n) { ZERO2 }
        val paiseTotal = t.movePointRight(2).setScale(0, RoundingMode.DOWN).toLong()
        val raw = weights.map { BigDecimal(paiseTotal).multiply(it, MC).divide(wsum, MC) }
        val floors = raw.map { it.setScale(0, RoundingMode.FLOOR).toLong() }.toMutableList()
        val remainder = paiseTotal - floors.sum()
        val order = (0 until n).sortedByDescending { raw[it].subtract(BigDecimal(floors[it])) }
        for (i in order.take(remainder.toInt().coerceAtLeast(0))) floors[i] += 1
        return floors.map { BigDecimal.valueOf(it, 2) }
    }

    /** (taxable, gst, total) for an amount at [rate] percent. */
    fun splitTax(amount: BigDecimal, rate: BigDecimal, inclusive: Boolean): Triple<BigDecimal, BigDecimal, BigDecimal> {
        val a = q2(amount)
        if (rate.signum() <= 0) return Triple(a, ZERO2, a)
        return if (inclusive) {
            val taxable = q2(a.multiply(HUNDRED, MC).divide(HUNDRED.add(rate), MC))
            Triple(taxable, a.subtract(taxable), a)
        } else {
            val gst = q2(a.multiply(rate, MC).divide(HUNDRED, MC))
            Triple(a, gst, a.add(gst))
        }
    }

    fun bill(lines: List<LineIn>, billDiscount: Long, gstEnabled: Boolean, inclusive: Boolean, roundOff: Boolean): BillOut {
        val grosses = lines.map { q2(BigDecimal(it.qty).multiply(Money.rupees(it.price))) }
        val discs = lines.map { q2(Money.rupees(it.discount)) }
        val nets = grosses.zip(discs).map { (g, d) -> g.subtract(d) }
        val bd = q2(Money.rupees(billDiscount))
        val shares = allocate(bd, nets.map { if (it.signum() < 0) BigDecimal.ZERO else it })
        val outs = mutableListOf<LineOut>()
        val sums = Array(8) { BigDecimal("0.00") }
        for (i in lines.indices) {
            val rate = if (gstEnabled) Money.rupees(lines[i].rate) else BigDecimal.ZERO
            val g = grosses[i]
            val amount = g.subtract(discs[i]).subtract(shares[i])
            val (taxable, gst, total) = splitTax(amount, rate, inclusive)
            val gEx = if (rate.signum() > 0 && inclusive) q2(g.multiply(HUNDRED, MC).divide(HUNDRED.add(rate), MC)) else g
            val dEx = gEx.subtract(taxable)
            outs += LineOut(Money.paise(g), Money.paise(discs[i]), Money.paise(shares[i]), Money.paise(gEx),
                Money.paise(dEx), Money.paise(taxable), Money.paise(gst), Money.paise(total))
            val vals = listOf(g, discs[i], gEx, dEx, taxable, gst, total)
            for (k in vals.indices) sums[k] = sums[k].add(vals[k])
        }
        val before = sums[6]
        val grand = if (roundOff) before.setScale(0, RoundingMode.HALF_UP).setScale(2) else before
        return BillOut(outs, Money.paise(sums[0]), Money.paise(sums[1]), Money.paise(bd), Money.paise(sums[2]),
            Money.paise(sums[3]), Money.paise(sums[4]), Money.paise(sums[5]), Money.paise(before),
            Money.paise(grand.subtract(before)), Money.paise(grand))
    }

    /** Repair job total (port of recalc_job): labour + parts, discount shared, GST like the PC. */
    fun jobTotal(labour: Long, discount: Long, parts: List<Pair<Long, Long>>, serviceRate: Long, gstEnabled: Boolean,
                 inclusive: Boolean): Long {
        val partsAmount = parts.fold(BigDecimal("0.00")) { a, p -> a.add(Money.rupees(p.first)) }
        val lab = Money.rupees(labour)
        val (labShare, partsShare) = allocate(Money.rupees(discount), listOf(lab, partsAmount)).let { it[0] to it[1] }
        val itemShares = allocate(partsShare, parts.map { Money.rupees(it.first) })
        val srate = if (gstEnabled) Money.rupees(serviceRate) else BigDecimal.ZERO
        var total = splitTax(lab.subtract(labShare), srate, inclusive).third
        parts.forEachIndexed { i, p ->
            val rate = if (gstEnabled) Money.rupees(p.second) else BigDecimal.ZERO
            total = total.add(splitTax(Money.rupees(p.first).subtract(itemShares[i]), rate, inclusive).third)
        }
        return Money.paise(total)
    }

    private val UNITS = listOf("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
        "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen")
    private val TENS = listOf("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")

    private fun two(n: Int): String = if (n < 20) UNITS[n] else (TENS[n / 10] + if (n % 10 != 0) " " + UNITS[n % 10] else "").trim()
    private fun three(n: Int): String {
        val h = n / 100
        val r = n % 100
        val parts = mutableListOf<String>()
        if (h > 0) parts += UNITS[h] + " Hundred"
        if (r > 0) parts += two(r)
        return parts.joinToString(" ")
    }

    /** 125050.50 → "Rupees One Lakh Twenty Five Thousand Fifty and Fifty Paise Only" (same as the PC). */
    fun amountInWords(p: Long): String {
        val neg = p < 0
        val a = kotlin.math.abs(p)
        val rupees = a / 100
        val paise = (a % 100).toInt()
        val words = if (rupees == 0L) "Zero" else {
            val crore = rupees / 10_000_000
            var rest = rupees % 10_000_000
            val lakh = (rest / 100_000).toInt()
            rest %= 100_000
            val thousand = (rest / 1000).toInt()
            val last = (rest % 1000).toInt()
            val parts = mutableListOf<String>()
            if (crore > 0) parts += (if (crore < 1000) three(crore.toInt()) else
                amountInWords(crore * 100).removePrefix("Rupees ").removeSuffix(" Only")) + " Crore"
            if (lakh > 0) parts += two(lakh) + " Lakh"
            if (thousand > 0) parts += two(thousand) + " Thousand"
            if (last > 0) parts += three(last)
            parts.joinToString(" ")
        }
        var text = "Rupees $words"
        if (paise > 0) text += " and ${two(paise)} Paise"
        text += " Only"
        return if (neg) "Minus $text" else text
    }
}

package com.sdj.mobilecare.data

import android.content.Context
import android.content.Intent
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import androidx.core.content.FileProvider
import java.io.File

/** A bill as a PDF (receipt width) that can be shared on WhatsApp or any app. */
object BillPdf {
    private const val W = 300f
    private const val M = 16f

    private class Line(val text: String, val right: String = "", val size: Float = 9f, val bold: Boolean = false,
                       val center: Boolean = false, val gapAfter: Float = 0f, val rule: Boolean = false)

    private fun lines(s: Sale, shop: Shop): List<Line> {
        val out = mutableListOf<Line>()
        out += Line(shop.name, size = 14f, bold = true, center = true)
        if (shop.tagline.isNotBlank()) out += Line(shop.tagline, size = 8f, center = true)
        if (shop.address.isNotBlank()) shop.address.lines().filter { it.isNotBlank() }.forEach { out += Line(it, size = 8f, center = true) }
        val contact = listOf(shop.phone.takeIf { it.isNotBlank() }?.let { "Ph: $it" }, shop.email.takeIf { it.isNotBlank() }).filterNotNull()
        if (contact.isNotEmpty()) out += Line(contact.joinToString("  ·  "), size = 8f, center = true)
        if (s.gstOn && shop.gstin.isNotBlank()) out += Line("GSTIN: ${shop.gstin}", size = 8f, center = true)
        out += Line("", rule = true)
        out += Line(if (s.gstOn) "TAX INVOICE" else "BILL", size = 11f, bold = true, center = true, gapAfter = 2f)
        out += Line("Invoice: ${s.no}", Dates.nice(s.date), bold = true)
        if (s.status == "CANCELLED") out += Line("CANCELLED", bold = true, size = 11f, center = true)
        val cust = if (s.customerName.isNotBlank() && !s.customerName.startsWith("Walk-in")) s.customerName else "Walk-in customer"
        out += Line("Customer: $cust", s.customerPhone)
        out += Line("", rule = true)
        for (l in s.lines) {
            wrap(l.name, 34).forEachIndexed { i, part -> out += Line(part, if (i == 0) Money.fmt(l.total) else "", bold = i == 0) }
            out += Line("   ${l.qty} × ${Money.fmt(l.price)}" + (if (l.disc > 0) "  (disc ${Money.fmt(l.disc)})" else ""), size = 8f)
            if (l.imei.isNotBlank()) out += Line("   IMEI: ${l.imei}", size = 8f)
            if (l.warrantyMonths > 0) out += Line("   Warranty: ${l.warrantyMonths} month(s)", size = 8f)
        }
        out += Line("", rule = true)
        out += Line("Items total", Money.fmt(s.gross))
        if (s.itemDisc + s.billDisc > 0) out += Line("Discount", "-" + Money.fmt(s.itemDisc + s.billDisc))
        if (s.gstOn) {
            out += Line("Taxable value", Money.fmt(s.taxable))
            val cgst = (s.gst + 1) / 2          // same rounding as the PC (half up)
            out += Line("CGST", Money.fmt(cgst))
            out += Line("SGST", Money.fmt(s.gst - cgst))
        }
        if (s.roundOff != 0L) out += Line("Round off", Money.fmt(s.roundOff))
        out += Line("GRAND TOTAL", Money.fmt(s.total), size = 12f, bold = true, gapAfter = 2f)
        s.pays.filter { it.status == "ACTIVE" && it.dir == "IN" }.forEach { out += Line("Paid (${MODE_LABELS[it.mode] ?: it.mode})", Money.fmt(it.amount)) }
        if (s.due > 0) out += Line("Balance due", Money.fmt(s.due), bold = true)
        out += Line(Calc.amountInWords(s.total), size = 7.5f, gapAfter = 4f)
        if (shop.upi.isNotBlank() && s.due > 0) out += Line("UPI: ${shop.upi}", size = 8f, center = true)
        if (shop.footer.isNotBlank()) out += Line(shop.footer, size = 9f, bold = true, center = true)
        if (shop.terms.isNotBlank()) wrap(shop.terms, 52).forEach { out += Line(it, size = 7f, center = true) }
        return out
    }

    private fun wrap(text: String, width: Int): List<String> {
        val words = text.split(" ")
        val out = mutableListOf<String>()
        var cur = ""
        for (w in words) {
            if ((cur + " " + w).trim().length > width && cur.isNotEmpty()) { out += cur; cur = w } else cur = (cur + " " + w).trim()
        }
        if (cur.isNotEmpty()) out += cur
        return out.ifEmpty { listOf("") }
    }

    fun write(context: Context, s: Sale, shop: Shop): File {
        val ls = lines(s, shop)
        val height = M * 2 + ls.sumOf { (it.size * 1.45f + it.gapAfter + if (it.rule) 6f else 0f).toDouble() }.toFloat()
        val doc = PdfDocument()
        val page = doc.startPage(PdfDocument.PageInfo.Builder(W.toInt(), height.toInt() + 10, 1).create())
        val c = page.canvas
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        var y = M
        for (l in ls) {
            if (l.rule) {
                p.strokeWidth = 0.6f
                c.drawLine(M, y + 3f, W - M, y + 3f, p)
                y += 6f
                continue
            }
            p.textSize = l.size
            p.typeface = Typeface.create(Typeface.DEFAULT, if (l.bold) Typeface.BOLD else Typeface.NORMAL)
            y += l.size * 1.2f
            if (l.center) {
                p.textAlign = Paint.Align.CENTER
                c.drawText(l.text, W / 2, y, p)
            } else {
                p.textAlign = Paint.Align.LEFT
                c.drawText(l.text, M, y, p)
                if (l.right.isNotEmpty()) {
                    p.textAlign = Paint.Align.RIGHT
                    c.drawText(l.right, W - M, y, p)
                }
            }
            y += l.size * 0.25f + l.gapAfter
        }
        doc.finishPage(page)
        val dir = File(context.cacheDir, "bills").apply { mkdirs() }
        val f = File(dir, s.no.replace("/", "-").replace(" ", "_") + ".pdf")
        f.outputStream().use { doc.writeTo(it) }
        doc.close()
        return f
    }

    fun share(context: Context, s: Sale, shop: Shop) {
        val f = write(context, s, shop)
        val uri = FileProvider.getUriForFile(context, context.packageName + ".files", f)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "application/pdf"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_TEXT, "${shop.name} – bill ${s.no} – ${Money.fmt(s.total)}")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(Intent.createChooser(send, "Share bill").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

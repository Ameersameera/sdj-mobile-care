package com.sdj.mobilecare

import com.sdj.mobilecare.data.Calc
import com.sdj.mobilecare.data.LineIn
import com.sdj.mobilecare.data.mapObj
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CalcTest {
    @Test
    fun everyBillMatchesThePcToThePaisa() {
        val root = Fixtures.json("calc_vectors.json")
        val bills = root.getJSONArray("bills")
        for (i in 0 until bills.length()) {
            val v = bills.getJSONObject(i)
            val lines = v.getJSONArray("lines").mapObj { LineIn(it.getInt("qty"), it.getLong("price"), it.getLong("disc"), it.getLong("rate")) }
            val b = Calc.bill(lines, v.getLong("bdisc"), v.getBoolean("gst_enabled"), v.getBoolean("inclusive"), v.getBoolean("round_off"))
            val o = v.getJSONObject("out")
            val m = "bill #$i"
            assertEquals("$m gross", o.getLong("gross"), b.gross)
            assertEquals("$m item disc", o.getLong("idisc"), b.itemDisc)
            assertEquals("$m bill disc", o.getLong("bdisc"), b.billDisc)
            assertEquals("$m gross ex", o.getLong("gross_ex"), b.grossEx)
            assertEquals("$m disc ex", o.getLong("disc_ex"), b.discEx)
            assertEquals("$m taxable", o.getLong("taxable"), b.taxable)
            assertEquals("$m gst", o.getLong("gst"), b.gst)
            assertEquals("$m before round", o.getLong("before"), b.beforeRound)
            assertEquals("$m round", o.getLong("round"), b.roundOff)
            assertEquals("$m total", o.getLong("total"), b.grandTotal)
            assertEquals("$m cgst", o.getLong("cgst"), b.cgst)
            val ol = o.getJSONArray("lines")
            for (j in 0 until ol.length()) {
                val x = ol.getJSONObject(j)
                val y = b.lines[j]
                assertEquals("$m line $j share", x.getLong("share"), y.share)
                assertEquals("$m line $j taxable", x.getLong("taxable"), y.taxable)
                assertEquals("$m line $j gst", x.getLong("gst"), y.gst)
                assertEquals("$m line $j total", x.getLong("total"), y.total)
                assertEquals("$m line $j gross ex", x.getLong("gross_ex"), y.grossEx)
            }
        }
        val words = root.getJSONObject("words")
        words.keys().forEach { k -> assertEquals(words.getString(k), Calc.amountInWords(k.toLong())) }
    }
}

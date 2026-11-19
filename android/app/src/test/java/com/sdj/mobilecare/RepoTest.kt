package com.sdj.mobilecare

import com.sdj.mobilecare.data.Crypto
import com.sdj.mobilecare.data.UserError
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class RepoTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun offlineEntriesUpdateTheCopyAndAreSentOnce() = runBlocking {
        val net = OfflineNet()
        val r = testRepo(tmp.newFolder(), net)
        r.load()
        try { r.signIn("wrong", true); fail("wrong password accepted") } catch (e: UserError) { /* expected */ }
        r.signIn("admin123", true)
        val before = r.state.value
        val charger0 = before.products.first { it.name == "Charger 20W" }.qty
        val ravi0 = before.customers.first { it.name == "Ravi Kumar" }.due
        addSampleEntries(r)
        val st = r.state.value
        assertEquals(7, st.pending.size)
        assertEquals(charger0 - 3, st.products.first { it.name == "Charger 20W" }.qty)        // 2 → edited to 3
        val phone = st.products.first { it.name.startsWith("Vivo") }
        assertEquals(2, phone.qty)
        assertEquals(2, st.imeis.count { it.product == phone.id })
        val bill2 = st.sales.first { it.pending && it.customerName == "Ravi Kumar" }
        assertEquals(ravi0 + bill2.due, st.customers.first { it.name == "Ravi Kumar" }.due)
        assertTrue(st.customers.any { it.name == "Karthik" && it.pending })
        val edited = st.sales.filter { it.pending && it.lines.any { l -> l.name == "Charger 20W" } }
        assertEquals(1, edited.size)
        assertTrue(edited[0].no.startsWith("SDJ-P1/"))
        assertTrue(st.jobs.first().no.startsWith("JOB-P1-"))
        assertTrue(st.expenses.first().pending)
        val d0 = before.dash!!
        if (d0.date == st.businessDate) assertEquals(d0.bills + 3, st.dash!!.bills)      // 3 new bills (one edited)

        // the first sync attempt fails (no Wi-Fi) – nothing is lost, and the request has every entry once
        assertFalse(r.syncOnce())
        assertEquals(7, r.state.value.pending.size)
        val body = net.lastBody!!
        val dev = Fixtures.json("device.json")
        val req = Crypto.unseal(Crypto.unb64(dev.getString("key")), body, dev.getString("device").toByteArray())
        val ops = req.getJSONArray("ops")
        assertEquals(7, ops.length())
        for (i in 0 until ops.length()) assertFalse(ops.getJSONObject(i).keys().asSequence().any { it.startsWith("_") })
        Fixtures.out("phone_sync_request.json").writeText(body.toString())      // the CI replays it on the PC code
    }

    @Test
    fun cancellingAPcBillReturnsStockOnThePhone() = runBlocking {
        val r = testRepo(tmp.newFolder())
        r.load()
        r.signIn("admin123", true)
        val s = r.state.value.sales.first { !it.pending && it.status == "COMPLETED" && it.customerName == "Ravi Kumar" }
        val q0 = r.state.value.products.first { it.name == "Charger 20W" }.qty
        r.cancelBill(s, "Wrong customer")
        val st = r.state.value
        assertEquals(q0 + 2, st.products.first { it.name == "Charger 20W" }.qty)
        assertEquals("CANCELLED", st.sales.first { it.id == s.id }.status)
    }
}

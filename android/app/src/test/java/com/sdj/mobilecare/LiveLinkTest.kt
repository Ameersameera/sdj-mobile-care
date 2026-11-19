package com.sdj.mobilecare

import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.Store
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Links and syncs over real HTTP with the PC's sync service (started by the CI before the tests). */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class LiveLinkTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun linkSignInSellAndSyncWithTheRealPcService() = runBlocking {
        val f = Fixtures.out("live_code.json")
        assumeTrue("PC sync service not running (only in CI)", f.exists())
        val live = JSONObject(f.readText())
        val r = Repo(Store(tmp.newFolder()), Net(null), Dispatchers.IO, "test")
        r.load()
        val qr = r.parseQr("SDJLINK:1;h=127.0.0.1;p=${live.getInt("port")};c=${live.getString("code")};id=${live.getString("pc")};n=Test shop")!!
        r.link(qr.code, qr, null, "CI phone")
        val st = r.state.value
        assertTrue(st.linked && st.hasData)
        assertTrue(st.shop!!.invSeries.startsWith("SDJ-P"))
        r.signIn("admin123", true)
        addSampleEntries(r)
        assertEquals(7, r.state.value.pending.size)
        assertTrue(r.syncOnce())
        val after = r.state.value
        assertEquals(0, after.pending.size)
        assertEquals(0, after.failed.size)
        assertTrue(after.sales.any { !it.pending && it.no.startsWith(st.shop!!.invSeries) })
        assertTrue(r.syncOnce())                     // nothing new: quick, nothing breaks
    }
}

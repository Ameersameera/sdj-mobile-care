package com.sdj.mobilecare

import com.sdj.mobilecare.data.AppUpdate
import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.Store
import com.sdj.mobilecare.data.isNewerVersion
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.File
import java.io.IOException

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class UpdateLogicTest {
    @get:Rule val tmp = TemporaryFolder()

    private class ApkNet(val bytes: ByteArray, val fail: Boolean = false) : Net(null) {
        var path = ""
        override fun download(host: String, port: Int, path: String, body: JSONObject, dest: File, readTimeoutMs: Int): String {
            this.path = path
            if (fail) throw IOException("offline in test")
            dest.writeBytes(bytes)
            return Backups.sha256(bytes.inputStream()).second
        }
    }

    private fun meta(bytes: ByteArray, version: String) = JSONObject().put("version", version).put("size", bytes.size.toLong())
        .put("sha", Backups.sha256(bytes.inputStream()).second).put("name", "SDJMobileCare-Phone-$version.apk")

    @Test fun versionsCompare() {
        assertTrue(isNewerVersion("1.5.0", "1.4.0"))
        assertTrue(isNewerVersion("1.10.0", "1.9.9"))
        assertTrue(isNewerVersion("2.0", "1.99.99"))
        assertFalse(isNewerVersion("1.4.0", "1.4.0"))
        assertFalse(isNewerVersion("1.3.9", "1.4.0"))
        assertFalse(isNewerVersion("", "1.4.0"))
    }

    @Test fun newerAppIsDownloadedAndKeptReady() = runBlocking {
        val bytes = ByteArray(5000) { (it * 7).toByte() }
        val net = ApkNet(bytes)
        val r = testRepo(tmp.newFolder(), net)
        r.offerUpdate(meta(bytes, "99.0.0"), "dev-1", ByteArray(32) { it.toByte() })
        val u = r.update.value
        assertNotNull(u)
        assertEquals(AppUpdate.READY, u!!.state)
        assertEquals("/sdj/apk", net.path)
        assertTrue(File(u.path).exists())
        assertEquals(bytes.size.toLong(), File(u.path).length())
    }

    @Test fun sameOrOlderVersionIsIgnored() = runBlocking {
        val bytes = ByteArray(100)
        val r = testRepo(tmp.newFolder(), ApkNet(bytes))
        r.offerUpdate(meta(bytes, "0.0.0"), "dev-1", ByteArray(32))
        assertNull(r.update.value)
        r.offerUpdate(null, "dev-1", ByteArray(32))
        assertNull(r.update.value)
    }

    @Test fun brokenDownloadIsReportedAndLeavesNoFile() = runBlocking {
        val bytes = ByteArray(100)
        val r = testRepo(tmp.newFolder(), ApkNet(bytes, fail = true))
        r.offerUpdate(meta(bytes, "99.0.0"), "dev-1", ByteArray(32))
        assertEquals(AppUpdate.FAILED, r.update.value!!.state)
        assertTrue(r.update.value!!.error.contains("tried again"))
    }

    @Test fun wrongChecksumIsRejected() = runBlocking {
        val bytes = ByteArray(100) { 1 }
        val m = meta(bytes, "99.0.0").put("sha", "0".repeat(64))
        val r = testRepo(tmp.newFolder(), ApkNet(bytes))
        r.offerUpdate(m, "dev-1", ByteArray(32))
        assertEquals(AppUpdate.FAILED, r.update.value!!.state)
    }

    private val key = ByteArray(32) { it.toByte() }

    @Test fun whatsNewShowsOnceAfterAnUpdateAndIsKeptInTheHistory() = runBlocking {
        val dir = tmp.newFolder()
        val bytes = ByteArray(3000) { (it % 251).toByte() }
        val old = testRepo(dir, ApkNet(bytes), version = "0.0.1")
        old.load()
        assertNull(old.whatsNew.value)                                   // first start: nothing to show
        old.offerUpdate(meta(bytes, "0.0.2").put("notes", "- Faster bills\n- Update popup").put("date", "2026-11-25").put("recommended", true), "dev", key)
        val u = old.update.value!!
        assertEquals(AppUpdate.READY, u.state)
        assertEquals("2026-11-25", u.date)
        assertTrue(u.recommended)
        val now = Repo(Store(dir), OfflineNet(), kotlinx.coroutines.Dispatchers.Unconfined, "0.0.2")
        now.load()
        assertEquals("0.0.2", now.whatsNew.value!!.version)
        assertTrue(now.whatsNew.value!!.notes.contains("Faster bills"))
        now.dismissWhatsNew()
        val again = Repo(Store(dir), OfflineNet(), kotlinx.coroutines.Dispatchers.Unconfined, "0.0.2")
        again.load()
        assertNull(again.whatsNew.value)                                 // only once
        assertEquals(listOf("0.0.2"), again.updateHistory().map { it.version })
    }

    @Test fun laterAsksAgainTomorrow() = runBlocking {
        val r = testRepo(tmp.newFolder())
        assertFalse(r.updateSnoozed())
        r.snoozeUpdate(24)
        assertTrue(r.updateSnoozed())
        r.snoozeUpdate(-1)
        assertFalse(r.updateSnoozed())
    }

    @Test fun notificationIsAnnouncedOncePerVersion() = runBlocking {
        val bytes = ByteArray(500) { 3 }
        val r = testRepo(tmp.newFolder(), ApkNet(bytes), version = "0.0.1")
        assertNull(r.takeUpdateNotice())                                 // nothing ready
        r.offerUpdate(meta(bytes, "0.0.2"), "dev", key)
        assertEquals("0.0.2", r.takeUpdateNotice()!!.version)
        assertNull(r.takeUpdateNotice())                                 // not again
    }
}

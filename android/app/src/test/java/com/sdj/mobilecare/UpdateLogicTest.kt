package com.sdj.mobilecare

import com.sdj.mobilecare.data.AppUpdate
import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.Net
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
import java.io.File
import java.io.IOException

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
        r.offerUpdate(meta(bytes, "0.0.1"), "dev-1", ByteArray(32))
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
}

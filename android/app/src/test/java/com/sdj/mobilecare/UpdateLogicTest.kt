package com.sdj.mobilecare

import com.sdj.mobilecare.data.AppUpdate
import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.GitHubRelease
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

    // ---------------------------------------------------------------- v1.5.2: straight from the internet (GitHub)
    private class WebNet(val rel: JSONObject?, val bytes: ByteArray, val wifi: Boolean = true) : Net(null) {
        var downloads = 0
        override fun webJson(url: String): JSONObject = rel ?: throw IOException("no internet in test")
        override fun webDownload(url: String, dest: File, maxBytes: Long): String {
            downloads++
            dest.writeBytes(bytes)
            return Backups.sha256(bytes.inputStream()).second
        }
        override fun unmetered(): Boolean = wifi
        override fun post(host: String, port: Int, path: String, body: JSONObject, readTimeoutMs: Int): JSONObject =
            throw IOException("no PC in test")
        override fun discover(pcId: String?, timeoutMs: Int): List<Pair<String, JSONObject>> = emptyList()
    }

    private fun release(version: String, bytes: ByteArray): JSONObject {
        val body = "## SDJ\n\n### What's new in $version – faster\n- **Faster** sync\nRecommended: yes\n\n### What's new in 1.0.0\n- old notes\n"
        val asset = JSONObject().put("name", "SDJMobileCare-Phone-$version.apk").put("size", bytes.size.toLong())
            .put("digest", "sha256:" + Backups.sha256(bytes.inputStream()).second)
            .put("browser_download_url", "https://github.com/Ameersameera/sdj-mobile-care/releases/download/v$version/SDJMobileCare-Phone-$version.apk")
        return JSONObject().put("tag_name", "v$version").put("published_at", "2026-11-25T10:00:00Z").put("body", body)
            .put("assets", org.json.JSONArray().put(asset))
    }

    @Test fun releaseNotesAndFileAreReadFromGitHub() {
        val bytes = ByteArray(10) { 1 }
        val info = GitHubRelease.parse(release("2.0.0", bytes))!!
        assertEquals("2.0.0", info.version)
        assertTrue(info.recommended)
        assertTrue(info.notes.contains("Faster sync"))
        assertFalse(info.notes.contains("old notes"))
        assertFalse(info.notes.contains("Recommended"))
        assertEquals("2026-11-25", info.date)
        val noSha = release("2.0.0", bytes).also { it.getJSONArray("assets").getJSONObject(0).remove("digest") }
        assertNull(GitHubRelease.parse(noSha))
        val foreign = release("2.0.0", bytes).also { it.getJSONArray("assets").getJSONObject(0).put("browser_download_url", "https://evil.example.com/a.apk") }
        assertNull(GitHubRelease.parse(foreign))
        assertNull(GitHubRelease.parse(release("2.0.0", bytes).put("prerelease", true)))
        assertFalse(GitHubRelease.okUrl("http://github.com/x"))
        assertFalse(GitHubRelease.okUrl("https://github.com.evil.com/x"))
        assertTrue(GitHubRelease.okUrl("https://objects.githubusercontent.com/x"))
    }

    @Test fun phoneGetsTheUpdateStraightFromTheInternet() = runBlocking {
        val bytes = ByteArray(4000) { (it % 13).toByte() }
        val net = WebNet(release("99.0.0", bytes), bytes)
        val r = testRepo(tmp.newFolder(), net, version = "1.5.2")
        val msg = r.checkOnline(force = true)
        assertTrue(msg, msg.contains("99.0.0"))
        val u = r.update.value!!
        assertEquals(AppUpdate.READY, u.state)
        assertTrue(u.recommended)
        assertTrue(u.notes.contains("Faster sync"))
        assertEquals(bytes.size.toLong(), File(u.path).length())
        assertEquals("", r.checkOnline())                      // checked a moment ago: not again for 6 hours
        assertEquals(1, net.downloads)
    }

    @Test fun upToDateAndNoInternetAreToldPlainly() = runBlocking {
        val bytes = ByteArray(10)
        assertTrue(testRepo(tmp.newFolder(), WebNet(release("1.5.2", bytes), bytes), version = "1.5.2").checkOnline(true).contains("newest"))
        assertTrue(testRepo(tmp.newFolder(), WebNet(null, bytes), version = "1.5.2").checkOnline(true).contains("internet"))
    }

    @Test fun mobileDataSettingIsRespectedForAutomaticChecks() = runBlocking {
        val bytes = ByteArray(100) { 2 }
        val r = testRepo(tmp.newFolder(), WebNet(release("99.0.0", bytes), bytes, wifi = false), version = "1.5.2")
        assertTrue(r.updatesOnMobileData())                     // on by default
        r.setUpdatesOnMobileData(false)
        assertEquals("", r.checkOnline())                        // on mobile data: waits for Wi-Fi
        assertNull(r.update.value)
        assertTrue(r.checkOnline(force = true).contains("99.0.0"))   // the person asked: downloads now
    }

    @Test fun internetUpdateSurvivesRestartIsNotWipedByThePcAndIsTidiedOnceInstalled() = runBlocking {
        val dir = tmp.newFolder()
        val bytes = ByteArray(800) { 5 }
        val r = testRepo(dir, WebNet(release("99.0.0", bytes), bytes), version = "1.5.2")
        r.checkOnline(force = true)
        r.offerUpdate(null, "dev", key)                         // a PC without the new app offers nothing
        assertEquals(AppUpdate.READY, r.update.value!!.state)
        val again = Repo(Store(dir), OfflineNet(), kotlinx.coroutines.Dispatchers.Unconfined, "1.5.2")
        again.load()
        assertEquals("99.0.0", again.update.value!!.version)
        val installed = Repo(Store(dir), OfflineNet(), kotlinx.coroutines.Dispatchers.Unconfined, "99.0.0")
        installed.load()
        assertNull(installed.update.value)
        assertFalse(File(dir, "update").listFiles()?.any { it.name.endsWith(".apk") } ?: false)
    }
}

package com.sdj.mobilecare

import com.sdj.mobilecare.data.BackupItem
import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.Crypto
import com.sdj.mobilecare.data.DriveTarget
import com.sdj.mobilecare.data.FileSink
import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.PcError
import com.sdj.mobilecare.data.UserError
import com.sdj.mobilecare.data.arr
import com.sdj.mobilecare.data.lng
import com.sdj.mobilecare.data.obj
import com.sdj.mobilecare.data.str
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
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
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest
import java.time.LocalDate

fun sha(b: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }

/** Plays the PC: answers syncs (offering backups), hands out backup files and receives restores. */
class FakePcNet(private val key: ByteArray, private val device: String, val code: String = "ABCDEFGHJK",
                val pc: String = "PCNEW") : Net(null) {
    var epoch = "e1"
    var offer = JSONArray()
    val files = HashMap<Long, ByteArray>()
    val requests = mutableListOf<JSONObject>()
    var uploaded: ByteArray? = null
    var uploadAuth = ""
    var rebuildEnv: JSONObject? = null

    override fun post(host: String, port: Int, path: String, body: JSONObject, readTimeoutMs: Int): JSONObject {
        when (path) {
            "/sdj/sync" -> {
                val req = Crypto.unseal(key, body, device.toByteArray())
                requests += req
                val results = JSONArray()
                val ops = req.arr("ops")
                for (i in 0 until ops.length()) {
                    results.put(JSONObject().put("id", ops.getJSONObject(i).str("id")).put("st", "DONE").put("msg", "")
                        .put("res", JSONObject()))
                }
                val ans = JSONObject().put("seq", req.lng("seq")).put("results", results).put("token", req.str("token"))
                    .put("time", "2026-11-20T21:00:00").put("backups", offer).put("epoch", epoch)
                return Crypto.seal(key, ans, device.toByteArray())
            }
            "/sdj/rebuild" -> {
                rebuildEnv = body
                val k = Crypto.pairKey(code, pc)
                val req = Crypto.unseal(k, body, "rebuild".toByteArray())
                val summary = JSONObject().put("products", req.obj("data").arr("products").length())
                    .put("customers", req.obj("data").arr("customers").length()).put("open_jobs", 1)
                return Crypto.seal(k, JSONObject().put("ok", true).put("hello", req.str("hello")).put("summary", summary),
                    "rebuild".toByteArray())
            }
        }
        throw IOException("unknown path $path")
    }

    override fun download(host: String, port: Int, path: String, body: JSONObject, dest: File, readTimeoutMs: Int): String {
        val req = Crypto.unseal(key, body, "$device|backup".toByteArray())
        val bytes = files[req.lng("id")] ?: throw PcError("This backup is no longer on the PC.", "gone", 404)
        dest.writeBytes(bytes)
        return sha(bytes)
    }

    override fun upload(host: String, port: Int, path: String, auth: String, size: Long, source: () -> InputStream,
                        readTimeoutMs: Int): JSONObject {
        uploadAuth = auth
        uploaded = source().use { it.readBytes() }
        val k = Crypto.pairKey(code, pc)
        val meta = Crypto.unseal(k, JSONObject(auth), "restore".toByteArray())
        return Crypto.seal(k, JSONObject().put("ok", true).put("hello", meta.str("hello")), "restore".toByteArray())
    }

    override fun hello(host: String, port: Int): JSONObject = JSONObject().put("pc", pc)
    override fun discover(pcId: String?, timeoutMs: Int): List<Pair<String, JSONObject>> = emptyList()
}

class FakeDrive(private val file: File) : DriveTarget {
    var writes = 0
    override fun write(uri: String, src: File) {
        writes++
        src.copyTo(file, overwrite = true)
    }
    override fun title(uri: String): String = "SDJ_Backup_latest.sdjb"
}

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class BackupLogicTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private val dev = Fixtures.json("device.json")
    private val key = Crypto.unb64(dev.getString("key"))
    private val device = dev.getString("device")

    private fun meta(id: Long, kind: String, name: String, bytes: ByteArray): JSONObject = JSONObject().put("id", id)
        .put("kind", kind).put("name", name).put("size", bytes.size).put("sha", sha(bytes)).put("date", "2026-11-20")
        .put("created", "2026-11-20T21:00:00")

    @Test
    fun keepsLast30DaysAndOneBackupPerMonth() {
        val start = LocalDate.of(2026, 1, 1)
        val items = (0 until 120).map { i ->
            val d = start.plusDays(i.toLong())
            BackupItem(i + 1L, "BACKUP", "b$i.sdjb", 10, "", d.toString(), "${d}T21:00:00", "file:/x/b$i", "${d}T21:05:00")
        } + BackupItem(500, "REPORT", "r.pdf", 1, "", "2026-01-02", "", "file:/x/r", "2026-01-02T21:00:00")
        val keep = Backups.keepSet(items, 30)
        val newest = items.filter { !it.isReport }.sortedByDescending { it.date }.take(30).map { it.uri }.toSet()
        assertTrue(keep.containsAll(newest))
        assertTrue("file:/x/r" in keep)
        val monthly = keep - newest - "file:/x/r"
        assertEquals(setOf("2026-01-31", "2026-02-28", "2026-03-31"), monthly.map { u -> items.first { it.uri == u }.date }.toSet())
    }

    @Test
    fun closingBackupArrivesWithTheSyncAndGoesToDownloadsAndGoogleDrive() = runBlocking {
        val net = FakePcNet(key, device)
        val bundle = "SDJB1\n".toByteArray() + ByteArray(5000) { (it % 251).toByte() }
        val pdf = "%PDF-1.4 closing report".toByteArray()
        net.files[11] = bundle
        net.files[12] = pdf
        net.offer = JSONArray().put(meta(11, "BACKUP", "SDJ_Shop_2026-11-20_210000_closing.sdjb", bundle))
            .put(meta(12, "REPORT", "SDJ_Shop_Closing_2026-11-20.pdf", pdf))
        val dir = tmp.newFolder()
        val downloads = File(dir, "Download/SDJ Backups")
        val driveFile = File(dir, "drive.sdjb")
        val drive = FakeDrive(driveFile)
        var wifi = false
        val backups = Backups(dir, FileSink(downloads), drive) { wifi }
        val r = testRepo(dir, net, backups)
        r.load()
        r.signIn("admin123", true)
        backups.setDrive("content://drive/sdj")
        assertTrue(r.syncOnce())
        val st = backups.state.value
        assertEquals(2, st.items.size)
        assertArrayEquals(bundle, File(downloads, "SDJ_Shop_2026-11-20_210000_closing.sdjb").readBytes())
        assertArrayEquals(pdf, File(downloads, "Reports/SDJ_Shop_Closing_2026-11-20.pdf").readBytes())
        assertTrue(st.drivePending)                          // "only on Wi-Fi" and the phone is on mobile data
        assertFalse(driveFile.exists())
        assertEquals("SDJ_Shop_2026-11-20_210000_closing.sdjb", st.latestBackup?.name)
        // next sync: the phone tells the PC what it saved; on Wi-Fi the Google Drive copy is made
        net.offer = JSONArray()
        wifi = true
        assertTrue(r.syncOnce())
        val acks = net.requests.last().arr("acks")
        assertEquals(setOf(sha(bundle), sha(pdf)), (0 until acks.length()).map { acks.getString(it) }.toSet())
        assertArrayEquals(bundle, driveFile.readBytes())
        assertFalse(backups.state.value.drivePending)
        assertTrue(backups.state.value.driveLast.isNotEmpty())
        // a backup that arrives damaged is not kept (and is tried again next time)
        net.files[13] = bundle
        net.offer = JSONArray().put(meta(13, "BACKUP", "SDJ_Shop_2026-11-21_210000_closing.sdjb", bundle).put("sha", "0".repeat(64)))
        assertTrue(r.syncOnce())
        assertEquals(2, backups.state.value.items.size)
        assertTrue(backups.state.value.error.isNotEmpty())
        assertEquals(1, drive.writes)
    }

    @Test
    fun entriesAreSentAgainWhenThePcWasRestoredFromABackup() = runBlocking {
        val net = FakePcNet(key, device)
        val r = testRepo(tmp.newFolder(), net)
        r.load()
        r.signIn("admin123", true)
        addSampleEntries(r)
        assertEquals(7, r.state.value.pending.size)
        assertTrue(r.syncOnce())                            // the PC saves all of them
        assertEquals(0, r.state.value.pending.size)
        net.epoch = "e2"                                    // the PC's data was restored from an older backup
        assertTrue(r.syncOnce())
        assertEquals(7, r.state.value.pending.size)         // queued again
        assertTrue(r.state.value.notices.first().contains("restored"))
        assertTrue(r.syncOnce())                            // sent again – the PC saves each entry only once
        assertEquals(7, net.requests.last().arr("ops").length())
        assertEquals(0, r.state.value.pending.size)
        assertTrue(r.syncOnce())
        assertEquals(0, net.requests.last().arr("ops").length())
    }

    @Test
    fun backupGoesToANewPcAndTheShopCopyCanRebuildIt() = runBlocking {
        val net = FakePcNet(key, device)
        val dir = tmp.newFolder()
        val backups = Backups(dir, FileSink(File(dir, "Download/SDJ Backups")), null)
        val r = testRepo(dir, net, backups)
        r.load()
        r.signIn("admin123", true)
        val bundle = "SDJB1\n".toByteArray() + ByteArray(3000) { 7 }
        val item = r.importBackup("SDJ_Shop_2026-11-20_210000_closing.sdjb") { bundle.inputStream() }
        assertEquals(1, backups.state.value.items.size)
        try {
            r.importBackup("notes.txt") { "hello".byteInputStream() }
            fail("a file that is not a backup was accepted")
        } catch (e: UserError) {
            // expected
        }
        val qr = r.parseRestoreQr("SDJRESTORE:1;h=127.0.0.1;p=47810;c=ABCDE-FGHJK;id=PCNEW;n=New PC")!!
        assertEquals(listOf("127.0.0.1"), qr.hosts)
        r.sendToPc(item, qr, "Owner phone")
        assertArrayEquals(bundle, net.uploaded)
        val m = Crypto.unseal(Crypto.pairKey("ABCDEFGHJK", "PCNEW"), JSONObject(net.uploadAuth), "restore".toByteArray())
        assertEquals(bundle.size.toLong(), m.getLong("size"))
        assertEquals(sha(bundle), m.str("sha"))
        assertEquals("Owner phone", m.str("phone"))
        try {
            r.sendToPc(item, qr.copy(code = "WRONGCODE9"), "Owner phone")
            fail("wrong code accepted")
        } catch (e: UserError) {
            // the fake PC cannot read it → wrong code
        } catch (e: Exception) {
            // the fake PC throws while reading the header – also fine
        }
        val sum = r.rebuildPc(qr, "Owner phone")
        assertTrue(sum.optInt("products") > 0)
        val req = Crypto.unseal(Crypto.pairKey("ABCDEFGHJK", "PCNEW"), net.rebuildEnv!!, "rebuild".toByteArray())
        assertEquals(device, req.obj("device").str("uid"))
        assertEquals(dev.getString("key"), req.obj("device").str("key"))      // the phone stays linked to the rebuilt PC
        assertTrue(req.obj("data").arr("users").length() > 0)
        assertTrue(req.obj("data").obj("shop").str("pc").isNotEmpty())
        assertTrue(req.obj("data").arr("products").length() > 0)
    }

    @Test
    fun newestBackupOfEachDayIsKept() {
        val items = (1..10).flatMap { d ->
            listOf("09", "13", "21").map { h ->
                BackupItem(d * 100L + h.toLong(), "BACKUP", "b$d-$h", 1, "", "2026-11-%02d".format(d),
                    "2026-11-%02dT$h:00:00".format(d), "file:/x/$d-$h", "")
            }
        }
        val keep = Backups.keepSet(items, 5)
        assertEquals((6..10).map { "file:/x/$it-21" }.toSet(), keep)
    }
}

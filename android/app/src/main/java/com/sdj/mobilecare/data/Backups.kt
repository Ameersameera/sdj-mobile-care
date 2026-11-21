package com.sdj.mobilecare.data

import android.Manifest
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.security.MessageDigest
import java.time.LocalDateTime

/** One backup (or daily closing report) kept on this phone. [id] is the PC's number; files added by hand are < 0. */
data class BackupItem(
    val id: Long, val kind: String, val name: String, val size: Long, val sha: String, val date: String,
    val created: String, val uri: String, val savedAt: String,
) {
    val isReport: Boolean get() = kind == "REPORT"
    val stamp: String get() = date.ifBlank { created.take(10).ifBlank { savedAt.take(10) } } + "|" + created.ifBlank { savedAt }

    fun toJson(): JSONObject = JSONObject().put("id", id).put("kind", kind).put("name", name).put("size", size)
        .put("sha", sha).put("date", date).put("created", created).put("uri", uri).put("at", savedAt)

    companion object {
        fun of(j: JSONObject) = BackupItem(j.lng("id"), j.str("kind", "BACKUP"), j.str("name"), j.lng("size"), j.str("sha"),
            j.str("date"), j.str("created"), j.str("uri"), j.str("at"))
    }
}

data class BackupState(
    val items: List<BackupItem> = emptyList(),
    val keepAll: Boolean = false,
    val lastSaved: String = "",
    val error: String = "",
    val ready: Boolean = true,
    val place: String = "",
    val driveUri: String = "",
    val driveName: String = "",
    val driveWifiOnly: Boolean = true,
    val driveLast: String = "",
    val driveError: String = "",
    val drivePending: Boolean = false,
) {
    val latestBackup: BackupItem? get() = items.firstOrNull { !it.isReport }
}

/** Where backups are kept so that they stay even if the app is removed: Downloads → SDJ Backups. */
interface BackupSink {
    val place: String
    fun ready(): Boolean = true
    fun save(name: String, report: Boolean, src: File): String
    fun open(uri: String): InputStream?
    fun delete(uri: String)
}

private fun fileOf(uri: String): File? = if (uri.startsWith("file:")) runCatching { File(URI(uri)) }.getOrNull() else null

/** Plain folder (tests, and Android 8/9 where Downloads is a normal folder). */
open class FileSink(private val dir: File, override val place: String = "Downloads → SDJ Backups") : BackupSink {
    override fun save(name: String, report: Boolean, src: File): String {
        val folder = (if (report) File(dir, "Reports") else dir).apply { mkdirs() }
        val dest = File(folder, name)
        src.copyTo(dest, overwrite = true)
        return dest.toURI().toString()
    }

    override fun open(uri: String): InputStream? = fileOf(uri)?.takeIf { it.exists() }?.inputStream()
    override fun delete(uri: String) {
        fileOf(uri)?.delete()
    }
}

/** The phone's Downloads folder: MediaStore on Android 10+, the public folder (with permission) on Android 8/9. */
class DownloadsSink(private val ctx: Context) : BackupSink {
    override val place = "Downloads → SDJ Backups"
    private val legacy: Boolean get() = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q

    override fun ready(): Boolean = !legacy ||
        ctx.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED

    @Suppress("DEPRECATION")
    private fun legacySink(): FileSink =
        FileSink(File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "SDJ Backups"))

    override fun save(name: String, report: Boolean, src: File): String {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return legacySink().save(name, report, src)
        val resolver = ctx.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            put(MediaStore.MediaColumns.MIME_TYPE, if (report) "application/pdf" else "application/octet-stream")
            put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/SDJ Backups" + if (report) "/Reports" else "")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IOException("Could not create the file in Downloads.")
        try {
            val out = resolver.openOutputStream(uri) ?: throw IOException("Could not write to Downloads.")
            out.use { o -> src.inputStream().use { it.copyTo(o, 64 * 1024) } }
            val done = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
            resolver.update(uri, done, null, null)
        } catch (e: Exception) {
            runCatching { resolver.delete(uri, null, null) }
            throw if (e is IOException) e else IOException(e.message ?: "Could not save the backup.")
        }
        return uri.toString()
    }

    override fun open(uri: String): InputStream? {
        fileOf(uri)?.let { return if (it.exists()) it.inputStream() else null }
        return runCatching { ctx.contentResolver.openInputStream(Uri.parse(uri)) }.getOrNull()
    }

    override fun delete(uri: String) {
        val f = fileOf(uri)
        if (f != null) {
            f.delete()
            return
        }
        runCatching { ctx.contentResolver.delete(Uri.parse(uri), null, null) }
    }
}

/** The Google Drive file the owner chose once (through Android's file picker). Each new backup replaces its contents;
 *  Google Drive keeps the earlier versions for a while. */
interface DriveTarget {
    fun write(uri: String, src: File)
    fun title(uri: String): String
}

class SafDrive(private val ctx: Context) : DriveTarget {
    override fun write(uri: String, src: File) {
        val u = Uri.parse(uri)
        val out = runCatching { ctx.contentResolver.openOutputStream(u, "wt") }.getOrNull()
            ?: ctx.contentResolver.openOutputStream(u, "w")
            ?: throw IOException("The Google Drive file is not available – choose it again.")
        out.use { o -> src.inputStream().use { it.copyTo(o, 64 * 1024) } }
    }

    override fun title(uri: String): String = runCatching {
        ctx.contentResolver.query(Uri.parse(uri), arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }
    }.getOrNull() ?: "Google Drive file"
}

/**
 * Backups kept on this phone: what was saved where, the "keep" rule (last 30 daily backups and one per month, or all),
 * and the copy to Google Drive. The list lives in its own file, so unlinking the phone does not forget it.
 */
class Backups(private val dir: File, val sink: BackupSink, private val drive: DriveTarget?,
              private val unmetered: () -> Boolean = { true }) {
    private val file = File(dir, "backups.json")
    private var data: JSONObject = read()
    private val _state = MutableStateFlow(snapshot())
    val state: StateFlow<BackupState> = _state

    private fun read(): JSONObject = try {
        if (file.exists()) JSONObject(file.readText()) else JSONObject()
    } catch (e: Exception) {
        JSONObject()
    }

    private fun write() {
        dir.mkdirs()
        val tmp = File(dir, "backups.json.tmp")
        tmp.writeText(data.toString())
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
        _state.value = snapshot()
    }

    private fun list(): MutableList<BackupItem> {
        val a = data.arr("items")
        return (0 until a.length()).map { BackupItem.of(a.getJSONObject(it)) }.toMutableList()
    }

    private fun putList(items: List<BackupItem>) {
        data.put("items", JSONArray().apply { items.sortedByDescending { it.stamp }.forEach { put(it.toJson()) } })
    }

    private fun snapshot(): BackupState {
        val d = data.obj("drive")
        return BackupState(items = list().sortedByDescending { it.stamp }, keepAll = data.bool("keep_all"),
            lastSaved = data.str("last_saved"), error = data.str("error"), ready = sink.ready(), place = sink.place,
            driveUri = d.str("uri"), driveName = d.str("name"), driveWifiOnly = d.bool("wifi_only", true),
            driveLast = d.str("last"), driveError = d.str("error"), drivePending = d.bool("pending"))
    }

    fun refresh() {
        _state.value = snapshot()
    }

    private fun now() = LocalDateTime.now().withNano(0).toString()

    /** Already saved? Checked by the file's SHA-256 – a restored or new PC numbers its backups again. */
    @Synchronized fun has(sha: String): Boolean = sha.isNotEmpty() && list().any { it.sha == sha }

    /** SHA-256 of what this phone saved lately – sent to the PC so it knows the phone has them. */
    @Synchronized fun recentShas(): List<String> {
        val since = LocalDateTime.now().minusDays(10).toString()
        return list().filter { it.id > 0 && it.sha.length == 64 && it.savedAt >= since }.map { it.sha }.take(40)
    }

    @Synchronized fun setError(msg: String) {
        data.put("error", msg)
        write()
    }

    @Synchronized fun setKeepAll(on: Boolean) {
        data.put("keep_all", on)
        write()
        if (!on) retain()
    }

    /** A file downloaded from the PC (already checked against its SHA-256) is kept in Downloads → SDJ Backups. */
    @Synchronized fun saveDownloaded(meta: JSONObject, tmp: File): BackupItem {
        val kind = meta.str("kind", "BACKUP")
        val name = meta.str("name").ifBlank { "SDJ_backup_${System.currentTimeMillis()}.sdjb" }
        val uri = sink.save(name, kind == "REPORT", tmp)
        val item = BackupItem(meta.lng("id"), kind, name, tmp.length(), meta.str("sha"), meta.str("date"),
            meta.str("created"), uri, now())
        val items = list().filter { it.uri != uri && it.sha != item.sha }.toMutableList()
        items += item
        putList(items)
        data.put("last_saved", item.savedAt).put("error", "")
        if (!item.isReport && data.obj("drive").str("uri").isNotEmpty()) data.put("drive", data.obj("drive").put("pending", true))
        write()
        retain()
        if (!item.isReport) copyToDrive(false)
        return item
    }

    /** A backup file chosen by hand (e.g. downloaded from Google Drive on a new phone), so it can be sent to a PC. */
    @Synchronized fun importFile(name: String, input: InputStream, work: File): BackupItem {
        val tmp = File(work, "import.part")
        val md = MessageDigest.getInstance("SHA-256")
        input.use { ins ->
            tmp.outputStream().use { out ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = ins.read(buf)
                    if (n < 0) break
                    md.update(buf, 0, n)
                    out.write(buf, 0, n)
                }
            }
        }
        try {
            val head = tmp.inputStream().use { s -> ByteArray(6).also { s.read(it) } }
            val report = name.endsWith(".pdf", true)
            if (!report && String(head, Charsets.ISO_8859_1) != "SDJB1\n")
                throw UserError("This is not an SDJ Mobile Care backup (.sdjb) file.")
            val clean = name.ifBlank { "SDJ_backup.sdjb" }
            val uri = sink.save(clean, report, tmp)
            val item = BackupItem(-System.currentTimeMillis(), if (report) "REPORT" else "BACKUP", clean, tmp.length(),
                md.digest().joinToString("") { "%02x".format(it) }, "", "", uri, now())
            putList(list() + item)
            write()
            return item
        } finally {
            tmp.delete()
        }
    }

    @Synchronized fun remove(item: BackupItem) {
        runCatching { sink.delete(item.uri) }
        putList(list().filter { it.uri != item.uri })
        write()
    }

    /** Keeps the newest backup of each of the last 30 days and of every month (unless "keep all"); reports are all kept. */
    @Synchronized fun retain(keepDaily: Int = 30) {
        if (data.bool("keep_all")) return
        val items = list()
        val keep = keepSet(items, keepDaily)
        val gone = items.filter { it.uri !in keep }
        if (gone.isEmpty()) return
        gone.forEach { runCatching { sink.delete(it.uri) } }
        putList(items.filter { it.uri in keep })
        write()
    }

    // ---- Google Drive
    @Synchronized fun setDrive(uri: String) {
        val name = drive?.title(uri) ?: "Google Drive file"
        data.put("drive", JSONObject().put("uri", uri).put("name", name).put("wifi_only", data.obj("drive").bool("wifi_only", true))
            .put("pending", true))
        write()
    }

    @Synchronized fun clearDrive() {
        data.put("drive", JSONObject().put("wifi_only", data.obj("drive").bool("wifi_only", true)))
        write()
    }

    @Synchronized fun setDriveWifiOnly(on: Boolean) {
        data.put("drive", data.obj("drive").put("wifi_only", on))
        write()
    }

    /** Copies the newest backup to the chosen Google Drive file. Returns true when it was copied now. */
    @Synchronized fun copyToDrive(force: Boolean): Boolean {
        val d = data.obj("drive")
        val uri = d.str("uri")
        if (uri.isEmpty() || drive == null) return false
        if (!force && !d.bool("pending")) return false
        if (!force && d.bool("wifi_only", true) && !unmetered()) {
            data.put("drive", d.put("pending", true).put("error", "Waiting for Wi-Fi (set to copy only on Wi-Fi)."))
            write()
            return false
        }
        val latest = list().filter { !it.isReport }.maxByOrNull { it.stamp } ?: return false
        val tmp = File(dir, "drive.part")
        return try {
            val ins = sink.open(latest.uri) ?: throw IOException("The backup file is missing from Downloads.")
            ins.use { i -> tmp.outputStream().use { i.copyTo(it, 64 * 1024) } }
            drive.write(uri, tmp)
            data.put("drive", d.put("pending", false).put("last", now()).put("error", "").put("copied", latest.name))
            write()
            true
        } catch (e: Exception) {
            data.put("drive", d.put("pending", true).put("error", "Could not copy to Google Drive: ${e.message}"))
            write()
            false
        } finally {
            tmp.delete()
        }
    }

    companion object {
        /** The newest backup of each of the last [keepDaily] days (the PC makes several a day), the newest of every month
         *  (kept for good), and all closing reports. */
        fun keepSet(items: List<BackupItem>, keepDaily: Int = 30): Set<String> {
            val backups = items.filter { !it.isReport }.sortedByDescending { it.stamp }
            val keep = mutableSetOf<String>()
            backups.firstOrNull()?.let { keep += it.uri }
            val days = LinkedHashSet<String>()
            val months = HashSet<String>()
            for (b in backups) {
                val d = b.stamp.take(10)
                if (days.size < keepDaily && d.length == 10 && days.add(d)) keep += b.uri
                val m = b.stamp.take(7)
                if (m.length == 7 && months.add(m)) keep += b.uri
            }
            items.filter { it.isReport }.forEach { keep += it.uri }
            return keep
        }

        fun sha256(input: InputStream): Pair<Long, String> {
            val md = MessageDigest.getInstance("SHA-256")
            var size = 0L
            input.use { ins ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = ins.read(buf)
                    if (n < 0) break
                    md.update(buf, 0, n)
                    size += n
                }
            }
            return size to md.digest().joinToString("") { "%02x".format(it) }
        }
    }
}

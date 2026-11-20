package com.sdj.mobilecare.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.nio.ByteBuffer
import java.security.MessageDigest

/** What the phone keeps on disk: settings, the PC's data (one file per list) and entries not yet synced. */
class Store(val dir: File) {
    private val dataDir = File(dir, "data").apply { mkdirs() }
    private val cfgFile = File(dir, "config.json")
    private val opsFile = File(dir, "ops.json")
    private val sentFile = File(dir, "sent.json")

    var config: JSONObject = read(cfgFile)?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
        private set

    fun saveConfig() = write(cfgFile, config.toString())
    fun data(name: String): String? = read(File(dataDir, "$name.json"))
    fun putData(name: String, json: String) = write(File(dataDir, "$name.json"), json)
    fun ops(): JSONArray = read(opsFile)?.let { runCatching { JSONArray(it) }.getOrNull() } ?: JSONArray()
    fun saveOps(a: JSONArray) = write(opsFile, a.toString())

    /** Entries the PC already saved (last 10 days) – sent again if the PC's data is restored from a backup. */
    fun sent(): JSONArray = read(sentFile)?.let { runCatching { JSONArray(it) }.getOrNull() } ?: JSONArray()
    fun saveSent(a: JSONArray) = write(sentFile, a.toString())

    fun wipe() {
        dataDir.deleteRecursively()
        dataDir.mkdirs()
        cfgFile.delete()
        opsFile.delete()
        sentFile.delete()
        config = JSONObject()
    }

    private fun read(f: File): String? = try { if (f.exists()) f.readText() else null } catch (e: Exception) { null }

    private fun write(f: File, text: String) {
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(text)
        if (!tmp.renameTo(f)) {
            f.delete()
            tmp.renameTo(f)
        }
    }
}

class PcError(message: String, val code: String = "error", val status: Int = 0) : Exception(message)

/** Talks to the PC on the shop Wi-Fi only (no internet). */
open class Net(private val context: Context?) {
    companion object {
        const val PORT = 47810
        const val UDP_PORT = 47811
    }

    /** The Wi-Fi network, so the PC is reached over Wi-Fi even when mobile data is the phone's default. */
    fun wifi(): Network? {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return null
        val active = cm.activeNetwork
        if (active != null && cm.getNetworkCapabilities(active)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true) return active
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }

    /**
     * Ways to reach the PC, best first: the phone's normal route when it is on Wi-Fi, then every Wi-Fi network
     * (some phones keep two Wi-Fi links, or prefer mobile data when the Wi-Fi has no internet).
     */
    fun routes(): List<Network?> {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return listOf(null)
        @Suppress("DEPRECATION")
        val wifis = cm.allNetworks.filter { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
        val active = cm.activeNetwork
        return if (active != null && active in wifis) listOf<Network?>(null) + wifis.filter { it != active }
        else wifis + listOf<Network?>(null)
    }

    private fun open(net: Network?, url: URL, connectMs: Int, readMs: Int): HttpURLConnection {
        val conn = ((net?.openConnection(url)) ?: url.openConnection()) as HttpURLConnection
        conn.connectTimeout = connectMs
        conn.readTimeout = readMs
        return conn
    }

    /** Connects over the first route that works; once connected, errors are not retried (no double sending). */
    private fun <T> viaRoutes(url: URL, connectMs: Int, readMs: Int, method: String, contentType: String? = null,
                              headers: Map<String, String> = emptyMap(), fixedLength: Long = -1L,
                              use: (HttpURLConnection) -> T): T {
        var last: IOException? = null
        for (r in routes()) {
            val conn = open(r, url, connectMs, readMs)
            try {
                conn.requestMethod = method
                if (method == "POST") conn.doOutput = true
                if (contentType != null) conn.setRequestProperty("Content-Type", contentType)
                headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
                if (fixedLength >= 0) conn.setFixedLengthStreamingMode(fixedLength)
                try {
                    conn.connect()
                } catch (e: IOException) {
                    last = e
                    continue
                }
                return use(conn)
            } finally {
                conn.disconnect()
            }
        }
        throw last ?: IOException("No network")
    }

    open fun post(host: String, port: Int, path: String, body: JSONObject, readTimeoutMs: Int = 60_000): JSONObject {
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        return viaRoutes(URL("http://$host:$port$path"), 3500, readTimeoutMs, "POST", "application/json") { conn ->
            conn.outputStream.use { it.write(bytes) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.use { String(it.readBytes(), Charsets.UTF_8) } ?: ""
            if (code !in 200..299) {
                val j = runCatching { JSONObject(text) }.getOrNull() ?: JSONObject()
                throw PcError(j.str("error", "The PC answered with error $code."), j.str("code", "http"), code)
            }
            JSONObject(text)
        }
    }

    private fun fail(conn: HttpURLConnection, code: Int): Nothing {
        val text = conn.errorStream?.use { String(it.readBytes(), Charsets.UTF_8) } ?: ""
        val j = runCatching { JSONObject(text) }.getOrNull() ?: JSONObject()
        throw PcError(j.str("error", "The PC answered with error $code."), j.str("code", "http"), code)
    }

    /** Downloads a backup into [dest] (encrypted request); returns the SHA-256 of what arrived. */
    open fun download(host: String, port: Int, path: String, body: JSONObject, dest: File, readTimeoutMs: Int = 120_000): String {
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        return viaRoutes(URL("http://$host:$port$path"), 3500, readTimeoutMs, "POST", "application/json") { conn ->
            conn.outputStream.use { it.write(bytes) }
            val code = conn.responseCode
            if (code !in 200..299) fail(conn, code)
            val md = MessageDigest.getInstance("SHA-256")
            conn.inputStream.use { ins ->
                dest.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = ins.read(buf)
                        if (n < 0) break
                        md.update(buf, 0, n)
                        out.write(buf, 0, n)
                    }
                }
            }
            md.digest().joinToString("") { "%02x".format(it) }
        }
    }

    /** Sends a file as the raw body to a PC that shows a restore code; [auth] proves the phone knows the code. */
    open fun upload(host: String, port: Int, path: String, auth: String, size: Long, source: () -> InputStream,
                    readTimeoutMs: Int = 180_000): JSONObject =
        viaRoutes(URL("http://$host:$port$path"), 3500, readTimeoutMs, "POST", "application/octet-stream",
            mapOf("X-SDJ-Auth" to auth), size) { conn ->
            conn.outputStream.use { out -> source().use { it.copyTo(out, 64 * 1024) } }
            val code = conn.responseCode
            if (code !in 200..299) fail(conn, code)
            JSONObject(conn.inputStream.use { String(it.readBytes(), Charsets.UTF_8) })
        }

    /** True on Wi-Fi (or another network that is not charged by the MB). */
    open fun unmetered(): Boolean {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return true
        val caps = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }

    open fun hello(host: String, port: Int): JSONObject =
        viaRoutes(URL("http://$host:$port/sdj/hello"), 3000, 5000, "GET") { conn ->
            if (conn.responseCode != 200) throw IOException("Not an SDJ PC")
            JSONObject(conn.inputStream.use { String(it.readBytes(), Charsets.UTF_8) })
        }

    /** Asks every PC on the Wi-Fi "where is SDJ?" and returns (address, info) for each answer. */
    open fun discover(pcId: String?, timeoutMs: Int = 1600): List<Pair<String, JSONObject>> {
        val found = LinkedHashMap<String, JSONObject>()
        val routes = routes()
        val each = maxOf(700, timeoutMs / routes.size)
        for (r in routes) {
            try {
                DatagramSocket().use { sock ->
                    if (r != null) runCatching { r.bindSocket(sock) }
                    sock.broadcast = true
                    sock.soTimeout = 300
                    val msg = ("SDJ?" + (pcId ?: "")).toByteArray()
                    val targets = (broadcastAddresses(r) + InetAddress.getByName("255.255.255.255")).distinct()
                    repeat(2) {
                        for (t in targets) runCatching { sock.send(DatagramPacket(msg, msg.size, t, UDP_PORT)) }
                    }
                    val end = System.currentTimeMillis() + each
                    val buf = ByteArray(4096)
                    while (System.currentTimeMillis() < end) {
                        try {
                            val p = DatagramPacket(buf, buf.size)
                            sock.receive(p)
                            val s = String(p.data, 0, p.length, Charsets.UTF_8)
                            val addr = p.address?.hostAddress
                            if (s.startsWith("SDJ!") && addr != null) {
                                val j = JSONObject(s.substring(4))
                                if (pcId == null || j.str("pc") == pcId) found[addr] = j
                            }
                        } catch (e: SocketTimeoutException) {
                            // keep listening until the time is up
                        }
                    }
                }
            } catch (e: Exception) {
                // this route has no network – try the next one
            }
            if (found.isNotEmpty()) break
        }
        return found.entries.map { it.key to it.value }
    }

    private fun broadcastAddresses(route: Network?): List<InetAddress> {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return emptyList()
        val net = route ?: wifi() ?: cm.activeNetwork ?: return emptyList()
        val lp = cm.getLinkProperties(net) ?: return emptyList()
        return lp.linkAddresses.mapNotNull { la ->
            val a = la.address
            if (a is Inet4Address && la.prefixLength in 1..31) {
                val ip = ByteBuffer.wrap(a.address).int
                val mask = -1 shl (32 - la.prefixLength)
                InetAddress.getByAddress(ByteBuffer.allocate(4).putInt(ip or mask.inv()).array())
            } else null
        }
    }
}

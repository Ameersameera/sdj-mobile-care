package com.sdj.mobilecare.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URL
import java.nio.ByteBuffer

/** What the phone keeps on disk: settings, the PC's data (one file per list) and entries not yet synced. */
class Store(val dir: File) {
    private val dataDir = File(dir, "data").apply { mkdirs() }
    private val cfgFile = File(dir, "config.json")
    private val opsFile = File(dir, "ops.json")

    var config: JSONObject = read(cfgFile)?.let { runCatching { JSONObject(it) }.getOrNull() } ?: JSONObject()
        private set

    fun saveConfig() = write(cfgFile, config.toString())
    fun data(name: String): String? = read(File(dataDir, "$name.json"))
    fun putData(name: String, json: String) = write(File(dataDir, "$name.json"), json)
    fun ops(): JSONArray = read(opsFile)?.let { runCatching { JSONArray(it) }.getOrNull() } ?: JSONArray()
    fun saveOps(a: JSONArray) = write(opsFile, a.toString())

    fun wipe() {
        dataDir.deleteRecursively()
        dataDir.mkdirs()
        cfgFile.delete()
        opsFile.delete()
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
        @Suppress("DEPRECATION")
        return cm.allNetworks.firstOrNull { cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true }
    }

    open fun post(host: String, port: Int, path: String, body: JSONObject, readTimeoutMs: Int = 60_000): JSONObject {
        val url = URL("http://$host:$port$path")
        val conn = ((wifi()?.openConnection(url)) ?: url.openConnection()) as HttpURLConnection
        try {
            conn.connectTimeout = 3500
            conn.readTimeout = readTimeoutMs
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            conn.setFixedLengthStreamingMode(bytes.size)
            conn.outputStream.use { it.write(bytes) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)?.use { String(it.readBytes(), Charsets.UTF_8) } ?: ""
            if (code !in 200..299) {
                val j = runCatching { JSONObject(text) }.getOrNull() ?: JSONObject()
                throw PcError(j.str("error", "The PC answered with error $code."), j.str("code", "http"), code)
            }
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    open fun hello(host: String, port: Int): JSONObject {
        val url = URL("http://$host:$port/sdj/hello")
        val conn = ((wifi()?.openConnection(url)) ?: url.openConnection()) as HttpURLConnection
        try {
            conn.connectTimeout = 3000
            conn.readTimeout = 5000
            if (conn.responseCode != 200) throw IOException("Not an SDJ PC")
            return JSONObject(conn.inputStream.use { String(it.readBytes(), Charsets.UTF_8) })
        } finally {
            conn.disconnect()
        }
    }

    /** Asks every PC on the Wi-Fi "where is SDJ?" and returns (address, info) for each answer. */
    open fun discover(pcId: String?, timeoutMs: Int = 1600): List<Pair<String, JSONObject>> {
        val found = LinkedHashMap<String, JSONObject>()
        try {
            DatagramSocket().use { sock ->
                wifi()?.let { runCatching { it.bindSocket(sock) } }
                sock.broadcast = true
                sock.soTimeout = 350
                val msg = ("SDJ?" + (pcId ?: "")).toByteArray()
                val targets = (broadcastAddresses() + InetAddress.getByName("255.255.255.255")).distinct()
                repeat(2) {
                    for (t in targets) runCatching { sock.send(DatagramPacket(msg, msg.size, t, UDP_PORT)) }
                }
                val end = System.currentTimeMillis() + timeoutMs
                val buf = ByteArray(4096)
                while (System.currentTimeMillis() < end) {
                    try {
                        val p = DatagramPacket(buf, buf.size)
                        sock.receive(p)
                        val s = String(p.data, 0, p.length, Charsets.UTF_8)
                        if (s.startsWith("SDJ!")) {
                            val j = JSONObject(s.substring(4))
                            if (pcId == null || j.str("pc") == pcId) found[p.address.hostAddress ?: continue] = j
                        }
                    } catch (e: SocketTimeoutException) {
                        // keep listening until the time is up
                    }
                }
            }
        } catch (e: Exception) {
            // no network – nothing found
        }
        return found.entries.map { it.key to it.value }
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val cm = context?.getSystemService(ConnectivityManager::class.java) ?: return emptyList()
        val net = wifi() ?: cm.activeNetwork ?: return emptyList()
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

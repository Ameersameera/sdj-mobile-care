package com.sdj.mobilecare.data

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.zip.Deflater
import java.util.zip.Inflater
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

class CryptoException(message: String) : Exception(message)

/** Same envelope as sdj/core/phonesync.py: zlib → AES-256-GCM (with associated data) → base64 JSON. */
object Crypto {
    private val rnd = SecureRandom()

    fun b64(b: ByteArray): String = Base64.getEncoder().encodeToString(b)
    fun unb64(s: String): ByteArray = Base64.getDecoder().decode(s)

    fun randomHex(bytes: Int): String = ByteArray(bytes).also { rnd.nextBytes(it) }.joinToString("") { "%02x".format(it) }

    fun pbkdf2(password: ByteArray, salt: ByteArray, iterations: Int, length: Int = 32): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(password, "HmacSHA256"))
        val out = ByteArray(length)
        var block = 1
        var pos = 0
        while (pos < length) {
            mac.update(salt)
            mac.update(byteArrayOf((block ushr 24).toByte(), (block ushr 16).toByte(), (block ushr 8).toByte(), block.toByte()))
            var u = mac.doFinal()
            val t = u.copyOf()
            for (i in 1 until iterations) {
                u = mac.doFinal(u)
                for (k in t.indices) t[k] = (t[k].toInt() xor u[k].toInt()).toByte()
            }
            val n = minOf(t.size, length - pos)
            System.arraycopy(t, 0, out, pos, n)
            pos += n
            block++
        }
        return out
    }

    /** Checks a PC password hash "pbkdf2_sha256$240000$<salt hex>$<digest hex>". */
    fun verifyPassword(password: String, stored: String): Boolean {
        val parts = stored.split("$")
        if (parts.size != 4 || parts[0] != "pbkdf2_sha256" || password.isEmpty()) return false
        return try {
            val digest = pbkdf2(password.toByteArray(Charsets.UTF_8), hex(parts[2]), parts[1].toInt(), 32)
            MessageDigest.isEqual(digest, hex(parts[3]))
        } catch (e: Exception) { false }
    }

    fun normaliseCode(code: String) = code.uppercase().filter { it.isLetterOrDigit() }

    fun pairKey(code: String, pcId: String): ByteArray =
        pbkdf2(normaliseCode(code).toByteArray(Charsets.UTF_8), "SDJ-LINK:$pcId".toByteArray(Charsets.UTF_8), 100_000)

    fun seal(key: ByteArray, obj: JSONObject, aad: ByteArray): JSONObject {
        val nonce = ByteArray(12).also { rnd.nextBytes(it) }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        c.updateAAD(aad)
        val ct = c.doFinal(deflate(obj.toString().toByteArray(Charsets.UTF_8)))
        return JSONObject().put("n", b64(nonce)).put("c", b64(ct))
    }

    fun unseal(key: ByteArray, env: JSONObject, aad: ByteArray): JSONObject {
        try {
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, unb64(env.getString("n"))))
            c.updateAAD(aad)
            return JSONObject(String(inflate(c.doFinal(unb64(env.getString("c")))), Charsets.UTF_8))
        } catch (e: Exception) {
            throw CryptoException("Could not read the PC's answer (wrong key).")
        }
    }

    private fun hex(s: String): ByteArray = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    private fun deflate(data: ByteArray): ByteArray {
        val d = Deflater(6)
        d.setInput(data)
        d.finish()
        val out = ByteArrayOutputStream(data.size / 2 + 64)
        val buf = ByteArray(16384)
        while (!d.finished()) out.write(buf, 0, d.deflate(buf))
        d.end()
        return out.toByteArray()
    }

    private fun inflate(data: ByteArray): ByteArray {
        val inf = Inflater()
        inf.setInput(data)
        val out = ByteArrayOutputStream(data.size * 4 + 64)
        val buf = ByteArray(65536)
        while (!inf.finished()) {
            val n = inf.inflate(buf)
            if (n == 0 && (inf.needsInput() || inf.needsDictionary())) break
            out.write(buf, 0, n)
        }
        inf.end()
        return out.toByteArray()
    }
}

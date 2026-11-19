package com.sdj.mobilecare

import com.sdj.mobilecare.data.Crypto
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class CryptoTest {
    private val c = Fixtures.json("crypto.json")

    @Test
    fun pcPasswordHashesWork() {
        val users = Fixtures.json("sync_full.json").getJSONObject("data").getJSONArray("users")
        val admin = (0 until users.length()).map { users.getJSONObject(it) }.first { it.getString("username") == "admin" }
        assertTrue(Crypto.verifyPassword("admin123", admin.getString("hash")))
        assertFalse(Crypto.verifyPassword("admin124", admin.getString("hash")))
        assertFalse(Crypto.verifyPassword("", admin.getString("hash")))
    }

    @Test
    fun pairingKeyIsTheSameAsOnThePc() {
        val k = Crypto.pairKey(c.getString("code"), c.getString("pc"))
        assertEquals(c.getString("pair_key"), k.joinToString("") { "%02x".format(it) })
        assertEquals(c.getString("pair_key"), Crypto.pairKey("qt26sdycv2", c.getString("pc")).joinToString("") { "%02x".format(it) })
    }

    @Test
    fun envelopesWorkBothWays() {
        val key = Crypto.unb64(c.getString("key"))
        val aad = c.getString("aad").toByteArray()
        val fromPc = Crypto.unseal(key, c.getJSONObject("envelope"), aad)
        assertEquals("வணக்கம்", fromPc.getString("hello"))
        assertEquals(12345, fromPc.getInt("n"))
        val mine = Crypto.seal(key, JSONObject().put("from", "phone").put("text", "Tamil: தமிழ் ₹1,250.50"), aad)
        assertEquals("phone", Crypto.unseal(key, mine, aad).getString("from"))
        Fixtures.out("kotlin_envelope.json").writeText(mine.toString())        // the CI checks it with the PC code
    }
}

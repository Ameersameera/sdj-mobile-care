package com.sdj.mobilecare

import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.BillDraft
import com.sdj.mobilecare.data.CustChoice
import com.sdj.mobilecare.data.DraftLine
import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.Store
import kotlinx.coroutines.Dispatchers
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException

object Fixtures {
    fun text(name: String): String = javaClass.classLoader!!.getResource("fixtures/$name")!!.readText()
    fun json(name: String) = JSONObject(text(name))
    fun out(name: String): File {
        val dir = File(System.getProperty("sdj.out") ?: "build/test-out").apply { mkdirs() }
        return File(dir, name)
    }
}

/** No PC in unit tests: every request fails like a phone that is away from the shop Wi-Fi. */
class OfflineNet : Net(null) {
    var lastBody: JSONObject? = null
    override fun post(host: String, port: Int, path: String, body: JSONObject, readTimeoutMs: Int): JSONObject {
        lastBody = body
        throw IOException("offline in test")
    }
    override fun hello(host: String, port: Int): JSONObject = throw IOException("offline in test")
    override fun discover(pcId: String?, timeoutMs: Int): List<Pair<String, JSONObject>> = emptyList()
}

/** A phone linked to the fixture shop with the PC's full data already downloaded. */
fun testRepo(dir: File, net: Net = OfflineNet(), backups: Backups? = null): Repo {
    val store = Store(dir)
    val dev = Fixtures.json("device.json")
    store.config.put("device", dev.getString("device")).put("key", dev.getString("key")).put("no", dev.getInt("no"))
        .put("pc", dev.getString("pc")).put("shop_name", dev.getString("shop")).put("user", dev.getJSONObject("user"))
        .put("seq", dev.getLong("seq")).put("token", dev.getString("token")).put("hashes", dev.getJSONObject("hashes"))
        .put("hosts", JSONArray().put("127.0.0.1")).put("port", Net.PORT).put("last_sync", "2026-11-19T17:44:00")
    store.saveConfig()
    val data = Fixtures.json("sync_full.json").getJSONObject("data")
    data.keys().forEach { k -> store.putData(k, data.get(k).toString()) }
    return if (backups != null) Repo(store, net, Dispatchers.Unconfined, "test", backups)
    else Repo(store, net, Dispatchers.Unconfined, "test")
}

/** The same kinds of entries a shop makes on the phone during a day away from the Wi-Fi. */
suspend fun addSampleEntries(r: Repo) {
    val st = r.state.value
    val charger = st.products.first { it.name == "Charger 20W" }
    val phone = st.products.first { it.name.startsWith("Vivo") }
    val display = st.products.first { it.name.startsWith("Display") }
    val ravi = st.customers.first { it.name == "Ravi Kumar" }
    val unit = st.imeis.first { it.product == phone.id }
    val cash = r.saveBill(BillDraft(listOf(DraftLine(charger, 2, charger.price)), pays = mapOf("CASH" to 79800)))
    r.saveBill(BillDraft(listOf(DraftLine(display, 1, display.price, 5000)), CustChoice.Existing(ravi), pays = mapOf("UPI" to 50000)))
    r.saveBill(BillDraft(listOf(DraftLine(phone, 1, phone.price, 0, unit)), CustChoice.New("Karthik", "9000011111"),
        billDisc = 99900, pays = mapOf("CASH" to 1000000, "UPI" to 300000)))
    val pending = r.state.value.sales.first { it.ref == cash.ref }
    r.saveBill(BillDraft(listOf(DraftLine(charger, 3, charger.price)), pays = mapOf("CASH" to 119700), editOf = pending))
    r.addExpense("Transportation", 6000, "CASH", "Courier")
    r.collectPayment(r.state.value.customers.first { it.name == "Meena S" }, 30000, "CASH", "")
    r.newJob(Repo.JobForm("Arul", "9000022222", "Redmi", "13C", "", "Back cover", "Display broken", "Kumar", 30000,
        null, 10000, "CASH", ""))
}

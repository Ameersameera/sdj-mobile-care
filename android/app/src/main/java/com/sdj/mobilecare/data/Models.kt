package com.sdj.mobilecare.data

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate

// ---------------------------------------------------------------- JSON helpers (null-safe)
fun JSONObject.str(k: String, d: String = ""): String = if (isNull(k)) d else optString(k, d)
fun JSONObject.lng(k: String, d: Long = 0L): Long = if (isNull(k)) d else optLong(k, d)
fun JSONObject.int(k: String, d: Int = 0): Int = if (isNull(k)) d else optInt(k, d)
fun JSONObject.bool(k: String, d: Boolean = false): Boolean = if (isNull(k)) d else optBoolean(k, d)
fun JSONObject.lngOrNull(k: String): Long? = if (isNull(k)) null else optLong(k)
fun JSONObject.strOrNull(k: String): String? = if (isNull(k)) null else optString(k).ifBlank { null }
fun JSONObject.arr(k: String): JSONArray = optJSONArray(k) ?: JSONArray()
fun JSONObject.obj(k: String): JSONObject = optJSONObject(k) ?: JSONObject()
inline fun <T> JSONArray.mapObj(f: (JSONObject) -> T): List<T> = (0 until length()).map { f(getJSONObject(it)) }
fun JSONArray.strings(): List<String> = (0 until length()).map { optString(it) }

// ---------------------------------------------------------------- shop data (as sent by the PC)
data class Category(val id: Long, val name: String, val group: String, val imei: Boolean, val active: Boolean)

data class Product(
    val id: Long, val code: String, val name: String, val brand: String, val category: Long, val model: String,
    val barcode: String, val price: Long, val gst: Long, val hsn: String, val qty: Int, val min: Int,
    val imei: Boolean, val active: Boolean, val warrantyMonths: Int,
) {
    val low: Boolean get() = active && min > 0 && qty <= min
}

data class Imei(val id: Long, val product: Long, val imei1: String, val imei2: String, val serial: String) {
    val label: String get() = if (imei2.isNotBlank()) "$imei1 / $imei2" else imei1
    fun matches(text: String): Boolean {
        val t = text.trim()
        return t.isNotEmpty() && (imei1 == t || imei2 == t || serial == t)
    }
}

data class Customer(val id: Long, val name: String, val phone: String, val address: String, val due: Long,
                    val pending: Boolean = false)

data class SaleLine(
    val productId: Long, val name: String, val imeiId: Long?, val imei: String, val qty: Int, val price: Long,
    val disc: Long, val share: Long, val taxable: Long, val rate: Long, val gst: Long, val total: Long,
    val warrantyMonths: Int = 0,
)

data class Pay(val date: String, val dir: String, val mode: String, val amount: Long, val kind: String,
               val status: String = "ACTIVE")

data class Sale(
    val id: Long, val no: String, val ref: String, val type: String, val customerId: Long, val customerName: String,
    val customerPhone: String, val date: String, val createdAt: String, val gstOn: Boolean, val inclusive: Boolean,
    val gross: Long, val itemDisc: Long, val billDisc: Long, val taxable: Long, val gst: Long, val roundOff: Long,
    val total: Long, val paid: Long, val returned: Long, val refunded: Long, val due: Long, val status: String,
    val notes: String, val cancelReason: String, val block: String, val lines: List<SaleLine>, val pays: List<Pay>,
    val pending: Boolean = false, val replacedOnPhone: Boolean = false,
) {
    val key: String get() = if (id > 0) "id:$id" else "ref:$ref"
    val active: Boolean get() = status == "COMPLETED" && !replacedOnPhone
}

data class JobLine(val id: Long, val productId: Long, val name: String, val qty: Int, val price: Long, val amount: Long)

data class Job(
    val id: Long, val no: String, val ref: String, val customerId: Long, val customerName: String,
    val customerPhone: String, val brand: String, val model: String, val imei: String, val accessories: String,
    val problem: String, val diagnosis: String, val technician: String, val date: String, val est: String?,
    val status: String, val labour: Long, val discount: Long, val parts: Long, val total: Long, val paid: Long,
    val completed: String?, val delivered: String?, val warrantyDays: Int, val notes: String,
    val lines: List<JobLine>, val pays: List<Pay>, val pending: Boolean = false,
) {
    val key: String get() = if (id > 0) "id:$id" else "ref:$ref"
    val balance: Long get() = if (status == "CANCELLED") 0 else total - paid
    val device: String get() = "$brand $model".trim()
    val open: Boolean get() = status in OPEN_STATUSES
}

val OPEN_STATUSES = setOf("RECEIVED", "INSPECTION", "WAITING_PARTS", "IN_PROGRESS")
val JOB_STATUS_LABELS = linkedMapOf(
    "RECEIVED" to "Received", "INSPECTION" to "Inspection", "WAITING_PARTS" to "Waiting for parts",
    "IN_PROGRESS" to "Repair in progress", "READY" to "Ready for delivery", "DELIVERED" to "Delivered",
    "CANCELLED" to "Cancelled",
)

data class Expense(val id: Long, val date: String, val category: String, val desc: String, val amount: Long,
                   val mode: String, val pending: Boolean = false)

data class Dash(
    val date: String, val bills: Int, val sales: Long, val service: Long, val expenses: Long, val opening: Long,
    val cash: Long, val dues: Long, val low: Int, val openJobs: Int, val ready: Int,
)

data class User(val id: Long, val username: String, val name: String, val role: String, val perms: Set<String>,
                val hash: String) {
    fun can(p: String) = p in perms
}

class Shop(val raw: JSONObject) {
    val name get() = raw.str("shop_name", "SDJ Mobile Care")
    val tagline get() = raw.str("shop_tagline")
    val address get() = raw.str("shop_address")
    val phone get() = raw.str("shop_phone")
    val email get() = raw.str("shop_email")
    val gstin get() = raw.str("shop_gstin")
    val footer get() = raw.str("invoice_footer")
    val terms get() = raw.str("terms")
    val serviceTerms get() = raw.str("service_terms")
    val upi get() = raw.str("upi_id")
    val gstEnabled get() = raw.bool("gst_enabled")
    val inclusive get() = raw.bool("prices_include_gst", true)
    val roundOff get() = raw.bool("round_off", true)
    val serviceGst get() = Money.ratePaise(raw.opt("service_gst_rate"))
    val ownerApproval get() = raw.bool("owner_approval", true)
    val discountLimit: Double get() = raw.optDouble("staff_discount_limit", 0.0).let { if (it.isNaN()) 0.0 else it }
    val expenseCategories get() = raw.arr("expense_categories").strings()
    val technicians get() = raw.arr("technicians").strings()
    val walkIn get() = raw.lng("walk_in")
    val invSeries get() = raw.str("inv_series")
    val jobSeries get() = raw.str("job_series")
    val fy get() = raw.str("fy")
    val invLast get() = raw.int("inv_last")
    val jobLast get() = raw.int("job_last")
    val closed: Set<String> get() = raw.arr("closed").strings().toSet()
    val businessDate get() = raw.str("business_date")
    val userId get() = raw.lng("user_id")
    val pcId get() = raw.str("pc")
    val historyDays get() = raw.int("history_days", 45)
    val warrantyDaysDefault get() = raw.int("default_repair_warranty_days")
}

// ---------------------------------------------------------------- parsing
object Parse {
    fun categories(a: JSONArray) = a.mapObj { Category(it.lng("id"), it.str("name"), it.str("grp"), it.bool("imei"), it.bool("on", true)) }

    fun products(a: JSONArray) = a.mapObj {
        Product(it.lng("id"), it.str("code"), it.str("name"), it.str("brand"), it.lng("cat"), it.str("model"),
            it.str("bc"), it.lng("price"), it.lng("gst"), it.str("hsn"), it.int("qty"), it.int("min"),
            it.bool("imei"), it.bool("on", true), it.int("wm"))
    }

    fun imeis(a: JSONArray) = a.mapObj { Imei(it.lng("id"), it.lng("p"), it.str("i1"), it.str("i2"), it.str("sn")) }

    fun customers(a: JSONArray) = a.mapObj { Customer(it.lng("id"), it.str("name"), it.str("phone"), it.str("addr"), it.lng("due")) }

    fun sales(a: JSONArray) = a.mapObj { s ->
        Sale(s.lng("id"), s.str("no"), s.str("ref"), s.str("type", "BILL"), s.lng("cust"), s.str("cname"),
            s.str("cphone"), s.str("d"), s.str("at"), s.bool("gst_on"), s.bool("incl", true), s.lng("gross"),
            s.lng("idisc"), s.lng("bdisc"), s.lng("taxable"), s.lng("gst"), s.lng("round"), s.lng("total"),
            s.lng("paid"), s.lng("returned"), s.lng("refunded"), s.lng("due"), s.str("st", "COMPLETED"),
            s.str("notes"), s.str("why"), s.str("blk"),
            s.arr("items").mapObj { i ->
                SaleLine(i.lng("p"), i.str("name"), i.lngOrNull("imei_id"), i.str("imei"), i.int("q"), i.lng("price"),
                    i.lng("disc"), i.lng("share"), i.lng("taxable"), i.lng("rate"), i.lng("gst"), i.lng("total"),
                    i.int("wm"))
            },
            s.arr("pays").mapObj { p -> Pay(p.str("d"), p.str("dir"), p.str("mode"), p.lng("amt"), p.str("kind"), p.str("st", "ACTIVE")) })
    }

    fun jobs(a: JSONArray) = a.mapObj { j ->
        Job(j.lng("id"), j.str("no"), j.str("ref"), j.lng("cust"), j.str("cname"), j.str("cphone"), j.str("brand"),
            j.str("model"), j.str("imei"), j.str("acc"), j.str("problem"), j.str("diag"), j.str("tech"), j.str("d"),
            j.strOrNull("est"), j.str("st"), j.lng("labour"), j.lng("disc"), j.lng("parts"), j.lng("total"),
            j.lng("paid"), j.strOrNull("done"), j.strOrNull("dlv"), j.int("wd"), j.str("notes"),
            j.arr("items").mapObj { i -> JobLine(i.lng("id"), i.lng("p"), i.str("name"), i.int("q"), i.lng("price"), i.lng("amt")) },
            j.arr("pays").mapObj { p -> Pay(p.str("d"), p.str("dir"), p.str("mode"), p.lng("amt"), p.str("kind")) })
    }

    fun expenses(a: JSONArray) = a.mapObj { Expense(it.lng("id"), it.str("d"), it.str("cat"), it.str("desc"), it.lng("amt"), it.str("mode")) }

    fun dash(o: JSONObject) = Dash(o.str("d"), o.int("bills"), o.lng("sales"), o.lng("service"), o.lng("expenses"),
        o.lng("opening"), o.lng("cash"), o.lng("dues"), o.int("low"), o.int("open_jobs"), o.int("ready"))

    fun users(a: JSONArray) = a.mapObj { User(it.lng("id"), it.str("username"), it.str("name"), it.str("role"), it.arr("perms").strings().toSet(), it.str("hash")) }
}

object Dates {
    fun today(): LocalDate = LocalDate.now()

    /** Indian financial year label: 19-Nov-2026 → "26-27" (April start, like the PC). */
    fun fyLabel(d: LocalDate): String {
        val start = if (d.monthValue >= 4) d.year else d.year - 1
        return "%02d-%02d".format(start % 100, (start + 1) % 100)
    }

    /** Today, or the next day the PC has not closed (same rule as the PC). */
    fun businessDate(closed: Set<String>, from: LocalDate = today()): LocalDate {
        var d = from
        while (d.toString() in closed) d = d.plusDays(1)
        return d
    }

    fun nice(iso: String?): String {
        if (iso.isNullOrBlank()) return ""
        return try {
            val d = LocalDate.parse(iso.take(10))
            "%02d %s %d".format(d.dayOfMonth, MONTHS[d.monthValue - 1], d.year)
        } catch (e: Exception) { iso }
    }

    fun niceTime(iso: String?): String {
        if (iso.isNullOrBlank() || iso.length < 16) return nice(iso)
        val t = iso.substring(11, 16)
        val h = t.take(2).toIntOrNull() ?: return nice(iso)
        val ampm = if (h >= 12) "PM" else "AM"
        val h12 = if (h % 12 == 0) 12 else h % 12
        return nice(iso) + " " + "%d:%s %s".format(h12, t.substring(3, 5), ampm)
    }

    private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
}

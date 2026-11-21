package com.sdj.mobilecare.data

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.time.LocalDate
import java.time.LocalDateTime
import java.util.UUID

open class UserError(message: String) : Exception(message)

/** Linking failed; [host] is the PC address that was tried (the app offers a browser test for it). */
class LinkError(message: String, val host: String?, val port: Int) : UserError(message)

/** The PC could not be reached; the message says why in shop words. */
class PcUnreachable(message: String) : IOException(message)

fun reachError(host: String, e: IOException): String = when (e) {
    is SocketTimeoutException -> "The PC at $host did not answer. Usually Windows Firewall or an antivirus on the PC is " +
        "blocking SDJ Mobile Care. On the PC open Settings → Phone link → 'Allow phone sync in Windows Firewall', then try again."
    is NoRouteToHostException -> "This phone cannot reach the PC at $host. The Wi-Fi router may stop devices from talking " +
        "to each other (guest Wi-Fi / AP isolation). Connect both to the main shop Wi-Fi."
    is ConnectException -> if ((e.message ?: "").contains("refused", ignoreCase = true))
        "The PC at $host refused the connection. Open SDJ Mobile Care on the PC (Settings → Phone link) and try again."
    else "Could not connect to the PC at $host (${e.message}). Check that the phone and the PC are on the same Wi-Fi."
    else -> "Could not reach the PC at $host (${e.javaClass.simpleName}). Is the phone on the shop Wi-Fi?"
}

data class PendingInfo(val id: String, val kind: String, val title: String, val at: String, val message: String = "")

data class SyncInfo(
    val running: Boolean = false, val lastOk: String = "", val error: String = "", val host: String = "",
    val removed: Boolean = false,
)

data class UiState(
    val ready: Boolean = false,
    val linked: Boolean = false,
    val hasData: Boolean = false,
    val user: User? = null,
    val linkedName: String = "",
    val linkedUsername: String = "",
    val pcShopName: String = "",
    val shop: Shop? = null,
    val categories: List<Category> = emptyList(),
    val products: List<Product> = emptyList(),
    val imeis: List<Imei> = emptyList(),
    val customers: List<Customer> = emptyList(),
    val sales: List<Sale> = emptyList(),
    val jobs: List<Job> = emptyList(),
    val expenses: List<Expense> = emptyList(),
    val dash: Dash? = null,
    val businessDate: String = "",
    val pending: List<PendingInfo> = emptyList(),
    val failed: List<PendingInfo> = emptyList(),
    val notices: List<String> = emptyList(),
    val sync: SyncInfo = SyncInfo(),
)

data class DraftLine(val product: Product, val qty: Int, val price: Long, val disc: Long = 0, val imei: Imei? = null)

sealed class CustChoice {
    object WalkIn : CustChoice()
    data class Existing(val c: Customer) : CustChoice()
    data class New(val name: String, val phone: String) : CustChoice()
}

data class BillDraft(
    val lines: List<DraftLine>, val customer: CustChoice = CustChoice.WalkIn, val billDisc: Long = 0,
    val pays: Map<String, Long> = emptyMap(), val notes: String = "", val type: String = "BILL", val editOf: Sale? = null,
)

data class Approval(val approver: User, val perms: Set<String>)

data class BillCheck(val calc: BillOut, val paid: Long, val balance: Long, val error: String?, val stockWarning: String?,
                     val needPerms: Set<String>)

val PAY_MODES = listOf("CASH", "UPI", "CARD", "OTHER")
val MODE_LABELS = mapOf("CASH" to "Cash", "UPI" to "UPI", "CARD" to "Card", "OTHER" to "Other", "EXCHANGE" to "Exchange")

/**
 * The phone's copy of the shop. The PC is the main copy: the phone keeps what the PC last sent, adds the entries
 * made here that are not synced yet (so stock, dues and today's figures are right offline), and sends those entries
 * to the PC whenever the PC can be reached on the shop Wi-Fi.
 */
/** How long the live sync waits before trying again when the PC is not reachable: quick at first. */
private val RETRY_MS = longArrayOf(2_000L, 4_000L, 8_000L, 15_000L)

class Repo(private val store: Store, private val net: Net, private val io: CoroutineDispatcher = Dispatchers.IO,
           private val appVersion: String = "1",
           val backups: Backups = Backups(store.dir, FileSink(File(store.dir, "backup-files")), null)) {
    private val mutex = Mutex()
    private val syncMutex = Mutex()
    private val filesMutex = Mutex()
    private val updateMutex = Mutex()
    private val bg = CoroutineScope(SupervisorJob() + io)
    private var filesJob: kotlinx.coroutines.Job? = null      // (Job alone is a repair job here)
    private val nudges = Channel<Unit>(Channel.CONFLATED)
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state
    private val _update = MutableStateFlow<AppUpdate?>(null)
    /** A newer phone app that the PC offered (and this phone has downloaded from it over the shop Wi-Fi). */
    val update: StateFlow<AppUpdate?> = _update
    private val _whatsNew = MutableStateFlow<WhatsNew?>(null)
    /** Set once, right after the app was updated: what is new in this version. */
    val whatsNew: StateFlow<WhatsNew?> = _whatsNew
    val version: String get() = appVersion

    private var shop: Shop? = null
    private var categories = emptyList<Category>()
    private var products = emptyList<Product>()
    private var imeis = emptyList<Imei>()
    private var customers = emptyList<Customer>()
    private var sales = emptyList<Sale>()
    private var jobs = emptyList<Job>()
    private var expenses = emptyList<Expense>()
    private var dash: Dash? = null
    private var users = emptyList<User>()
    private var signedIn: User? = null
    private var notices = mutableListOf<String>()
    private var sync = SyncInfo()

    private val cfg: JSONObject get() = store.config

    /** Called after an entry is saved on the phone (the app starts a sync right away). */
    var onNewEntry: (() -> Unit)? = null

    // ================================================================ loading
    suspend fun load() = mutex.withLock {
        withContext(io) { DATASETS.forEach { parse(it, store.data(it)) } }
        backups.refresh()
        checkWhatsNew()
        restoreUpdate()
        if (cfg.bool("stay")) signedIn = users.firstOrNull { it.id == cfg.lng("signed_user") && it.id == shop?.userId }
        sync = sync.copy(lastOk = cfg.str("last_sync"), host = cfg.str("last_host"))
        publish(ready = true)
    }

    /** The first start of a NEW version shows "What's new" once (the first ever start shows nothing). */
    private fun checkWhatsNew() {
        val seen = cfg.str("seen_version")
        if (seen == appVersion) return
        cfg.put("seen_version", appVersion)
        val next = cfg.obj("whats_new_next")
        val entry = when {
            next.str("version") == appVersion -> WhatsNew(appVersion, next.str("notes"), next.str("date"))
            seen.isNotEmpty() -> WhatsNew(appVersion, "", "")
            else -> null
        }
        if (entry != null) {
            val hist = JSONArray().put(entry.toJson())
            val old = cfg.arr("history")
            for (i in 0 until old.length()) {
                val o = old.optJSONObject(i) ?: continue
                if (o.str("version") != appVersion && hist.length() < 20) hist.put(o)
            }
            cfg.put("history", hist)
            cfg.put("whats_new_next", JSONObject())
            _whatsNew.value = entry
        }
        store.saveConfig()
    }

    fun dismissWhatsNew() { _whatsNew.value = null }

    fun updateHistory(): List<WhatsNew> = cfg.arr("history").let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(WhatsNew::of) } }

    /** "Later" on the update popup: ask again tomorrow. */
    suspend fun snoozeUpdate(hours: Long = 24) = mutex.withLock {
        cfg.put("update_snooze_until", System.currentTimeMillis() + hours * 3_600_000L)
        store.saveConfig()
    }

    fun updateSnoozed(): Boolean = cfg.lng("update_snooze_until") > System.currentTimeMillis()

    /** An update that is ready and has not been announced by a notification yet (the background sync calls this). */
    suspend fun takeUpdateNotice(): AppUpdate? = mutex.withLock {
        val u = _update.value
        if (u != null && u.state == AppUpdate.READY && cfg.str("notified_version") != u.version) {
            cfg.put("notified_version", u.version)
            store.saveConfig()
            u
        } else null
    }

    fun notifAsked(): Boolean = cfg.bool("notif_asked")
    suspend fun setNotifAsked() = mutex.withLock { cfg.put("notif_asked", true); store.saveConfig() }

    private suspend fun readyUpdate(u: AppUpdate) {
        mutex.withLock {
            cfg.put("whats_new_next", JSONObject().put("version", u.version).put("notes", u.notes).put("date", u.date))
            cfg.put("update_ready", JSONObject().put("version", u.version).put("size", u.size).put("sha", u.sha)
                .put("path", u.path).put("notes", u.notes).put("date", u.date).put("recommended", u.recommended))
            store.saveConfig()
        }
        _update.value = u
    }

    /** A downloaded update survives an app restart: show it again (or tidy up once it is installed). */
    private fun restoreUpdate() {
        val u = cfg.obj("update_ready")
        val ver = u.str("version")
        if (ver.isEmpty()) return
        val f = File(u.str("path"))
        if (isNewerVersion(ver, appVersion) && f.exists() && f.length() == u.lng("size")) {
            _update.value = AppUpdate(ver, u.lng("size"), u.str("sha"), AppUpdate.READY, path = f.absolutePath,
                notes = u.str("notes"), date = u.str("date"), recommended = u.bool("recommended"))
        } else {
            cfg.put("update_ready", JSONObject())
            store.saveConfig()
            File(store.dir, "update").listFiles()?.forEach { it.delete() }
        }
    }

    fun updatesOnMobileData(): Boolean = cfg.bool("updates_mobile_data", true)

    suspend fun setUpdatesOnMobileData(on: Boolean) = mutex.withLock {
        cfg.put("updates_mobile_data", on)
        store.saveConfig()
    }

    /**
     * Looks on GitHub for a newer phone app (internet on any Wi-Fi or mobile data – the shop PC is not needed) and
     * downloads it inside the app. At most every 6 hours unless [force]. Returns a short message for the person.
     */
    suspend fun checkOnline(force: Boolean = false): String {
        val now = System.currentTimeMillis()
        if (!force && now - cfg.lng("gh_checked") < 6 * 3_600_000L) return ""
        val info = try {
            withContext(io) { GitHubRelease.parse(net.webJson(GitHubRelease.LATEST)) }
        } catch (e: Exception) {
            return "Could not check for updates. Check the phone's internet and try again."
        }
        mutex.withLock {
            cfg.put("gh_checked", now)
            store.saveConfig()
        }
        if (info == null || !isNewerVersion(info.version, appVersion)) {
            tidyInstalledUpdate()
            return "You have the newest version ($appVersion)."
        }
        if (info.apkSize !in 1..MAX_APK) return "The update file looks wrong, so it was not downloaded."
        if (!force && !updatesOnMobileData() && !net.unmetered()) return ""          // waits for Wi-Fi
        val ok = getUpdate(info.version, info.apkSize, info.apkSha, info.notes, info.date, info.recommended) { tmp ->
            withContext(io) { net.webDownload(info.apkUrl, tmp, MAX_APK) }
        }
        return if (ok) "Version ${info.version} is ready – tap Update now." else (_update.value?.error ?: "The update could not be downloaded.")
    }

    /** Both ways of getting an update (GitHub, or the shop PC) end here: check the file and keep it ready to install. */
    private suspend fun getUpdate(ver: String, size: Long, sha: String, notes: String, date: String, rec: Boolean,
                                  fetch: suspend (File) -> String): Boolean = updateMutex.withLock {
        val cur = _update.value
        if (cur != null && cur.state == AppUpdate.READY && cur.version == ver && cur.sha == sha) return@withLock true
        val dir = File(store.dir, "update")
        val dest = File(dir, "SDJMobileCare-Phone-$ver.apk")
        val have = withContext(io) { dest.exists() && dest.inputStream().use { Backups.sha256(it).second } == sha }
        if (have) {
            readyUpdate(AppUpdate(ver, size, sha, AppUpdate.READY, path = dest.absolutePath, notes = notes, date = date, recommended = rec))
            return@withLock true
        }
        _update.value = AppUpdate(ver, size, sha, AppUpdate.LOADING, notes = notes, date = date, recommended = rec)
        val tmp = File(dir.apply { mkdirs() }, "apk.part")
        try {
            val got = fetch(tmp)
            if (got != sha) throw IOException("the update file did not arrive complete")
            withContext(io) {
                dir.listFiles()?.forEach { if (it != tmp) it.delete() }
                if (!tmp.renameTo(dest)) throw IOException("the update file could not be saved")
            }
            readyUpdate(AppUpdate(ver, size, sha, AppUpdate.READY, path = dest.absolutePath, notes = notes, date = date, recommended = rec))
            true
        } catch (e: PcError) {
            if (e.code == "replay") mutex.withLock { cfg.put("seq", cfg.optLong("seq", 0) + 1000); store.saveConfig() }
            _update.value = AppUpdate(ver, size, sha, AppUpdate.FAILED, error = e.message ?: "The update did not arrive.",
                notes = notes, date = date, recommended = rec)
            false
        } catch (e: Exception) {
            _update.value = AppUpdate(ver, size, sha, AppUpdate.FAILED,
                error = "Update not downloaded yet (${e.message}) – it is tried again later.", notes = notes, date = date, recommended = rec)
            false
        } finally {
            withContext(io) { tmp.delete() }
        }
    }

    /** This app already is the offered version (it was installed): forget the update and delete its file. */
    private suspend fun tidyInstalledUpdate() {
        val cur = _update.value
        if (cur != null && isNewerVersion(cur.version, appVersion)) return          // still waiting to be installed
        _update.value = null
        withContext(io) { File(store.dir, "update").listFiles()?.forEach { it.delete() } }
        mutex.withLock {
            if (cfg.obj("update_ready").length() > 0) {
                cfg.put("update_ready", JSONObject())
                store.saveConfig()
            }
        }
    }

    private fun parse(name: String, text: String?) {
        if (text.isNullOrBlank()) return
        try {
            when (name) {
                "shop" -> shop = Shop(JSONObject(text))
                "users" -> users = Parse.users(JSONArray(text))
                "categories" -> categories = Parse.categories(JSONArray(text))
                "products" -> products = Parse.products(JSONArray(text))
                "imeis" -> imeis = Parse.imeis(JSONArray(text))
                "customers" -> customers = Parse.customers(JSONArray(text))
                "sales" -> sales = Parse.sales(JSONArray(text))
                "jobs" -> jobs = Parse.jobs(JSONArray(text))
                "expenses" -> expenses = Parse.expenses(JSONArray(text))
                "dash" -> dash = Parse.dash(JSONObject(text))
            }
        } catch (e: Exception) {
            // a damaged file is replaced by the next sync
        }
    }

    val linked: Boolean get() = cfg.str("device").isNotEmpty()

    // ================================================================ overlay: PC data + entries not yet synced
    private fun opsList(): List<JSONObject> {
        val a = store.ops()
        return (0 until a.length()).map { a.getJSONObject(it) }
    }

    private fun publish(ready: Boolean = _state.value.ready) {
        val ops = opsList()
        val live = ops.filter { it.str("_st", "PENDING") == "PENDING" }
        val sh = shop
        val bdate = Dates.businessDate(sh?.closed ?: emptySet()).toString()

        // ---- bills
        val serverById = sales.associateBy { it.id }
        val local = LinkedHashMap<String, Sale>()
        val voided = HashMap<Long, String>()
        val newOnPhone = HashSet<String>()          // refs of bills that started as a new bill on this phone
        for (op in live) {
            when (op.str("k")) {
                "sale.new" -> viewOf(op)?.let { local[op.str("ref")] = it; newOnPhone += op.str("ref") }
                "sale.edit" -> {
                    val t = op.obj("target")
                    val tref = t.str("ref")
                    if (tref.isNotEmpty() && local.containsKey(tref)) {
                        local.remove(tref)
                        if (tref in newOnPhone) newOnPhone += op.str("ref")
                    }
                    else serverById[t.lng("id")]?.let { voided[it.id] = "edited" }
                    viewOf(op)?.let { local[op.str("ref")] = it }
                }
                "sale.cancel" -> {
                    val t = op.obj("target")
                    val tref = t.str("ref")
                    val cur = local[tref]
                    if (tref.isNotEmpty() && cur != null) local[tref] = cur.copy(status = "CANCELLED", cancelReason = op.str("reason"), due = 0)
                    else serverById[t.lng("id")]?.let { voided[it.id] = "cancelled" }
                }
            }
        }
        val localBills = local.values.toList().reversed()
        val serverShown = sales.map { s ->
            when (voided[s.id]) {
                "edited" -> s.copy(replacedOnPhone = true, block = "Edited on this phone – waiting to sync")
                "cancelled" -> s.copy(status = "CANCELLED", due = 0, block = "Cancelled on this phone – waiting to sync",
                    cancelReason = "Cancelled on phone")
                else -> s
            }
        }
        val voidedServer = voided.keys.mapNotNull { serverById[it] }.filter { it.status == "COMPLETED" }
        val activeLocal = localBills.filter { it.status == "COMPLETED" }

        // ---- stock & IMEIs
        val delta = HashMap<Long, Int>()
        voidedServer.forEach { s -> s.lines.forEach { delta[it.productId] = (delta[it.productId] ?: 0) + it.qty } }
        activeLocal.forEach { s -> s.lines.forEach { delta[it.productId] = (delta[it.productId] ?: 0) - it.qty } }
        live.filter { it.str("k") == "job.part" }.forEach { op ->
            val p = op.lng("p")
            delta[p] = (delta[p] ?: 0) - op.int("q", 1)
        }
        val shownProducts = products.map { p -> delta[p.id]?.let { p.copy(qty = p.qty + it) } ?: p }
        val used = activeLocal.flatMap { s -> s.lines.mapNotNull { it.imeiId } }.toSet()
        val freed = voidedServer.flatMap { s ->
            s.lines.filter { it.imeiId != null }.map { l ->
                val parts = l.imei.split(" / ")
                Imei(l.imeiId!!, l.productId, parts.getOrElse(0) { "" }, parts.getOrElse(1) { "" }, "")
            }
        }
        val shownImeis = (imeis + freed).distinctBy { it.id }.filter { it.id !in used }

        // ---- customers & dues
        val due = HashMap<Long, Long>()
        val newCust = LinkedHashMap<String, Customer>()
        fun addDue(cust: JSONObject?, id: Long, amount: Long) {
            if (id > 0) { due[id] = (due[id] ?: 0) + amount; return }
            if (cust == null) return
            val phone = cust.str("phone")
            if (phone.isEmpty()) return
            val existing = customers.firstOrNull { it.phone == phone }
            if (existing != null) { due[existing.id] = (due[existing.id] ?: 0) + amount; return }
            val c = newCust[phone] ?: Customer(-(newCust.size + 1).toLong(), cust.str("name"), phone, "", 0, pending = true)
            newCust[phone] = c.copy(due = c.due + amount)
        }
        for (op in live) {
            when (op.str("k")) {
                "sale.new", "sale.edit" -> { /* counted below from the current versions */ }
                "cust.pay" -> addDue(op.obj("cust"), op.obj("cust").lng("id"), -op.lng("amount"))
                "job.new" -> addDue(JSONObject().put("name", op.str("customer_name")).put("phone", op.str("customer_phone")), 0, 0)
            }
        }
        val opByRef = live.associateBy { it.str("ref") }
        activeLocal.forEach { s ->
            val cust = opByRef[s.ref]?.optJSONObject("cust")
            addDue(cust, s.customerId, s.due)
        }
        voidedServer.forEach { s -> if (s.customerId > 0) due[s.customerId] = (due[s.customerId] ?: 0) - s.due }
        val shownCustomers = customers.map { c -> due[c.id]?.let { c.copy(due = c.due + it) } ?: c } + newCust.values

        // ---- repair jobs
        val jmap = LinkedHashMap<String, Job>()
        jobs.forEach { jmap[it.key] = it }
        val rate = products.associate { it.id to it.gst }
        fun recalc(j: Job): Job = if (sh == null) j else j.copy(
            parts = j.lines.sumOf { it.amount },
            total = Calc.jobTotal(j.labour, j.discount, j.lines.map { it.amount to (rate[it.productId] ?: 0L) },
                sh.serviceGst, sh.gstEnabled, sh.inclusive))
        fun key(t: JSONObject): String = if (t.str("ref").isNotEmpty() && jmap.containsKey("ref:" + t.str("ref"))) "ref:" + t.str("ref") else "id:" + t.lng("id")
        for (op in live) {
            val d = op.str("d")
            when (op.str("k")) {
                "job.new" -> jmap["ref:" + op.str("ref")] = recalc(Job(0, op.str("no"), op.str("ref"), 0, op.str("customer_name"),
                    op.str("customer_phone"), op.str("brand"), op.str("model"), op.str("imei"), op.str("accessories"),
                    op.str("problem"), "", op.str("technician"), d, op.strOrNull("est"), "RECEIVED", op.lng("labour"), 0, 0, 0,
                    op.lng("adv"), null, null, op.int("warranty_days"), op.str("notes"), emptyList(),
                    if (op.lng("adv") > 0) listOf(Pay(d, "IN", op.str("adv_mode", "CASH"), op.lng("adv"), "SERVICE_ADVANCE")) else emptyList(),
                    pending = true))
                "job.status" -> jmap[key(op.obj("target"))]?.let { j ->
                    val st = op.str("status")
                    val done = if (st == "READY") j.completed ?: d else if (st in OPEN_STATUSES) null else j.completed
                    jmap[j.key] = j.copy(status = st, completed = done, pending = true)
                }
                "job.pay" -> jmap[key(op.obj("target"))]?.let { j ->
                    jmap[j.key] = j.copy(paid = j.paid + op.lng("amount"), pays = j.pays + Pay(d, "IN", op.str("mode"), op.lng("amount"), "SERVICE_RECEIPT"), pending = true)
                }
                "job.deliver" -> jmap[key(op.obj("target"))]?.let { j ->
                    val collect = op.lng("collect")
                    jmap[j.key] = j.copy(status = "DELIVERED", completed = j.completed ?: d, delivered = d, paid = j.paid + collect,
                        pays = if (collect > 0) j.pays + Pay(d, "IN", op.str("mode"), collect, "SERVICE_RECEIPT") else j.pays, pending = true)
                }
                "job.part" -> jmap[key(op.obj("target"))]?.let { j ->
                    val amt = op.int("q", 1) * op.lng("price")
                    jmap[j.key] = recalc(j.copy(lines = j.lines + JobLine(0, op.lng("p"), op.str("_name"), op.int("q", 1), op.lng("price"), amt),
                        status = if (j.status in setOf("RECEIVED", "INSPECTION", "WAITING_PARTS")) "IN_PROGRESS" else j.status, pending = true))
                }
                "job.edit" -> jmap[key(op.obj("target"))]?.let { j ->
                    val f = op.obj("fields")
                    jmap[j.key] = recalc(j.copy(
                        diagnosis = if (f.has("diagnosis")) f.str("diagnosis") else j.diagnosis,
                        technician = if (f.has("technician")) f.str("technician") else j.technician,
                        notes = if (f.has("notes")) f.str("notes") else j.notes,
                        labour = if (f.has("labour_charge")) f.lng("labour_charge") else j.labour,
                        discount = if (f.has("discount")) f.lng("discount") else j.discount,
                        est = if (f.has("estimated_delivery")) f.strOrNull("estimated_delivery") else j.est, pending = true))
                }
            }
        }
        val shownJobs = jmap.values.sortedWith(compareByDescending<Job> { it.pending && it.id == 0L }.thenByDescending { it.id })

        // ---- expenses
        val pendingExp = live.filter { it.str("k") == "exp.new" }.map {
            Expense(0, it.str("d"), it.str("cat"), it.str("desc"), it.lng("amount"), it.str("mode"), pending = true)
        }.reversed()

        // ---- today's figures
        val shownDash = dash?.let { d0 ->
            var bills = d0.bills
            var salesT = d0.sales
            var cash = d0.cash
            var exp = d0.expenses
            val sameDay = d0.date == bdate
            fun cashOf(pays: List<Pay>) = pays.filter { it.mode == "CASH" && it.status == "ACTIVE" }.sumOf { if (it.dir == "OUT") -it.amount else it.amount }
            if (sameDay) {
                activeLocal.filter { it.date == bdate }.forEach { s ->
                    if (s.ref in newOnPhone) bills += 1
                    salesT += s.total
                    cash += cashOf(s.pays)
                }
                voidedServer.filter { it.date == bdate }.forEach { s ->
                    if (voided[s.id] == "cancelled") bills -= 1
                    salesT -= s.total
                    cash -= cashOf(s.pays)
                }
                for (op in live) {
                    if (op.str("d") != bdate) continue
                    when (op.str("k")) {
                        "cust.pay", "job.pay" -> if (op.str("mode") == "CASH") cash += op.lng("amount")
                        "job.new" -> if (op.str("adv_mode", "CASH") == "CASH") cash += op.lng("adv")
                        "job.deliver" -> if (op.str("mode", "CASH") == "CASH") cash += op.lng("collect")
                        "exp.new" -> { exp += op.lng("amount"); if (op.str("mode") == "CASH") cash -= op.lng("amount") }
                    }
                }
            }
            d0.copy(bills = bills, sales = salesT, cash = cash, expenses = exp,
                dues = shownCustomers.sumOf { maxOf(it.due, 0L) }, low = shownProducts.count { it.low },
                openJobs = shownJobs.count { it.open }, ready = shownJobs.count { it.status == "READY" })
        }

        val pend = live.map { info(it) }
        val failed = ops.filter { it.str("_st") == "FAILED" }.map { info(it) }
        val u = cfg.obj("user")
        _state.value = UiState(
            ready = ready, linked = linked, hasData = shop != null && users.isNotEmpty(), user = signedIn,
            linkedName = u.str("name"), linkedUsername = u.str("username"), pcShopName = cfg.str("shop_name"),
            shop = sh, categories = categories, products = shownProducts, imeis = shownImeis,
            customers = shownCustomers.sortedBy { it.name.lowercase() }, sales = localBills + serverShown,
            jobs = shownJobs, expenses = pendingExp + expenses, dash = shownDash, businessDate = bdate,
            pending = pend, failed = failed, notices = notices.toList(), sync = sync,
        )
    }

    private fun viewOf(op: JSONObject): Sale? = op.optJSONObject("_view")?.let { v ->
        Parse.sales(JSONArray().put(v)).firstOrNull()?.copy(pending = true)
    }

    private fun info(op: JSONObject): PendingInfo {
        val k = op.str("k")
        val title = when (k) {
            "sale.new" -> "Bill ${op.str("no")} · ${Money.fmt(op.lng("total"))}"
            "sale.edit" -> "Bill change ${op.optJSONObject("_view")?.str("no") ?: ""} · ${Money.fmt(op.lng("total"))}"
            "sale.cancel" -> "Bill cancel ${op.str("_no")}"
            "cust.pay" -> "Payment from ${op.str("_name")} · ${Money.fmt(op.lng("amount"))}"
            "exp.new" -> "Expense ${op.str("cat")} · ${Money.fmt(op.lng("amount"))}"
            "job.new" -> "Repair ${op.str("no")} · ${op.str("customer_name")}"
            "job.status" -> "Repair ${op.str("_no")} → ${JOB_STATUS_LABELS[op.str("status")] ?: op.str("status")}"
            "job.pay" -> "Repair ${op.str("_no")} payment · ${Money.fmt(op.lng("amount"))}"
            "job.deliver" -> "Repair ${op.str("_no")} delivered"
            "job.part" -> "Repair ${op.str("_no")} · part ${op.str("_name")}"
            "job.edit" -> "Repair ${op.str("_no")} details"
            else -> k
        }
        return PendingInfo(op.str("id"), k, title, op.str("at"), op.str("_msg"))
    }

    // ================================================================ sign in / approval
    fun linkedUser(): User? = users.firstOrNull { it.id == shop?.userId }

    suspend fun signIn(password: String, stay: Boolean) {
        val u = linkedUser() ?: throw UserError("The shop data is not on this phone yet. Connect to the shop Wi-Fi and tap Sync now.")
        val ok = withContext(Dispatchers.Default) { Crypto.verifyPassword(password, u.hash) }
        if (!ok) throw UserError("Wrong password.")
        mutex.withLock {
            signedIn = u
            cfg.put("stay", stay).put("signed_user", u.id)
            store.saveConfig()
            publish()
        }
    }

    suspend fun signOut() = mutex.withLock {
        signedIn = null
        cfg.put("stay", false)
        store.saveConfig()
        publish()
    }

    fun ownerApproval(): Boolean = shop?.ownerApproval ?: false

    suspend fun verifyApprover(username: String, password: String, perms: Set<String>): Approval {
        val u = users.firstOrNull { it.username.equals(username.trim(), ignoreCase = true) }
            ?: throw UserError("Incorrect username or password.")
        val ok = withContext(Dispatchers.Default) { Crypto.verifyPassword(password, u.hash) }
        if (!ok) throw UserError("Incorrect username or password.")
        if (!u.perms.containsAll(perms)) throw UserError("${u.name} is not allowed to approve this either. Ask the owner.")
        if (u.id == signedIn?.id) throw UserError("Someone else (the owner or a manager) has to approve this.")
        return Approval(u, perms)
    }

    private fun me(): User = signedIn ?: throw UserError("Please sign in again.")

    private fun need(perm: String, approval: Approval?) {
        if (me().can(perm)) return
        if (approval != null && perm in approval.perms) return
        throw UserError(if (ownerApproval()) "Owner approval is needed for this." else "You do not have permission for this. Please ask the owner.")
    }

    fun missing(vararg perms: String): Set<String> = perms.filter { signedIn?.can(it) != true }.toSet()

    // ================================================================ bills
    fun checkBill(d: BillDraft): BillCheck {
        val sh = shop
        val calc = Calc.bill(d.lines.map { LineIn(it.qty, it.price, it.disc, it.product.gst) }, d.billDisc,
            sh?.gstEnabled ?: false, sh?.inclusive ?: true, sh?.roundOff ?: true)
        val paid = d.pays.values.filter { it > 0 }.sum()
        val balance = calc.grandTotal - paid
        var err: String? = null
        val stock = _state.value.products.associateBy { it.id }
        val editQty = HashMap<Long, Int>()
        d.editOf?.lines?.forEach { editQty[it.productId] = (editQty[it.productId] ?: 0) + it.qty }
        val needed = HashMap<Long, Int>()
        val imeiSeen = HashSet<Long>()
        for (l in d.lines) {
            if (l.qty <= 0) err = err ?: "Quantity for '${l.product.name}' must be at least 1."
            if (l.price < 0) err = err ?: "Selling price for '${l.product.name}' cannot be negative."
            if (l.disc < 0 || l.disc > l.qty * l.price) err = err ?: "Discount for '${l.product.name}' cannot be more than the item amount."
            if (l.product.imei) {
                if (l.qty != 1 || l.imei == null) err = err ?: "Select the IMEI for '${l.product.name}' (one line per phone)."
                else if (!imeiSeen.add(l.imei.id)) err = err ?: "IMEI ${l.imei.imei1} is added twice in this bill."
            }
            needed[l.product.id] = (needed[l.product.id] ?: 0) + l.qty
        }
        var warn: String? = null
        for ((pid, q) in needed) {
            val have = (stock[pid]?.qty ?: 0) + (editQty[pid] ?: 0)
            if (have < q) warn = "Only $have of '${stock[pid]?.name ?: ""}' in stock on this phone's copy."
        }
        if (d.lines.isEmpty()) err = err ?: "Add at least one product to the bill."
        if (d.billDisc < 0 || d.billDisc > calc.gross - calc.itemDisc) err = err ?: "Bill discount cannot be more than the bill amount."
        if (paid > calc.grandTotal) err = err ?: "Payment (${Money.fmt(paid)}) is more than the bill total (${Money.fmt(calc.grandTotal)})."
        if (balance > 0 && d.customer is CustChoice.WalkIn)
            err = err ?: "This is a credit / part-payment bill. Choose or add the customer (name and phone) so the balance can be tracked."
        if (d.customer is CustChoice.New) {
            if (d.customer.name.isBlank()) err = err ?: "Enter the customer's name."
            if (d.customer.phone.count { it.isDigit() } < 6) err = err ?: "Enter the customer's phone number."
        }
        val perms = mutableSetOf<String>()
        perms += if (d.type == "QUICK") "quick_sale" else "billing"
        if (d.editOf != null) perms += "cancel_sale"
        val limit = sh?.discountLimit ?: 0.0
        if (limit > 0 && calc.gross > 0) {
            val pct = (calc.itemDisc + calc.billDisc) * 100.0 / calc.gross
            if (pct > limit + 1e-9) perms += "discount_override"
        }
        return BillCheck(calc, paid, balance, err, warn, perms.filter { signedIn?.can(it) != true }.toSet())
    }

    private fun custJson(c: CustChoice): Any = when (c) {
        is CustChoice.WalkIn -> JSONObject.NULL
        is CustChoice.Existing -> if (c.c.id > 0) JSONObject().put("id", c.c.id) else JSONObject().put("name", c.c.name).put("phone", c.c.phone)
        is CustChoice.New -> JSONObject().put("name", c.name.trim()).put("phone", c.phone.filter { it.isDigit() || it == '+' })
    }

    private fun newOp(kind: String, day: String, approval: Approval?): JSONObject {
        val op = JSONObject().put("id", UUID.randomUUID().toString().replace("-", "")).put("k", kind)
            .put("at", LocalDateTime.now().withNano(0).toString()).put("d", day).put("u", me().id).put("_st", "PENDING")
        if (approval != null) op.put("ap", approval.approver.id).put("apf", JSONArray(approval.perms.toList()))
        return op
    }

    private fun nextInvoice(day: LocalDate): String {
        val sh = shop ?: throw UserError("The shop data is not on this phone yet. Sync first.")
        val fy = Dates.fyLabel(day)
        val inv = cfg.optJSONObject("inv") ?: JSONObject()
        val n = maxOf(inv.optInt(fy, 0), if (sh.fy == fy) sh.invLast else 0) + 1
        cfg.put("inv", inv.put(fy, n))
        return "%s/%s/%05d".format(sh.invSeries, fy, n)
    }

    private fun nextJobNo(): String {
        val sh = shop ?: throw UserError("The shop data is not on this phone yet. Sync first.")
        val n = maxOf(cfg.optInt("job_last", 0), sh.jobLast) + 1
        cfg.put("job_last", n)
        return "%s-%04d".format(sh.jobSeries, n)
    }

    /** Saves a new bill (or a changed bill) on the phone; it goes to the PC with the next sync. */
    suspend fun saveBill(d: BillDraft, approval: Approval? = null): Sale = mutex.withLock {
        val sh = shop ?: throw UserError("The shop data is not on this phone yet. Sync first.")
        val chk = checkBill(d)
        chk.error?.let { throw UserError(it) }
        for (p in chk.needPerms) need(p, approval)
        val edit = d.editOf
        val day = edit?.date ?: Dates.businessDate(sh.closed).toString()
        val op = newOp(if (edit == null) "sale.new" else "sale.edit", day, approval)
        val ref = UUID.randomUUID().toString().replace("-", "")
        val no = edit?.no ?: nextInvoice(LocalDate.parse(day))
        op.put("ref", ref).put("type", if (edit != null) edit.type else d.type).put("cust", custJson(d.customer))
            .put("bdisc", d.billDisc).put("notes", d.notes.trim()).put("total", chk.calc.grandTotal)
        if (edit == null) op.put("no", no)
        else if (edit.pending) op.put("target", JSONObject().put("ref", edit.ref))
        else op.put("target", JSONObject().put("id", edit.id)).put("base", JSONObject().put("no", edit.no).put("paid", edit.paid))
        val items = JSONArray()
        d.lines.forEach { l ->
            val it = JSONObject().put("p", l.product.id).put("q", l.qty).put("price", l.price).put("disc", l.disc)
            if (l.imei != null) it.put("imei_id", l.imei.id).put("imei", l.imei.label)
            items.put(it)
        }
        op.put("items", items)
        val pays = JSONObject()
        d.pays.filter { it.value > 0 }.forEach { (m, v) -> pays.put(m, v) }
        op.put("pay", pays)
        // what the phone shows for this bill until the PC's copy arrives
        val (cid, cname, cphone) = when (val c = d.customer) {
            is CustChoice.WalkIn -> Triple(sh.walkIn, "Walk-in Customer", "")
            is CustChoice.Existing -> Triple(c.c.id, c.c.name, c.c.phone)
            is CustChoice.New -> Triple(0L, c.name.trim(), c.phone)
        }
        val view = JSONObject().put("id", 0).put("no", no).put("ref", ref).put("type", op.str("type")).put("cust", cid)
            .put("cname", cname).put("cphone", cphone).put("d", day).put("at", op.str("at")).put("gst_on", sh.gstEnabled)
            .put("incl", sh.inclusive).put("gross", chk.calc.gross).put("idisc", chk.calc.itemDisc).put("bdisc", chk.calc.billDisc)
            .put("taxable", chk.calc.taxable).put("gst", chk.calc.gst).put("round", chk.calc.roundOff)
            .put("total", chk.calc.grandTotal).put("paid", chk.paid).put("returned", 0).put("refunded", 0)
            .put("due", chk.balance).put("st", "COMPLETED").put("notes", d.notes.trim()).put("blk", "")
        val vItems = JSONArray()
        d.lines.forEachIndexed { i, l ->
            val lo = chk.calc.lines[i]
            vItems.put(JSONObject().put("p", l.product.id).put("name", l.product.name).put("imei_id", l.imei?.id ?: JSONObject.NULL)
                .put("imei", l.imei?.label ?: "").put("q", l.qty).put("price", l.price).put("disc", lo.itemDisc)
                .put("share", lo.share).put("taxable", lo.taxable).put("rate", if (sh.gstEnabled) l.product.gst else 0)
                .put("gst", lo.gst).put("total", lo.total).put("wm", l.product.warrantyMonths))
        }
        view.put("items", vItems)
        val vp = JSONArray()
        d.pays.filter { it.value > 0 }.forEach { (m, v) -> vp.put(JSONObject().put("d", day).put("dir", "IN").put("mode", m).put("amt", v).put("kind", "SALE_RECEIPT").put("st", "ACTIVE")) }
        view.put("pays", vp)
        op.put("_view", view)
        append(op)
        viewOf(op)!!
    }

    suspend fun cancelBill(s: Sale, reason: String, approval: Approval? = null) = mutex.withLock {
        if (reason.isBlank()) throw UserError("Please enter the reason for cancelling this bill.")
        if (s.block.isNotEmpty() && !s.pending) throw UserError(s.block)
        need("cancel_sale", approval)
        val op = newOp("sale.cancel", s.date, approval).put("reason", reason.trim()).put("_no", s.no)
        if (s.pending) op.put("target", JSONObject().put("ref", s.ref))
        else op.put("target", JSONObject().put("id", s.id)).put("base", JSONObject().put("no", s.no).put("paid", s.paid))
        append(op)
    }

    // ================================================================ payments, expenses
    suspend fun collectPayment(c: Customer, amount: Long, mode: String, notes: String, approval: Approval? = null) = mutex.withLock {
        val sh = shop ?: throw UserError("Sync first.")
        need("collect_payment", approval)
        if (amount <= 0) throw UserError("Enter the amount received.")
        if (amount > c.due) throw UserError("Amount is more than the pending balance (${Money.fmt(c.due)}).")
        val op = newOp("cust.pay", Dates.businessDate(sh.closed).toString(), approval).put("ref", uuid())
            .put("cust", if (c.id > 0) JSONObject().put("id", c.id) else JSONObject().put("name", c.name).put("phone", c.phone))
            .put("amount", amount).put("mode", mode).put("notes", notes.trim()).put("_name", c.name)
        append(op)
    }

    suspend fun addExpense(category: String, amount: Long, mode: String, desc: String, approval: Approval? = null) = mutex.withLock {
        val sh = shop ?: throw UserError("Sync first.")
        need("expenses", approval)
        if (category.isBlank()) throw UserError("Choose an expense category.")
        if (amount <= 0) throw UserError("Enter the expense amount.")
        append(newOp("exp.new", Dates.businessDate(sh.closed).toString(), approval).put("ref", uuid()).put("cat", category)
            .put("amount", amount).put("mode", mode).put("desc", desc.trim()))
    }

    // ================================================================ repair jobs
    data class JobForm(val name: String, val phone: String, val brand: String, val model: String, val imei: String,
                       val accessories: String, val problem: String, val technician: String, val labour: Long,
                       val est: String?, val advance: Long, val advanceMode: String, val notes: String)

    suspend fun newJob(f: JobForm, approval: Approval? = null): String = mutex.withLock {
        val sh = shop ?: throw UserError("Sync first.")
        need("services", approval)
        if (f.phone.count { it.isDigit() } < 6) throw UserError("Enter the customer's phone number (needed to inform them when ready).")
        if (f.brand.isBlank() && f.model.isBlank()) throw UserError("Enter the mobile brand / model.")
        if (f.problem.isBlank()) throw UserError("Describe the reported problem.")
        if (f.labour < 0 || f.advance < 0) throw UserError("Amounts cannot be negative.")
        val no = nextJobNo()
        val op = newOp("job.new", Dates.businessDate(sh.closed).toString(), approval).put("ref", uuid()).put("no", no)
            .put("customer_name", f.name.trim()).put("customer_phone", f.phone.filter { it.isDigit() || it == '+' })
            .put("brand", f.brand.trim()).put("model", f.model.trim()).put("imei", f.imei.trim())
            .put("accessories", f.accessories.trim()).put("problem", f.problem.trim()).put("technician", f.technician.trim())
            .put("labour", f.labour).put("est", f.est ?: JSONObject.NULL).put("adv", f.advance)
            .put("adv_mode", f.advanceMode).put("notes", f.notes.trim())
        append(op)
        no
    }

    private fun jobTarget(j: Job): JSONObject = if (j.id > 0) JSONObject().put("id", j.id) else JSONObject().put("ref", j.ref)

    suspend fun jobStatus(j: Job, status: String, reason: String = "", refundMode: String = "CASH") = mutex.withLock {
        need("services", null)
        if (j.status in setOf("DELIVERED", "CANCELLED")) throw UserError("Job ${j.no} cannot be changed any more.")
        if (status == "CANCELLED" && reason.isBlank()) throw UserError("Please enter the reason for cancelling this job.")
        append(newOp("job.status", today(), null).put("target", jobTarget(j)).put("status", status).put("reason", reason)
            .put("refund_mode", refundMode).put("_no", j.no))
    }

    suspend fun jobPayment(j: Job, amount: Long, mode: String) = mutex.withLock {
        need("services", null)
        if (amount <= 0) throw UserError("Enter the amount received.")
        if (j.status == "CANCELLED") throw UserError("This job is cancelled.")
        if (j.status in setOf("READY", "DELIVERED") && amount > j.balance) throw UserError("Amount is more than the pending balance (${Money.fmt(j.balance)}).")
        append(newOp("job.pay", today(), null).put("ref", uuid()).put("target", jobTarget(j)).put("amount", amount)
            .put("mode", mode).put("_no", j.no))
    }

    suspend fun jobDeliver(j: Job, collect: Long, mode: String) = mutex.withLock {
        need("services", null)
        if (j.status in setOf("DELIVERED", "CANCELLED")) throw UserError("Job ${j.no} cannot be changed any more.")
        if (collect < 0 || collect > maxOf(j.balance, 0)) throw UserError("Amount to collect must be between ₹0 and the balance ${Money.fmt(maxOf(j.balance, 0))}.")
        append(newOp("job.deliver", today(), null).put("target", jobTarget(j)).put("collect", collect).put("mode", mode)
            .put("refund_mode", "CASH").put("_no", j.no))
    }

    suspend fun jobPart(j: Job, p: Product, qty: Int, price: Long) = mutex.withLock {
        need("services", null)
        if (j.status !in OPEN_STATUSES) throw UserError("Move the job back to 'Repair in progress' to add parts.")
        if (p.imei) throw UserError("IMEI-tracked phones cannot be used as spare parts.")
        if (qty <= 0) throw UserError("Quantity must be at least 1.")
        if (price < 0) throw UserError("Price cannot be negative.")
        append(newOp("job.part", today(), null).put("ref", uuid()).put("target", jobTarget(j)).put("p", p.id).put("q", qty)
            .put("price", price).put("_no", j.no).put("_name", p.name))
    }

    suspend fun jobEdit(j: Job, diagnosis: String, technician: String, labour: Long, discount: Long, notes: String) = mutex.withLock {
        need("services", null)
        if (j.status in setOf("DELIVERED", "CANCELLED")) throw UserError("Job ${j.no} cannot be changed any more.")
        val f = JSONObject().put("diagnosis", diagnosis.trim()).put("technician", technician.trim()).put("notes", notes.trim())
        if (labour != j.labour || discount != j.discount) {
            if (j.status in setOf("READY", "DELIVERED")) throw UserError("Charges of a completed job cannot be changed. Move it back to 'Repair in progress' first.")
            if (labour < 0 || discount < 0) throw UserError("Charges cannot be negative.")
            if (discount > labour + j.parts) throw UserError("Discount cannot be more than labour + spare parts amount.")
            f.put("labour_charge", labour).put("discount", discount)
        }
        append(newOp("job.edit", today(), null).put("target", jobTarget(j)).put("fields", f).put("_no", j.no))
    }

    private fun today(): String = Dates.businessDate(shop?.closed ?: emptySet()).toString()
    private fun uuid() = UUID.randomUUID().toString().replace("-", "")

    private fun append(op: JSONObject) {
        val a = store.ops()
        a.put(op)
        store.saveOps(a)
        store.saveConfig()
        publish()
        onNewEntry?.invoke()
    }

    suspend fun discardFailed(id: String) = mutex.withLock {
        val a = store.ops()
        val keep = JSONArray()
        for (i in 0 until a.length()) { val o = a.getJSONObject(i); if (o.str("id") != id) keep.put(o) }
        store.saveOps(keep)
        publish()
    }

    suspend fun retryFailed(id: String) = mutex.withLock {
        val a = store.ops()
        for (i in 0 until a.length()) { val o = a.getJSONObject(i); if (o.str("id") == id) o.put("_st", "PENDING").remove("_msg") }
        store.saveOps(a)
        publish()
    }

    suspend fun clearNotices() = mutex.withLock { notices.clear(); publish() }

    // ================================================================ linking
    data class QrLink(val hosts: List<String>, val port: Int, val code: String, val pcId: String, val name: String)

    fun parseQr(text: String): QrLink? {
        if (!text.startsWith("SDJLINK:")) return null
        val kv = text.substringAfter(";").split(";").mapNotNull { p -> p.split("=", limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } }.toMap()
        val code = kv["c"] ?: return null
        val pc = kv["id"] ?: return null
        return QrLink(kv["h"].orEmpty().split(",").filter { it.isNotBlank() }, kv["p"]?.toIntOrNull() ?: Net.PORT, code, pc, kv["n"].orEmpty())
    }

    /** Links this phone with a one-time code (typed or from the PC's QR). */
    suspend fun link(code: String, qr: QrLink?, manualHost: String?, phoneName: String) {
        if (Crypto.normaliseCode(code).length < 10) throw UserError("Enter the 10-letter code shown on the PC.")
        val port = qr?.port ?: Net.PORT
        val candidates = withContext(io) {
            val list = mutableListOf<Pair<String, String?>>()
            qr?.hosts?.forEach { list += it to qr.pcId }
            manualHost?.trim()?.takeIf { it.isNotEmpty() }?.let { list += it to qr?.pcId }
            if (list.isEmpty()) net.discover(null).forEach { (host, info) -> list += host to info.str("pc") }
            list
        }
        if (candidates.isEmpty()) throw UserError("Could not find the PC on this Wi-Fi. Open SDJ Mobile Care on the PC, connect " +
            "the phone to the shop Wi-Fi, or type the PC address shown in Settings → Phone link.")
        var lastError = "Could not reach the PC."
        var lastHost: String? = null
        for ((host, knownPc) in candidates) {
            try {
                val pcId = knownPc ?: withContext(io) { net.hello(host, port) }.str("pc")
                val key = withContext(Dispatchers.Default) { Crypto.pairKey(code, pcId) }
                val hello = uuid()
                val env = Crypto.seal(key, JSONObject().put("name", phoneName).put("hello", hello).put("app", appVersion), "pair".toByteArray())
                env.put("pc", pcId)
                val ans = Crypto.unseal(key, withContext(io) { net.post(host, port, "/sdj/pair", env) }, "pair".toByteArray())
                if (ans.str("hello") != hello || !ans.bool("ok")) throw UserError("The PC's answer did not match. Try again.")
                mutex.withLock {
                    store.wipe()
                    cfg.put("device", ans.str("device")).put("key", ans.str("key")).put("no", ans.int("no"))
                        .put("pc", ans.str("pc")).put("shop_name", ans.str("shop")).put("user", ans.obj("user"))
                        .put("hosts", JSONArray((listOf(host) + (qr?.hosts ?: emptyList())).distinct())).put("port", port)
                        .put("seq", 0).put("last_host", host)
                    store.saveConfig()
                    shop = null; users = emptyList(); products = emptyList(); sales = emptyList(); jobs = emptyList()
                    customers = emptyList(); expenses = emptyList(); imeis = emptyList(); dash = null; signedIn = null
                    sync = SyncInfo(host = host)
                    publish()
                }
                syncOnce(full = true)
                return
            } catch (e: PcError) {
                lastError = e.message ?: lastError
            } catch (e: CryptoException) {
                lastError = "The code is wrong or has expired. Create a new code on the PC."
            } catch (e: IOException) {
                lastError = reachError(host, e)
                lastHost = host
            }
        }
        throw LinkError(lastError, lastHost, port)
    }

    suspend fun unlink() = mutex.withLock {
        store.wipe()
        shop = null; users = emptyList(); products = emptyList(); sales = emptyList(); jobs = emptyList()
        customers = emptyList(); expenses = emptyList(); imeis = emptyList(); dash = null; signedIn = null
        notices.clear(); sync = SyncInfo()
        publish()
    }

    // ================================================================ sync
    /** One round: send entries made here, receive what changed on the PC. Returns true if the PC answered. */
    suspend fun syncOnce(full: Boolean = false): Boolean {
        if (!linked) return false
        val files = syncMutex.withLock { syncLocked(full) } ?: return false
        startFiles(files)
        return true
    }

    /** What a sync answer asked the phone to download (backups, a newer app). */
    private class Files(val backups: JSONArray?, val update: JSONObject?, val device: String, val key: ByteArray)

    /** Backups and app updates download after the sync, so new entries never wait behind a big file. */
    private fun startFiles(f: Files) {
        if (filesJob?.isActive == true) return                 // one download round at a time; the next sync offers again
        filesJob = bg.launch {
            filesMutex.withLock {
                runCatching { fetchBackups(f.backups, f.device, f.key) }
                runCatching { offerUpdate(f.update, f.device, f.key) }
            }
        }
    }

    /** Waits for the backup / update downloads started by the last sync (background sync and tests). */
    suspend fun awaitFiles() {
        filesJob?.join()
    }

    // ================================================================ live sync (while the app is on screen)
    /** Wakes the live sync at once: Wi-Fi came back, or the shop PC was switched on. */
    fun nudge() {
        nudges.trySend(Unit)
    }

    internal enum class Wait { CHANGED, QUIET, OLD_PC, FAILED }

    /**
     * Runs while the app is on screen: syncs now, then again as soon as the PC's data changes (the PC answers a waiting
     * request at once), when Wi-Fi comes back, or when the shop PC is switched on. Quick retries when the PC is away.
     */
    suspend fun live(): Unit = coroutineScope {
        val stopWifi = net.watchWifi { nudge() }
        val pcId = cfg.str("pc")
        val listener = launch(io) { if (linked) net.listenForPc(pcId, { isActive }) { nudge() } }
        var fails = 0
        try {
            while (isActive) {
                if (syncOnce()) {
                    fails = 0
                    val started = System.currentTimeMillis()
                    when (waitForChange()) {
                        Wait.CHANGED, Wait.QUIET -> {}                                   // sync again right away
                        Wait.OLD_PC -> withTimeoutOrNull(5_000L) { nudges.receive() }    // older PC program: ask every 5 s
                        Wait.FAILED -> if (System.currentTimeMillis() - started < 2_000) withTimeoutOrNull(3_000L) { nudges.receive() }
                    }
                } else {
                    fails++
                    withTimeoutOrNull(RETRY_MS[minOf(fails, RETRY_MS.size) - 1]) { nudges.receive() }
                }
            }
        } finally {
            listener.cancel()
            stopWifi()
        }
    }

    /** Holds one request open on the PC until its data changes or ~25 s pass, so PC changes show here at once. */
    internal suspend fun waitForChange(seconds: Int = 25): Wait {
        val device = cfg.str("device")
        val host = cfg.str("last_host")
        if (device.isEmpty() || host.isEmpty()) return Wait.FAILED
        val key = Crypto.unb64(cfg.str("key"))
        val aad = "$device|wait".toByteArray()
        return try {
            val env = Crypto.seal(key, JSONObject().put("token", cfg.str("token")).put("wait", seconds), aad).put("d", device)
            val ans = withContext(io) { Crypto.unseal(key, net.post(host, cfg.optInt("port", Net.PORT), "/sdj/wait", env, 40_000), aad) }
            if (ans.bool("changed")) Wait.CHANGED else Wait.QUIET
        } catch (e: PcError) {
            if (e.status == 404) Wait.OLD_PC else Wait.FAILED
        } catch (e: Exception) {
            Wait.FAILED
        }
    }

    private suspend fun syncLocked(full: Boolean): Files? {
        val device = cfg.str("device")
        val key = Crypto.unb64(cfg.str("key"))
        val (env, sentIds) = mutex.withLock {
            val ops = opsList().filter { it.str("_st", "PENDING") == "PENDING" }
            val wire = JSONArray()
            ops.forEach { op ->
                val c = JSONObject()
                op.keys().forEach { k -> if (!k.startsWith("_")) c.put(k, op.get(k)) }
                wire.put(c)
            }
            val seq = cfg.optLong("seq", 0) + 1
            cfg.put("seq", seq)
            store.saveConfig()
            val needFull = full || shop == null || users.isEmpty()
            val req = JSONObject().put("seq", seq).put("ops", wire).put("full", needFull).put("app", appVersion)
                .put("token", if (needFull) JSONObject.NULL else cfg.str("token")).put("hashes", cfg.optJSONObject("hashes") ?: JSONObject())
                .put("acks", JSONArray(backups.recentShas()))
            sync = sync.copy(running = true)
            publish()
            Crypto.seal(key, req, device.toByteArray()).put("d", device) to ops.map { it.str("id") }.toSet()
        }
        return try {
            val ans = withContext(io) { Crypto.unseal(key, postToPc(env), device.toByteArray()) }
            mutex.withLock { withContext(io) { applyAnswer(ans, sentIds) } }
            Files(ans.optJSONArray("backups"), ans.optJSONObject("update"), device, key)
        } catch (e: PcError) {
            mutex.withLock {
                when (e.code) {
                    "removed", "unknown" -> sync = sync.copy(running = false, removed = true, error = e.message ?: "")
                    "replay" -> { cfg.put("seq", cfg.optLong("seq", 0) + 1000); store.saveConfig(); sync = sync.copy(running = false, error = "Try again.") }
                    else -> sync = sync.copy(running = false, error = e.message ?: "The PC refused the sync.")
                }
                publish()
            }
            null
        } catch (e: Exception) {
            mutex.withLock {
                val why = if (e is PcUnreachable && net.wifi() != null) "\nIf you are at the shop: ${e.message}" else ""
                sync = sync.copy(running = false, error = "PC not reachable – entries are safe on this phone and will sync on the shop Wi-Fi.$why")
                publish()
            }
            null
        }
    }

    private fun postToPc(env: JSONObject): JSONObject {
        val port = cfg.optInt("port", Net.PORT)
        val hosts = (listOf(cfg.str("last_host")) + cfg.arr("hosts").strings()).filter { it.isNotBlank() }.distinct()
        var last: Pair<String, IOException>? = null
        fun attempt(h: String): JSONObject? = try {
            net.post(h, port, "/sdj/sync", env).also { cfg.put("last_host", h) }
        } catch (e: IOException) {
            last = h to e
            null
        }
        // 1. where the PC answered last time (almost always right)
        hosts.firstOrNull()?.let { h -> attempt(h)?.let { return it } }
        // 2. ask the Wi-Fi where the PC is now – its address may have changed (quicker than trying old addresses)
        for ((h, _) in net.discover(cfg.str("pc"), 1200)) {
            val r = attempt(h) ?: continue
            cfg.put("hosts", JSONArray((listOf(h) + hosts).distinct().take(6)))
            return r
        }
        // 3. the PC's other addresses
        for (h in hosts.drop(1)) attempt(h)?.let { return it }
        throw PcUnreachable(last?.let { reachError(it.first, it.second) } ?: "PC not found on this Wi-Fi.")
    }

    private fun applyAnswer(ans: JSONObject, sentIds: Set<String>) {
        val data = ans.optJSONObject("data")
        if (data != null) {
            data.keys().forEach { k ->
                val v = data.get(k).toString()
                store.putData(k, v)
                parse(k, v)
            }
            cfg.put("hashes", ans.optJSONObject("hashes") ?: JSONObject())
        }
        cfg.put("token", ans.str("token"))
        val results = ans.arr("results")
        val byId = HashMap<String, JSONObject>()
        for (i in 0 until results.length()) results.getJSONObject(i).let { byId[it.str("id")] = it }
        val a = store.ops()
        val keep = JSONArray()
        val sent = store.sent()
        val nowIso = LocalDateTime.now().withNano(0).toString()
        for (i in 0 until a.length()) {
            val op = a.getJSONObject(i)
            val r = byId[op.str("id")]
            if (r == null || op.str("id") !in sentIds) { keep.put(op); continue }
            when (r.str("st")) {
                "DONE" -> sent.put(op.put("_sent", nowIso))
                "CONFLICT" -> notices.add(0, "${info(op).title}: changed on the PC too – the owner will decide on the PC (Sync alerts).")
                else -> keep.put(op.put("_st", "FAILED").put("_msg", r.str("msg")))
            }
        }
        store.saveOps(keep)
        store.saveSent(recentSent(sent))
        val epoch = ans.str("epoch")
        if (epoch.isNotEmpty()) {
            val old = cfg.str("epoch")
            if (old.isNotEmpty() && old != epoch) requeueSent()
            cfg.put("epoch", epoch)
        }
        while (notices.size > 20) notices.removeAt(notices.size - 1)
        val now = LocalDateTime.now().withNano(0).toString()
        cfg.put("last_sync", now)
        store.saveConfig()
        if (signedIn != null) signedIn = users.firstOrNull { it.id == signedIn?.id && it.id == shop?.userId }
        sync = SyncInfo(running = false, lastOk = now, error = "", host = cfg.str("last_host"))
        publish()
    }

    private fun recentSent(a: JSONArray): JSONArray {
        val since = LocalDateTime.now().minusDays(10).toString()
        val all = (0 until a.length()).map { a.getJSONObject(it) }.filter { it.str("_sent") >= since }
        return JSONArray().apply { all.takeLast(1500).forEach { put(it) } }
    }

    /** The PC's data was restored from a backup: entries it had already saved may be missing there now. They are sent
     *  again – the PC saves each entry only once, so nothing is doubled. */
    private fun requeueSent() {
        val sent = recentSent(store.sent())
        val ops = store.ops()
        val have = HashSet<String>()
        for (i in 0 until ops.length()) have += ops.getJSONObject(i).str("id")
        val again = JSONArray()
        var n = 0
        for (i in 0 until sent.length()) {
            val op = sent.getJSONObject(i)
            if (op.str("id") in have) continue
            op.remove("_sent")
            op.remove("_st")
            op.remove("_msg")
            again.put(op)
            n++
        }
        for (i in 0 until ops.length()) again.put(ops.getJSONObject(i))
        store.saveOps(again)
        store.saveSent(JSONArray())
        if (n > 0) notices.add(0, "The PC's data was restored from a backup – $n entr${if (n == 1) "y" else "ies"} from this " +
            "phone are being sent again (the PC saves each one only once).")
    }

    // ================================================================ backups
    /** Downloads the backups / closing reports the PC offers and keeps them in Downloads → SDJ Backups. */
    private suspend fun fetchBackups(list: JSONArray?, device: String, key: ByteArray) {
        if (list == null || list.length() == 0) {
            withContext(io) { backups.copyToDrive(false) }             // a Google Drive copy waiting for Wi-Fi
            return
        }
        if (!backups.sink.ready()) {
            backups.setError("Allow storage access on the Backups screen, so the PC's backups can be saved in Downloads.")
            return
        }
        val work = File(store.dir, "dl").apply { mkdirs() }
        for (i in 0 until list.length()) {
            val meta = list.optJSONObject(i) ?: continue
            val id = meta.lng("id")
            if (id <= 0 || backups.has(meta.str("sha"))) continue
            val tmp = File(work, "backup.part")
            try {
                val seq = mutex.withLock {
                    val s = cfg.optLong("seq", 0) + 1
                    cfg.put("seq", s)
                    store.saveConfig()
                    s
                }
                val env = Crypto.seal(key, JSONObject().put("seq", seq).put("id", id).put("app", appVersion),
                    "$device|backup".toByteArray()).put("d", device)
                val sha = withContext(io) { downloadFromPc(env, tmp) }
                if (sha != meta.str("sha")) throw IOException("the file did not arrive complete")
                withContext(io) { backups.saveDownloaded(meta, tmp) }
            } catch (e: PcError) {
                if (e.code == "replay") mutex.withLock { cfg.put("seq", cfg.optLong("seq", 0) + 1000); store.saveConfig() }
                backups.setError(e.message ?: "The PC did not send the backup.")
                break
            } catch (e: Exception) {
                backups.setError("Backup not saved yet (${e.message}) – it is tried again at the next sync.")
                break
            } finally {
                tmp.delete()
            }
        }
    }

    /** The PC says a newer phone app is waiting: download it from the PC (shop Wi-Fi, no internet) and keep it ready. */
    suspend fun offerUpdate(meta: JSONObject?, device: String, key: ByteArray) {
        val ver = meta?.str("version").orEmpty()
        val sha = meta?.str("sha").orEmpty().lowercase()
        val size = meta?.lng("size") ?: 0L
        if (meta == null || !isNewerVersion(ver, appVersion) || sha.length != 64 || size !in 1..MAX_APK) {
            tidyInstalledUpdate()
            return
        }
        val cur = _update.value
        if (cur != null && cur.state == AppUpdate.READY && !isNewerVersion(ver, cur.version)) return   // already have it
        getUpdate(ver, size, sha, meta.str("notes"), meta.str("date"), meta.bool("recommended")) { tmp ->
            val seq = mutex.withLock {
                val n = cfg.optLong("seq", 0) + 1
                cfg.put("seq", n)
                store.saveConfig()
                n
            }
            val env = Crypto.seal(key, JSONObject().put("seq", seq).put("app", appVersion), "$device|apk".toByteArray()).put("d", device)
            withContext(io) { downloadFromPc(env, tmp, "/sdj/apk") }
        }
    }

    private fun downloadFromPc(env: JSONObject, dest: File, path: String = "/sdj/backup"): String {
        val port = cfg.optInt("port", Net.PORT)
        val hosts = (listOf(cfg.str("last_host")) + cfg.arr("hosts").strings()).filter { it.isNotBlank() }.distinct()
        var last: IOException? = null
        for (h in hosts) {
            try {
                return net.download(h, port, path, env, dest)
            } catch (e: IOException) {
                last = e
            }
        }
        throw last ?: IOException("PC not reachable")
    }

    /** What the PC's "Restore from phone" QR says: where the PC is and the restore code. */
    data class RestoreQr(val hosts: List<String>, val port: Int, val code: String, val pcId: String, val name: String)

    fun parseRestoreQr(text: String): RestoreQr? {
        if (!text.startsWith("SDJRESTORE:")) return null
        val kv = text.substringAfter(";").split(";").mapNotNull { p -> p.split("=", limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } }.toMap()
        val code = kv["c"] ?: return null
        return RestoreQr(kv["h"].orEmpty().split(",").filter { it.isNotBlank() }, kv["p"]?.toIntOrNull() ?: Net.PORT, code,
            kv["id"].orEmpty(), kv["n"].orEmpty())
    }

    /** Sends a backup to a (new) PC that shows "Restore from phone". The PC then asks for the owner's password. */
    suspend fun sendToPc(item: BackupItem, qr: RestoreQr, phoneName: String) {
        if (Crypto.normaliseCode(qr.code).length < 10) throw UserError("Scan the QR shown on the PC (Restore from phone).")
        if (qr.hosts.isEmpty()) throw UserError("The code has no PC address. Type the PC's address.")
        val (size, sha) = withContext(io) {
            val ins = backups.sink.open(item.uri) ?: throw UserError("The backup file is missing – it may have been deleted from Downloads.")
            Backups.sha256(ins)
        }
        var lastErr = "Could not reach the PC."
        for (host in qr.hosts) {
            try {
                val pcId = qr.pcId.ifBlank { withContext(io) { net.hello(host, qr.port) }.str("pc") }
                val key = withContext(Dispatchers.Default) { Crypto.pairKey(qr.code, pcId) }
                val hello = uuid()
                val meta = JSONObject().put("name", item.name).put("size", size).put("sha", sha).put("phone", phoneName)
                    .put("hello", hello)
                val auth = Crypto.seal(key, meta, "restore".toByteArray()).put("pc", pcId)
                val ans = withContext(io) {
                    net.upload(host, qr.port, "/sdj/restore", auth.toString(), size,
                        source = { backups.sink.open(item.uri) ?: throw IOException("The backup file is missing.") })
                }
                val r = Crypto.unseal(key, ans, "restore".toByteArray())
                if (!r.bool("ok") || r.str("hello") != hello) throw UserError("The PC's answer did not match. Try again.")
                return
            } catch (e: PcError) {
                throw UserError(e.message ?: "The PC refused the backup.")
            } catch (e: CryptoException) {
                throw UserError("The restore code is wrong or has expired. Make a new code on the PC.")
            } catch (e: IOException) {
                lastErr = reachError(host, e)
            }
        }
        throw UserError(lastErr)
    }

    /** Emergency (no backup anywhere): sends this phone's copy of the shop to a new PC, which rebuilds products, stock,
     *  IMEIs, customers with dues and open repair jobs from it. Owner only. */
    suspend fun rebuildPc(qr: RestoreQr, phoneName: String): JSONObject {
        if (!linked || shop == null || users.isEmpty())
            throw UserError("This phone has no shop copy yet – it must have synced with the old PC at least once.")
        if (signedIn?.role != "ADMIN") throw UserError("Only the owner can rebuild a PC from this phone.")
        if (Crypto.normaliseCode(qr.code).length < 10) throw UserError("Scan the QR shown on the PC (Restore from phone).")
        if (qr.hosts.isEmpty()) throw UserError("The code has no PC address. Type the PC's address.")
        val hello = uuid()
        val payload = mutex.withLock {
            withContext(io) {
                val data = JSONObject()
                for (k in listOf("shop", "users", "categories", "products", "imeis", "customers", "sales", "jobs", "expenses")) {
                    val text = store.data(k) ?: continue
                    data.put(k, if (text.trimStart().startsWith("[")) JSONArray(text) else JSONObject(text))
                }
                val device = JSONObject().put("uid", cfg.str("device")).put("key", cfg.str("key")).put("no", cfg.int("no"))
                    .put("name", phoneName).put("user_id", cfg.obj("user").lng("id"))
                JSONObject().put("data", data).put("device", device).put("epoch", cfg.str("epoch"))
                    .put("made", LocalDateTime.now().withNano(0).toString()).put("last_sync", cfg.str("last_sync"))
                    .put("hello", hello)
            }
        }
        var lastErr = "Could not reach the PC."
        for (host in qr.hosts) {
            try {
                val pcId = qr.pcId.ifBlank { withContext(io) { net.hello(host, qr.port) }.str("pc") }
                val key = withContext(Dispatchers.Default) { Crypto.pairKey(qr.code, pcId) }
                val env = Crypto.seal(key, payload, "rebuild".toByteArray()).put("pc", pcId)
                val ans = Crypto.unseal(key, withContext(io) { net.post(host, qr.port, "/sdj/rebuild", env, 180_000) },
                    "rebuild".toByteArray())
                if (!ans.bool("ok") || ans.str("hello") != hello) throw UserError("The PC's answer did not match. Try again.")
                return ans.obj("summary")
            } catch (e: PcError) {
                throw UserError(e.message ?: "The PC refused the shop copy.")
            } catch (e: CryptoException) {
                throw UserError("The restore code is wrong or has expired. Make a new code on the PC.")
            } catch (e: IOException) {
                lastErr = reachError(host, e)
            }
        }
        throw UserError(lastErr)
    }

    /** A backup file picked by hand (Google Drive download, file manager) is added to SDJ Backups. */
    suspend fun importBackup(name: String, open: () -> InputStream?): BackupItem = withContext(io) {
        val ins = open() ?: throw UserError("Could not open that file.")
        backups.importFile(name, ins, File(store.dir, "dl").apply { mkdirs() })
    }

    companion object {
        val DATASETS = listOf("shop", "users", "categories", "products", "imeis", "customers", "sales", "jobs", "expenses", "dash")
    }
}

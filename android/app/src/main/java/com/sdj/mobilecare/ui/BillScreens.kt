package com.sdj.mobilecare.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sdj.mobilecare.data.Approval
import com.sdj.mobilecare.data.BillDraft
import com.sdj.mobilecare.data.BillPdf
import com.sdj.mobilecare.data.CustChoice
import com.sdj.mobilecare.data.Customer
import com.sdj.mobilecare.data.Dates
import com.sdj.mobilecare.data.DraftLine
import com.sdj.mobilecare.data.Imei
import com.sdj.mobilecare.data.MODE_LABELS
import com.sdj.mobilecare.data.Money
import com.sdj.mobilecare.data.PAY_MODES
import com.sdj.mobilecare.data.Product
import com.sdj.mobilecare.data.Sale
import com.sdj.mobilecare.data.UiState

fun findSale(st: UiState, key: String): Sale? {
    st.sales.firstOrNull { it.key == key }?.let { return it }
    if (key.startsWith("ref:")) {
        val ref = key.removePrefix("ref:")
        return st.sales.firstOrNull { it.ref == ref && it.active } ?: st.sales.firstOrNull { it.ref == ref }
    }
    return null
}

@Composable
fun SaleRow(s: Sale, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable { onClick() }.padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(s.no, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                if (s.pending) { HGap(6); PendingPill() }
            }
            Text("${s.customerName.ifBlank { "Walk-in" }} · ${Dates.nice(s.date)}", color = Muted, fontSize = 12.sp, maxLines = 1)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(Money.fmt(s.total), fontWeight = FontWeight.Bold)
            when {
                s.replacedOnPhone -> Pill("Edited", Muted)
                s.status == "CANCELLED" -> Pill("Cancelled", Bad)
                s.due > 0 -> Pill("Due ${Money.short(s.due)}", Warn)
                s.returned > 0 -> Pill("Returned", Info)
                else -> Pill("Paid", Good)
            }
        }
    }
}

@Composable
fun SalesScreen(ui: Ui, st: UiState, nav: Nav) {
    var q by remember { mutableStateOf("") }
    var range by remember { mutableStateOf("TODAY") }
    val today = st.businessDate
    val weekAgo = runCatching { java.time.LocalDate.parse(today).minusDays(6).toString() }.getOrDefault("")
    val list = st.sales.filter { s ->
        (q.isBlank() || s.no.contains(q, true) || s.customerName.contains(q, true) || s.customerPhone.contains(q.trim()) ||
            s.lines.any { it.imei.contains(q.trim()) || it.name.contains(q, true) }) &&
            when (range) { "TODAY" -> s.date == today; "WEEK" -> s.date >= weekAgo; else -> true }
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 14.dp, end = 14.dp, top = 14.dp, bottom = 96.dp)) {
        item {
            SearchBox(q, { q = it }, "Search bill no, customer, phone, IMEI")
            Gap()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("TODAY" to "Today", "WEEK" to "7 days", "ALL" to "All on phone").forEach { (k, t) ->
                    FilterChip(selected = range == k, onClick = { range = k }, label = { Text(t) })
                }
            }
            Gap()
            val active = list.filter { it.active }
            Text("${active.size} bills · ${Money.fmt(active.sumOf { it.total })}", color = Muted, fontSize = 13.sp)
        }
        if (list.isEmpty()) item { Empty("No bills") }
        items(list, key = { it.key + it.no }) { s ->
            SaleRow(s) { nav.go(Route.SaleView(s.key)) }
            HorizontalDivider()
        }
    }
}

@Composable
fun SaleScreen(ui: Ui, st: UiState, nav: Nav, key: String) {
    val s = findSale(st, key) ?: return Empty("This bill is not on the phone any more.")
    val ctx = LocalContext.current
    var cancel by remember { mutableStateOf(false) }
    var reason by remember { mutableStateOf("") }
    var approvalFor by remember { mutableStateOf<Set<String>>(emptySet()) }
    var afterApproval by remember { mutableStateOf<((Approval) -> Unit)?>(null) }
    val canChange = s.active && s.block.isEmpty() && s.type != "EXCHANGE"
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Section(s.no, trailing = {
            when {
                s.replacedOnPhone -> Pill("Edited on phone", Muted)
                s.status == "CANCELLED" -> Pill("Cancelled", Bad)
                s.pending -> PendingPill()
                else -> Pill(if (s.due > 0) "Due" else "Paid", if (s.due > 0) Warn else Good)
            }
        }) {
            Row2("Date", Dates.nice(s.date))
            Row2("Customer", s.customerName.ifBlank { "Walk-in" } + if (s.customerPhone.isNotBlank()) " · ${s.customerPhone}" else "")
            if (s.type == "QUICK") Row2("Type", "Quick sale")
            if (s.block.isNotEmpty() && !s.pending) Text(s.block, color = Muted, fontSize = 12.sp)
            if (s.cancelReason.isNotBlank()) Text("Reason: ${s.cancelReason}", color = Bad, fontSize = 13.sp)
        }
        Section("Items") {
            s.lines.forEach { l ->
                Row {
                    Column(Modifier.weight(1f)) {
                        Text(l.name, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        Text("${l.qty} × ${Money.fmt(l.price)}" + (if (l.disc + l.share > 0) " · disc ${Money.fmt(l.disc + l.share)}" else ""), color = Muted, fontSize = 12.sp)
                        if (l.imei.isNotBlank()) Text("IMEI ${l.imei}", color = Muted, fontSize = 12.sp)
                    }
                    Text(Money.fmt(l.total), fontWeight = FontWeight.Medium)
                }
            }
            HorizontalDivider()
            Row2("Items total", Money.fmt(s.gross))
            if (s.itemDisc + s.billDisc > 0) Row2("Discount", "-" + Money.fmt(s.itemDisc + s.billDisc))
            if (s.gstOn) { Row2("Taxable", Money.fmt(s.taxable)); Row2("GST", Money.fmt(s.gst)) }
            if (s.roundOff != 0L) Row2("Round off", Money.fmt(s.roundOff))
            Row2("Grand total", Money.fmt(s.total), bold = true)
            s.pays.filter { it.status == "ACTIVE" && it.dir == "IN" }.forEach { Row2("Paid · ${MODE_LABELS[it.mode] ?: it.mode}", Money.fmt(it.amount)) }
            if (s.returned > 0) Row2("Returned", Money.fmt(s.returned))
            if (s.due > 0) Row2("Balance due", Money.fmt(s.due), bold = true, color = Warn)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SmallButton("Share bill", {
                val shop = st.shop
                if (shop != null) runCatching { BillPdf.share(ctx, s, shop) }.onFailure { ui.toast("Could not create the PDF: ${it.message}") }
            }, Modifier.weight(1f), primary = true)
            if (canChange) SmallButton("Edit", { nav.go(Route.BillEdit(editKey = s.key)) }, Modifier.weight(1f))
            if (canChange) SmallButton("Cancel bill", { cancel = true }, Modifier.weight(1f))
        }
    }
    if (cancel) AlertDialog(onDismissRequest = { cancel = false }, title = { Text("Cancel ${s.no}?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Stock goes back and the bill's payments are voided (also on the PC).", color = Muted)
                TextIn("Reason", reason, { reason = it }, Modifier.fillMaxWidth())
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val doIt: (Approval?) -> Unit = { ap -> ui.run("Bill cancelled", onDone = { cancel = false }) { ui.repo.cancelBill(s, reason, ap) } }
                val miss = ui.repo.missing("cancel_sale")
                if (miss.isEmpty()) doIt(null)
                else if (ui.repo.ownerApproval()) { approvalFor = miss; afterApproval = { ap -> doIt(ap) } }
                else ui.toast("You do not have permission to cancel bills.")
            }) { Text("Cancel bill", color = Bad, fontWeight = FontWeight.Bold) }
        },
        dismissButton = { TextButton(onClick = { cancel = false }) { Text("Keep", color = Muted) } })
    if (approvalFor.isNotEmpty()) ApprovalDialog(ui.repo, approvalFor, onDone = { ap -> approvalFor = emptySet(); afterApproval?.invoke(ap) },
        onDismiss = { approvalFor = emptySet() })
}

// ================================================================ new / edit bill
@Composable
fun BillEditor(ui: Ui, st: UiState, nav: Nav, quick: Boolean, editKey: String?) {
    val edit = remember(editKey) { editKey?.let { findSale(st, it) } }
    val byId = st.products.associateBy { it.id }
    val lines = remember(editKey) {
        mutableStateListOf<DraftLine>().apply {
            edit?.lines?.forEach { l ->
                val p = byId[l.productId] ?: return@forEach
                val parts = l.imei.split(" / ")
                add(DraftLine(p, l.qty, l.price, l.disc, l.imeiId?.let { Imei(it, p.id, parts.getOrElse(0) { "" }, parts.getOrElse(1) { "" }, "") }))
            }
        }
    }
    var customer by remember(editKey) {
        mutableStateOf<CustChoice>(edit?.let { e ->
            st.customers.firstOrNull { it.id == e.customerId }?.let { CustChoice.Existing(it) }
                ?: if (e.customerName.isNotBlank() && e.customerId != st.shop?.walkIn) CustChoice.New(e.customerName, e.customerPhone) else CustChoice.WalkIn
        } ?: CustChoice.WalkIn)
    }
    var billDisc by remember(editKey) { mutableStateOf(edit?.billDisc ?: 0L) }
    var notes by remember(editKey) { mutableStateOf(edit?.notes ?: "") }
    val split = remember(editKey) { mutableStateMapOf<String, Long>().apply { edit?.pays?.filter { it.status == "ACTIVE" && it.dir == "IN" }?.forEach { put(it.mode, (get(it.mode) ?: 0) + it.amount) } } }
    var splitOn by remember(editKey) { mutableStateOf(edit != null && (split.size > 1 || (edit.pays.sumOf { it.amount } != edit.total))) }
    var payMode by remember(editKey) { mutableStateOf(split.keys.firstOrNull() ?: "CASH") }
    var q by remember { mutableStateOf("") }
    var pickImei by remember { mutableStateOf<Product?>(null) }
    var editLine by remember { mutableStateOf<Int?>(null) }
    var pickCust by remember { mutableStateOf(false) }
    var approvalFor by remember { mutableStateOf<Set<String>>(emptySet()) }
    var stockConfirm by remember { mutableStateOf<String?>(null) }

    val type = edit?.type ?: if (quick) "QUICK" else "BILL"
    fun draft(): BillDraft {
        val total = ui.repo.checkBill(BillDraft(lines.toList(), customer, billDisc, emptyMap(), notes, type, edit)).calc.grandTotal
        val pays = if (splitOn) split.toMap() else mapOf(payMode to total)
        return BillDraft(lines.toList(), customer, billDisc, pays, notes, type, edit)
    }
    val d = draft()
    val chk = ui.repo.checkBill(d)

    fun addProduct(p: Product, imei: Imei? = null) {
        if (p.imei) {
            if (imei == null) { pickImei = p; return }
            if (lines.any { it.imei?.id == imei.id }) { ui.toast("IMEI already in this bill"); return }
            lines.add(DraftLine(p, 1, p.price, 0, imei))
        } else {
            val i = lines.indexOfFirst { it.product.id == p.id && it.imei == null }
            if (i >= 0) lines[i] = lines[i].copy(qty = lines[i].qty + 1) else lines.add(DraftLine(p, 1, p.price))
        }
        q = ""
    }

    val scan = rememberScanner("Scan the barcode or IMEI") { text ->
        val t = text.trim()
        val unit = st.imeis.firstOrNull { it.matches(t) }
        val p = st.products.firstOrNull { it.active && (it.barcode == t || it.code.equals(t, true)) }
        when {
            unit != null -> byId[unit.product]?.let { addProduct(it, unit) }
            p != null -> addProduct(p)
            else -> ui.toast("No product or IMEI '$t' in stock on this phone")
        }
    }

    fun save(approval: Approval?) {
        ui.run("Bill saved – it goes to the PC with the next sync") {
            val sale = ui.repo.saveBill(draft(), approval)
            nav.replace(Route.SaleView(sale.key))
        }
    }
    fun trySave(skipStock: Boolean = false) {
        val c = ui.repo.checkBill(draft())
        if (c.error != null) { ui.toast(c.error); return }
        if (c.stockWarning != null && !skipStock) { stockConfirm = c.stockWarning; return }
        if (c.needPerms.isNotEmpty()) {
            if (ui.repo.ownerApproval()) approvalFor = c.needPerms else ui.toast("You do not have permission for this bill. Please ask the owner.")
            return
        }
        save(null)
    }

    val results = if (q.isBlank()) emptyList() else st.products.filter {
        it.active && (it.name.contains(q, true) || it.code.contains(q, true) || it.barcode == q.trim() || it.model.contains(q, true))
    }.take(25)

    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (edit != null) item { Text("Editing ${edit.no} – the invoice number stays the same.", color = Info, fontSize = 13.sp) }
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SearchBox(q, { q = it }, "Search product or code", Modifier.weight(1f))
                    HGap()
                    OutlinedButton(onClick = scan) { Text("Scan") }
                }
            }
            items(results, key = { "r" + it.id }) { p ->
                Row(Modifier.fillMaxWidth().clickable { addProduct(p) }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.name, fontWeight = FontWeight.SemiBold)
                        Text("${p.code} · ${Money.fmt(p.price)}", color = Muted, fontSize = 12.sp)
                    }
                    Pill("${p.qty}", if (p.qty <= 0) Bad else Good)
                }
            }
            item {
                Section("Items (${lines.size})") {
                    if (lines.isEmpty()) Text("Search or scan to add products.", color = Muted)
                    lines.forEachIndexed { i, l ->
                        val lo = chk.calc.lines.getOrNull(i)
                        Row(Modifier.fillMaxWidth().clickable { editLine = i }, verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(l.product.name, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                                Text("${l.qty} × ${Money.fmt(l.price)}" + (if (l.disc > 0) " · disc ${Money.fmt(l.disc)}" else ""), color = Muted, fontSize = 12.sp)
                                l.imei?.let { Text("IMEI ${it.label}", color = Muted, fontSize = 12.sp) }
                            }
                            Text(Money.fmt(lo?.total ?: 0), fontWeight = FontWeight.Medium)
                            IconButton(onClick = { lines.removeAt(i) }) { Icon(Icons.Default.Close, contentDescription = "Remove") }
                        }
                    }
                }
            }
            item {
                Section("Customer") {
                    val label = when (val c = customer) {
                        is CustChoice.WalkIn -> "Walk-in customer"
                        is CustChoice.Existing -> "${c.c.name} · ${c.c.phone}"
                        is CustChoice.New -> "${c.name} · ${c.phone} (new)"
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(label, modifier = Modifier.weight(1f), fontWeight = FontWeight.SemiBold)
                        TextButton(onClick = { pickCust = true }) { Text("Change", color = Info) }
                    }
                    (customer as? CustChoice.Existing)?.c?.let { if (it.due > 0) Text("Pending dues (all bills): ${Money.fmt(it.due)}", color = Warn, fontSize = 12.sp) }
                }
            }
            item {
                Section("Payment") {
                    MoneyField("Bill discount", billDisc, { billDisc = it }, Modifier.fillMaxWidth())
                    Row2("Items total", Money.fmt(chk.calc.gross))
                    if (chk.calc.itemDisc + chk.calc.billDisc > 0) Row2("Discount", "-" + Money.fmt(chk.calc.itemDisc + chk.calc.billDisc))
                    val shop = st.shop
                    if (shop != null && shop.gstEnabled) Row2(if (shop.inclusive) "GST (included)" else "GST", Money.fmt(chk.calc.gst))
                    if (chk.calc.roundOff != 0L) Row2("Round off", Money.fmt(chk.calc.roundOff))
                    Row2("Grand total", Money.fmt(chk.calc.grandTotal), bold = true)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = splitOn, onCheckedChange = { on -> splitOn = on; if (on && split.isEmpty()) split[payMode] = chk.calc.grandTotal })
                        Text("Part payment / split between modes", fontSize = 14.sp)
                    }
                    if (!splitOn) {
                        Text("Paid in full by", color = Muted, fontSize = 13.sp)
                        ModeChips(payMode, { payMode = it })
                    } else {
                        PAY_MODES.forEach { m -> MoneyField(MODE_LABELS[m] ?: m, split[m] ?: 0, { split[m] = it }, Modifier.fillMaxWidth()) }
                        Row2("Paid", Money.fmt(chk.paid))
                        if (chk.balance > 0) Row2("Balance (credit)", Money.fmt(chk.balance), bold = true, color = Warn)
                    }
                    TextIn("Notes (optional)", notes, { notes = it }, Modifier.fillMaxWidth())
                }
            }
        }
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            chk.error?.takeIf { lines.isNotEmpty() }?.let { Text(it, color = Bad, fontSize = 12.sp, modifier = Modifier.padding(bottom = 6.dp)) }
            SmallButton(if (edit != null) "Save changes · ${Money.fmt(chk.calc.grandTotal)}" else "Save bill · ${Money.fmt(chk.calc.grandTotal)}",
                { trySave() }, Modifier.fillMaxWidth().heightIn(min = 52.dp), primary = true, enabled = lines.isNotEmpty())
        }
    }

    pickImei?.let { p ->
        val taken = lines.mapNotNull { it.imei?.id }.toSet()
        val units = st.imeis.filter { it.product == p.id && it.id !in taken }
        AlertDialog(onDismissRequest = { pickImei = null }, title = { Text("IMEI for ${p.name}") },
            text = {
                Column(Modifier.heightIn(max = 380.dp).verticalScroll(rememberScrollState())) {
                    if (units.isEmpty()) Text("No IMEI of this model is in stock on the phone's copy. Sync with the PC and try again.", color = Bad)
                    units.forEach { u ->
                        Text(u.label, modifier = Modifier.fillMaxWidth().clickable { addProduct(p, u); pickImei = null }.padding(vertical = 10.dp))
                        HorizontalDivider()
                    }
                }
            },
            confirmButton = { TextButton(onClick = { pickImei = null }) { Text("Close") } })
    }
    val li = editLine
    if (li != null) {
        val l = lines.getOrNull(li)
        if (l != null) LineDialog(l, onDismiss = { editLine = null }, onSave = { nl -> lines[li] = nl; editLine = null })
    }
    if (pickCust) CustomerPicker(st, onDismiss = { pickCust = false }) { customer = it; pickCust = false }
    stockConfirm?.let { w -> Confirm("Low stock on this phone", "$w The PC may have newer stock. Save the bill anyway?", "Save anyway",
        onOk = { trySave(skipStock = true) }, onDismiss = { stockConfirm = null }) }
    if (approvalFor.isNotEmpty()) ApprovalDialog(ui.repo, approvalFor, onDone = { ap -> approvalFor = emptySet(); save(ap) },
        onDismiss = { approvalFor = emptySet() })
}

@Composable
fun LineDialog(l: DraftLine, onDismiss: () -> Unit, onSave: (DraftLine) -> Unit) {
    var qty by remember { mutableStateOf(l.qty.toString()) }
    var price by remember { mutableStateOf(l.price) }
    var disc by remember { mutableStateOf(l.disc) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(l.product.name) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (!l.product.imei) TextIn("Quantity", qty, { qty = it.filter { c -> c.isDigit() }.take(5) }, Modifier.fillMaxWidth(), number = true)
                MoneyField("Price (each)", price, { price = it }, Modifier.fillMaxWidth())
                MoneyField("Discount on this item", disc, { disc = it }, Modifier.fillMaxWidth())
            }
        },
        confirmButton = { TextButton(onClick = { onSave(l.copy(qty = qty.toIntOrNull() ?: 1, price = price, disc = disc)) }) { Text("OK", fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } })
}

@Composable
fun CustomerPicker(st: UiState, onDismiss: () -> Unit, onPick: (CustChoice) -> Unit) {
    var q by remember { mutableStateOf("") }
    var newName by remember { mutableStateOf("") }
    var newPhone by remember { mutableStateOf("") }
    val found: List<Customer> = if (q.isBlank()) st.customers.take(8) else st.customers.filter { it.name.contains(q, true) || it.phone.contains(q.trim()) }.take(20)
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Customer") },
        text = {
            Column(Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                TextButton(onClick = { onPick(CustChoice.WalkIn) }) { Text("Walk-in customer (paid in full)", color = Info) }
                SearchBox(q, { q = it }, "Search name or phone")
                found.forEach { c ->
                    Row(Modifier.fillMaxWidth().clickable { onPick(CustChoice.Existing(c)) }.padding(vertical = 8.dp)) {
                        Column(Modifier.weight(1f)) { Text(c.name, fontWeight = FontWeight.SemiBold); Text(c.phone, color = Muted, fontSize = 12.sp) }
                        if (c.due > 0) Text(Money.short(c.due), color = Warn, fontSize = 12.sp)
                    }
                }
                HorizontalDivider()
                Text("New customer", fontWeight = FontWeight.Bold)
                TextIn("Name", newName, { newName = it }, Modifier.fillMaxWidth())
                TextIn("Phone", newPhone, { newPhone = it.filter { c -> c.isDigit() || c == '+' }.take(15) }, Modifier.fillMaxWidth(), phone = true)
                SmallButton("Use new customer", {
                    val existing = st.customers.firstOrNull { it.phone == newPhone && newPhone.isNotBlank() }
                    onPick(if (existing != null) CustChoice.Existing(existing) else CustChoice.New(newName, newPhone))
                }, Modifier.fillMaxWidth(), enabled = newName.isNotBlank() && newPhone.count { it.isDigit() } >= 6)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } })
}

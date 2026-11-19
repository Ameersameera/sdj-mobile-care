package com.sdj.mobilecare.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ListItem
import androidx.compose.material3.ListItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sdj.mobilecare.data.Customer
import com.sdj.mobilecare.data.Dates
import com.sdj.mobilecare.data.Money
import com.sdj.mobilecare.data.UiState

@Composable
fun SyncSummary(st: UiState, onClick: () -> Unit) {
    val s = st.sync
    val (text, color) = when {
        s.running -> "Syncing with the PC…" to Info
        s.error.isNotEmpty() && st.pending.isNotEmpty() -> "${st.pending.size} entr${if (st.pending.size == 1) "y" else "ies"} waiting · PC not reachable" to Warn
        s.error.isNotEmpty() -> "PC not reachable · showing data from ${Dates.niceTime(s.lastOk).ifBlank { "before" }}" to Warn
        st.pending.isNotEmpty() -> "${st.pending.size} entr${if (st.pending.size == 1) "y" else "ies"} waiting to sync" to Warn
        s.lastOk.isNotEmpty() -> "Synced with PC · ${Dates.niceTime(s.lastOk)}" to Good
        else -> "Not synced yet" to Warn
    }
    Section(modifier = Modifier.clickable { onClick() }) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(if (s.error.isEmpty() && !s.running) "●" else "○", color)
            HGap()
            Text(text, color = color, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, modifier = Modifier.weight(1f))
        }
        if (st.failed.isNotEmpty()) Text("${st.failed.size} entr${if (st.failed.size == 1) "y was" else "ies were"} not accepted by the PC – tap to see.", color = Bad, fontSize = 13.sp)
        if (st.notices.isNotEmpty()) Text(st.notices.first(), color = Warn, fontSize = 13.sp)
    }
}

@Composable
fun HomeScreen(ui: Ui, st: UiState, nav: Nav) {
    val d = st.dash
    val u = st.user
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SyncSummary(st) { nav.go(Route.Sync) }
        if (d != null && u?.can("dashboard") == true) {
            val stale = d.date != st.businessDate
            Text(if (stale) "Figures for ${Dates.nice(d.date)} (from the last sync)" else "Today · ${Dates.nice(st.businessDate)}", color = Muted, fontSize = 13.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Kpi("Today's sales", Money.short(d.sales), Modifier.weight(1f)) { nav.tab(Route.Sales) }
                Kpi("Bills", d.bills.toString(), Modifier.weight(1f)) { nav.tab(Route.Sales) }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Kpi("Cash in drawer", Money.short(d.cash), Modifier.weight(1f), tone = Good)
                Kpi("Customer dues", Money.short(d.dues), Modifier.weight(1f), tone = if (d.dues > 0) Warn else Ink) { nav.go(Route.Customers) }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Kpi("Open repairs", "${d.openJobs}" + if (d.ready > 0) " · ${d.ready} ready" else "", Modifier.weight(1f)) { nav.tab(Route.Jobs) }
                Kpi("Low stock", d.low.toString(), Modifier.weight(1f), tone = if (d.low > 0) Bad else Ink) { nav.tab(Route.Stock) }
            }
        }
        Section("Quick actions") {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SmallButton("New bill", { nav.go(Route.BillEdit()) }, primary = true)
                SmallButton("Quick sale", { nav.go(Route.BillEdit(quick = true)) })
                SmallButton("New repair", { nav.go(Route.JobNew) })
                SmallButton("Add expense", { nav.go(Route.Expenses) })
                SmallButton("Collect dues", { nav.go(Route.Customers) })
            }
        }
        val recent = st.sales.take(5)
        if (recent.isNotEmpty()) Section("Latest bills", trailing = { TextButton(onClick = { nav.tab(Route.Sales) }) { Text("All", color = Info) } }) {
            recent.forEach { s -> SaleRow(s) { nav.go(Route.SaleView(s.key)) } }
        }
    }
}

@Composable
fun MoreScreen(ui: Ui, st: UiState, nav: Nav) {
    var confirmOut by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Section {
            listOf("Customers & dues" to Route.Customers, "Expenses" to Route.Expenses, "Sync with PC" to Route.Sync).forEach { (t, r) ->
                ListItem(headlineContent = { Text(t, fontWeight = FontWeight.SemiBold) }, modifier = Modifier.clickable { nav.go(r) },
                    colors = ListItemDefaults.colors(containerColor = Color.White))
                HorizontalDivider()
            }
        }
        Section("Signed in") {
            Text("${st.user?.name} (${st.user?.username}) · ${st.user?.role?.lowercase()}", color = Muted)
            Text("Shop: ${st.shop?.name ?: st.pcShopName}", color = Muted)
            OutlinedButton(onClick = { confirmOut = true }) { Text("Sign out") }
        }
    }
    if (confirmOut) Confirm("Sign out?", "Entries not synced yet stay safely on this phone.", "Sign out",
        onOk = { ui.run { ui.repo.signOut() } }, onDismiss = { confirmOut = false })
}

// ================================================================ stock
@Composable
fun StockScreen(st: UiState, nav: Nav) {
    var q by remember { mutableStateOf("") }
    var filter by remember { mutableStateOf("ALL") }
    val cats = st.categories.associateBy { it.id }
    val list = st.products.filter { p ->
        p.active && (q.isBlank() || p.name.contains(q, true) || p.code.contains(q, true) || p.barcode == q.trim() || p.brand.contains(q, true) || p.model.contains(q, true)) &&
            when (filter) {
                "LOW" -> p.low || p.qty <= 0
                "PHONES" -> p.imei
                "SPARE" -> cats[p.category]?.group == "SPARE"
                else -> true
            }
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            SearchBox(q, { q = it }, "Search name, code, barcode, model")
            Gap()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("ALL" to "All", "LOW" to "Low / out", "PHONES" to "Phones", "SPARE" to "Spares").forEach { (k, t) ->
                    FilterChip(selected = filter == k, onClick = { filter = k }, label = { Text(t) })
                }
            }
        }
        if (list.isEmpty()) item { Empty("No products") }
        items(list, key = { it.id }) { p ->
            Section(modifier = Modifier.clickable { nav.go(Route.ProductView(p.id)) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.name, fontWeight = FontWeight.SemiBold)
                        Text("${p.code} · ${cats[p.category]?.name ?: ""} · ${Money.fmt(p.price)}", color = Muted, fontSize = 12.sp)
                    }
                    Pill("${p.qty} in stock", when { p.qty <= 0 -> Bad; p.low -> Warn; else -> Good })
                }
            }
        }
    }
}

@Composable
fun ProductScreen(st: UiState, id: Long) {
    val p = st.products.firstOrNull { it.id == id } ?: return Empty("Product not found")
    val units = st.imeis.filter { it.product == id }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Section(p.name) {
            Row2("Code", p.code)
            if (p.brand.isNotBlank()) Row2("Brand / model", "${p.brand} ${p.model}".trim())
            if (p.barcode.isNotBlank()) Row2("Barcode", p.barcode)
            Row2("Selling price", Money.fmt(p.price))
            Row2("In stock", p.qty.toString(), bold = true, color = if (p.qty <= 0) Bad else if (p.low) Warn else Good)
            if (p.min > 0) Row2("Low-stock level", p.min.toString())
            if (p.warrantyMonths > 0) Row2("Warranty", "${p.warrantyMonths} month(s)")
        }
        if (p.imei) Section("IMEI units in stock (${units.size})") {
            if (units.isEmpty()) Text("None", color = Muted)
            units.forEach { Text(it.label, fontSize = 14.sp) }
        }
    }
}

// ================================================================ customers & dues
@Composable
fun CustomersScreen(ui: Ui, st: UiState) {
    var q by remember { mutableStateOf("") }
    var duesOnly by remember { mutableStateOf(true) }
    var paying by remember { mutableStateOf<Customer?>(null) }
    val list = st.customers.filter { c ->
        (!duesOnly || c.due > 0) && (q.isBlank() || c.name.contains(q, true) || c.phone.contains(q.trim()))
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            SearchBox(q, { q = it }, "Search name or phone")
            Gap()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = duesOnly, onClick = { duesOnly = true }, label = { Text("With dues") })
                FilterChip(selected = !duesOnly, onClick = { duesOnly = false }, label = { Text("All customers") })
            }
            Gap()
            Text("Total dues: ${Money.fmt(st.customers.sumOf { maxOf(it.due, 0L) })}", fontWeight = FontWeight.Bold)
        }
        if (list.isEmpty()) item { Empty(if (duesOnly) "No pending dues" else "No customers") }
        items(list, key = { it.id }) { c ->
            Section {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(c.name, fontWeight = FontWeight.SemiBold)
                            if (c.pending) { HGap(6); PendingPill() }
                        }
                        Text(c.phone, color = Muted, fontSize = 12.sp)
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Text(if (c.due > 0) Money.fmt(c.due) else if (c.due < 0) "Advance ${Money.fmt(-c.due)}" else "No dues",
                            color = if (c.due > 0) Warn else Muted, fontWeight = FontWeight.Bold)
                        if (c.due > 0) TextButton(onClick = { paying = c }) { Text("Collect", color = Info) }
                    }
                }
            }
        }
    }
    paying?.let { c -> PaymentDialog("Collect from ${c.name}", "Pending: ${Money.fmt(c.due)}", c.due, onDismiss = { paying = null }) { amt, mode ->
        ui.run("Payment saved", onDone = { paying = null }) { ui.repo.collectPayment(c, amt, mode, "") }
    } }
}

@Composable
fun PaymentDialog(title: String, hint: String, suggested: Long, onDismiss: () -> Unit, onSave: (Long, String) -> Unit) {
    var amount by remember { mutableStateOf(suggested) }
    var mode by remember { mutableStateOf("CASH") }
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(hint, color = Muted)
                MoneyField("Amount received", amount, { amount = it }, Modifier.fillMaxWidth())
                ModeChips(mode, { mode = it })
            }
        },
        confirmButton = { TextButton(onClick = { onSave(amount, mode) }) { Text("Save", fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } })
}

// ================================================================ expenses
@Composable
fun ExpensesScreen(ui: Ui, st: UiState) {
    val cats = st.shop?.expenseCategories.orEmpty().ifEmpty { listOf("Miscellaneous") }
    var cat by remember { mutableStateOf(cats.first()) }
    var amount by remember { mutableStateOf(0L) }
    var mode by remember { mutableStateOf("CASH") }
    var desc by remember { mutableStateOf("") }
    var open by remember { mutableStateOf(false) }
    var key by remember { mutableStateOf(0) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Section("Add expense · ${Dates.nice(st.businessDate)}") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Category", color = Muted, modifier = Modifier.weight(1f))
                    Column {
                        OutlinedButton(onClick = { open = true }) { Text(cat) }
                        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                            cats.forEach { c -> DropdownMenuItem(text = { Text(c) }, onClick = { cat = c; open = false }) }
                        }
                    }
                }
                androidx.compose.runtime.key(key) {
                    MoneyField("Amount", amount, { amount = it }, Modifier.fillMaxWidth())
                }
                TextIn("Description (optional)", desc, { desc = it }, Modifier.fillMaxWidth())
                ModeChips(mode, { mode = it })
                SmallButton("Save expense", {
                    ui.run("Expense saved", onDone = { amount = 0; desc = ""; key++ }) { ui.repo.addExpense(cat, amount, mode, desc) }
                }, Modifier.fillMaxWidth(), primary = true)
            }
        }
        item { Text("Recent expenses", fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp)) }
        if (st.expenses.isEmpty()) item { Empty("No expenses in the last weeks") }
        items(st.expenses.take(150)) { e ->
            Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(e.category, fontWeight = FontWeight.SemiBold)
                        if (e.pending) { HGap(6); PendingPill() }
                    }
                    Text("${Dates.nice(e.date)} · ${e.mode.lowercase()}" + if (e.desc.isNotBlank()) " · ${e.desc}" else "", color = Muted, fontSize = 12.sp)
                }
                Text(Money.fmt(e.amount), fontWeight = FontWeight.Bold)
            }
            HorizontalDivider()
        }
    }
}

// ================================================================ sync
@Composable
fun SyncScreen(ui: Ui, st: UiState) {
    var unlink by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SyncSummary(st) {}
        Section("This phone") {
            Row2("Shop PC", st.pcShopName)
            Row2("Signs in as", st.linkedName.ifBlank { st.linkedUsername })
            Row2("Bill series", st.shop?.invSeries ?: "")
            Row2("Last sync", Dates.niceTime(st.sync.lastOk).ifBlank { "never" })
            if (st.sync.host.isNotBlank()) Row2("PC address", st.sync.host)
            if (st.sync.error.isNotEmpty()) Text(st.sync.error, color = Warn, fontSize = 13.sp)
            SmallButton("Sync now", { ui.run { if (!ui.repo.syncOnce()) throw com.sdj.mobilecare.data.UserError(ui.repo.state.value.sync.error.ifBlank { "PC not reachable." }) } },
                Modifier.fillMaxWidth(), primary = true)
            Text("Sync works on the shop Wi-Fi while SDJ Mobile Care is open on the PC. No internet or mobile data is used.", color = Muted, fontSize = 12.sp)
        }
        Section("Waiting to sync (${st.pending.size})") {
            if (st.pending.isEmpty()) Text("Nothing waiting – all entries are on the PC.", color = Muted)
            st.pending.forEach { p -> Text("• ${p.title}", fontSize = 14.sp); Text("   ${Dates.niceTime(p.at)}", color = Muted, fontSize = 12.sp) }
        }
        if (st.failed.isNotEmpty()) Section("Not accepted by the PC (${st.failed.size})") {
            st.failed.forEach { f ->
                Text(f.title, fontWeight = FontWeight.SemiBold)
                Text(f.message, color = Bad, fontSize = 13.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = { ui.run { ui.repo.retryFailed(f.id) } }) { Text("Try again", color = Info) }
                    TextButton(onClick = { ui.run("Removed") { ui.repo.discardFailed(f.id) } }) { Text("Remove", color = Bad) }
                }
                HorizontalDivider()
            }
        }
        if (st.notices.isNotEmpty()) Section("Messages", trailing = { TextButton(onClick = { ui.run { ui.repo.clearNotices() } }) { Text("Clear", color = Info) } }) {
            st.notices.forEach { Text("• $it", fontSize = 13.sp) }
        }
        Section("Unlink") {
            Text("Use only if this phone will no longer be used for the shop. Entries not synced yet are lost.", color = Muted, fontSize = 13.sp)
            OutlinedButton(onClick = { unlink = true }) { Text("Unlink this phone", color = Bad) }
        }
    }
    if (unlink) Confirm("Unlink this phone?",
        if (st.pending.isNotEmpty()) "${st.pending.size} entries are not synced yet and will be lost. Sync first if you can." else "You will need a new code from the PC to link again.",
        "Unlink", onOk = { ui.run { ui.repo.unlink() } }, onDismiss = { unlink = false }, danger = true)
}

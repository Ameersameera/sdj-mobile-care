package com.sdj.mobilecare.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sdj.mobilecare.data.Dates
import com.sdj.mobilecare.data.JOB_STATUS_LABELS
import com.sdj.mobilecare.data.Job
import com.sdj.mobilecare.data.MODE_LABELS
import com.sdj.mobilecare.data.Money
import com.sdj.mobilecare.data.OPEN_STATUSES
import com.sdj.mobilecare.data.Product
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.UiState
import java.time.LocalDate

fun findJob(st: UiState, key: String): Job? {
    st.jobs.firstOrNull { it.key == key }?.let { return it }
    if (key.startsWith("ref:")) return st.jobs.firstOrNull { it.ref == key.removePrefix("ref:") }
    return null
}

fun statusColor(s: String) = when (s) {
    "READY" -> Info
    "DELIVERED" -> Good
    "CANCELLED" -> Bad
    "WAITING_PARTS" -> Warn
    else -> Muted
}

@Composable
fun JobsScreen(st: UiState, nav: Nav) {
    var q by remember { mutableStateOf("") }
    var filter by remember { mutableStateOf("OPEN") }
    val list = st.jobs.filter { j ->
        (q.isBlank() || j.no.contains(q, true) || j.customerName.contains(q, true) || j.customerPhone.contains(q.trim()) ||
            j.imei.contains(q.trim()) || j.device.contains(q, true)) &&
            when (filter) {
                "OPEN" -> j.open
                "READY" -> j.status == "READY"
                "DONE" -> j.status == "DELIVERED"
                else -> true
            }
    }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 14.dp, end = 14.dp, top = 14.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            SearchBox(q, { q = it }, "Search job no, customer, phone, IMEI, model")
            Gap()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("OPEN" to "Open", "READY" to "Ready", "DONE" to "Delivered", "ALL" to "All").forEach { (k, t) ->
                    FilterChip(selected = filter == k, onClick = { filter = k }, label = { Text(t) })
                }
            }
        }
        if (list.isEmpty()) item { Empty("No repair jobs") }
        items(list, key = { it.key }) { j ->
            Section(modifier = Modifier.clickable { nav.go(Route.JobView(j.key)) }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(j.no, fontWeight = FontWeight.SemiBold)
                            if (j.pending) { HGap(6); PendingPill() }
                        }
                        Text("${j.customerName} · ${j.device}", fontSize = 13.sp, maxLines = 1)
                        Text(j.problem, color = Muted, fontSize = 12.sp, maxLines = 1)
                    }
                    Column(horizontalAlignment = Alignment.End) {
                        Pill(JOB_STATUS_LABELS[j.status] ?: j.status, statusColor(j.status))
                        if (j.balance > 0 && j.status in setOf("READY", "DELIVERED")) Text("Due ${Money.short(j.balance)}", color = Warn, fontSize = 12.sp)
                    }
                }
            }
        }
    }
}

@Composable
fun JobScreen(ui: Ui, st: UiState, nav: Nav, key: String) {
    val j = findJob(st, key) ?: return Empty("This job is not on the phone any more.")
    var statusMenu by remember { mutableStateOf(false) }
    var paying by remember { mutableStateOf(false) }
    var delivering by remember { mutableStateOf(false) }
    var addPart by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf(false) }
    var cancelling by remember { mutableStateOf(false) }
    val changeable = j.status !in setOf("DELIVERED", "CANCELLED")
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Section(j.no, trailing = { Pill(JOB_STATUS_LABELS[j.status] ?: j.status, statusColor(j.status)) }) {
            if (j.pending) PendingPill()
            Row2("Customer", j.customerName)
            Row2("Phone", j.customerPhone)
            Row2("Device", j.device)
            if (j.imei.isNotBlank()) Row2("IMEI", j.imei)
            if (j.accessories.isNotBlank()) Row2("With", j.accessories)
            Row2("Received", Dates.nice(j.date))
            j.est?.let { Row2("Promised", Dates.nice(it)) }
            if (j.technician.isNotBlank()) Row2("Technician", j.technician)
            Text("Problem: ${j.problem}", fontSize = 14.sp)
            if (j.diagnosis.isNotBlank()) Text("Diagnosis: ${j.diagnosis}", fontSize = 14.sp, color = Muted)
            if (j.notes.isNotBlank()) Text("Notes: ${j.notes}", fontSize = 13.sp, color = Muted)
        }
        Section("Charges") {
            Row2("Labour", Money.fmt(j.labour))
            j.lines.forEach { Row2("${it.name} × ${it.qty}", Money.fmt(it.amount)) }
            if (j.discount > 0) Row2("Discount", "-" + Money.fmt(j.discount))
            Row2("Total", Money.fmt(j.total), bold = true)
            j.pays.forEach { Row2("${if (it.dir == "OUT") "Refund" else "Paid"} · ${MODE_LABELS[it.mode] ?: it.mode} · ${Dates.nice(it.date)}", Money.fmt(it.amount)) }
            if (j.status != "CANCELLED") Row2(if (j.balance >= 0) "Balance" else "Advance to refund", Money.fmt(kotlin.math.abs(j.balance)), bold = true,
                color = if (j.balance > 0) Warn else Good)
        }
        if (changeable) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Column {
                    SmallButton("Change status", { statusMenu = true })
                    DropdownMenu(expanded = statusMenu, onDismissRequest = { statusMenu = false }) {
                        listOf("RECEIVED", "INSPECTION", "WAITING_PARTS", "IN_PROGRESS", "READY").filter { it != j.status }.forEach { s ->
                            DropdownMenuItem(text = { Text(JOB_STATUS_LABELS[s] ?: s) }, onClick = {
                                statusMenu = false
                                ui.run("Status changed") { ui.repo.jobStatus(j, s) }
                            })
                        }
                    }
                }
                SmallButton("Deliver", { delivering = true }, primary = true)
                SmallButton("Add payment", { paying = true })
                if (j.status in OPEN_STATUSES) SmallButton("Add spare part", { addPart = true })
                SmallButton("Edit", { editing = true })
                SmallButton("Cancel job", { cancelling = true })
            }
        }
    }
    if (paying) PaymentDialog("Payment for ${j.no}", if (j.balance > 0) "Balance: ${Money.fmt(j.balance)}" else "Advance", maxOf(j.balance, 0),
        onDismiss = { paying = false }) { amt, mode -> ui.run("Payment saved", onDone = { paying = false }) { ui.repo.jobPayment(j, amt, mode) } }
    if (delivering) PaymentDialog("Deliver ${j.no}", "Collect the balance now (0 if nothing is due).", maxOf(j.balance, 0),
        onDismiss = { delivering = false }) { amt, mode -> ui.run("Marked delivered", onDone = { delivering = false }) { ui.repo.jobDeliver(j, amt, mode) } }
    if (addPart) PartDialog(st, onDismiss = { addPart = false }) { p, qty, price ->
        ui.run("Spare part added", onDone = { addPart = false }) { ui.repo.jobPart(j, p, qty, price) }
    }
    if (editing) JobEditDialog(j, st, onDismiss = { editing = false }) { diag, tech, labour, disc, notes ->
        ui.run("Job updated", onDone = { editing = false }) { ui.repo.jobEdit(j, diag, tech, labour, disc, notes) }
    }
    if (cancelling) {
        var reason by remember { mutableStateOf("") }
        AlertDialog(onDismissRequest = { cancelling = false }, title = { Text("Cancel ${j.no}?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Spare parts go back to stock" + if (j.paid > 0) "; the advance ${Money.fmt(j.paid)} is refunded in cash." else ".", color = Muted)
                    TextIn("Reason", reason, { reason = it }, Modifier.fillMaxWidth())
                }
            },
            confirmButton = { TextButton(onClick = { ui.run("Job cancelled", onDone = { cancelling = false }) { ui.repo.jobStatus(j, "CANCELLED", reason) } }) { Text("Cancel job", color = Bad) } },
            dismissButton = { TextButton(onClick = { cancelling = false }) { Text("Keep", color = Muted) } })
    }
}

@Composable
fun PartDialog(st: UiState, onDismiss: () -> Unit, onSave: (Product, Int, Long) -> Unit) {
    var q by remember { mutableStateOf("") }
    var picked by remember { mutableStateOf<Product?>(null) }
    var qty by remember { mutableStateOf("1") }
    var price by remember { mutableStateOf(0L) }
    val list = st.products.filter { it.active && !it.imei && (q.isBlank() || it.name.contains(q, true) || it.code.contains(q, true)) }.take(20)
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Spare part used") },
        text = {
            Column(Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val p = picked
                if (p == null) {
                    SearchBox(q, { q = it }, "Search spare part")
                    list.forEach { item ->
                        Row(Modifier.fillMaxWidth().clickable { picked = item; price = item.price }.padding(vertical = 8.dp)) {
                            Text(item.name, modifier = Modifier.weight(1f))
                            Text("${item.qty} · ${Money.short(item.price)}", color = Muted, fontSize = 12.sp)
                        }
                    }
                } else {
                    Text(p.name, fontWeight = FontWeight.Bold)
                    TextIn("Quantity", qty, { qty = it.filter { c -> c.isDigit() }.take(4) }, Modifier.fillMaxWidth(), number = true)
                    MoneyField("Price charged (each)", price, { price = it }, Modifier.fillMaxWidth())
                    TextButton(onClick = { picked = null }) { Text("Choose another part", color = Info) }
                }
            }
        },
        confirmButton = { TextButton(enabled = picked != null, onClick = { picked?.let { onSave(it, qty.toIntOrNull() ?: 1, price) } }) { Text("Add", fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } })
}

@Composable
fun JobEditDialog(j: Job, st: UiState, onDismiss: () -> Unit, onSave: (String, String, Long, Long, String) -> Unit) {
    var diag by remember { mutableStateOf(j.diagnosis) }
    var tech by remember { mutableStateOf(j.technician) }
    var labour by remember { mutableStateOf(j.labour) }
    var disc by remember { mutableStateOf(j.discount) }
    var notes by remember { mutableStateOf(j.notes) }
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Edit ${j.no}") },
        text = {
            Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                TextIn("Diagnosis", diag, { diag = it }, Modifier.fillMaxWidth(), singleLine = false)
                TextIn("Technician", tech, { tech = it }, Modifier.fillMaxWidth())
                val techs = st.shop?.technicians.orEmpty()
                if (techs.isNotEmpty()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    techs.forEach { t -> FilterChip(selected = tech == t, onClick = { tech = t }, label = { Text(t) }) }
                }
                MoneyField("Labour charge", labour, { labour = it }, Modifier.fillMaxWidth(), enabled = j.status in OPEN_STATUSES)
                MoneyField("Discount", disc, { disc = it }, Modifier.fillMaxWidth(), enabled = j.status in OPEN_STATUSES)
                TextIn("Notes", notes, { notes = it }, Modifier.fillMaxWidth(), singleLine = false)
            }
        },
        confirmButton = { TextButton(onClick = { onSave(diag, tech, labour, disc, notes) }) { Text("Save", fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = Muted) } })
}

@Composable
fun JobForm(ui: Ui, st: UiState, nav: Nav) {
    var name by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var brand by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var imei by remember { mutableStateOf("") }
    var acc by remember { mutableStateOf("") }
    var problem by remember { mutableStateOf("") }
    var tech by remember { mutableStateOf("") }
    var labour by remember { mutableStateOf(0L) }
    var days by remember { mutableStateOf(-1) }
    var adv by remember { mutableStateOf(0L) }
    var advMode by remember { mutableStateOf("CASH") }
    var notes by remember { mutableStateOf("") }
    val scan = rememberScanner("Scan the IMEI barcode") { imei = it.trim() }
    val known = st.customers.firstOrNull { phone.length >= 6 && it.phone == phone }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Section("Customer") {
            TextIn("Phone number", phone, { phone = it.filter { c -> c.isDigit() || c == '+' }.take(15) }, Modifier.fillMaxWidth(), phone = true)
            if (known != null && name.isBlank()) TextButton(onClick = { name = known.name }) { Text("Use ${known.name}", color = Info) }
            TextIn("Name", name, { name = it }, Modifier.fillMaxWidth())
        }
        Section("Device") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextIn("Brand", brand, { brand = it }, Modifier.weight(1f))
                TextIn("Model", model, { model = it }, Modifier.weight(1f))
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextIn("IMEI (optional)", imei, { imei = it }, Modifier.weight(1f), number = true)
                HGap()
                OutlinedButton(onClick = scan) { Text("Scan") }
            }
            TextIn("Received with (cover, SIM, charger…)", acc, { acc = it }, Modifier.fillMaxWidth())
            TextIn("Problem reported", problem, { problem = it }, Modifier.fillMaxWidth(), singleLine = false)
        }
        Section("Estimate") {
            MoneyField("Labour charge (estimate)", labour, { labour = it }, Modifier.fillMaxWidth())
            Text("Promised delivery", color = Muted, fontSize = 13.sp)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf(-1 to "Not set", 0 to "Today", 1 to "Tomorrow", 2 to "2 days", 3 to "3 days", 7 to "1 week").forEach { (n, t) ->
                    FilterChip(selected = days == n, onClick = { days = n }, label = { Text(t) })
                }
            }
            val techs = st.shop?.technicians.orEmpty()
            if (techs.isNotEmpty()) {
                Text("Technician", color = Muted, fontSize = 13.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    techs.forEach { t -> FilterChip(selected = tech == t, onClick = { tech = if (tech == t) "" else t }, label = { Text(t) }) }
                }
            }
            MoneyField("Advance received", adv, { adv = it }, Modifier.fillMaxWidth())
            if (adv > 0) ModeChips(advMode, { advMode = it })
            TextIn("Notes (optional)", notes, { notes = it }, Modifier.fillMaxWidth())
        }
        SmallButton("Save repair job", {
            val base = runCatching { LocalDate.parse(st.businessDate) }.getOrDefault(LocalDate.now())
            val est = if (days >= 0) base.plusDays(days.toLong()).toString() else null
            ui.run("Repair job saved", onDone = { nav.back() }) {
                ui.repo.newJob(Repo.JobForm(name, phone, brand, model, imei, acc, problem, tech, labour, est, adv, advMode, notes))
            }
        }, Modifier.fillMaxWidth().heightIn(min = 52.dp), primary = true)
        HorizontalDivider()
        Text("The job gets a phone number series (${st.shop?.jobSeries ?: "JOB-P"}-…) so numbers never clash with the PC.", color = Muted, fontSize = 12.sp)
    }
}

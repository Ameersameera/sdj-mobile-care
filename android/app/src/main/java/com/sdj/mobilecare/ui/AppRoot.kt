package com.sdj.mobilecare.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.sdj.mobilecare.R
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.UiState
import com.sdj.mobilecare.data.LinkError
import com.sdj.mobilecare.data.UserError
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/** Runs an action, shows its error (or success) as a snackbar. */
class Ui(val repo: Repo, val scope: CoroutineScope, val snack: SnackbarHostState) {
    fun toast(msg: String) {
        scope.launch { snack.showSnackbar(msg) }
    }

    fun run(success: String? = null, onDone: () -> Unit = {}, block: suspend () -> Unit) {
        scope.launch {
            try {
                block()
                onDone()
                if (success != null) toast(success)
            } catch (e: UserError) {
                toast(e.message ?: "Could not save.")
            } catch (e: Exception) {
                toast("Something went wrong: ${e.message}")
            }
        }
    }
}

sealed class Route {
    data object Home : Route()
    data object Sales : Route()
    data object Stock : Route()
    data object Jobs : Route()
    data object More : Route()
    data class SaleView(val key: String) : Route()
    data class BillEdit(val quick: Boolean = false, val editKey: String? = null) : Route()
    data class JobView(val key: String) : Route()
    data object JobNew : Route()
    data class ProductView(val id: Long) : Route()
    data object Customers : Route()
    data object Expenses : Route()
    data object Sync : Route()
}

class Nav(private val stack: SnapshotStateList<Route>) {
    val top: Route get() = stack.last()
    val depth: Int get() = stack.size
    fun go(r: Route) { stack.add(r) }
    fun back() { if (stack.size > 1) stack.removeAt(stack.lastIndex) }
    fun tab(r: Route) { stack.clear(); stack.add(r) }
    fun replace(r: Route) { stack.removeAt(stack.lastIndex); stack.add(r) }
}

val TABS = listOf(Route.Home, Route.Sales, Route.Stock, Route.Jobs, Route.More)

/** Opens the camera to read a barcode, IMEI or the PC's QR code. */
@Composable
fun rememberScanner(prompt: String, onResult: (String) -> Unit): () -> Unit {
    val launcher = rememberLauncherForActivityResult(ScanContract()) { r -> r.contents?.let(onResult) }
    return {
        launcher.launch(ScanOptions().setPrompt(prompt).setBeepEnabled(true).setOrientationLocked(false))
    }
}

@Composable
fun AppRoot(repo: Repo, phoneName: String, startAt: Route = Route.Home) {
    val st by repo.state.collectAsState()
    val scope = rememberCoroutineScope()
    val snack = remember { SnackbarHostState() }
    val ui = remember { Ui(repo, scope, snack) }
    when {
        !st.ready -> Splash()
        !st.linked || st.sync.removed -> LinkScreen(ui, st, phoneName)
        st.user == null -> LoginScreen(ui, st)
        else -> MainShell(ui, st, startAt)
    }
}

@Composable
fun Splash() {
    Box(Modifier.fillMaxSize().background(Ink), contentAlignment = Alignment.Center) {
        Image(painterResource(R.mipmap.ic_launcher), contentDescription = null, modifier = Modifier.size(96.dp))
    }
}

@Composable
fun Brand(subtitle: String) {
    Column(Modifier.fillMaxWidth().background(Ink).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Image(painterResource(R.mipmap.ic_launcher), contentDescription = null, modifier = Modifier.size(72.dp))
        Gap(10)
        Text("SDJ Mobile Care", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 22.sp)
        Text(subtitle, color = Gold, fontSize = 13.sp, textAlign = TextAlign.Center)
    }
}

// ================================================================ link this phone
@Composable
fun LinkScreen(ui: Ui, st: UiState, phoneName: String) {
    var code by remember { mutableStateOf("") }
    var host by remember { mutableStateOf("") }
    var showHost by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf(if (st.sync.removed) "This phone was removed on the PC. Ask the owner for a new code." else "") }
    var testUrl by remember { mutableStateOf("") }
    val ctx = LocalContext.current
    fun go(c: String, qr: Repo.QrLink?) {
        busy = true
        err = ""
        testUrl = ""
        ui.scope.launch {
            try {
                ui.repo.link(c, qr, host, phoneName)
            } catch (e: LinkError) {
                err = e.message ?: ""
                testUrl = e.host?.let { "http://$it:${e.port}" } ?: ""
            } catch (e: UserError) {
                err = e.message ?: ""
            } catch (e: Exception) {
                err = "Could not link: ${e.message}"
            } finally {
                busy = false
            }
        }
    }
    val scan = rememberScanner("Point the camera at the QR code in SDJ Mobile Care on the PC (Settings → Phone link)") { text ->
        val qr = ui.repo.parseQr(text)
        if (qr == null) err = "That is not an SDJ link code." else { code = qr.code; go(qr.code, qr) }
    }
    Column(Modifier.fillMaxSize().background(Cream).verticalScroll(rememberScrollState())) {
        Brand("Phone app · works with the shop PC over Wi-Fi")
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Section("Link this phone to the shop PC") {
                Text("1. On the PC open Settings → Phone link and tap 'Create one-time code'.\n" +
                    "2. Keep this phone on the shop's Wi-Fi.\n3. Scan the QR code, or type the code.", color = Muted, fontSize = 14.sp)
                Button(onClick = scan, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("Scan QR from PC", fontWeight = FontWeight.Bold) }
                Text("or type the code", color = Muted, fontSize = 12.sp, modifier = Modifier.align(Alignment.CenterHorizontally))
                TextIn("One-time code (e.g. QT26S-DYCV2)", code, { code = it.uppercase().take(11) }, Modifier.fillMaxWidth())
                if (showHost) TextIn("PC address (only if asked, e.g. 192.168.1.5)", host, { host = it.trim() }, Modifier.fillMaxWidth())
                else TextButton(onClick = { showHost = true }) { Text("PC not found? Type its address", color = Info) }
                OutlinedButton(onClick = { go(code, null) }, enabled = !busy && code.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
                    if (busy) Busy() else Text("Link with code")
                }
                if (err.isNotEmpty()) Text(err, color = Bad, fontSize = 14.sp)
                if (testUrl.isNotEmpty()) {
                    Text("Test: this opens $testUrl in the browser. If it says 'SDJ Mobile Care phone sync is working', " +
                        "the network is fine – link again. If it does not open, allow SDJ in the PC's firewall.", color = Muted, fontSize = 12.sp)
                    OutlinedButton(onClick = {
                        runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(testUrl))) }
                    }, modifier = Modifier.fillMaxWidth()) { Text("Test the connection in the browser") }
                }
            }
            Text("No cloud and no internet are used. The phone keeps its own copy, so you can make bills even when the " +
                "PC is off; everything syncs when the phone is back on the shop Wi-Fi.", color = Muted, fontSize = 12.sp)
        }
    }
}

// ================================================================ sign in
@Composable
fun LoginScreen(ui: Ui, st: UiState) {
    var pw by remember { mutableStateOf("") }
    var stay by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().background(Cream).verticalScroll(rememberScrollState())) {
        Brand(st.shop?.name ?: st.pcShopName)
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Section("Sign in") {
                Text("This phone signs in as ${st.linkedName.ifBlank { st.linkedUsername }} (${st.linkedUsername}) – same password as on the PC.", color = Muted, fontSize = 14.sp)
                TextIn("Password", pw, { pw = it }, Modifier.fillMaxWidth(), password = true)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = stay, onCheckedChange = { stay = it })
                    Text("Stay signed in on this phone", fontSize = 14.sp)
                }
                Button(enabled = !busy && pw.isNotEmpty(), modifier = Modifier.fillMaxWidth(), onClick = {
                    busy = true
                    err = ""
                    ui.scope.launch {
                        try { ui.repo.signIn(pw, stay) } catch (e: UserError) { err = e.message ?: "" } finally { busy = false }
                    }
                }) { if (busy) Busy() else Text("Sign in", fontWeight = FontWeight.Bold) }
                if (err.isNotEmpty()) Text(err, color = Bad, fontSize = 14.sp)
                if (!st.hasData) {
                    Text("The shop data has not been downloaded yet.", color = Warn, fontSize = 13.sp)
                    OutlinedButton(onClick = { ui.run("Synced") { if (!ui.repo.syncOnce(full = true)) throw UserError(ui.repo.state.value.sync.error.ifBlank { "PC not reachable." }) } }) { Text("Sync now") }
                }
            }
        }
    }
}

// ================================================================ main screens
@Composable
fun MainShell(ui: Ui, st: UiState, startAt: Route) {
    val stack = remember { mutableStateListOf(startAt) }
    val nav = remember { Nav(stack) }
    val top = nav.top
    BackHandler(enabled = nav.depth > 1) { nav.back() }
    val isTab = top in TABS
    Scaffold(
        containerColor = Cream,
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(titleOf(top, st), fontWeight = FontWeight.Bold, fontSize = 18.sp, maxLines = 1)
                        if (isTab) Text(st.user?.name ?: "", fontSize = 12.sp, color = Gold, maxLines = 1)
                    }
                },
                navigationIcon = {
                    if (!isTab) IconButton(onClick = { nav.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                },
                actions = { SyncButton(ui, st) },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Ink, titleContentColor = Color.White,
                    navigationIconContentColor = Color.White, actionIconContentColor = Color.White),
            )
        },
        bottomBar = {
            if (isTab) NavigationBar(containerColor = Color.White) {
                val items = listOf(Triple(Route.Home, "Home", Icons.Default.Home), Triple(Route.Sales, "Bills", Icons.AutoMirrored.Filled.List),
                    Triple(Route.Stock, "Stock", Icons.Default.ShoppingCart), Triple(Route.Jobs, "Repairs", Icons.Default.Build),
                    Triple(Route.More, "More", Icons.Default.Menu))
                items.forEach { (r, label, icon) ->
                    NavigationBarItem(selected = top == r, onClick = { nav.tab(r) }, icon = { Icon(icon, contentDescription = label) }, label = { Text(label) })
                }
            }
        },
        floatingActionButton = {
            when (top) {
                Route.Sales -> ExtendedFloatingActionButton(onClick = { nav.go(Route.BillEdit()) }, containerColor = Gold, contentColor = Ink,
                    icon = { Icon(Icons.Default.Add, contentDescription = null) }, text = { Text("New bill", fontWeight = FontWeight.Bold) })
                Route.Jobs -> ExtendedFloatingActionButton(onClick = { nav.go(Route.JobNew) }, containerColor = Gold, contentColor = Ink,
                    icon = { Icon(Icons.Default.Add, contentDescription = null) }, text = { Text("New repair", fontWeight = FontWeight.Bold) })
                else -> Unit
            }
        },
        snackbarHost = { SnackbarHost(ui.snack) },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (top) {
                Route.Home -> HomeScreen(ui, st, nav)
                Route.Sales -> SalesScreen(ui, st, nav)
                Route.Stock -> StockScreen(st, nav)
                Route.Jobs -> JobsScreen(st, nav)
                Route.More -> MoreScreen(ui, st, nav)
                is Route.SaleView -> SaleScreen(ui, st, nav, top.key)
                is Route.BillEdit -> BillEditor(ui, st, nav, top.quick, top.editKey)
                is Route.JobView -> JobScreen(ui, st, nav, top.key)
                Route.JobNew -> JobForm(ui, st, nav)
                is Route.ProductView -> ProductScreen(st, top.id)
                Route.Customers -> CustomersScreen(ui, st)
                Route.Expenses -> ExpensesScreen(ui, st)
                Route.Sync -> SyncScreen(ui, st)
            }
        }
    }
}

fun titleOf(r: Route, st: UiState): String = when (r) {
    Route.Home -> st.shop?.name ?: "SDJ Mobile Care"
    Route.Sales -> "Bills"
    Route.Stock -> "Stock"
    Route.Jobs -> "Repair jobs"
    Route.More -> "More"
    is Route.SaleView -> "Bill"
    is Route.BillEdit -> if (r.editKey != null) "Edit bill" else if (r.quick) "Quick sale" else "New bill"
    is Route.JobView -> "Repair job"
    Route.JobNew -> "New repair job"
    is Route.ProductView -> "Product"
    Route.Customers -> "Customers & dues"
    Route.Expenses -> "Expenses"
    Route.Sync -> "Sync with PC"
}

@Composable
fun SyncButton(ui: Ui, st: UiState) {
    val color = when {
        st.sync.running -> Gold
        st.sync.error.isNotEmpty() -> Color(0xFFF0705F)
        st.pending.isNotEmpty() -> Color(0xFFE8A33A)
        else -> Color(0xFF4CBF65)
    }
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(end = 4.dp)) {
        if (st.pending.isNotEmpty()) Text("${st.pending.size}", color = Color(0xFFE8A33A), fontWeight = FontWeight.Bold, fontSize = 13.sp)
        IconButton(onClick = { ui.run { ui.repo.syncOnce() } }) {
            Box(contentAlignment = Alignment.TopEnd) {
                if (st.sync.running) Busy() else Icon(Icons.Default.Refresh, contentDescription = "Sync now")
                Box(Modifier.size(8.dp).background(color, CircleShape))
            }
        }
    }
}

@Composable
fun SmallButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, primary: Boolean = false, enabled: Boolean = true) {
    if (primary) Button(onClick = onClick, modifier = modifier, enabled = enabled) { Text(text, fontWeight = FontWeight.Bold) }
    else OutlinedButton(onClick = onClick, modifier = modifier, enabled = enabled, colors = ButtonDefaults.outlinedButtonColors(contentColor = Ink)) { Text(text) }
}

package com.sdj.mobilecare.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.sdj.mobilecare.data.BackupItem
import com.sdj.mobilecare.data.BackupState
import com.sdj.mobilecare.data.Dates
import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.UiState
import com.sdj.mobilecare.data.UserError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

fun sizeText(n: Long): String = if (n >= 1024 * 1024) "%.1f MB".format(n / 1024.0 / 1024.0) else "${maxOf(n / 1024, 1)} KB"

fun backupTitle(b: BackupItem): String {
    val day = Dates.nice(b.date.ifBlank { b.created.ifBlank { b.savedAt } })
    return when {
        b.isReport -> "Closing report · $day"
        b.id < 0 -> "Backup file · ${b.name}"
        else -> "Backup · " + (if (b.created.isNotBlank()) Dates.niceTime(b.created) else day)
    }
}

private fun displayName(ctx: Context, uri: Uri): String? = runCatching {
    ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }
}.getOrNull()

private fun contentUri(ctx: Context, b: BackupItem): Uri {
    val u = Uri.parse(b.uri)
    val path = u.path
    return if (u.scheme == "file" && path != null) FileProvider.getUriForFile(ctx, ctx.packageName + ".files", File(path)) else u
}

private fun shareBackup(ctx: Context, b: BackupItem) {
    val send = Intent(Intent.ACTION_SEND).apply {
        type = if (b.isReport) "application/pdf" else "application/octet-stream"
        putExtra(Intent.EXTRA_STREAM, contentUri(ctx, b))
        putExtra(Intent.EXTRA_TEXT, backupTitle(b))
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    runCatching { ctx.startActivity(Intent.createChooser(send, "Save or share the backup").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
}

private fun openReport(ctx: Context, b: BackupItem) {
    val view = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(contentUri(ctx, b), "application/pdf")
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    runCatching { ctx.startActivity(view) }
}

/** Short line for the home screen. */
fun backupLine(bs: BackupState): Pair<String, Boolean> {
    val last = bs.latestBackup ?: return "No backup on this phone yet – it comes after the PC's daily closing." to false
    val drive = if (bs.driveUri.isNotEmpty() && bs.driveLast.isNotEmpty()) " · Google Drive ✓" else ""
    return "Last backup on this phone: ${Dates.niceTime(last.created.ifBlank { last.savedAt })}$drive" to true
}

/** Backups kept on this phone; send one to a new PC; Google Drive copy; emergency rebuild of a new PC. */
@Composable
fun BackupsScreen(ui: Ui, st: UiState, standalone: Boolean = false) {
    val bs by ui.repo.backups.state.collectAsState()
    val ctx = LocalContext.current
    var target by remember { mutableStateOf<BackupItem?>(null) }       // backup to send
    var action by remember { mutableStateOf("") }                      // "send" / "rebuild": waiting for the PC's code
    var busy by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<Pair<String, String>?>(null) }
    var typing by remember { mutableStateOf(false) }
    var host by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var askRebuild by remember { mutableStateOf(false) }

    fun go(qr: Repo.RestoreQr) {
        val what = action
        val item = target
        busy = if (what == "rebuild") "Sending this phone's shop copy to the PC…" else "Sending the backup to the PC…"
        ui.scope.launch {
            try {
                if (what == "rebuild") {
                    val s = ui.repo.rebuildPc(qr, ui.phoneName)
                    result = "Shop copy sent" to ("On the PC: enter the owner's password and tap 'Rebuild this PC from the " +
                        "phone'.\n\n${s.optInt("products")} products · ${s.optInt("customers")} customers · " +
                        "${s.optInt("open_jobs")} open repair jobs")
                } else if (item != null) {
                    ui.repo.sendToPc(item, qr, ui.phoneName)
                    result = "Backup sent" to "On the PC: enter the owner's password (or the recovery code) and tap " +
                        "'Restore this backup'."
                }
            } catch (e: UserError) {
                result = "Not sent" to (e.message ?: "Could not send.")
            } catch (e: Exception) {
                result = "Not sent" to "Something went wrong: ${e.message}"
            } finally {
                busy = ""
                action = ""
                target = null
            }
        }
    }
    val scan = rememberScanner("Point the camera at the QR code on the PC (Restore from phone)") { text ->
        val qr = ui.repo.parseRestoreQr(text)
        if (qr == null) {
            result = "Wrong QR code" to "That is not the PC's restore code. On the new PC choose 'Restore from phone' and scan " +
                "the QR shown there."
            action = ""
        } else go(qr)
    }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
        if (uri != null) ui.run("Backup file added") {
            val name = displayName(ctx, uri) ?: "SDJ_backup.sdjb"
            ui.repo.importBackup(name) { ctx.contentResolver.openInputStream(uri) }
        }
    }
    val pickDrive = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri: Uri? ->
        if (uri != null) {
            runCatching {
                ctx.contentResolver.takePersistableUriPermission(uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
            }
            ui.run("Google Drive file chosen") {
                withContext(Dispatchers.IO) {
                    ui.repo.backups.setDrive(uri.toString())
                    ui.repo.backups.copyToDrive(true)
                }
            }
        }
    }
    val askStorage = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { ui.repo.backups.refresh() }
    val owner = st.user?.role == "ADMIN"

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (standalone) Section("Restore a new PC") {
            Text("1. Tap 'Add a backup file' below and pick the backup (.sdjb) – from Google Drive or Downloads → SDJ Backups.\n" +
                "2. On the new PC choose 'Restore from phone' – it shows a QR code.\n" +
                "3. Tap 'Send to PC' on the backup and scan that QR. The PC asks for the owner's password (or the " +
                "recovery code).", fontSize = 14.sp)
        }
        Section("Backups on this phone") {
            Text("After every daily closing the PC sends a locked backup and the closing report to this phone over the " +
                "shop Wi-Fi. They are saved in ${bs.place} and stay even if this app is removed.", color = Muted, fontSize = 13.sp)
            if (!bs.ready) {
                Text("Allow storage access so the backups can be saved in Downloads.", color = Warn, fontSize = 13.sp)
                Button(onClick = { askStorage.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE) }) { Text("Allow storage access") }
            }
            Row2("Last saved", Dates.niceTime(bs.lastSaved).ifBlank { "not yet" })
            if (bs.error.isNotEmpty()) Text(bs.error, color = Warn, fontSize = 13.sp)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Keep every backup (otherwise the last 30 days + one per month)", fontSize = 14.sp, modifier = Modifier.weight(1f))
                Switch(checked = bs.keepAll, onCheckedChange = { on -> ui.run { withContext(Dispatchers.IO) { ui.repo.backups.setKeepAll(on) } } })
            }
        }
        Section("Google Drive (optional)") {
            if (bs.driveUri.isEmpty()) {
                Text("Choose a file in your Google Drive once – every new backup is then copied there too (Google Drive " +
                    "keeps older versions for 30 days). It helps if this phone is lost as well.", color = Muted, fontSize = 13.sp)
                Button(onClick = { pickDrive.launch("SDJ_Backup_latest.sdjb") }, modifier = Modifier.fillMaxWidth()) {
                    Text("Choose Google Drive file", fontWeight = FontWeight.Bold)
                }
                Text("In the picker tap ☰ (menu) → Google Drive → a folder → Save.", color = Muted, fontSize = 12.sp)
            } else {
                Row2("Drive file", bs.driveName)
                Row2("Last copied", Dates.niceTime(bs.driveLast).ifBlank { "not yet" })
                if (bs.driveError.isNotEmpty()) Text(bs.driveError, color = Warn, fontSize = 13.sp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Copy only on Wi-Fi", fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Switch(checked = bs.driveWifiOnly, onCheckedChange = { on -> ui.repo.backups.setDriveWifiOnly(on) })
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SmallButton("Copy now", {
                        ui.run("Copied to Google Drive") {
                            val ok = withContext(Dispatchers.IO) { ui.repo.backups.copyToDrive(true) }
                            if (!ok) throw UserError(ui.repo.backups.state.value.driveError.ifBlank { "No backup to copy yet." })
                        }
                    }, primary = true)
                    SmallButton("Stop", { ui.repo.backups.clearDrive() })
                }
                Text("Google Drive uploads it when the phone has internet. To restore on a new PC: download the file from " +
                    "drive.google.com (older versions: right-click → Manage versions) and choose 'Restore from a backup file'.",
                    color = Muted, fontSize = 12.sp)
            }
        }
        Section("Saved backups (${bs.items.size})") {
            if (bs.items.isEmpty()) Text("No backup yet. It arrives after the next daily closing on the PC, when this phone is " +
                "on the shop Wi-Fi.", color = Muted, fontSize = 13.sp)
            bs.items.forEach { b ->
                Column {
                    Text(backupTitle(b), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                    Text("${sizeText(b.size)} · saved ${Dates.niceTime(b.savedAt)}", color = Muted, fontSize = 12.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        if (!b.isReport) TextButton(onClick = { target = b; action = "send" }) { Text("Send to PC", color = Info, fontWeight = FontWeight.Bold) }
                        if (b.isReport) TextButton(onClick = { openReport(ctx, b) }) { Text("Open", color = Info) }
                        TextButton(onClick = { shareBackup(ctx, b) }) { Text("Share", color = Info) }
                    }
                }
                HorizontalDivider()
            }
            OutlinedButton(onClick = { pickFile.launch(arrayOf("*/*")) }, modifier = Modifier.fillMaxWidth()) {
                Text("Add a backup file (Google Drive / file manager)")
            }
        }
        Section("New PC?") {
            Text("On the new PC choose 'Restore from phone'. Then tap 'Send to PC' on a backup above and scan the PC's QR " +
                "code. The PC asks for the owner's password (or the recovery code).", color = Muted, fontSize = 13.sp)
            if (!standalone && owner) {
                Text("No backup anywhere? This phone can rebuild the new PC from its own copy: products, stock, IMEIs, " +
                    "customers with dues and open repair jobs.", color = Muted, fontSize = 13.sp)
                OutlinedButton(onClick = { askRebuild = true }, modifier = Modifier.fillMaxWidth()) {
                    Text("Rebuild a new PC from this phone", color = Bad)
                }
            }
        }
    }

    if (action.isNotEmpty() && busy.isEmpty() && !typing) {
        AlertDialog(onDismissRequest = { action = ""; target = null },
            title = { Text(if (action == "rebuild") "Rebuild the new PC" else "Send to the PC") },
            text = { Text("On the PC: 'Restore from phone' shows a QR code. Scan it – or type the PC's address and the code.") },
            confirmButton = { TextButton(onClick = { scan() }) { Text("Scan the PC's QR", fontWeight = FontWeight.Bold) } },
            dismissButton = { TextButton(onClick = { typing = true }) { Text("Type address & code") } })
    }
    if (typing) {
        AlertDialog(onDismissRequest = { typing = false; action = ""; target = null },
            title = { Text("PC address and restore code") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextIn("PC address (e.g. 192.168.1.6)", host, { host = it.trim() }, Modifier.fillMaxWidth())
                    TextIn("Restore code (e.g. UT86Z-THAZC)", code, { code = it.uppercase().take(11) }, Modifier.fillMaxWidth())
                }
            },
            confirmButton = {
                TextButton(enabled = host.isNotBlank() && code.isNotBlank(), onClick = {
                    typing = false
                    go(Repo.RestoreQr(listOf(host), Net.PORT, code, "", ""))
                }) { Text("Send", fontWeight = FontWeight.Bold) }
            },
            dismissButton = { TextButton(onClick = { typing = false; action = ""; target = null }) { Text("Cancel", color = Muted) } })
    }
    if (busy.isNotEmpty()) {
        AlertDialog(onDismissRequest = {}, confirmButton = {}, title = { Text("Please wait") },
            text = { Row(verticalAlignment = Alignment.CenterVertically) { Busy(); HGap(12); Text(busy) } })
    }
    result?.let { (title, text) ->
        AlertDialog(onDismissRequest = { result = null }, title = { Text(title) }, text = { Text(text) },
            confirmButton = { TextButton(onClick = { result = null }) { Text("OK", fontWeight = FontWeight.Bold) } })
    }
    if (askRebuild) Confirm("Rebuild a new PC from this phone?",
        "Use this only when there is no backup. This phone's copy of the shop (products, stock, IMEIs, customers with dues, " +
            "open repair jobs and the logins) is sent to the new PC over the Wi-Fi. Old bills, purchases, expenses and cost " +
            "prices are not on the phone.", "Continue",
        onOk = { action = "rebuild" }, onDismiss = { askRebuild = false })
}

/** The Backups screen before this phone is linked (e.g. a new phone with a backup downloaded from Google Drive). */
@Composable
fun StandaloneBackups(ui: Ui, st: UiState, onBack: () -> Unit) {
    Column(Modifier.fillMaxSize().background(Cream)) {
        Row(Modifier.fillMaxWidth().background(Ink).padding(horizontal = 6.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("← Back", color = Color.White) }
            Text("Backups", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 18.sp)
        }
        BackupsScreen(ui, st, standalone = true)
    }
}

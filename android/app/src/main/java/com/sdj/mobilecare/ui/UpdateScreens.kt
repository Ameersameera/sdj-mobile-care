package com.sdj.mobilecare.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import com.sdj.mobilecare.UpdateNotice
import com.sdj.mobilecare.data.AppUpdate
import java.io.File

/** Hands the downloaded phone app to Android's own installer (Android never lets an app update itself silently). */
object PhoneInstaller {
    fun canInstall(ctx: Context): Boolean = Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()

    /** Returns a message to show the person, or null when the installer screen was opened. */
    fun install(ctx: Context, apk: File): String? {
        if (!apk.exists()) return "The update file is missing. Sync with the PC again."
        if (!canInstall(ctx)) {
            val i = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + ctx.packageName)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            return try {
                ctx.startActivity(i)
                "Turn on \"Allow from this source\" for SDJ Mobile Care, then come back and tap Update now again."
            } catch (e: ActivityNotFoundException) {
                "Allow SDJ Mobile Care to install apps in the phone Settings, then tap Update now again."
            }
        }
        val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", apk)
        val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            ctx.startActivity(i)
            null
        } catch (e: ActivityNotFoundException) {
            "This phone has no app installer screen. Open the file ${apk.name} from the Files app."
        }
    }
}

/** "New version ready" card shown on the Home and Sync screens. */
@Composable
fun UpdateBanner(ui: Ui) {
    val up by ui.repo.update.collectAsState()
    val u = up ?: return
    val ctx = LocalContext.current
    Section {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            when (u.state) {
                AppUpdate.READY -> {
                    Text("Update ready · version ${u.version}", fontWeight = FontWeight.SemiBold)
                    Text("Your data and entries stay as they are. Tap Update now, then Install on the next screen.", color = Muted, fontSize = 13.sp)
                    SmallButton("Update now", { PhoneInstaller.install(ctx, File(u.path))?.let { ui.toast(it) } }, Modifier.fillMaxWidth(), primary = true)
                }
                AppUpdate.LOADING -> {
                    Text("Getting version ${u.version} from the PC…", fontWeight = FontWeight.SemiBold)
                    Text("Keep the phone on the shop Wi-Fi.", color = Muted, fontSize = 13.sp)
                }
                else -> {
                    Text("Update ${u.version} is waiting on the PC", fontWeight = FontWeight.SemiBold)
                    Text(u.error, color = Warn, fontSize = 13.sp)
                }
            }
        }
    }
}

private fun meta(u: AppUpdate): String {
    val bits = mutableListOf<String>()
    if (u.size > 0) bits += "%.0f MB".format(u.size / 1048576.0)
    if (u.date.length >= 10) bits += u.date.take(10)
    return bits.joinToString(" · ")
}

/** Popup when a new version is ready on the phone: what's new, Update now / Later (asks again tomorrow). */
@Composable
fun UpdatePopup(ui: Ui, pending: Int) {
    val up by ui.repo.update.collectAsState()
    var hidden by remember { mutableStateOf(false) }
    val ctx = LocalContext.current
    val u = up
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    val show = u != null && u.state == AppUpdate.READY && !hidden && !ui.repo.updateSnoozed()
    LaunchedEffect(show) {
        // once: allow notifications, so "new version ready" can arrive even when the app is closed
        if (show && android.os.Build.VERSION.SDK_INT >= 33 && !UpdateNotice.allowed(ctx) && !ui.repo.notifAsked()) {
            ui.repo.setNotifAsked()
            ask.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    if (!show || u == null) return
    AlertDialog(
        onDismissRequest = { hidden = true },
        title = { Text("Version ${u.version} is ready") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val m = meta(u)
                if (m.isNotBlank()) Text(m, color = Muted, fontSize = 13.sp)
                if (u.recommended) Text("★ Recommended update", color = Warn, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                if (u.notes.isNotBlank()) Text(u.notes, fontSize = 14.sp)
                Text("Your data and entries stay as they are.", color = Muted, fontSize = 13.sp)
                if (pending > 0) Text("$pending entr${if (pending == 1) "y is" else "ies are"} not synced yet. They stay safe, but sync with the PC first if you can.", color = Warn, fontSize = 13.sp)
            }
        },
        confirmButton = { TextButton(onClick = { hidden = true; PhoneInstaller.install(ctx, File(u.path))?.let { ui.toast(it) } }) { Text("Update now", color = Info) } },
        dismissButton = { TextButton(onClick = { hidden = true; ui.run { ui.repo.snoozeUpdate() } }) { Text("Later") } },
    )
}

/** Shown once, the first time the app opens after an update. */
@Composable
fun WhatsNewPopup(ui: Ui) {
    val w by ui.repo.whatsNew.collectAsState()
    val n = w ?: return
    AlertDialog(
        onDismissRequest = { ui.repo.dismissWhatsNew() },
        title = { Text("✔ Updated to version ${n.version}") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (n.date.length >= 10) Text(n.date.take(10), color = Muted, fontSize = 13.sp)
                Text(n.notes.ifBlank { "The app was updated. Your data is as it was." }, fontSize = 14.sp)
            }
        },
        confirmButton = { TextButton(onClick = { ui.repo.dismissWhatsNew() }) { Text("Got it", color = Info) } },
    )
}

/** Sync screen: this app's version and the update history. */
@Composable
fun UpdateHistorySection(ui: Ui) {
    val hist = ui.repo.updateHistory()
    Section("App version") {
        Text("Version ${ui.repo.version}", fontWeight = FontWeight.SemiBold)
        if (hist.isEmpty()) Text("No updates yet.", color = Muted, fontSize = 13.sp)
        hist.forEach { h ->
            Text("• ${h.version}" + if (h.date.length >= 10) " · ${h.date.take(10)}" else "", fontSize = 14.sp)
            if (h.notes.isNotBlank()) Text(h.notes.lines().take(4).joinToString("\n"), color = Muted, fontSize = 12.sp)
        }
    }
}

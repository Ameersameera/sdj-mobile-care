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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
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

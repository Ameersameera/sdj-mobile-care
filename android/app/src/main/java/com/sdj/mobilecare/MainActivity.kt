package com.sdj.mobilecare

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.sdj.mobilecare.ui.AppRoot
import com.sdj.mobilecare.ui.AppTheme
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private var syncLoop: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as App
        setContent { AppTheme { AppRoot(app.repo, App.phoneName()) } }
    }

    /** While the app is on screen it syncs every few seconds (only on the shop Wi-Fi; no internet is used). */
    override fun onStart() {
        super.onStart()
        val app = application as App
        syncLoop = app.scope.launch {
            while (isActive) {
                val ok = app.repo.syncOnce()
                delay(if (ok) 8_000L else 20_000L)
            }
        }
    }

    override fun onStop() {
        syncLoop?.cancel()
        syncLoop = null
        super.onStop()
    }
}

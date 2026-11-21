package com.sdj.mobilecare

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.sdj.mobilecare.ui.AppRoot
import com.sdj.mobilecare.ui.AppTheme
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private var syncLoop: Job? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as App
        setContent { AppTheme { AppRoot(app.repo, App.phoneName()) } }
    }

    /** While the app is on screen it stays in live sync with the shop PC (changes show within a second or two). */
    override fun onStart() {
        super.onStart()
        val app = application as App
        app.repo.backups.refresh()                 // storage permission may have changed
        syncLoop = app.scope.launch { app.repo.live() }
    }

    override fun onStop() {
        syncLoop?.cancel()
        syncLoop = null
        super.onStop()
    }
}

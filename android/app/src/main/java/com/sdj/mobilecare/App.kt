package com.sdj.mobilecare

import android.app.Application
import android.os.Build
import com.sdj.mobilecare.data.Backups
import com.sdj.mobilecare.data.DownloadsSink
import com.sdj.mobilecare.data.Net
import com.sdj.mobilecare.data.Repo
import com.sdj.mobilecare.data.SafDrive
import com.sdj.mobilecare.data.Store
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class App : Application() {
    lateinit var repo: Repo
        private set
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    override fun onCreate() {
        super.onCreate()
        val net = Net(this)
        repo = Repo(Store(filesDir), net, appVersion = BuildConfig.VERSION_NAME,
            backups = Backups(filesDir, DownloadsSink(this), SafDrive(this)) { net.unmetered() })
        repo.onNewEntry = { scope.launch { repo.syncOnce() } }
        scope.launch {
            repo.load()
            // app opened: look for a newer version on the internet (at most every 6 hours; never in unit tests)
            if (Build.FINGERPRINT != "robolectric") runCatching { repo.checkOnline() }
        }
        runCatching { SyncWorker.start(this) }          // background: sync, backups and update checks every 15 minutes
    }

    companion object {
        fun phoneName(): String {
            val maker = Build.MANUFACTURER.orEmpty().replaceFirstChar { it.uppercase() }
            val model = Build.MODEL.orEmpty()
            return if (model.startsWith(maker, ignoreCase = true)) model else "$maker $model".trim()
        }
    }
}

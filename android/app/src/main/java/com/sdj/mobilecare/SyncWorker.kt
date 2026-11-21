package com.sdj.mobilecare

import android.content.Context
import androidx.work.Configuration
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Runs every 15 minutes even when the app is closed (Android's shortest repeat): syncs with the shop PC when the phone
 * is on its Wi-Fi (entries reach the PC, backups reach the phone), and looks for a newer app version on the internet.
 */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? App ?: return Result.success()
        try {
            if (!app.repo.state.value.ready) app.repo.load()
            if (app.repo.syncOnce()) app.repo.awaitFiles()   // not at the shop / PC off: the next run tries again
        } catch (e: Exception) {
            // ignore – the next run tries again
        }
        runCatching { app.repo.checkOnline() }
        runCatching { app.repo.takeUpdateNotice()?.let { UpdateNotice.show(applicationContext, it) } }
        return Result.success()
    }

    companion object {
        fun start(context: Context) {
            WorkManager.initialize(context, Configuration.Builder().build())
            val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("sdj-sync", ExistingPeriodicWorkPolicy.UPDATE, req)
        }
    }
}

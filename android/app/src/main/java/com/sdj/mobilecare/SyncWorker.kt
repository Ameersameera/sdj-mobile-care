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
 * Syncs with the shop PC now and then even when the app is closed – only on Wi-Fi (never mobile data). So entries reach
 * the PC, and the daily closing backup reaches the owner's phone, without anyone opening the app.
 */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val app = applicationContext as? App ?: return Result.success()
        try {
            if (!app.repo.state.value.ready) app.repo.load()
            app.repo.syncOnce()
            app.repo.takeUpdateNotice()?.let { UpdateNotice.show(applicationContext, it) }
        } catch (e: Exception) {
            // not at the shop / PC off: the next run tries again
        }
        return Result.success()
    }

    companion object {
        fun start(context: Context) {
            WorkManager.initialize(context, Configuration.Builder().build())
            val req = PeriodicWorkRequestBuilder<SyncWorker>(30, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.UNMETERED).build())
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork("sdj-sync", ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}

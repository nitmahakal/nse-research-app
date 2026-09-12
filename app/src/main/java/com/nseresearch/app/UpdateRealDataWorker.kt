package com.nseresearch.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class UpdateRealDataWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    companion object {
        private const val CHANNEL_ID = "real_data_update"
        private const val NOTIFICATION_ID = 1001
    }

    private class ProgressReporter(
        private val worker: UpdateRealDataWorker
    ) {
        fun onProgress(
            done: Int,
            total: Int,
            phase: String
        ) {
            worker.setProgressAsync(
                workDataOf(
                    "done" to done,
                    "total" to total,
                    "phase" to phase
                )
            )
        }
    }

    private fun createForegroundInfo(): ForegroundInfo {

        val notificationManager =
            applicationContext.getSystemService(
                Context.NOTIFICATION_SERVICE
            ) as NotificationManager

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {

            val channel = NotificationChannel(
                CHANNEL_ID,
                "Real Data Update",
                NotificationManager.IMPORTANCE_LOW
            )

            channel.description =
                "Shows NSE market-data update progress"

            notificationManager.createNotificationChannel(
                channel
            )
        }

        val notification: Notification =
            NotificationCompat.Builder(
                applicationContext,
                CHANNEL_ID
            )
                .setSmallIcon(
                    android.R.drawable.stat_notify_sync
                )
                .setContentTitle(
                    "NSE Research App"
                )
                .setContentText(
                    "Updating market data..."
                )
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setPriority(
                    NotificationCompat.PRIORITY_LOW
                )
                .build()

        val serviceType =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            } else {
                0
            }

        return ForegroundInfo(
            NOTIFICATION_ID,
            notification,
            serviceType
        )
    }

    override suspend fun doWork(): Result {

        // IMPORTANT:
        // This update may take many minutes.
        // Keep the Worker alive as a foreground task.
        setForeground(
            createForegroundInfo()
        )

        return try {

            if (!Python.isStarted()) {
                Python.start(
                    AndroidPlatform(
                        applicationContext
                    )
                )
            }

            val report = withContext(
                Dispatchers.IO
            ) {

                val python =
                    Python.getInstance()

                val dbPath =
                    applicationContext.filesDir.absolutePath +
                            "/nse_research.db"

                val module =
                    python.getModule(
                        "app_bridge"
                    )

                val symbolsPath =
                    copySymbolsAsset(
                        applicationContext
                    )

                module.callAttr(
                    "update_real_data_report",
                    dbPath,
                    symbolsPath,
                    ProgressReporter(
                        this@UpdateRealDataWorker
                    )
                )
            }

            val reportText =
                report.toString()

            val truncated =
                if (reportText.length > 3000) {
                    reportText.take(3000) +
                            "\n...(truncated)"
                } else {
                    reportText
                }

            val status =
                try {
                    report.get("status")
                        ?.toString()
                } catch (_: Exception) {
                    null
                }

            val referenceLatestDate =
                try {
                    report.get(
                        "reference_latest_date"
                    )?.toString()
                } catch (_: Exception) {
                    null
                }

            val marketDataThrough =
                try {
                    report.get(
                        "market_data_through"
                    )?.toString()
                } catch (_: Exception) {
                    null
                }

            val lastUpdateFinished =
                try {
                    report.get(
                        "last_update_finished"
                    )?.toString()
                } catch (_: Exception) {
                    null
                }

            val updated =
                try {
                    report.get("updated")
                        ?.toString()
                } catch (_: Exception) {
                    null
                }

            val upToDate =
                try {
                    report.get("up_to_date")
                        ?.toString()
                } catch (_: Exception) {
                    null
                }

            val lastAvailable =
                try {
                    report.get(
                        "last_available"
                    )?.toString()
                } catch (_: Exception) {
                    null
                }

            val noData =
                try {
                    report.get("no_data")
                        ?.toString()
                } catch (_: Exception) {
                    null
                }

            val output =
                workDataOf(
                    "report" to truncated,

                    "status" to
                            (status ?: "ERROR"),

                    "reference_latest_date" to
                            (referenceLatestDate ?: ""),

                    "market_data_through" to
                            (marketDataThrough ?: ""),

                    "last_update_finished" to
                            (lastUpdateFinished ?: ""),

                    "updated" to
                            (updated ?: "0"),

                    "up_to_date" to
                            (upToDate ?: "0"),

                    "last_available" to
                            (lastAvailable ?: "0"),

                    "no_data" to
                            (noData ?: "0")
                )

            if (status == "SUCCESS") {

                Result.success(
                    output
                )

            } else {

                Result.failure(
                    output
                )
            }

        } catch (e: Exception) {

            Result.failure(
                workDataOf(
                    "report" to
                            "ERROR: ${e.message}"
                )
            )
        }
    }
}

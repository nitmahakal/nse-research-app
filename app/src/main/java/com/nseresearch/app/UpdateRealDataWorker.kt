package com.nseresearch.app

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
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

    override suspend fun doWork(): Result {
        return try {

            if (!Python.isStarted()) {
                Python.start(
                    AndroidPlatform(applicationContext)
                )
            }

            val report = withContext(Dispatchers.IO) {

                val python = Python.getInstance()

                val dbPath =
                    applicationContext.filesDir.absolutePath +
                    "/nse_research.db"

                val module =
                    python.getModule("app_bridge")

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

            Log.i(
                "UpdateRealDataWorker",
                reportText
            )

            val truncated =
                if (reportText.length > 3000) {
                    reportText.take(3000) +
                    "\n...(truncated)"
                } else {
                    reportText
                }

            /*
             * Python status:
             * SUCCESS -> WorkManager SUCCESS
             * PARTIAL -> WorkManager SUCCESS
             * ERROR   -> WorkManager FAILURE
             */

            val status =
                try {
                    report.asMap()
                        .get("status")
                        ?.toString()
                } catch (_: Exception) {
                    null
                }

            if (status == "ERROR") {

                Result.failure(
                    workDataOf(
                        "report" to truncated
                    )
                )

            } else {

                Result.success(
                    workDataOf(
                        "report" to truncated
                    )
                )
            }

        } catch (e: Exception) {

            Log.e(
                "UpdateRealDataWorker",
                "Real data update failed",
                e
            )

            Result.failure(
                workDataOf(
                    "report" to
                        "ERROR: ${e.message}"
                )
            )
        }
    }
}

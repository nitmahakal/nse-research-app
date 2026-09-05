package com.nseresearch.app

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.chaquo.python.Python

class UpdateRealDataWorker(
    appContext: Context,
    workerParams: WorkerParameters
) : CoroutineWorker(appContext, workerParams) {

    class ProgressReporter(
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

    override suspend fun doWork(): Result =
        withContext(Dispatchers.IO) {

            try {
                val context = applicationContext

                val dbPath =
                    context.getDatabasePath("nse_research.db")
                        .absolutePath

                MainActivity.copySymbolsAsset(
                    context
                )

                val symbolsPath =
                    context.filesDir
                        .resolve("nse_symbols.csv")
                        .absolutePath

                val reporter =
                    ProgressReporter(this@UpdateRealDataWorker)

                val python =
                    Python.getInstance()

                val bridge =
                    python.getModule("app_bridge")

                val report =
                    bridge.callAttr(
                        "update_real_data_report",
                        dbPath,
                        symbolsPath,
                        reporter
                    )

                val reportText =
                    report.toString()

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
                            "report" to reportText
                        )
                    )
                } else {
                    Result.success(
                        workDataOf(
                            "report" to reportText
                        )
                    )
                }

            } catch (e: Exception) {

                Result.failure(
                    workDataOf(
                        "report" to (
                            "ERROR: " +
                            (e.message ?: e.toString())
                        )
                    )
                )
            }
        }
}

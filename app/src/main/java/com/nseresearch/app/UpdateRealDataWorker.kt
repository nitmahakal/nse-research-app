package com.nseresearch.app

import android.content.Context
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * Downloads real NSE price data via yfinance. This MUST run off the
 * main thread (Android throws NetworkOnMainThreadException otherwise)
 * - WorkManager's Worker.doWork() already runs on a background thread,
 * so this reuses the exact same pattern as DailyUpdateWorker rather
 * than introducing new threading code.
 */
class UpdateRealDataWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        return try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(applicationContext))
            }
            val python = Python.getInstance()
            val dbPath = applicationContext.filesDir.absolutePath + "/nse_research.db"

            val module = python.getModule("app_bridge")
            val symbolsPath = copySymbolsAsset(applicationContext)
            val report: PyObject = module.callAttr("update_real_data_report", dbPath, symbolsPath)
            val reportText = report.toString()

            android.util.Log.i("UpdateRealDataWorker", reportText)

            val truncated = if (reportText.length > 3000) reportText.take(3000) + "\n...(truncated)" else reportText
            Result.success(workDataOf("report" to truncated))
        } catch (e: Exception) {
            android.util.Log.e("UpdateRealDataWorker", "Real data update failed", e)
            Result.failure(workDataOf("report" to "ERROR: ${e.message}"))
        }
    }
}

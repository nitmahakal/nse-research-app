package com.nseresearch.app

import android.content.Context
import androidx.work.Worker
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class DailyUpdateWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        return try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(applicationContext))
            }
            val python = Python.getInstance()
            val dbPath = applicationContext.filesDir.absolutePath + "/nse_research.db"

            val module = python.getModule("app_bridge")
            val report: PyObject = module.callAttr("run_tracked_scans_and_update_report", dbPath)
            val reportText = report.toString()

            android.util.Log.i("DailyUpdateWorker", reportText)

            val truncated = if (reportText.length > 3000) reportText.take(3000) + "\n...(truncated)" else reportText
            Result.success(workDataOf("report" to truncated))
        } catch (e: Exception) {
            android.util.Log.e("DailyUpdateWorker", "Daily update failed", e)
            Result.retry()
        }
    }
}

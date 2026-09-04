package com.nseresearch.app

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

fun copySymbolsAsset(context: Context): String {
    val destFile = File(context.filesDir, "nse_symbols.csv")
    context.assets.open("nse_symbols.csv").use { input ->
        FileOutputStream(destFile).use { output ->
            input.copyTo(output)
        }
    }
    return destFile.absolutePath
}

class MainActivity : AppCompatActivity() {

    private var ownerModeUnlocked = false
    private var updateActionCheckRunning = false

    private val realUpdateWorkName = "real_data_update"
    private val updatePrefsName = "update_prefs"
    private val lastSuccessfulUpdateKey = "last_successful_update"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }

        val python = Python.getInstance()
        val dbPath = filesDir.absolutePath + "/nse_research.db"

        copySymbolsAsset(this)

        val resultView = findViewById<TextView>(R.id.resultText)
        val openScannerButton = findViewById<Button>(R.id.openScannerButton)
        val updateRealDataButton = findViewById<Button>(R.id.updateRealDataButton)
        val ownerModeStatus = findViewById<TextView>(R.id.ownerModeStatus)
        val runButton = findViewById<Button>(R.id.runTestButton)
        val runScanButton = findViewById<Button>(R.id.runScanButton)
        val saveLockedScanButton = findViewById<Button>(R.id.saveLockedScanButton)
        val listScansButton = findViewById<Button>(R.id.listScansButton)
        val runSavedScanButton = findViewById<Button>(R.id.runSavedScanButton)
        val setPinButton = findViewById<Button>(R.id.setPinButton)
        val ownerModeButton = findViewById<Button>(R.id.ownerModeButton)
        val dailyUpdateButton = findViewById<Button>(R.id.dailyUpdateButton)
        val listSignalsButton = findViewById<Button>(R.id.listSignalsButton)
        val showRatingButton = findViewById<Button>(R.id.showRatingButton)
        val scheduleAutoUpdateButton = findViewById<Button>(R.id.scheduleAutoUpdateButton)
        val cancelAutoUpdateButton = findViewById<Button>(R.id.cancelAutoUpdateButton)
        val testWorkerNowButton = findViewById<Button>(R.id.testWorkerNowButton)
        val autoUpdateStatus = findViewById<TextView>(R.id.autoUpdateStatus)

        val dailyUpdateWorkName = "daily_update"

        val workManager = WorkManager.getInstance(applicationContext)

        fun refreshOwnerModeStatus() {
            ownerModeStatus.text =
                "Owner Mode: ${if (ownerModeUnlocked) "ON" else "OFF"}"
        }

        fun promptForPin(title: String, onEntered: (String) -> Unit) {
            val input = EditText(this)

            input.inputType =
                InputType.TYPE_CLASS_NUMBER or
                        InputType.TYPE_NUMBER_VARIATION_PASSWORD

            AlertDialog.Builder(this)
                .setTitle(title)
                .setView(input)
                .setPositiveButton("OK") { _, _ ->
                    onEntered(input.text.toString())
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        runButton.setOnClickListener {
            resultView.text = "Running environment test..."

            try {
                val module = python.getModule("environment_test")
                val result: PyObject =
                    module.callAttr("run_all_checks")

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        runScanButton.setOnClickListener {
            resultView.text = "Running test scan..."

            try {
                val module = python.getModule("test_scan")
                val result: PyObject =
                    module.callAttr("run_test_scan", dbPath)

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        saveLockedScanButton.setOnClickListener {
            resultView.text = "Saving scan..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "save_demo_locked_scan",
                        dbPath
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        listScansButton.setOnClickListener {
            resultView.text = "Loading saved scans..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "list_saved_scans_report",
                        dbPath,
                        ownerModeUnlocked
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        runSavedScanButton.setOnClickListener {
            resultView.text = "Running saved scan..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "run_saved_scan_report",
                        dbPath,
                        "Momentum Test Scan"
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        setPinButton.setOnClickListener {
            promptForPin("Set Owner PIN (min 4 digits)") { pin ->
                try {
                    val module = python.getModule("app_bridge")
                    val result: PyObject =
                        module.callAttr(
                            "set_owner_pin_report",
                            dbPath,
                            pin
                        )

                    resultView.text = result.toString()
                } catch (e: Exception) {
                    resultView.text = "ERROR:\n${e.message}"
                }
            }
        }

        ownerModeButton.setOnClickListener {
            if (ownerModeUnlocked) {
                ownerModeUnlocked = false
                refreshOwnerModeStatus()
                resultView.text = "Owner Mode locked."
            } else {
                promptForPin("Enter Owner PIN") { pin ->
                    try {
                        val module = python.getModule("app_bridge")

                        val correct =
                            module.callAttr(
                                "verify_owner_pin_check",
                                dbPath,
                                pin
                            ).toBoolean()

                        if (correct) {
                            ownerModeUnlocked = true
                            resultView.text = "Owner Mode unlocked."
                        } else {
                            resultView.text = "Incorrect PIN."
                        }

                        refreshOwnerModeStatus()
                    } catch (e: Exception) {
                        resultView.text = "ERROR:\n${e.message}"
                    }
                }
            }
        }

        dailyUpdateButton.setOnClickListener {
            resultView.text =
                "Running daily update (track + mark-to-market)..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "run_tracked_scans_and_update_report",
                        dbPath
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        listSignalsButton.setOnClickListener {
            resultView.text = "Loading signals..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "list_signals_report",
                        dbPath,
                        "Momentum Test Scan"
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        showRatingButton.setOnClickListener {
            resultView.text = "Computing rating..."

            try {
                val module = python.getModule("app_bridge")
                val result: PyObject =
                    module.callAttr(
                        "get_rating_report",
                        dbPath,
                        "Momentum Test Scan"
                    )

                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        refreshOwnerModeStatus()

        openScannerButton.setOnClickListener {
            startActivity(
                Intent(
                    this,
                    ScannerActivity::class.java
                )
            )
        }

        updateRealDataButton.setOnClickListener {

            if (updateActionCheckRunning) {
                return@setOnClickListener
            }

            updateActionCheckRunning = true

            lifecycleScope.launch {

                val activeWork =
                    withContext(Dispatchers.IO) {

                        try {
                            workManager
                                .getWorkInfosForUniqueWork(
                                    realUpdateWorkName
                                )
                                .get()
                                .firstOrNull {
                                    it.state == WorkInfo.State.RUNNING ||
                                            it.state == WorkInfo.State.ENQUEUED ||
                                            it.state == WorkInfo.State.BLOCKED
                                }
                        } catch (e: Exception) {
                            null
                        }
                    }

                updateActionCheckRunning = false

                if (activeWork != null) {

                    AlertDialog.Builder(this@MainActivity)
                        .setTitle("Update already running")
                        .setMessage(
                            "Real-data update is already in progress.\n\n" +
                                    "Do you want to cancel it and restart?"
                        )
                        .setNegativeButton("NO", null)
                        .setPositiveButton("YES") { _, _ ->

                            val newRequest =
                                OneTimeWorkRequestBuilder<UpdateRealDataWorker>()
                                    .setConstraints(
                                        Constraints.Builder()
                                            .setRequiredNetworkType(
                                                NetworkType.CONNECTED
                                            )
                                            .build()
                                    )
                                    .build()

                            workManager.enqueueUniqueWork(
                                realUpdateWorkName,
                                androidx.work.ExistingWorkPolicy.REPLACE,
                                newRequest
                            )
                        }
                        .show()

                    return@launch
                }

                resultView.text =
                    "Starting real-data update..."

                val request =
                    OneTimeWorkRequestBuilder<UpdateRealDataWorker>()
                        .setConstraints(
                            Constraints.Builder()
                                .setRequiredNetworkType(
                                    NetworkType.CONNECTED
                                )
                                .build()
                        )
                        .build()

                workManager.enqueueUniqueWork(
                    realUpdateWorkName,
                    androidx.work.ExistingWorkPolicy.KEEP,
                    request
                )
            }
        }

        workManager
            .getWorkInfosForUniqueWorkLiveData(
                realUpdateWorkName
            )
            .observe(this) { infos ->

                val info =
                    infos.firstOrNull()
                        ?: return@observe

                when (info.state) {

                    WorkInfo.State.RUNNING -> {

                        updateRealDataButton.text =
                            "Update In Progress..."

                        val done =
                            info.progress.getInt(
                                "done",
                                -1
                            )

                        val total =
                            info.progress.getInt(
                                "total",
                                -1
                            )

                        val phase =
                            info.progress.getString(
                                "phase"
                            ) ?: "Working..."

                        resultView.text =
                            if (done >= 0 && total > 0) {
                                "$phase\n($done / $total)"
                            } else {
                                phase
                            }
                    }

                    WorkInfo.State.SUCCEEDED -> {

                        updateRealDataButton.text =
                            "Update Real Data"

                        val report =
                            info.outputData.getString(
                                "report"
                            ) ?: "(no report text)"

                        val timestamp =
                            SimpleDateFormat(
                                "dd-MM-yyyy HH:mm:ss",
                                Locale.getDefault()
                            ).format(Date())

                        getSharedPreferences(
                            updatePrefsName,
                            Context.MODE_PRIVATE
                        )
                            .edit()
                            .putString(
                                lastSuccessfulUpdateKey,
                                timestamp
                            )
                            .apply()

                        resultView.text =
                            "Real data update SUCCEEDED:\n\n" +
                                    "Last successful update: $timestamp\n\n" +
                                    report
                    }

                    WorkInfo.State.FAILED -> {

                        updateRealDataButton.text =
                            "Update Real Data"

                        val report =
                            info.outputData.getString(
                                "report"
                            ) ?: "(no error details)"

                        resultView.text =
                            "Real data update FAILED:\n\n$report"
                    }

                    WorkInfo.State.CANCELLED -> {

                        updateRealDataButton.text =
                            "Update Real Data"

                        resultView.text =
                            "Real data update cancelled."
                    }

                    else -> {

                        updateRealDataButton.text =
                            "Update Real Data"
                    }
                }
            }

        scheduleAutoUpdateButton.setOnClickListener {

            val request =
                PeriodicWorkRequestBuilder<DailyUpdateWorker>(
                    24,
                    TimeUnit.HOURS
                )
                    .setConstraints(
                        Constraints.Builder()
                            .build()
                    )
                    .build()

            workManager.enqueueUniquePeriodicWork(
                dailyUpdateWorkName,
                ExistingPeriodicWorkPolicy.KEEP,
                request
            )

            autoUpdateStatus.text =
                "Auto-Update: scheduled (runs roughly every 24h in the background)"

            resultView.text =
                "Daily auto-update scheduled. " +
                        "It will keep running even if you close the app."
        }

        cancelAutoUpdateButton.setOnClickListener {

            workManager.cancelUniqueWork(
                dailyUpdateWorkName
            )

            autoUpdateStatus.text =
                "Auto-Update: not scheduled"

            resultView.text =
                "Daily auto-update cancelled."
        }

        testWorkerNowButton.setOnClickListener {

            resultView.text =
                "Running update via WorkManager (background thread)..."

            val request =
                OneTimeWorkRequestBuilder<DailyUpdateWorker>()
                    .build()

            workManager.enqueue(request)

            workManager
                .getWorkInfoByIdLiveData(request.id)
                .observe(this) { info ->

                    if (info == null) {
                        return@observe
                    }

                    when (info.state) {

                        WorkInfo.State.SUCCEEDED -> {

                            val report =
                                info.outputData.getString(
                                    "report"
                                ) ?: "(no report text)"

                            resultView.text =
                                "WorkManager run SUCCEEDED:\n\n$report"
                        }

                        WorkInfo.State.FAILED -> {

                            resultView.text =
                                "WorkManager run FAILED. " +
                                        "Check Logcat for " +
                                        "'DailyUpdateWorker' for details."
                        }

                        WorkInfo.State.RUNNING -> {

                            resultView.text =
                                "WorkManager run in progress..."
                        }

                        else -> {
                            // ENQUEUED, BLOCKED, CANCELLED
                            // No UI change needed.
                        }
                    }
                }
        }
    }
}

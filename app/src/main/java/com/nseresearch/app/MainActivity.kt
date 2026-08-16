package com.nseresearch.app

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {

    private var ownerModeUnlocked = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        val python = Python.getInstance()
        val dbPath = filesDir.absolutePath + "/nse_research.db"

        val resultView = findViewById<TextView>(R.id.resultText)
        val ownerModeStatus = findViewById<TextView>(R.id.ownerModeStatus)
        val runButton = findViewById<Button>(R.id.runTestButton)
        val runScanButton = findViewById<Button>(R.id.runScanButton)
        val saveLockedScanButton = findViewById<Button>(R.id.saveLockedScanButton)
        val listScansButton = findViewById<Button>(R.id.listScansButton)
        val runSavedScanButton = findViewById<Button>(R.id.runSavedScanButton)
        val setPinButton = findViewById<Button>(R.id.setPinButton)
        val ownerModeButton = findViewById<Button>(R.id.ownerModeButton)

        fun refreshOwnerModeStatus() {
            ownerModeStatus.text = "Owner Mode: ${if (ownerModeUnlocked) "ON" else "OFF"}"
        }

        fun promptForPin(title: String, onEntered: (String) -> Unit) {
            val input = EditText(this)
            input.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD
            AlertDialog.Builder(this)
                .setTitle(title)
                .setView(input)
                .setPositiveButton("OK") { _, _ -> onEntered(input.text.toString()) }
                .setNegativeButton("Cancel", null)
                .show()
        }

        runButton.setOnClickListener {
            resultView.text = "Running environment test..."
            try {
                val module = python.getModule("environment_test")
                val result: PyObject = module.callAttr("run_all_checks")
                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        runScanButton.setOnClickListener {
            resultView.text = "Running test scan..."
            try {
                val module = python.getModule("test_scan")
                val result: PyObject = module.callAttr("run_test_scan", dbPath)
                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        saveLockedScanButton.setOnClickListener {
            resultView.text = "Saving scan..."
            try {
                val module = python.getModule("app_bridge")
                val result: PyObject = module.callAttr("save_demo_locked_scan", dbPath)
                resultView.text = result.toString()
            } catch (e: Exception) {
                resultView.text = "ERROR:\n${e.message}"
            }
        }

        listScansButton.setOnClickListener {
            resultView.text = "Loading saved scans..."
            try {
                val module = python.getModule("app_bridge")
                val result: PyObject = module.callAttr(
                    "list_saved_scans_report", dbPath, ownerModeUnlocked
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
                val result: PyObject = module.callAttr(
                    "run_saved_scan_report", dbPath, "Momentum Test Scan"
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
                    val result: PyObject = module.callAttr("set_owner_pin_report", dbPath, pin)
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
                        val correct = module.callAttr(
                            "verify_owner_pin_check", dbPath, pin
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

        refreshOwnerModeStatus()
    }
}

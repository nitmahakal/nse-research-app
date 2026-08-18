package com.nseresearch.app

import android.app.AlertDialog
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject

class ScannerActivity : AppCompatActivity() {

    private val indicatorParams = linkedMapOf(
        "Close" to listOf(),
        "EMA" to listOf("length"),
        "HMA" to listOf("length"),
        "RSI" to listOf("length"),
        "EMA of RSI" to listOf("rsi_length", "ema_length"),
        "MACD" to listOf("fast_length", "slow_length", "signal_length"),
        "MACD Signal" to listOf("fast_length", "slow_length", "signal_length"),
        "MACD Histogram" to listOf("fast_length", "slow_length", "signal_length"),
        "Stoch RSI %K" to listOf("rsi_length", "stoch_length", "k_length", "d_length"),
        "Stoch RSI %D" to listOf("rsi_length", "stoch_length", "k_length", "d_length"),
        "Numeric Value" to listOf("value"),
        "Reverse RSI 40" to listOf("rsi_length", "smoothing_length"),
        "Reverse RSI 50" to listOf("rsi_length", "smoothing_length"),
        "Reverse RSI 60" to listOf("rsi_length", "smoothing_length"),
    )
    private val comparators = listOf(
        "Equal", "Greater", "Greater Equal", "Less", "Less Equal",
        "Cross Above", "Cross Below", "Near", "Near Below", "Near Above",
    )
    private val nearComparators = setOf("Near", "Near Below", "Near Above")
    private val timeframes = listOf("Daily", "Weekly", "Monthly", "Quarterly", "Six Month", "Yearly", "All Timeframes")

    private val conditionJsons = mutableListOf<String>()
    private val logicConnectors = mutableListOf<String>()

    private lateinit var dbPath: String
    private lateinit var python: Python

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_scanner)

        dbPath = filesDir.absolutePath + "/nse_research.db"
        python = Python.getInstance()

        val timeframeSpinner = findViewById<Spinner>(R.id.timeframeSpinner)
        val connectorSpinner = findViewById<Spinner>(R.id.connectorSpinner)
        val conditionsListContainer = findViewById<LinearLayout>(R.id.conditionsListContainer)
        val addConditionButton = findViewById<Button>(R.id.addConditionButton)
        val clearConditionsButton = findViewById<Button>(R.id.clearConditionsButton)
        val runScanButton = findViewById<Button>(R.id.runScanButton)
        val saveScanButton = findViewById<Button>(R.id.saveScanButton)
        val resultText = findViewById<TextView>(R.id.scannerResultText)

        timeframeSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, timeframes)
        connectorSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, listOf("AND", "OR"))

        fun buildFullConditionSetJson(): String {
            val entries = JSONArray()
            for (i in conditionJsons.indices) {
                val entry = JSONObject()
                entry.put("condition", JSONObject(conditionJsons[i]))
                entry.put("logic", if (i < logicConnectors.size) logicConnectors[i] else JSONObject.NULL)
                entries.put(entry)
            }
            return entries.toString()
        }

        fun refreshConditionsDisplay() {
            conditionsListContainer.removeAllViews()
            if (conditionJsons.isEmpty()) {
                val empty = TextView(this)
                empty.text = "(no conditions added yet)"
                conditionsListContainer.addView(empty)
                return
            }
            val module = python.getModule("app_bridge")
            val summary = module.callAttr("describe_condition_set_json", buildFullConditionSetJson()).toString()
            val summaryView = TextView(this)
            summaryView.text = summary
            summaryView.setPadding(0, 8, 0, 8)
            conditionsListContainer.addView(summaryView)
        }

        fun buildParamFields(container: LinearLayout, indicatorName: String): List<Pair<String, EditText>> {
            container.removeAllViews()
            val fields = mutableListOf<Pair<String, EditText>>()
            val paramNames = indicatorParams[indicatorName] ?: emptyList()
            for (paramName in paramNames) {
                val label = TextView(this)
                label.text = paramName.replace("_", " ").replaceFirstChar { it.uppercase() }
                val input = EditText(this)
                input.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL or InputType.TYPE_NUMBER_FLAG_SIGNED
                container.addView(label)
                container.addView(input)
                fields.add(paramName to input)
            }
            return fields
        }

        fun readParamsAsJson(fields: List<Pair<String, EditText>>): String? {
            val obj = JSONObject()
            for ((name, input) in fields) {
                val text = input.text.toString().trim()
                val value = text.toDoubleOrNull() ?: return null
                obj.put(name, value)
            }
            return obj.toString()
        }

        addConditionButton.setOnClickListener {
            val dialogLayout = LinearLayout(this)
            dialogLayout.orientation = LinearLayout.VERTICAL
            dialogLayout.setPadding(48, 24, 48, 24)

            fun sectionLabel(text: String): TextView {
                val tv = TextView(this)
                tv.text = text
                tv.setPadding(0, 24, 0, 4)
                tv.setTypeface(null, android.graphics.Typeface.BOLD)
                return tv
            }

            dialogLayout.addView(sectionLabel("Left side"))
            val leftIndicatorSpinner = Spinner(this)
            leftIndicatorSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, indicatorParams.keys.toList())
            dialogLayout.addView(leftIndicatorSpinner)
            val leftParamsContainer = LinearLayout(this)
            leftParamsContainer.orientation = LinearLayout.VERTICAL
            dialogLayout.addView(leftParamsContainer)
            var leftFields = buildParamFields(leftParamsContainer, indicatorParams.keys.first())

            dialogLayout.addView(sectionLabel("Comparator"))
            val comparatorSpinner = Spinner(this)
            comparatorSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, comparators)
            dialogLayout.addView(comparatorSpinner)

            val toleranceLabel = TextView(this)
            toleranceLabel.text = "Tolerance % (for Near comparators)"
            toleranceLabel.visibility = android.view.View.GONE
            val toleranceInput = EditText(this)
            toleranceInput.inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            toleranceInput.visibility = android.view.View.GONE
            dialogLayout.addView(toleranceLabel)
            dialogLayout.addView(toleranceInput)

            dialogLayout.addView(sectionLabel("Right side"))
            val rightIndicatorSpinner = Spinner(this)
            rightIndicatorSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, indicatorParams.keys.toList())
            dialogLayout.addView(rightIndicatorSpinner)
            val rightParamsContainer = LinearLayout(this)
            rightParamsContainer.orientation = LinearLayout.VERTICAL
            dialogLayout.addView(rightParamsContainer)
            var rightFields = buildParamFields(rightParamsContainer, indicatorParams.keys.first())

            leftIndicatorSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                    val name = indicatorParams.keys.toList()[position]
                    leftFields = buildParamFields(leftParamsContainer, name)
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
            rightIndicatorSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                    val name = indicatorParams.keys.toList()[position]
                    rightFields = buildParamFields(rightParamsContainer, name)
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
            comparatorSpinner.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: android.view.View?, position: Int, id: Long) {
                    val isNear = comparators[position] in nearComparators
                    toleranceLabel.visibility = if (isNear) android.view.View.VISIBLE else android.view.View.GONE
                    toleranceInput.visibility = if (isNear) android.view.View.VISIBLE else android.view.View.GONE
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }

            val scrollWrapper = android.widget.ScrollView(this)
            scrollWrapper.addView(dialogLayout)

            AlertDialog.Builder(this)
                .setTitle("Add Condition")
                .setView(scrollWrapper)
                .setPositiveButton("Add") { _, _ ->
                    val leftName = indicatorParams.keys.toList()[leftIndicatorSpinner.selectedItemPosition]
                    val rightName = indicatorParams.keys.toList()[rightIndicatorSpinner.selectedItemPosition]
                    val comparator = comparators[comparatorSpinner.selectedItemPosition]

                    val leftParamsJson = readParamsAsJson(leftFields)
                    val rightParamsJson = readParamsAsJson(rightFields)
                    if (leftParamsJson == null || rightParamsJson == null) {
                        resultText.text = "ERROR: all parameter fields must be valid numbers."
                        return@setPositiveButton
                    }
                    val tolerance = if (comparator in nearComparators) toleranceInput.text.toString() else ""

                    val module = python.getModule("app_bridge")
                    val resultJson = module.callAttr(
                        "build_and_validate_condition_json",
                        leftName, leftParamsJson, comparator, tolerance, rightName, rightParamsJson
                    ).toString()

                    if (resultJson.startsWith("ERROR:")) {
                        resultText.text = resultJson
                    } else {
                        if (conditionJsons.isNotEmpty()) {
                            logicConnectors.add(connectorSpinner.selectedItem as String)
                        }
                        conditionJsons.add(resultJson)
                        refreshConditionsDisplay()
                        resultText.text = "Condition added."
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        clearConditionsButton.setOnClickListener {
            conditionJsons.clear()
            logicConnectors.clear()
            refreshConditionsDisplay()
            resultText.text = "Conditions cleared."
        }

        runScanButton.setOnClickListener {
            if (conditionJsons.isEmpty()) {
                resultText.text = "Add at least one condition first."
                return@setOnClickListener
            }
            resultText.text = "Running scan..."
            try {
                val timeframe = timeframeSpinner.selectedItem as String
                val module = python.getModule("app_bridge")
                val result: PyObject = module.callAttr(
                    "run_ad_hoc_scan_report", dbPath, timeframe, buildFullConditionSetJson()
                )
                resultText.text = result.toString()
            } catch (e: Exception) {
                resultText.text = "ERROR:\n${e.message}"
            }
        }

        saveScanButton.setOnClickListener {
            if (conditionJsons.isEmpty()) {
                resultText.text = "Add at least one condition before saving."
                return@setOnClickListener
            }
            val dialogLayout = LinearLayout(this)
            dialogLayout.orientation = LinearLayout.VERTICAL
            dialogLayout.setPadding(48, 24, 48, 24)

            val nameInput = EditText(this)
            nameInput.hint = "Scan name"
            dialogLayout.addView(nameInput)

            val descInput = EditText(this)
            descInput.hint = "Description (visible even if locked)"
            dialogLayout.addView(descInput)

            val lockedCheckbox = CheckBox(this)
            lockedCheckbox.text = "Locked (hide formula from testers)"
            dialogLayout.addView(lockedCheckbox)

            val trackedCheckbox = CheckBox(this)
            trackedCheckbox.text = "Tracked (include in daily auto-update)"
            dialogLayout.addView(trackedCheckbox)

            AlertDialog.Builder(this)
                .setTitle("Save Scan")
                .setView(dialogLayout)
                .setPositiveButton("Save") { _, _ ->
                    val name = nameInput.text.toString().trim()
                    if (name.isEmpty()) {
                        resultText.text = "ERROR: scan name cannot be empty."
                        return@setPositiveButton
                    }
                    val description = descInput.text.toString()
                    val timeframe = timeframeSpinner.selectedItem as String
                    val module = python.getModule("app_bridge")
                    val result: PyObject = module.callAttr(
                        "save_new_scan_report", dbPath, name, description, timeframe,
                        buildFullConditionSetJson(), lockedCheckbox.isChecked, trackedCheckbox.isChecked
                    )
                    resultText.text = result.toString()
                }
                .setNegativeButton("Cancel", null)
                .show()
        }

        refreshConditionsDisplay()
    }
}

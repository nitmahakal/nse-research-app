package com.nseresearch.app

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        val python = Python.getInstance()

        val resultView = findViewById<TextView>(R.id.resultText)
        val runButton = findViewById<Button>(R.id.runTestButton)

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
    }
}

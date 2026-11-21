package com.sdj.mobilecare.data

/** A newer phone app offered by the PC. [state] is READY once the file is on this phone and checked. */
data class AppUpdate(val version: String, val size: Long, val sha: String, val state: String, val error: String = "", val path: String = "") {
    companion object {
        const val READY = "READY"
        const val LOADING = "LOADING"
        const val FAILED = "FAILED"
    }
}

/** The biggest phone app the PC may hand over (a safety limit). */
const val MAX_APK = 300L * 1024 * 1024

/** "1.5.0" vs "1.4.0": true when [a] is a higher version than [b]. */
fun isNewerVersion(a: String, b: String): Boolean {
    fun parts(v: String) = Regex("\\d+").findAll(v).map { it.value.toIntOrNull() ?: 0 }.take(4).toList().let { it + List(4 - it.size) { 0 } }
    val x = parts(a)
    val y = parts(b)
    for (i in 0 until 4) if (x[i] != y[i]) return x[i] > y[i]
    return false
}

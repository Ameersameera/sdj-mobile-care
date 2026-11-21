package com.sdj.mobilecare.data

import org.json.JSONObject
import java.net.URI

/** Reads the newest release on GitHub: version, phone app file, its SHA-256 and this version's notes. */
object GitHubRelease {
    const val LATEST = "https://api.github.com/repos/Ameersameera/sdj-mobile-care/releases/latest"
    private val APK = Regex("^SDJMobileCare-Phone-(\\d+(?:\\.\\d+){1,3})\\.apk$")

    data class Info(
        val version: String, val notes: String, val date: String, val recommended: Boolean,
        val apkName: String, val apkUrl: String, val apkSha: String, val apkSize: Long,
    )

    /** Only GitHub's own addresses (https) are ever used for updates. */
    fun okUrl(url: String): Boolean {
        if (!url.startsWith("https://")) return false
        val host = runCatching { URI(url).host }.getOrNull()?.lowercase() ?: return false
        return host == "github.com" || host == "api.github.com" || host.endsWith(".githubusercontent.com")
    }

    /** null when the release has no phone app with a checksum (or is a draft / pre-release). */
    fun parse(rel: JSONObject): Info? {
        if (rel.optBoolean("draft") || rel.optBoolean("prerelease")) return null
        val version = rel.optString("tag_name").trimStart('v', 'V')
        val assets = rel.optJSONArray("assets") ?: return null
        for (i in 0 until assets.length()) {
            val a = assets.optJSONObject(i) ?: continue
            val name = a.optString("name")
            if (!APK.matches(name)) continue
            val digest = a.optString("digest")
            val sha = if (digest.startsWith("sha256:") && digest.length == 71) digest.substring(7).lowercase() else return null
            val url = a.optString("browser_download_url")
            if (!okUrl(url)) return null
            val notes = sectionNotes(rel.optString("body"), version)
            return Info(version, cleanNotes(notes), rel.optString("published_at").take(10), isRecommended(notes),
                name, url, sha, a.optLong("size"))
        }
        return null
    }

    /** Only this version's part of the release notes ("### What's new in X" up to the next version or Download). */
    fun sectionNotes(body: String, version: String, limit: Int = 1800): String {
        val lines = body.replace("\r\n", "\n").trim().split("\n")
        val head = Regex("^#{2,4}\\s*What's new in " + Regex.escape(version) + "\\b")
        val start = lines.indexOfFirst { head.containsMatchIn(it.trim()) }
        if (start < 0) return body.trim().take(limit)
        val stop = Regex("^#{2,4}\\s*(What's new in |Download)")
        val out = mutableListOf<String>()
        for (line in lines.drop(start + 1)) {
            if (line.trim().startsWith("#") && stop.containsMatchIn(line.trim())) break
            out += line
        }
        return out.joinToString("\n").trim().take(limit)
    }

    fun isRecommended(notes: String): Boolean = Regex("(?im)^\\W*recommended\\s*:\\s*yes\\b").containsMatchIn(notes)

    fun cleanNotes(notes: String): String = notes.lines()
        .filterNot { Regex("(?i)^\\W*recommended\\s*:").containsMatchIn(it) }
        .joinToString("\n") { it.replace(Regex("^#{1,6}\\s*"), "").replace("**", "").replace("`", "").trimEnd() }
        .trim()
}

package com.goofishz.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.encodeToString
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Goofish-Z 后端 API 客户端。
 * 后端：FastAPI (goofish-z api)，默认 http://127.0.0.1:8787
 * 手机访问需改 host 为电脑局域网 IP（设置页可配）。
 */
class GoofishApi(private val baseUrlProvider: () -> String) {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS) // 搜索走浏览器路径，可能 30-60s
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun base() = baseUrlProvider().trimEnd('/')

    private suspend fun <T> get(path: String, parse: (String) -> T): T = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("${base()}$path")
            .get()
            .build()
        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw ApiException("HTTP ${resp.code}: ${body.take(200)}")
            parse(body)
        }
    }

    private suspend fun <T> post(path: String, body: String, parse: (String) -> T): T = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("${base()}$path")
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        client.newCall(req).execute().use { resp ->
            val respBody = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw ApiException("HTTP ${resp.code}: ${respBody.take(200)}")
            parse(respBody)
        }
    }

    private suspend fun <T> delete(path: String, parse: (String) -> T): T = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("${base()}$path")
            .delete()
            .build()
        client.newCall(req).execute().use { resp ->
            val respBody = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw ApiException("HTTP ${resp.code}: ${respBody.take(200)}")
            parse(respBody)
        }
    }

    // ---- 搜索 ----
    suspend fun search(query: String, limit: Int = 20): SearchResponse =
        get("/api/search?q=${query.urlEncode()}&limit=$limit") { json.decodeFromString(it) }

    // ---- 监控 ----
    suspend fun watchList(): WatchListResponse =
        get("/api/watch") { json.decodeFromString(it) }

    suspend fun watchAdd(keyword: String, maxPrice: Double? = null, minPrice: Double? = null): OkResponse =
        post("/api/watch", json.encodeToString(WatchAddRequest(keyword, maxPrice, minPrice))) { json.decodeFromString(it) }

    suspend fun watchRemove(id: Int): OkResponse =
        delete("/api/watch/$id") { json.decodeFromString(it) }

    suspend fun watchRun(watchId: Int? = null, all: Boolean = true, limit: Int = 10): WatchRunResponse =
        post("/api/watch/run", json.encodeToString(WatchRunRequest(watchId, all, limit))) { json.decodeFromString(it) }

    suspend fun watchStart(watchId: Int? = null, all: Boolean = true, limit: Int = 10): WatchJob =
        post("/api/watch/jobs", json.encodeToString(WatchRunRequest(watchId, all, limit))) { json.decodeFromString(it) }

    suspend fun watchJob(id: String): WatchJob =
        get("/api/watch/jobs/$id") { json.decodeFromString(it) }

    suspend fun watchJobs(): WatchJobsResponse =
        get("/api/watch/jobs") { json.decodeFromString(it) }

    suspend fun watchCancel(id: String): WatchJob =
        delete("/api/watch/jobs/$id") { json.decodeFromString(it) }

    // ---- 黑名单 ----
    suspend fun blacklistList(): BlacklistResponse =
        get("/api/blacklist") { json.decodeFromString(it) }

    suspend fun blacklistAdd(kind: String, value: String, note: String = ""): OkResponse =
        post("/api/blacklist/add", json.encodeToString(BlacklistAddRequest(kind, value, note))) { json.decodeFromString(it) }

    suspend fun blacklistRemove(id: Int): OkResponse =
        post("/api/blacklist/remove", json.encodeToString(BlacklistRemoveRequest(id))) { json.decodeFromString(it) }

    // ---- 信号 ----
    suspend fun signalsList(onlyBanned: Boolean = false): SignalsResponse =
        get("/api/signals/list?only_banned=$onlyBanned") { json.decodeFromString(it) }

    suspend fun signalsUnban(seller: String): OkResponse =
        post("/api/signals/unban", json.encodeToString(SignalsUnbanRequest(seller))) { json.decodeFromString(it) }
}

class ApiException(message: String) : Exception(message)

private fun String.urlEncode(): String =
    java.net.URLEncoder.encode(this, "UTF-8")

package com.goofishz.app.data

import kotlinx.serialization.Serializable

@Serializable
data class WatchAddRequest(
    val keyword: String,
    val max_price: Double? = null,
    val min_price: Double? = null,
)

@Serializable
data class WatchRunRequest(
    val watch_id: Int? = null,
    val all: Boolean = true,
    val limit: Int = 10,
)

@Serializable
data class BlacklistAddRequest(
    val kind: String,
    val value: String,
    val note: String = "",
)

@Serializable
data class BlacklistRemoveRequest(val rule_id: Int)

@Serializable
data class SignalsUnbanRequest(val seller_nick: String)

// ---- 搜索 ----
@Serializable
data class SearchItem(
    val rank: Int = 0,
    val item_id: String = "",
    val title: String = "",
    val price: String = "",
    val condition: String = "",
    val brand: String = "",
    val location: String = "",
    val badge: String = "",
    val url: String = "",
    val _price_flag: String? = null,
    val _noise: String? = null,
    val _cap_mismatch: String? = null,
    val _gen_mismatch: String? = null,
    val _is_system: Boolean = false,
)

@Serializable
data class SearchResponse(
    val items: List<SearchItem> = emptyList(),
    val count: Int = 0,
    val query: String = "",
    val blocked_count: Int = 0,
)

// ---- 监控 ----
@Serializable
data class WatchItem(
    val id: Int = 0,
    val keyword: String = "",
    val max_price: Double? = null,
    val min_price: Double? = null,
    val enabled: Int = 1,
    val created_at: Long = 0,
)

@Serializable
data class WatchListResponse(
    val watches: List<WatchItem> = emptyList(),
)

@Serializable
data class WatchRunResponse(
    val status: String = "",
    val succeeded: Int = 0,
    val failed: Int = 0,
    val skipped: Int = 0,
    val error: String? = null,
    val results: List<WatchRunResult> = emptyList(),
    val ran_at: String = "",
)

@Serializable
data class WatchRunResult(
    val status: String = "",
    val watch_id: Int = 0,
    val keyword: String = "",
    val captured: Int = 0,
    val blocked_count: Int = 0,
    val bargain_count: Int = 0,
    val bargains: List<Bargain> = emptyList(),
    val auto_banned: List<AutoBanned> = emptyList(),
    val alerts: List<Alert> = emptyList(),
    val error: String? = null,
)

@Serializable
data class Bargain(
    val title: String = "",
    val price: String = "",
    val flag: String = "",
)

@Serializable
data class AutoBanned(
    val seller: String = "",
    val score: Int = 0,
)

@Serializable
data class Alert(
    val title: String = "",
    val price: Double = 0.0,
    val reason: String = "",
)

// ---- 黑名单 ----
@Serializable
data class BlacklistRule(
    val id: Int = 0,
    val kind: String = "",
    val value: String = "",
    val note: String = "",
    val enabled: Int = 1,
)

@Serializable
data class BlacklistResponse(
    val rules: List<BlacklistRule> = emptyList(),
)

// ---- 信号引擎 ----
@Serializable
data class SellerProfile(
    val seller_nick: String = "",
    val total_score: Int = 0,
    val appearances: Int = 0,
    val auto_banned: Int = 0,
    val signals_json: String = "{}",
)

@Serializable
data class SignalsResponse(
    val profiles: List<SellerProfile> = emptyList(),
)

// ---- 通用 ----
@Serializable
data class OkResponse(
    val ok: Boolean = false,
    val message: String = "",
)

@Serializable
data class ApiError(
    val detail: String = "",
)

@Serializable
data class WatchProgress(
    val phase: String = "",
    val completed: Int = 0,
    val total: Int = 0,
    val keyword: String = "",
    val retry_after: Double = 0.0,
)

@Serializable
data class WatchJob(
    val id: String,
    val status: String = "queued",
    val progress: WatchProgress = WatchProgress(),
    val result: WatchRunResponse? = null,
    val cancel_requested: Boolean = false,
)

@Serializable
data class WatchJobsResponse(val jobs: List<WatchJob> = emptyList())

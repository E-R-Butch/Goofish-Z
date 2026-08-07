package com.goofishz.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.goofishz.app.data.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class GoofishViewModel(
    private val api: GoofishApi,
) : ViewModel() {

    // ---- 搜索 ----
    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery.asStateFlow()

    private val _searchResult = MutableStateFlow<SearchResponse?>(null)
    val searchResult: StateFlow<SearchResponse?> = _searchResult.asStateFlow()

    private val _searching = MutableStateFlow(false)
    val searching: StateFlow<Boolean> = _searching.asStateFlow()

    // ---- 监控 ----
    private val _watches = MutableStateFlow<List<WatchItem>>(emptyList())
    val watches: StateFlow<List<WatchItem>> = _watches.asStateFlow()

    private val _watchRunning = MutableStateFlow(false)
    val watchRunning: StateFlow<Boolean> = _watchRunning.asStateFlow()

    private val _lastRun = MutableStateFlow<WatchRunResponse?>(null)
    val lastRun: StateFlow<WatchRunResponse?> = _lastRun.asStateFlow()

    // ---- 黑名单 ----
    private val _rules = MutableStateFlow<List<BlacklistRule>>(emptyList())
    val rules: StateFlow<List<BlacklistRule>> = _rules.asStateFlow()

    // ---- 信号 ----
    private val _profiles = MutableStateFlow<List<SellerProfile>>(emptyList())
    val profiles: StateFlow<List<SellerProfile>> = _profiles.asStateFlow()

    // ---- 通用 ----
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    fun setSearchQuery(q: String) { _searchQuery.value = q }

    fun search() {
        val q = _searchQuery.value.trim()
        if (q.isEmpty()) return
        viewModelScope.launch {
            _searching.value = true
            _error.value = null
            try {
                _searchResult.value = api.search(q)
            } catch (e: Exception) {
                _error.value = e.message
            } finally {
                _searching.value = false
            }
        }
    }

    fun loadWatches() {
        viewModelScope.launch {
            try { _watches.value = api.watchList().watches } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun addWatch(keyword: String, maxPrice: Double?, minPrice: Double?) {
        viewModelScope.launch {
            try {
                api.watchAdd(keyword, maxPrice, minPrice)
                loadWatches()
            } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun removeWatch(id: Int) {
        viewModelScope.launch {
            try {
                api.watchRemove(id)
                loadWatches()
            } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun runWatches(all: Boolean = true, watchId: Int? = null) {
        viewModelScope.launch {
            _watchRunning.value = true
            _error.value = null
            try {
                _lastRun.value = api.watchRun(watchId, all)
            } catch (e: Exception) { _error.value = e.message }
            finally { _watchRunning.value = false }
        }
    }

    fun loadRules() {
        viewModelScope.launch {
            try { _rules.value = api.blacklistList().rules } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun addRule(kind: String, value: String, note: String = "") {
        viewModelScope.launch {
            try {
                api.blacklistAdd(kind, value, note)
                loadRules()
            } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun removeRule(id: Int) {
        viewModelScope.launch {
            try {
                api.blacklistRemove(id)
                loadRules()
            } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun loadProfiles(onlyBanned: Boolean = false) {
        viewModelScope.launch {
            _loading.value = true
            try { _profiles.value = api.signalsList(onlyBanned).profiles } catch (e: Exception) { _error.value = e.message }
            finally { _loading.value = false }
        }
    }

    fun unbanSeller(seller: String) {
        viewModelScope.launch {
            try {
                api.signalsUnban(seller)
                loadProfiles()
            } catch (e: Exception) { _error.value = e.message }
        }
    }

    fun clearError() { _error.value = null }
}

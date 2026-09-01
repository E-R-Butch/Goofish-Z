package com.goofishz.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "settings")

class SettingsRepository(private val context: Context) {

    companion object {
        val API_URL_KEY = stringPreferencesKey("api_url")
        const val DEFAULT_API_URL = "http://127.0.0.1:8787"
    }

    val apiUrl: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[API_URL_KEY] ?: DEFAULT_API_URL
    }

    suspend fun setApiUrl(url: String) {
        context.dataStore.edit { it[API_URL_KEY] = url.trim().trimEnd('/') }
    }
}

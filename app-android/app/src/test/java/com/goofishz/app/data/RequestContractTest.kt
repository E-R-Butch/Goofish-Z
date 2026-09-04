package com.goofishz.app.data

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

class RequestContractTest {
    @Test
    fun allWatchesIncludesDefaultValuesInActualHttpBody() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"id":"synthetic-job","status":"queued"}"""))
            val api = GoofishApi { server.url("/").toString() }
            api.watchStart()
            val request = server.takeRequest(2, TimeUnit.SECONDS)!!
            assertEquals("/api/watch/jobs", request.path)
            val body = Json.parseToJsonElement(request.body.readUtf8()).jsonObject
            assertEquals(JsonPrimitive(true), body["all"])
            assertEquals(JsonPrimitive(10), body["limit"])
        }
    }

    @Test
    fun individualWatchExplicitlyDisablesAll() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"id":"synthetic-job"}"""))
            val api = GoofishApi { server.url("/").toString() }
            api.watchStart(watchId = 7, all = false)
            val body = Json.parseToJsonElement(server.takeRequest(2, TimeUnit.SECONDS)!!.body.readUtf8()).jsonObject
            assertEquals(JsonPrimitive(false), body["all"])
            assertEquals(JsonPrimitive(7), body["watch_id"])
        }
    }

    @Test
    fun failedBackgroundRunRemainsVisibleToClient() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"id":"synthetic-job","status":"failed","result":{"status":"failed","failed":1,"results":[{"keyword":"synthetic","error":"synthetic failure"}]}}"""))
            val api = GoofishApi { server.url("/").toString() }
            val result = api.watchJob("synthetic-job").result!!
            assertEquals("failed", result.status)
            assertEquals(1, result.failed)
            assertTrue(result.results.first().error != null)
        }
    }
}

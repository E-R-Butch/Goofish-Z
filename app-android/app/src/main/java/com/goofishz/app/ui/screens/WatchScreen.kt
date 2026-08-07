package com.goofishz.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.goofishz.app.data.Bargain
import com.goofishz.app.data.WatchItem
import com.goofishz.app.data.WatchRunResponse
import com.goofishz.app.ui.GoofishViewModel
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WatchScreen(vm: GoofishViewModel, onOpenHistory: () -> Unit) {
    val watches by vm.watches.collectAsState()
    val running by vm.watchRunning.collectAsState()
    val lastRun by vm.lastRun.collectAsState()
    val error by vm.error.collectAsState()
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) { vm.loadWatches() }

    Column(modifier = Modifier.fillMaxSize()) {
        // 标题行 + 运行按钮
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("价格监控", style = MaterialTheme.typography.headlineMedium)
            Spacer(Modifier.weight(1f))
            Button(
                onClick = { vm.runWatches() },
                enabled = !running && watches.isNotEmpty(),
            ) {
                Icon(Icons.Default.PlayArrow, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text(if (running) "运行中…" else "全部运行")
            }
        }

        // 添加监控项
        var keyword by remember { mutableStateOf("") }
        var maxPrice by remember { mutableStateOf("") }
        AddWatchBar(
            keyword = keyword,
            maxPrice = maxPrice,
            onKeywordChange = { keyword = it },
            onMaxPriceChange = { maxPrice = it },
            onAdd = {
                if (keyword.isNotBlank()) {
                    vm.addWatch(keyword.trim(), maxPrice.toDoubleOrNull(), null)
                    keyword = ""
                    maxPrice = ""
                }
            },
        )

        error?.let {
            Text(
                it,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 16.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(watches, key = { it.id }) { w ->
                WatchCard(
                    watch = w,
                    onRemove = { vm.removeWatch(w.id) },
                    onRun = { vm.runWatches(all = false, watchId = w.id) },
                )
            }

            // 最近运行结果
            lastRun?.let { run ->
                item { Spacer(Modifier.height(8.dp)) }
                item { Text("最近运行", style = MaterialTheme.typography.titleMedium) }
                items(run.results, key = { "${it.watch_id}-${it.keyword}" }) { r ->
                    RunResultCard(r)
                }
            }
        }
    }
}

@Composable
fun AddWatchBar(
    keyword: String,
    maxPrice: String,
    onKeywordChange: (String) -> Unit,
    onMaxPriceChange: (String) -> Unit,
    onAdd: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = keyword,
            onValueChange = onKeywordChange,
            modifier = Modifier.weight(2f),
            placeholder = { Text("关键词") },
            singleLine = true,
        )
        Spacer(Modifier.width(8.dp))
        OutlinedTextField(
            value = maxPrice,
            onValueChange = onMaxPriceChange,
            modifier = Modifier.weight(1f),
            placeholder = { Text("最高价¥") },
            singleLine = true,
            keyboardOptions = androidx.compose.ui.text.input.KeyboardOptions(
                keyboardType = androidx.compose.ui.text.input.KeyboardType.Number
            ),
        )
        Spacer(Modifier.width(8.dp))
        FilledIconButton(onClick = onAdd, enabled = keyword.isNotBlank()) {
            Icon(Icons.Default.Add, contentDescription = "添加")
        }
    }
}

@Composable
fun WatchCard(watch: WatchItem, onRemove: () -> Unit, onRun: () -> Unit) {
    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(watch.keyword, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                val priceText = buildString {
                    if (watch.max_price != null) append("≤¥${watch.max_price}")
                    if (watch.min_price != null) {
                        if (isNotEmpty()) append("  ")
                        append("≥¥${watch.min_price}")
                    }
                    if (isEmpty()) append("无限价")
                }
                Text(
                    priceText,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onRun) {
                Icon(Icons.Default.PlayArrow, contentDescription = "运行")
            }
            IconButton(onClick = onRemove) {
                Icon(
                    Icons.Default.Delete,
                    contentDescription = "删除",
                    tint = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
fun RunResultCard(r: com.goofishz.app.data.WatchRunResult) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    r.keyword,
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                )
                if (r.error != null) {
                    Text("❌", color = MaterialTheme.colorScheme.error)
                } else {
                    Text("✓", color = MaterialTheme.colorScheme.primary)
                }
            }
            Spacer(Modifier.height(4.dp))
            r.error?.let {
                Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            } ?: run {
                Text(
                    "捕获 ${r.captured} · 屏蔽 ${r.blocked_count} · 捡漏 ${r.bargain_count} · 自动拉黑 ${r.auto_banned.size}",
                    style = MaterialTheme.typography.bodyMedium,
                )
                // 捡漏列表
                r.bargains.forEach { b ->
                    Spacer(Modifier.height(4.dp))
                    Row {
                        Text("💎 ", style = MaterialTheme.typography.bodyMedium)
                        Text(
                            b.price,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            b.title.take(24),
                            style = MaterialTheme.typography.bodyMedium,
                            maxLines = 1,
                            overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                        )
                    }
                }
                // 自动拉黑
                r.auto_banned.forEach { a ->
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "🔒 自动拉黑 ${a.seller} (${a.score}分)",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

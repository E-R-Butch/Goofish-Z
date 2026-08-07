package com.goofishz.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.goofishz.app.data.SearchItem
import com.goofishz.app.ui.GoofishViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(vm: GoofishViewModel) {
    val query by vm.searchQuery.collectAsState()
    val result by vm.searchResult.collectAsState()
    val searching by vm.searching.collectAsState()
    val error by vm.error.collectAsState()

    Column(modifier = Modifier.fillMaxSize()) {
        // 搜索栏
        OutlinedTextField(
            value = query,
            onValueChange = { vm.setSearchQuery(it) },
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            placeholder = { Text("搜闲鱼：如 DDR3 RECC 32G / CMP 90HX") },
            leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
            singleLine = true,
            trailingIcon = {
                Button(
                    onClick = { vm.search() },
                    enabled = !searching && query.isNotBlank(),
                ) {
                    Text(if (searching) "搜索中…" else "搜索")
                }
            },
        )

        // 错误提示
        error?.let {
            Text(
                it,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 16.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
        }

        if (searching) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
        } else {
            result?.let { r ->
                // 统计行
                Row(
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "${r.count} 条结果",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    if (r.blocked_count > 0) {
                        Spacer(Modifier.width(8.dp))
                        AssistChip(
                            onClick = {},
                            label = { Text("已屏蔽 ${r.blocked_count} 条劣质") },
                        )
                    }
                }

                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(r.items, key = { it.item_id }) { item ->
                        SearchItemCard(item)
                    }
                }
            }
        }
    }
}

@Composable
fun SearchItemCard(item: SearchItem) {
    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    item.price.ifBlank { "价格面议" },
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.weight(1f))
                // 捡漏标记
                item._price_flag?.let { flag ->
                    SuggestionChip(
                        onClick = {},
                        label = { Text(flag, style = MaterialTheme.typography.labelSmall) },
                    )
                }
                if (item._is_system) {
                    Spacer(Modifier.width(4.dp))
                    SuggestionChip(onClick = {}, label = { Text("整机") })
                }
            }
            Spacer(Modifier.height(4.dp))
            Text(
                item.title,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 3,
            )
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (item.badge.isNotBlank()) {
                    Text(
                        item.badge,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.secondary,
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text(
                    item.location.ifBlank { "未知地区" },
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    "id: ${item.item_id}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.outline,
                )
            }
            // 污染标记（若有）
            val mismatch = item._cap_mismatch ?: item._gen_mismatch
            if (mismatch != null) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "⚠️ $mismatch",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

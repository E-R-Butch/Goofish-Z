package com.goofishz.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Gavel
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.goofishz.app.data.BlacklistRule
import com.goofishz.app.data.SellerProfile
import com.goofishz.app.ui.GoofishViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BlacklistScreen(vm: GoofishViewModel) {
    val rules by vm.rules.collectAsState()
    val profiles by vm.profiles.collectAsState()
    val error by vm.error.collectAsState()

    LaunchedEffect(Unit) {
        vm.loadRules()
        vm.loadProfiles(onlyBanned = true)
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Text(
            "黑名单与自动屏蔽",
            style = MaterialTheme.typography.headlineMedium,
            modifier = Modifier.padding(16.dp),
        )

        // 添加规则
        var kind by remember { mutableStateOf("seller_nick") }
        var value by remember { mutableStateOf("") }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            FilterChip(
                selected = kind == "seller_nick",
                onClick = { kind = "seller_nick" },
                label = { Text("卖家") },
            )
            Spacer(Modifier.width(4.dp))
            FilterChip(
                selected = kind == "title_keyword",
                onClick = { kind = "title_keyword" },
                label = { Text("标题词") },
            )
            Spacer(Modifier.width(4.dp))
            FilterChip(
                selected = kind == "location",
                onClick = { kind = "location" },
                label = { Text("地区") },
            )
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = { value = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text(if (kind == "seller_nick") "卖家昵称，如：北冥有鱼" else "关键词 / 地区名") },
                singleLine = true,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = {
                    if (value.isNotBlank()) {
                        vm.addRule(kind, value.trim())
                        value = ""
                    }
                },
                enabled = value.isNotBlank(),
            ) {
                Icon(Icons.Default.Gavel, contentDescription = null)
                Spacer(Modifier.width(4.dp))
                Text("拉黑")
            }
        }

        error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp))
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            item { Text("手动规则", style = MaterialTheme.typography.titleMedium) }
            if (rules.isEmpty()) {
                item { Text("无规则", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            items(rules, key = { it.id }) { rule ->
                RuleCard(rule, onRemove = { vm.removeRule(rule.id) })
            }

            item { Spacer(Modifier.height(12.dp)) }
            item { Text("自动拉黑（信号引擎）", style = MaterialTheme.typography.titleMedium) }
            if (profiles.isEmpty()) {
                item { Text("暂无自动拉黑", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            items(profiles, key = { it.seller_nick }) { p ->
                ProfileCard(p, onUnban = { vm.unbanSeller(p.seller_nick) })
            }
        }
    }
}

@Composable
fun RuleCard(rule: BlacklistRule, onRemove: () -> Unit) {
    val kindLabel = when (rule.kind) {
        "seller_nick" -> "卖家"
        "title_keyword" -> "标题词"
        "location" -> "地区"
        "no_badge" -> "无信用标识"
        "price_drop" -> "反复降价"
        "price_anomaly" -> "低价异常"
        else -> rule.kind
    }
    ElevatedCard(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SuggestionChip(onClick = {}, label = { Text(kindLabel) })
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(rule.value, style = MaterialTheme.typography.bodyLarge)
                if (rule.note.isNotBlank()) {
                    Text(
                        rule.note,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            IconButton(onClick = onRemove) {
                Icon(Icons.Default.Delete, contentDescription = "删除", tint = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
fun ProfileCard(profile: SellerProfile, onUnban: () -> Unit) {
    ElevatedCard(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(profile.seller_nick, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(2.dp))
                Text(
                    "${profile.total_score} 分 · ${profile.appearances} 次出现",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            TextButton(onClick = onUnban) { Text("解除") }
        }
    }
}

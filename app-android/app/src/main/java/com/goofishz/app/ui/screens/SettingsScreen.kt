package com.goofishz.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.goofishz.app.data.SettingsRepository
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(settings: SettingsRepository) {
    val scope = rememberCoroutineScope()
    var apiUrl by remember { mutableStateOf("") }
    var saved by remember { mutableStateOf(false) }

    // 读取当前值
    LaunchedEffect(Unit) {
        apiUrl = settings.apiUrl.first()
    }

    Column(modifier = Modifier
        .fillMaxSize()
        .padding(16.dp)) {
        Text("设置", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(16.dp))

        OutlinedTextField(
            value = apiUrl,
            onValueChange = {
                apiUrl = it
                saved = false
            },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("后端 API 地址") },
            placeholder = { Text(SettingsRepository.DEFAULT_API_URL) },
            supportingText = {
                Text("电脑上跑 goofish-z api 后填 http://<电脑局域网IP>:8787")
            },
            singleLine = true,
        )
        Spacer(Modifier.height(12.dp))

        Button(
            onClick = {
                scope.launch {
                    settings.setApiUrl(apiUrl)
                    saved = true
                }
            },
            enabled = apiUrl.isNotBlank(),
        ) {
            Text("保存")
        }
        if (saved) {
            Spacer(Modifier.height(8.dp))
            Text("已保存", color = MaterialTheme.colorScheme.primary)
        }

        Spacer(Modifier.height(32.dp))
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp)) {
                Text("关于", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(4.dp))
                Text("Goofish-Z — 最终的也是唯一的闲鱼客户端")
                Text("Material Design 3 · 对接 goofish-z 后端")
                Spacer(Modifier.height(4.dp))
                Text("v0.1.0", style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

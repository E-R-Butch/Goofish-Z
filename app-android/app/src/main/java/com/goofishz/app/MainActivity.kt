package com.goofishz.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.MonitorHeart
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.viewmodel.compose.viewModel
import com.goofishz.app.data.GoofishApi
import com.goofishz.app.data.SettingsRepository
import com.goofishz.app.ui.GoofishViewModel
import com.goofishz.app.ui.screens.*
import com.goofishz.app.ui.theme.GoofishZTheme
import kotlinx.coroutines.flow.first

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            GoofishZTheme {
                GoofishZApp()
            }
        }
    }
}

private data class Tab(
    val label: String,
    val icon: ImageVector,
)

private val tabs = listOf(
    Tab("搜索", Icons.Default.Search),
    Tab("监控", Icons.Default.MonitorHeart),
    Tab("黑名单", Icons.Default.Block),
    Tab("设置", Icons.Default.Settings),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GoofishZApp() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val settings = remember { SettingsRepository(context.applicationContext) }

    // API URL 从设置读取
    var apiUrl by remember { mutableStateOf(SettingsRepository.DEFAULT_API_URL) }
    LaunchedEffect(Unit) { apiUrl = settings.apiUrl.first() }

    val api = remember(apiUrl) { GoofishApi { apiUrl } }
    val vm: GoofishViewModel = viewModel(factory = androidx.lifecycle.viewmodel.ViewModelProvider.Factory {
        GoofishViewModel(api)
    })

    var selectedTab by remember { mutableStateOf(0) }

    Scaffold(
        bottomBar = {
            NavigationBar {
                tabs.forEachIndexed { index, tab ->
                    NavigationBarItem(
                        selected = selectedTab == index,
                        onClick = { selectedTab = index },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { innerPadding ->
        Box(modifier = Modifier
            .fillMaxSize()
            .padding(innerPadding)) {
            when (selectedTab) {
                0 -> SearchScreen(vm)
                1 -> WatchScreen(vm, onOpenHistory = {})
                2 -> BlacklistScreen(vm)
                3 -> SettingsScreen(settings)
            }
        }
    }
}

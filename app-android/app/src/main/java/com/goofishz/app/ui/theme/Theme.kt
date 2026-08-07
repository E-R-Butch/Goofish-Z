package com.goofishz.app.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// 闲鱼橙 (#FF5000) 品牌色 + Material 3 tonal 扩展
private val LightColors = lightColorScheme(
    primary = Color(0xFFB43C00),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFFDBCB),
    onPrimaryContainer = Color(0xFF3A0B00),
    secondary = Color(0xFF77574B),
    secondaryContainer = Color(0xFFFFDBCB),
    onSecondaryContainer = Color(0xFF2C160D),
    tertiary = Color(0xFF6B5D2F),
    background = Color(0xFFFFF8F6),
    surface = Color(0xFFFFF8F6),
    surfaceVariant = Color(0xFFF5DED5),
    error = Color(0xFFBA1A1A),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFFFB596),
    onPrimary = Color(0xFF5E1B00),
    primaryContainer = Color(0xFF882B00),
    onPrimaryContainer = Color(0xFFFFDBCB),
    secondary = Color(0xFFE7BDAE),
    secondaryContainer = Color(0xFF5D3F34),
    onSecondaryContainer = Color(0xFFFFDBCB),
    tertiary = Color(0xFFD6C18A),
    background = Color(0xFF20130D),
    surface = Color(0xFF20130D),
    surfaceVariant = Color(0xFF53433C),
    error = Color(0xFFFFB4AB),
)

@Composable
fun GoofishZTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true, // Android 12+ 动态取色
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content,
    )
}

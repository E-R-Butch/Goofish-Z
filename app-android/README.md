# Goofish-Z Android App

Material Design 3 安卓客户端 — 对接 Goofish-Z 后端（FastAPI :8787）。

## 功能

| 页面 | 功能 |
|---|---|
| 搜索 | 搜闲鱼 + 三层过滤（收购帖/容量/代数）+ 捡漏标记 + 污染提示 |
| 监控 | watch 价格监控：添加/删除/运行 + 捡漏候选 + 自动拉黑报告 |
| 黑名单 | 手动规则（卖家/标题词/地区）+ 信号引擎自动拉黑 + 误判解除 |
| 设置 | 后端 API 地址（电脑局域网 IP） |

## 构建

需要 Android Studio (Ladybug 或更新) / JDK 17：
1. Android Studio 打开 `app-android/` 目录
2. 等 Gradle 同步完成（首次下载依赖较慢）
3. Run ▶ 到设备/模拟器

## 连接后端

1. 电脑上启动 Goofish-Z API：
   ```bash
   cd ~/Documents/Projects/Goofish-Z
   .venv/bin/goofish-z api   # 或 python -m goofish_z.api.app
   ```
2. 手机与电脑同一局域网
3. App 设置页填 `http://<电脑局域网IP>:8787`
4. 注意：Android 9+ 明文 HTTP 需 usesCleartextTraffic（已开启）

## 技术栈

- Kotlin + Jetpack Compose (BOM 2024.09)
- Material 3（动态取色，Android 12+ 跟随壁纸）
- OkHttp + kotlinx.serialization
- DataStore 存设置
- Navigation 底部导航 4 Tab

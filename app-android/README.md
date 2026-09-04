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
1. `git clone https://github.com/E-R-Butch/Goofish-Z.git`
2. Android Studio 打开 `Goofish-Z/app-android/` 目录
3. 等 Gradle 同步完成（首次下载依赖较慢）
4. Run ▶ 到设备/模拟器

## 连接后端

1. 电脑上启动 Goofish-Z API：
   ```bash
   cd Goofish-Z
   python3 -m venv .venv
   .venv/bin/pip install -e .
   .venv/bin/python -m goofish_z.api.app
   ```
2. USB 调试设备可执行 `adb reverse tcp:8787 tcp:8787`，App 保持默认
   `http://127.0.0.1:8787`，无需把 API 暴露到局域网。
3. 若确需局域网连接，只在受信任网络中显式配置监听地址和访问控制；不要暴露公网。
4. Android 9+ 明文 HTTP 需 usesCleartextTraffic（开发配置已开启）。

App 只调用本机 Goofish-Z API。账号、商品、Cookie、Token、会话和缓存都属于运行期
本地数据，不进入 Android 源码或 GitHub；测试和截图也只能使用合成数据。

## 技术栈

- Kotlin + Jetpack Compose (BOM 2024.09)
- Material 3（动态取色，Android 12+ 跟随壁纸）
- OkHttp + kotlinx.serialization
- DataStore 存设置
- Navigation 底部导航 4 Tab


## 0.2.0 监控任务

监控通过 `/api/watch/jobs` 提交，客户端轮询进度并显示限流等待、失败和取消状态。
“刷新进度”可重新连接最近任务；取消不会重试正在进行的请求。请求显式编码默认值，
确保全部运行时发送 `all=true`、单项运行时发送 `all=false` 和 `watch_id`。

使用 JDK 17、Gradle 8.13 与 Android SDK 35 验证：

```bash
gradle --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

单元测试通过 MockWebServer 检查真实 HTTP 请求体和失败任务的解析，不连接闲鱼。

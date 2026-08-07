# goofish-z — 闲鱼全功能整合包

> 博采众长：goofish-cli（CLI/MCP/registry 架构）+ XianYuApis（refresh_token 自动维持）
> + ai-goofish-monitor（监控/UI 思路）。一库双驱：GUI 可点，Agent 可调。

## 设计目标

1. **双驱动**：同一套命令层，同时暴露给
   - GUI：本地 Web 面板（搜索/监控/消息/商品管理）
   - Agent：MCP（Hermes 原生）+ CLI（terminal）+ HTTP API（curl/脚本）
2. **认证自愈**：token 过期自动刷新（移植 XianYuApis refresh_token），7×24 不掉线
3. **价格监控**：定时搜索 + SQLite 历史落盘 + 变更告警（生态空白点）

## 架构分层

```
┌─────────────────────────────────────────────────────────┐
│                     双驱动入口                            │
│   GUI (Web面板)        Agent (MCP / CLI / HTTP API)      │
└───────────┬─────────────────────────┬────────────────────┘
            │                         │
┌───────────▼─────────────────────────▼────────────────────┐
│  api/  FastAPI 层                                        │
│  /api/search /api/watch /api/message /api/item           │
│  /api/auth  + 静态前端挂载                                │
└───────────┬──────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────┐
│  commands/  命令层 (每个命令 = registry 注册, 三通道共享)  │
│  auth/ item/ search/ message/ watch/ media/ location/    │
└───────────┬──────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────┐
│  core/  核心层                                           │
│  sign.py     签名 (execjs 桥接 goofish_js_version_2.js)  │
│  session.py  会话 + refresh_token 自动维持 (XianYuApis)  │
│  limiter.py  写操作令牌桶 (1/min)                        │
│  guard.py    风控熔断                                    │
│  registry.py 命令注册表 (CLI+MCP+API 三通道自动注册)      │
└───────────┬──────────────────────────────────────────────┘
            │
┌───────────▼──────────────────────────────────────────────┐
│  db.py  SQLite: watch_items / price_history / messages   │
└──────────────────────────────────────────────────────────┘
```

## 命令 → 三通道映射

| 命令 | CLI | MCP | API | 说明 |
|---|---|---|---|---|
| auth login/status | ✅ | ✅ | POST /api/auth | Chrome cookie 探测 + QR |
| search items | ✅ | ✅ | GET /api/search | 商品搜索 |
| watch add/list/rm | ✅ | ✅ | GET/POST /api/watch | 价格监控 |
| watch history | ✅ | ✅ | GET /api/watch/history | 价格曲线 |
| message list/send | ✅ | ✅ | GET/POST /api/message | 消息 |
| item get/publish | ✅ | ✅ | GET/POST /api/item | 商品 |

## 认证自愈机制（核心创新）

```
请求 → 401/FAIL_SYS_TOKEN_EXOIRED
     → session.refresh_token()   # 用 _m_h5_tk 前半段重签
     → 成功: 更新 cookie 重试原请求
     → 失败: 尝试 Chrome cookie 探测
     → 再失败: 返回 AuthRequiredError, 等 GUI/Agent 干预
```

## 技术选型

- Python 3.11 (uv 管理，避免 Homebrew python 升级断链)
- typer CLI + FastMCP (MCP) + FastAPI (API/GUI)
- execjs + goofish_js_version_2.js（与上游同源，MD5 已验证一致）
- SQLite（零依赖落盘）
- 前端：单页原生 JS（无构建步骤，FastAPI 静态挂载）

## 项目结构

```
goofish-z/
├── pyproject.toml
├── src/goofish_z/
│   ├── core/       # sign/session/limiter/guard/registry
│   ├── commands/   # auth/item/search/message/watch/media/location
│   ├── api/        # FastAPI app + routes
│   ├── gui/        # 静态前端
│   ├── db.py
│   ├── cli.py      # typer 入口
│   └── mcp_server.py
├── static/goofish_js_version_2.js
└── data/           # cookies.json + watch.db
```

## 里程碑

1. M1: 骨架 + core 移植 (sign/session/refresh_token) + CLI 跑通
2. M2: watch 监控模块 + SQLite 落盘 + 告警
3. M3: FastAPI + GUI 面板
4. M4: MCP 接入 Hermes + 端到端验证

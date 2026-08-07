# goofish-z

闲鱼全功能整合包 — **GUI + Agent 双驱动**。

博采众长：goofish-cli（CLI/MCP/registry 架构）+ XianYuApis（refresh_token 自动维持）+ ai-goofish-monitor（监控/UI 思路）。

## 三通道

| 通道 | 入口 | 状态 |
|---|---|---|
| CLI | `goofish-z <ns> <cmd>` | ✅ |
| HTTP API | `python -m goofish_z.api.app` → :8787 | ✅ |
| MCP | `goofish-z-mcp` | ✅ (watch 通；search 见已知问题) |

## 快速开始

```bash
# 安装
cd ~/Documents/Projects/goofish-z
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -e .

# 认证（从 Chrome 自动抓 cookie，或复制 ~/.goofish-cli/cookies.json 到 ~/.goofish-z/）
mkdir -p ~/.goofish-z && cp ~/.goofish-cli/cookies.json ~/.goofish-z/ 2>/dev/null

# CLI
.venv/bin/goofish-z search items "DDR3 RECC 32G" --limit 5

# 价格监控
.venv/bin/goofish-z watch add "DDR3 RECC 32G" --max-price 160
.venv/bin/goofish-z watch run --all
.venv/bin/goofish-z watch history 1

# GUI
.venv/bin/python -m goofish_z.api.app  # → http://127.0.0.1:8787
```

## 架构

```
GUI (Web面板)        Agent (MCP / CLI / HTTP API)
      └──────────┬──────────┘
                 ▼
commands/  命令层 — registry 注册, 三通道共享
core/      签名(execjs+goofish_js_version_2.js) / 会话+refresh_token自愈 / 限流 / 熔断
db.py      SQLite: watch_items / price_history / alerts
```

认证自愈：请求 → 401 → `refresh_token()` 重签重试 → Chrome cookie 探测 → AuthRequiredError。

## 已知问题

- **MCP 里 search.items 卡住**：search 走 playwright 系统 Chrome（launch_persistent_context），在 MCP 的 to_thread 线程环境无法启动。CLI/HTTP 正常。解决方向：给 browser.py 加线程安全启动，或 search 改用 HTTP API 代理。
- Homebrew python 3.11/3.14 有 `platform._syscmd_file` decode bug，直接跑 goofish auth 会炸；本项目 CLI 已绕过（browser-cookie3 路径不触发）。

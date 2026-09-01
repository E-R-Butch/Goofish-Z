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
# 克隆并安装
git clone https://github.com/E-R-Butch/Goofish-Z.git
cd Goofish-Z
python3 -m venv .venv
.venv/bin/pip install -e .

# 认证（从 Chrome 自动抓 cookie，或复制 ~/.goofish-cli/cookies.json 到 ~/.goofish-z/）
mkdir -p ~/.goofish-z && cp ~/.goofish-cli/cookies.json ~/.goofish-z/ 2>/dev/null

# CLI
.venv/bin/goofish-z search items "示例商品" --limit 5

# 本人发布的商品（默认只返回仍在线的商品）
.venv/bin/goofish-z item mine --limit 50
.venv/bin/goofish-z item mine --status sold --limit 50
.venv/bin/goofish-z item mine --status all --limit 200 --format json

# 价格监控
.venv/bin/goofish-z watch add "示例商品" --max-price 100
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

`item mine` 直接读取个人主页列表接口的 `itemStatus`，不会把历史已售商品误判为在售；
CLI、MCP（工具名 `item.mine`）和 HTTP API（`GET /api/item/mine`）共享同一实现。
本机网站等只读消费者可通过 `GET /api/item/get?item_id=<商品ID>` 按需读取详情。
该 HTTP 路由只返回消费方白名单字段，不返回底层原始响应或卖家账号字段。

## 代码与运行期数据边界

本公开仓库只保存通用工具代码、文档和合成测试数据。账号、Cookie、Token、会话、
商品缓存、导出文件、价格历史和 SQLite 数据库都属于运行期本地数据，不得提交。

- 默认运行目录是 `~/.goofish-z/`，也可通过 `GOOFISH_Z_DATA` 指向仓库外目录；
  指向源码仓库内部会直接拒绝启动。Cookie、设备状态、限流/熔断状态和监控数据库
  都使用同一个运行目录。
- 仓库根目录下的 `data/`、`exports/` 和 `local/` 仅供本机使用，已被 Git 忽略。
- 网站或其他本地消费者应通过 localhost 的只读 HTTP 接口调用本工具，不应把业务
  JSON、商品图片或账号响应复制回本仓库。
- 测试和示例必须使用明显的合成数据；提交前运行 `python scripts/check_public_repo.py`。

## 已知问题

- **MCP 里 search.items 卡住**：search 走 playwright 系统 Chrome（launch_persistent_context），在 MCP 的 to_thread 线程环境无法启动。CLI/HTTP 正常。解决方向：给 browser.py 加线程安全启动，或 search 改用 HTTP API 代理。
- Homebrew python 3.11/3.14 有 `platform._syscmd_file` decode bug，直接跑 goofish auth 会炸；本项目 CLI 已绕过（browser-cookie3 路径不触发）。

# Goofish-Z

面向 PC 与 Agent 的闲鱼工具：通过 Web 监控台、CLI、MCP 和 HTTP API 使用同一套
搜索、详情、监控与告警能力。

当前优先维护 PC + Agent 使用体验。Android 客户端源码保留在 `app-android/`，
APK 工作链迁移与进一步升级暂缓。

## 0.2.0 更新

- HTTP 监控通过工作线程执行；批量搜索按限流等待，登录失效或风控时停止本轮。
- Web / Agent 共享后台监控任务，可查看进度、等待状态和取消本轮。
- 发布、删除和消息发送共享写操作限额；限流和熔断状态采用文件锁与原子写入。
- 告警按真实价格观测去重，支持已读；自动屏蔽状态参与搜索和监控过滤。
- 校验 HTTP 请求参数，修正消息列表命令名与 Android 默认值序列化。
- 网页使用文本节点展示外部字段，拒绝可执行 URL 协议。
- 新增本地 `doctor`，安装声明包含 Playwright 和文件锁依赖。
- MCP 升级至 2.1.1+，验证新旧协议连接、结构化结果和普通输出隔离。
- 签名模块的 UTF-8 设置限定在 JavaScript 桥接内，避免影响 MCP 导入和其他子进程。
- 搜索遇到本地限流时显示剩余秒数，等待后自动重试一次；状态栏显示 API 版本。

## 安装和启动

需要 Python 3.11+、Node.js 和系统 Google Chrome。搜索使用系统 Chrome，
默认需要可显示窗口的桌面环境；不会使用用户正在打开的 Chrome profile。
先在自己的 Chrome 中打开 [闲鱼](https://www.goofish.com) 并完成登录，再导入登录态：

```bash
git clone https://github.com/E-R-Butch/Goofish-Z.git
cd Goofish-Z
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/goofish-z doctor
.venv/bin/goofish-z auth login --browser chrome
.venv/bin/goofish-z auth status
.venv/bin/python -m goofish_z.api.app
```

Web 面板为 `http://127.0.0.1:8787`。MCP 入口是 `.venv/bin/goofish-z-mcp`。
首次登录或会话失效可能需要人工登录；自动刷新只做有限恢复，不保证永久在线。
本版 MCP 已迁移到 2.1.1+ 的 `MCPServer`：支持新旧协议客户端，stdio 隔离可防止
命令和子进程的普通输出污染协议流，见 [SDK 发布说明](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)。

`auth login --browser chrome` 读取本机 Chrome 的闲鱼相关 Cookie 并保存到外部运行
目录；`auth status` 才会请求闲鱼验证当前会话。Cookie 文件存在本身不代表登录有效。
需要其他导入方式时运行 `auth login --help`。

## 接入 Agent

在支持 stdio 的 MCP 客户端中配置入口，将下面的路径替换为实际安装目录的绝对路径：

```json
{
  "mcpServers": {
    "goofish-z": {
      "command": "/absolute/path/Goofish-Z/.venv/bin/goofish-z-mcp",
      "env": {
        "GOOFISH_Z_HTTP": "http://127.0.0.1:8787"
      }
    }
  }
}
```

`search.items`、`item.get` 和 `auth.doctor` 等工具可直接调用。后台监控的
`watch.start / job / jobs / cancel` 需要先启动上面的 HTTP 服务，与 PC 网页共享任务。
如果配置了 `GOOFISH_Z_DATA`，HTTP、CLI 和 MCP 应指向同一个仓库外运行目录。

## 常用命令

```bash
.venv/bin/goofish-z search items "示例商品" --limit 5
.venv/bin/goofish-z item mine --status online --limit 50
.venv/bin/goofish-z item mine --status all --limit 200 --format json
.venv/bin/goofish-z watch add "示例商品" --max-price 100
.venv/bin/goofish-z watch run --all --format json
.venv/bin/goofish-z watch alerts --unread-only --format json
.venv/bin/goofish-z watch read-alert 1
```

PC 与 Agent 的长批次监控建议走后台任务。先启动 HTTP API，再通过 CLI 或同名
MCP 工具提交、查询或取消；这组命令与网页共享任务，查询进度不会重新搜索：

```bash
.venv/bin/goofish-z watch start --all --format json
.venv/bin/goofish-z watch jobs --format json
.venv/bin/goofish-z watch job TASK_ID --format json
.venv/bin/goofish-z watch cancel TASK_ID --format json
```

MCP 工具名为 `watch.start`、`watch.jobs`、`watch.job`、`watch.cancel`。本地服务
地址默认 `http://127.0.0.1:8787`，可用 `GOOFISH_Z_HTTP` 配置。

搜索默认每 30 秒允许一次，网页、CLI、Agent 和后台监控共用同一限额。网页收到
带 `Retry-After` 的 429 响应后，会显示倒计时并自动重试一次；等待中不会重复提交。
如果仍被限流，页面会提示剩余时间并恢复操作。登录失效或风控错误不会自动重试。

`max_price` 是低价告警线（价格小于或等于时告警），`min_price` 是高价告警线
（价格大于或等于时告警）；两者是独立触发条件。相同价格持续满足相同条件不会重复
告警，价格改变或观察到离开条件后再次满足才产生新事件。搜索中未出现某商品不能
证明其已售出，因此不会据此清除该商品的告警状态。

自动屏蔽按现有卖家信号规则生效；没有卖家标识时不会猜测身份。卖家昵称补查仍需
显式启用 `--enrich-sellers`，可能产生额外请求。低价本身只作展示标记；卖家自动
评分与人工规则的结果均可通过命中原因检查。`signals unban` 清空该卖家的累计信号。

## HTTP 契约

| 功能 | 接口 |
| --- | --- |
| 服务存活 | `GET /health` |
| 本地诊断 | `GET /api/diagnostics` |
| 搜索 | `GET /api/search?q=...&limit=20` |
| 本人商品 | `GET /api/item/mine?status=online&limit=50` |
| 商品详情 | `GET /api/item/get?item_id=...` |
| 消息会话 | `GET /api/message/chats` |
| 添加 / 列出监控 | `POST /api/watch` / `GET /api/watch` |
| 同步运行 | `POST /api/watch/run`，传 `{"all":true}` 或 `{"watch_id":1}` |
| 后台运行 | `POST /api/watch/jobs`，同上请求体，返回 202 与任务 ID |
| 任务进度 | `GET /api/watch/jobs/{id}` / `GET /api/watch/jobs` |
| 取消任务 | `DELETE /api/watch/jobs/{id}` |
| 实际告警 | `GET /api/alerts?unread_only=true` |
| 告警已读 | `POST /api/alerts/{id}/read` |

同步运行成功返回 200，部分失败返回 207，全部失败返回 502；结果同时包含
`status`、`succeeded`、`failed`、`skipped` 和每项错误。CLI 对失败或部分失败返回
非零退出码；MCP 的全部失败以工具错误返回。限流响应使用 429 和 `Retry-After`。

每个 API 进程只接受一个活动后台任务；重复提交返回 409。限流等待可立即取消，
正在进行的浏览器请求会在结束后检查取消状态。任务进度保存在内存，服务重启后不
自动重跑；历史与告警保存在 SQLite。此版本请使用单个 API worker 以便一致查询任务。

`/health` 仅代表进程可响应。`doctor` 和 `/api/diagnostics` 不访问闲鱼、不读取
Cookie 内容，也不自动刷新登录态；其登录结果只是最近一次 `auth status` 的验证
记录，必须结合验证时间判断。运行 `auth status` 会执行真实账号验证。

## 代码与运行数据边界

公开仓库只保存通用代码、文档和合成测试。账号、Cookie、Token、设备状态、浏览器
临时 profile、限流/熔断状态、商品缓存、价格历史、SQLite 数据库都使用仓库外的
`~/.goofish-z/`，或 `GOOFISH_Z_DATA` 指定的外部目录。源码目录作为运行目录会被拒绝。

旧数据库首次使用时增补告警状态与已读字段，保留既有监控、价格历史和告警。
升级前可备份外部运行目录，使用 `pip install -e .` 安装新增依赖后再重启服务。

本机网站等消费者通过 localhost HTTP API 读取商品，详情接口只返回白名单字段。
实际发布、删除、回复、改价与交易必须按操作者明确指令执行。当前未提供经验证的
编辑、改价或上下架命令；不应以发布新商品替代编辑。

## 验证

0.2.0 已在全新 Python 3.14 环境通过 **56 项 Python 测试、9 项网页测试**，依赖检查
无冲突。CI 配置覆盖 Python 3.11 和 3.14。可在本地复现离线测试：

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/gui.test.js
.venv/bin/python scripts/check_public_repo.py
```

测试使用临时目录与合成数据，覆盖 API/MCP 调用、限流竞争、熔断、取消、告警去重、
旧数据库迁移、网页文本渲染，以及 MCP 新旧协议和命令/子进程输出隔离。
Android 请求契约测试见 `app-android/`。
离线测试不等同于真实闲鱼登录、搜索或交易验证；平台 DOM 与登录流程变化仍需实测。

### PC + Agent 实测记录

2026-09-04，在 macOS、系统 Chrome、Python 3.14 与 MCP SDK 2.1.1 环境下，
导入已登录的 Chrome 会话，对 0.2.0 做了一次只读抽样验证：

| 检查项 | 结果 |
| --- | --- |
| Chrome 登录态导入与账号验证 | 通过 |
| PC HTTP 搜索 | 限量返回 3 条商品，标题与价格字段完整 |
| MCP 连接与本地诊断 | 加载 37 个工具，返回结构化诊断与最近验证状态 |
| Agent 商品详情 | 成功读取搜索所得商品，商品标识一致，标题与价格完整 |
| PC / Agent 任务查询 | 两端返回相同任务列表 |

本次查询未触发登录错误或风控提示。真实后台监控长时间运行、交易与商品写入不在
本次实测范围内；账号、Cookie、商品返回内容及浏览器缓存不随代码发布。

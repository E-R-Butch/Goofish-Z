# Goofish-Z

> 项目边界：Goofish-Z 维护闲鱼工作台、搜索、商品、监控及 CLI/MCP/HTTP 接口。跨平台比价已拆为独立的 [PriceRadar](https://github.com/E-R-Butch/PriceRadar)；淘宝、京东、拼多多采价在该项目维护。旧 `feat/readonly-market-prices` 分支仅为拆分前历史，不是本项目后续开发入口。

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
- 工作台显示每页最多 30 条，支持翻页、当前页价格/标题/地区筛选和价格排序。
- 修复搜索价格漏读“万”单位；统一换算为元，保留页面原文，供 PC、Agent 和监控使用。
- Chrome/文件导入保留 Cookie 的域名、路径和有效期，避免同名淘宝凭证覆盖闲鱼登录态；搜索可等待登录过程的页面跳转后读取。
- 搜索数量旁增加垃圾桶入口，展开可查看自动过滤、屏蔽规则和当前筛选隐藏的商品及具体原因。

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
浏览器导入按名称、域名和路径保留 Cookie；HTTP 调用选择对应闲鱼域的凭证，刷新
不会擦除其他域的条目。旧版本导入过的缓存可重新运行 `auth login --browser chrome`
补齐来源信息。首页能返回商品不等同于翻页已通过登录校验。
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
.venv/bin/goofish-z search items "示例商品" --limit 30 --page 2
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

工作台搜索每页最多展示 30 条，可用“上一页 / 下一页”浏览更多结果。翻页也遵守
共享搜索间隔，首次读取另一页可能需要等待；返回本次已看过的页面使用内存缓存。
自动过滤和屏蔽可能使可见数量少于 30，页面会显示数量；筛选后为空也仍能翻页。
闲鱼要求重新登录时会提示更新登录态，翻页失败会保留原页，不把旧结果当作新页。
提交新搜索时会立即清空旧关键词的结果和分页缓存；失败提示固定显示在筛选区域上方，
不会被筛选或垃圾桶操作清除。响应必须对应本次关键词与页码，否则丢弃并明确报错。

容量支持中文紧接单位的写法（如「48G涡轮」），多规格标题包含目标容量即可保留。
RTX 型号保留 D / Ti / Super 后缀，`4080S` 与 `4080 SUPER` 等价：搜索 `4090 48G` 会过滤只包含 `4090D` 的商品，
标题同时包含目标型号的混售商品仍保留；垃圾桶中会注明容量或型号不匹配的依据。

价格上下限（元）、地区、标题包含/排除和价格排序只作用于**当前页已获取的结果**，
修改条件即时生效，不再次访问闲鱼；不代表对全站结果的筛选或排序。
点击数量旁醒目的「🗑 已过滤 N 条 · 查看原因」按钮，可展开被隐藏商品的完整标题、价格、地区、原商品链接和
逐条过滤原因；数量包含自动过滤、屏蔽规则及当前页面筛选。展开/收起使用本次已
获取的数据，不会再次搜索。
搜索返回的 `price` 统一为元字符串，`price_value` 为元数值，`price_text` 保留来源原文。
例如 `¥2.42万` 返回 `price="¥24200"`、`price_value=24200`；界面显示 `¥24,200`
及原文。闲鱼缩写标价本身可能经过舍入，精确标价仍以商品详情页为准。历史与告警
使用相同单位换算；旧记录中已丢失的单位不会凭猜测回填。

`max_price` 是低价告警线（价格小于或等于时告警），`min_price` 是高价告警线
（价格大于或等于时告警）；两者是独立触发条件。相同价格持续满足相同条件不会重复
告警，价格改变或观察到离开条件后再次满足才产生新事件。搜索中未出现某商品不能
证明其已售出，因此不会据此清除该商品的告警状态。

自动屏蔽按现有卖家信号规则生效；没有卖家标识时不会猜测身份。卖家昵称补查仍需
显式启用 `--enrich-sellers`，可能产生额外请求。低价本身只作展示标记；卖家自动
评分与人工规则的结果均可通过命中原因检查。`signals unban` 清空该卖家的累计信号。
已人工确认的非实价、引流价商品可用 `blacklist add` 的 `--kind item_id` 和
`--value <商品ID>` 精确屏蔽，`--note "非实价／引流价（人工确认）"` 会作为原因展示。
具体商品规则保存在本机数据目录，不进入公开代码仓库。

## HTTP 契约

| 功能 | 接口 |
| --- | --- |
| 服务存活 | `GET /health` |
| 本地诊断 | `GET /api/diagnostics` |
| 搜索 | `GET /api/search?q=...&limit=30&page=1` |
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

搜索 `page` 范围为 1–50，默认 1；`limit` 是当前页获取上限（1–50，默认 20），
实际数量取决于闲鱼当前页和过滤规则。返回 `query`、`page`、`has_next`、`source_count`、
`filtered_count` 和 `blocked_count`；是否还有下一页以来源分页控件为准。
`filtered` 和 `blocked` 列出被自动过滤和屏蔽的商品，保留标题、价格、链接等字段
及 `reasons` 原因列表，供 PC 和 Agent 查看；当前页面筛选原因由网页即时计算。

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

0.2.0 已在全新 Python 3.14 环境通过 **79 项 Python 测试、16 项网页测试**，依赖检查
无冲突。CI 配置覆盖 Python 3.11 和 3.14。可在本地复现离线测试：

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/gui.test.js
.venv/bin/python scripts/check_public_repo.py
```

测试使用临时目录与合成数据，覆盖 API/MCP 调用、限流竞争、熔断、取消、告警去重、
旧数据库迁移、网页文本渲染，以及 MCP 新旧协议和命令/子进程输出隔离。
搜索回归覆盖单位换算、监控阈值、分页参数、翻页时的登录/风控检查、当前页筛选、
数值排序和翻页缓存；另以隔离 Chrome 的合成页面验证实际 DOM 提取及桌面/手机布局。
登录态回归覆盖同名跨域 Cookie、路径/有效期/HttpOnly 保留、浏览器和文件导入、
HTTP 凭证选择、刷新合并，以及登录跳转期间的 DOM 读取。
Android 请求契约测试见 `app-android/`。
离线测试不等同于真实闲鱼登录、搜索或交易验证；平台 DOM 与登录流程变化仍需实测。

### PC + Agent 实测记录

2026-09-04，在 macOS、系统 Chrome、Python 3.14 与 MCP SDK 2.1.1 环境下，
导入已登录的 Chrome 会话，对 0.2.0 做了一次只读抽样验证：

| 检查项 | 结果 |
| --- | --- |
| Chrome 登录态导入与账号验证 | 通过 |
| PC HTTP 搜索 | 限量返回 3 条商品，标题与价格字段完整 |
| 搜索翻页 | 修复登录态域名丢失后，真实第二页读取通过，价格单位正确 |
| MCP 连接与本地诊断 | 加载 37 个工具，返回结构化诊断与最近验证状态 |
| Agent 商品详情 | 成功读取搜索所得商品，商品标识一致，标题与价格完整 |
| PC / Agent 任务查询 | 两端返回相同任务列表 |

本次查询未触发登录错误或风控提示。真实后台监控长时间运行、交易与商品写入不在
本次实测范围内；账号、Cookie、商品返回内容及浏览器缓存不随代码发布。

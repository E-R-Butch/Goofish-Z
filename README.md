# Goofish-Z

> 项目边界：Goofish-Z 维护闲鱼工作台、搜索、商品、监控及 CLI/MCP/HTTP 接口。跨平台比价已拆为独立的 [PriceRadar](https://github.com/E-R-Butch/PriceRadar)，淘宝、京东、拼多多采价在该项目维护；拆分前的实现留在 tag `archive/readonly-market-prices-20261020`，不是本项目后续开发入口。

面向 PC 与 Agent 的闲鱼工具：Web 监控台、CLI、MCP、HTTP API 共用同一套搜索、详情、监控与告警能力。

当前优先维护 PC + Agent 使用体验。Android 客户端源码保留在 `app-android/`，APK 工作链迁移与进一步升级暂缓。

## 运行环境

- **真实搜索默认需要可显示窗口的图形环境**（macOS 桌面，或 Linux 上的 X11/Wayland）。`browser.py` 默认 headful，代码注释记录了实测结论：headless 指纹即便用系统 Chrome 也会被闲鱼判风控。没有桌面时有三条路——`GOOFISH_HEADLESS=1` 切 headless（能跑，但更容易被风控）、在 Linux 上起 Xvfb 提供虚拟显示，或只跑离线测试（测试不启动浏览器）。
- **系统 Google Chrome**（不是 Chromium）。代码以 `channel="chrome"` 启动，所以**不需要**执行 `playwright install chromium`；Playwright 只提供 Python 侧驱动。启动时用独立临时 profile，不碰你正在使用的 Chrome 配置。
- **Python 3.11+**。
- **Node.js**：硬依赖。闲鱼请求签名由 `src/goofish_z/static/goofish_js_version_2.js` 计算，Python 通过 PyExecJS 调用 Node。没装 Node 时 `search`、`item` 等命令直接失败。

四项都可以用 `doctor` 一次性确认（见下）。

## 安装和启动

先在自己的 Chrome 里打开 [闲鱼](https://www.goofish.com) 并完成登录，再导入登录态：

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

Web 面板 `http://127.0.0.1:8787`，MCP 入口 `.venv/bin/goofish-z-mcp`。下文命令里的 `goofish-z` 都指 `.venv/bin/goofish-z`；也可以先 `source .venv/bin/activate` 后用裸命令。

`doctor` 输出 JSON，`dependencies` 三项要全为 `true`，其余字段的含义是：

```json
{
  "dependencies": {"playwright": true, "chrome": true, "node": true},
  "auth": {"cookies_present": true, "last_check": {"valid": true, "checked_at": 0}},
  "circuit": {"tripped": false, "remaining_seconds": 0},
  "limiter": {"search": {"next_available_in": 0}}
}
```

`cookies_present` 只表示本地有 Cookie 文件，**不代表登录有效**；有效性看 `last_check.valid` 和它的时间戳。`circuit.tripped` 为 `true` 表示正处于风控冷却，`remaining_seconds` 是剩余秒数。

`auth login --browser chrome` 读取本机 Chrome 的闲鱼相关 Cookie 并保存到外部运行目录；`auth status` 才会向闲鱼验证当前会话。Cookie 文件存在本身不代表登录有效。导入按名称、域名和路径保留 Cookie，HTTP 调用选择对应闲鱼域的凭证，刷新不会擦除其他域的条目；旧版本导入过的缓存可重新运行 `auth login --browser chrome` 补齐来源信息。首页能返回商品不等同于翻页已通过登录校验。缓存存在但关键 Cookie 缺失或已过期时，也会尝试从 Chrome 恢复一次，浏览器仍需有有效登录态；自动恢复不会切换到与缓存不同的账号，也不会用不完整 Cookie 覆盖缓存。`GOOFISH_NO_CHROME_BOOTSTRAP=1` 可禁用自动恢复。其他导入方式见 `auth login --help`。

首次登录或会话失效可能需要人工登录；自动刷新只做有限恢复，不保证永久在线。

## 第一次搜索

```bash
.venv/bin/goofish-z search items "4090" --limit 5
.venv/bin/goofish-z search items "4090" --sort price_asc --limit 30 --format json
```

默认输出表格，`--format json` 输出结构化结果，便于管道和 Agent 处理。搜索默认每 30 秒允许一次，网页、CLI、Agent 和后台监控共用同一限额。

### 可选 SKU 补全

`search items ... --resolve-skus --format json` 会对有多规格线索、或相对真实同款中位数明显低价的候选商品补充 SKU；不代表逐条抓取所有搜索结果。该选项与 `--no-filter-low-value` 可以独立组合，宽泛搜索不会使用假设的价格基准。

每次搜索最多尝试 3 条详情，遵守共享 `detail` 限流，并将累计限流等待限制为 12 秒（不包含详情请求本身的超时）。认证、风控或限流失败会立即停止后续补全；其他单条详情失败保留已有搜索结果并继续有限尝试。

启用时，CLI JSON、MCP 和 HTTP 结果新增 `sku_resolution`，原有 `items`、分页和过滤字段不变：
- `complete`：所有候选的可选补全是否完成；不是登录有效性，也不是全站搜索完整性。
- `candidates` / `attempted` / `resolved` / `without_skus`：候选数、详情调用尝试数、获取到 SKU 数、成功读取但没有 SKU 的数量。
- `skipped`：没有尝试详情的候选数；`failed` 和 `failures` 记录失败或无法尝试的原因，不含上游原始响应或异常文本。因此 `failed` 与 `skipped` 可能重叠。
- `stop_reason`：`auth_required`、`risk_control`、`rate_limited`、`detail_budget`、`wait_budget` 或 `null`。

补全不完整仍以尽力而为方式返回搜索结果（CLI 退出码 0、HTTP 200）；调用方需要读取 `sku_resolution.complete`，不要把搜索成功当成所有 SKU 均已核验。默认表格不展示此元数据，请使用 JSON。未启用补全时不新增该字段。

## 常用命令

```bash
# 搜索与商品
.venv/bin/goofish-z search items "示例商品" --limit 30 --page 2
.venv/bin/goofish-z item get <商品ID>
.venv/bin/goofish-z item mine --status online --limit 50
.venv/bin/goofish-z item mine --status all --limit 200 --format json

# 监控与告警
.venv/bin/goofish-z watch add "示例商品" --max-price 100
.venv/bin/goofish-z watch list
.venv/bin/goofish-z watch run --all --format json
.venv/bin/goofish-z watch history <监控ID>
.venv/bin/goofish-z watch alerts --unread-only --format json
.venv/bin/goofish-z watch read-alert 1

# 屏蔽规则（kind 是位置参数：item_id / title_keyword / location / no_badge / price_drop）
.venv/bin/goofish-z blacklist add item_id <商品ID> --note "非实价／引流价（人工确认）"
.venv/bin/goofish-z blacklist add title_keyword "求购" --note "求购帖"
.venv/bin/goofish-z blacklist list
.venv/bin/goofish-z blacklist remove <规则ID>

# 卖家信号
.venv/bin/goofish-z signals list
.venv/bin/goofish-z signals unban <卖家昵称>

# 消息（写操作，与发布、删除共享 60 秒单位限额）
.venv/bin/goofish-z message list-chats
.venv/bin/goofish-z message history <会话ID>
.venv/bin/goofish-z message send <会话ID> <对方ID> --text "内容"

# 其他
.venv/bin/goofish-z auth status
.venv/bin/goofish-z doctor
.venv/bin/goofish-z list-commands      # 打印全部已注册命令
```

完整命令树：`auth`(5) · `blacklist`(4) · `category`(1) · `item`(5) · `location`(1) · `media`(1) · `message`(4) · `search`(1) · `signals`(2) · `skills`(1) · `watch`(12)，加 `version` / `doctor` / `list-commands`。每个子命令都有 `--help`。

## 后台监控

`watch add` 只是登记关键词，**不会立刻查价**。要真正执行有两种方式：

- `watch run` — 同步跑一次，进程内完成，退出即结束。
- `watch start` — 交给本地 HTTP 服务后台跑，返回任务 ID，网页能同时看到进度。适合长批次。

```bash
# 需先启动 HTTP 服务
.venv/bin/goofish-z watch start --all --format json
.venv/bin/goofish-z watch jobs --format json
.venv/bin/goofish-z watch job TASK_ID --format json
.venv/bin/goofish-z watch cancel TASK_ID --format json
```

MCP 工具名为 `watch.start`、`watch.jobs`、`watch.job`、`watch.cancel`，与网页共享任务，查询进度不会重新搜索。本地服务地址默认 `http://127.0.0.1:8787`，可用 `GOOFISH_Z_HTTP` 配置。

每个 API 进程只接受一个活动后台任务，重复提交返回 409。限流等待可立即取消，正在进行的浏览器请求会在结束后检查取消状态。任务进度保存在内存，服务重启后不自动重跑；历史与告警保存在 SQLite。此版本请使用单个 API worker 以便一致查询任务。

网页进度以最近一次操作为准，晚到的旧响应不会覆盖新状态。服务重启或任务记录过期后，旧任务返回 404 时会恢复运行按钮；临时读取失败则保留当前任务，可点"刷新进度"重连。刷新和重连只查询进度，不会自动重新提交监控。

## 接入 Agent

在支持 stdio 的 MCP 客户端中配置入口，把路径替换为实际安装目录的绝对路径：

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

`search.items`、`item.get`、`auth.doctor` 等工具可直接调用；`watch.start / job / jobs / cancel` 需要先启动上面的 HTTP 服务。如果配置了 `GOOFISH_Z_DATA`，HTTP、CLI 和 MCP 应指向同一个仓库外运行目录。

本版 MCP 已迁移到 2.1.1+ 的 `MCPServer`：支持新旧协议客户端，stdio 隔离可防止命令和子进程的普通输出污染协议流，见 [SDK 发布说明](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)。

## HTTP 契约

| 功能 | 接口 |
| --- | --- |
| 服务存活 | `GET /health` |
| 本地诊断 | `GET /api/diagnostics` |
| 已注册命令 | `GET /api/commands` |
| 搜索 | `GET /api/search?q=...&limit=30&page=1` |
| 本人商品 | `GET /api/item/mine?status=online&limit=50` |
| 商品详情 | `GET /api/item/get?item_id=...` |
| 消息会话 | `GET /api/message/chats` |
| 添加 / 列出监控 | `POST /api/watch` / `GET /api/watch` |
| 删除监控 | `DELETE /api/watch/{id}` |
| 监控价格历史 | `GET /api/watch/{id}/history` |
| 同步运行 | `POST /api/watch/run`，传 `{"all":true}` 或 `{"watch_id":1}` |
| 同步运行全部 | `POST /api/watch/run-all` |
| 后台运行 | `POST /api/watch/jobs`，同上请求体，返回 202 与任务 ID |
| 任务进度 | `GET /api/watch/jobs/{id}` / `GET /api/watch/jobs` |
| 取消任务 | `DELETE /api/watch/jobs/{id}` |
| 实际告警 | `GET /api/alerts?unread_only=true` |
| 告警已读 | `POST /api/alerts/{id}/read` |
| 屏蔽规则 | `GET /api/blacklist` / `POST /api/blacklist/add` / `POST /api/blacklist/remove` |
| 卖家信号 | `GET /api/signals/list` / `POST /api/signals/unban` |

同步运行成功返回 200，部分失败返回 207，全部失败返回 502；结果同时包含 `status`、`succeeded`、`failed`、`skipped` 和每项错误。CLI 对失败或部分失败返回非零退出码；MCP 的全部失败以工具错误返回。限流响应使用 429 和 `Retry-After`。

搜索 `page` 范围为 1–50，默认 1；`limit` 是当前页获取上限（1–50，默认 20），实际数量取决于闲鱼当前页和过滤规则。返回 `query`、`page`、`has_next`、`source_count`、`filtered_count` 和 `blocked_count`；是否还有下一页以来源分页控件为准。`filtered` 和 `blocked` 列出被自动过滤和屏蔽的商品，保留标题、价格、链接等字段及 `reasons` 原因列表，供 PC 和 Agent 查看；当前页面筛选原因由网页即时计算。

`/health` 仅代表进程可响应。`doctor` 和 `/api/diagnostics` 不访问闲鱼、不读取 Cookie 内容，也不自动刷新登录态；其登录结果只是最近一次 `auth status` 的验证记录，必须结合验证时间判断。运行 `auth status` 会执行真实账号验证。

## 闲鱼原生排序

工作台的"闲鱼原生排序"支持综合、价格从低到高、价格从高到低和最新发布。切换后回到第一页重新搜索；翻页和本次缓存都绑定关键词及原生排序。下方的"当前页排序"只重排已经读取的商品，默认保留闲鱼返回顺序。

HTTP 为 `GET /api/search?q=4090&sort=price_asc&page=1`；CLI/MCP 的 `search.items` 共用 `sort` 参数，允许 `default`、`price_asc`、`price_desc`、`newest`。响应带 `sort`，筛选只删除不符合条件的条目，保留其余商品的原生次序。

原生排序通过闲鱼当前页面菜单触发，核对该页面实际搜索响应的关键词、排序、页码、成功状态与商品 ID；页面未更新、来源不符或登录失效时明确失败，不把旧结果当作新排序。非默认排序需先打开搜索页再选择菜单，两次请求均遵守共享搜索间隔，翻页也需等待。原生价格顺序反映平台标价，不代表完整实售价。

低价回归样本由实测模式改写为合成条目，覆盖租赁、散热器、无核心／无显存料板、模型、包装盒和定金；也包含完整低价显卡、保留核心显存的故障卡及明确配件搜索反例。测试集不保存真实商品 ID、链接、卖家资料或原始描述，不把低价本身当作引流证据。

## 搜索质量与过滤

搜索和后台监控使用同一套标题过滤。明确的型号／容量不符、求购和回收广告、上门或远程服务、只售配件、非卖帖，以及注明定金／押金／非实价的条目进入"已过滤"，保留链接和每条原因。自动规则只影响本次搜索结果，不自动新增卖家或商品黑名单。

显卡型号识别覆盖 CMP HX、GTX、RTX 与 RX 的常见数字型号，保留 D/Ti/Super/XT/XTX/GRE 后缀，支持全角、空格和连字符；"性能对标"后的型号不代表出售型号。`24/48G` 识别为两个容量。缺少型号／容量的显卡、整机打包、故障练手件和多型号混售仍保留；标题写着"送驱动／教程""支持上门自提""无维修"不会据此判成服务。

显式搜索维修、安装、驱动教程或具体配件时保留相关服务／配件。明确写"价格只是定金"等不完整售价仍单独过滤。低价本身仅显示观察提示，不能据此认定引流；无法从标题确认的非实价仍需人工核实，可用 `blacklist add item_id <商品ID> --note <原因>` 精确屏蔽。规则不是商品真伪或卖家信用鉴定，未核实的规格和售价不会被当作已确认。

## 限流、告警与屏蔽语义

搜索默认每 30 秒允许一次，网页、CLI、Agent 和后台监控共用同一限额。网页收到带 `Retry-After` 的 429 响应后，会显示倒计时并自动重试一次；等待中不会重复提交。如果仍被限流，页面会提示剩余时间并恢复操作。登录失效或风控错误不会自动重试——风控按等级熔断（硬风控 30 分钟、接口级 10 分钟、轻触 2 分钟），期间 `doctor` 的 `circuit.remaining_seconds` 给出剩余时间，继续请求只会加重。

工作台搜索每页最多展示 30 条，可用"上一页 / 下一页"浏览更多结果。翻页也遵守共享搜索间隔，首次读取另一页可能需要等待；返回本次已看过的页面使用内存缓存。自动过滤和屏蔽可能使可见数量少于 30，页面会显示数量；筛选后为空也仍能翻页。闲鱼要求重新登录时会提示更新登录态，翻页失败会保留原页，不把旧结果当作新页。提交新搜索时会立即清空旧关键词的结果和分页缓存；失败提示固定显示在筛选区域上方，不会被筛选或垃圾桶操作清除。响应必须对应本次关键词与页码，否则丢弃并明确报错。

容量支持中文紧接单位的写法（如「48G涡轮」），多规格标题包含目标容量即可保留。RTX 型号保留 D / Ti / Super 后缀，`4080S` 与 `4080 SUPER` 等价：搜索 `4090 48G` 会过滤只包含 `4090D` 的商品，标题同时包含目标型号的混售商品仍保留；垃圾桶中会注明容量或型号不匹配的依据。

价格上下限（元）、地区、标题包含/排除和价格排序只作用于**当前页已获取的结果**，修改条件即时生效，不再次访问闲鱼；不代表对全站结果的筛选或排序。点击数量旁醒目的「🗑 已过滤 N 条 · 查看原因」按钮，可展开被隐藏商品的完整标题、价格、地区、原商品链接和逐条过滤原因；数量包含自动过滤、屏蔽规则及当前页面筛选。展开/收起使用本次已获取的数据，不会再次搜索。

搜索返回的 `price` 统一为元字符串，`price_value` 为元数值，`price_text` 保留来源原文。例如 `¥2.42万` 返回 `price="¥24200"`、`price_value=24200`；界面显示 `¥24,200` 及原文。闲鱼缩写标价本身可能经过舍入，精确标价仍以商品详情页为准。历史与告警使用相同单位换算；旧记录中已丢失的单位不会凭猜测回填。

`max_price` 是低价告警线（价格小于或等于时告警），`min_price` 是高价告警线（价格大于或等于时告警）；两者是独立触发条件。相同价格持续满足相同条件不会重复告警，价格改变或观察到离开条件后再次满足才产生新事件。搜索中未出现某商品不能证明其已售出，因此不会据此清除该商品的告警状态。

自动屏蔽按现有卖家信号规则生效；没有卖家标识时不会猜测身份。卖家昵称补查仍需显式启用 `--enrich-sellers`，可能产生额外请求。低价本身只作展示标记；卖家自动评分与人工规则的结果均可通过命中原因检查。`signals unban` 清空该卖家的累计信号。具体商品规则保存在本机数据目录，不进入公开代码仓库。

## 排障

| 现象 | 处理 |
| --- | --- |
| `doctor` 里 `chrome: false` | 系统没装 Google Chrome。装 Chrome，不是 Chromium；也不需要 `playwright install` |
| `doctor` 里 `node: false` | 装 Node.js（任意 LTS）。签名桥依赖它，缺了搜索和详情全线失败 |
| `doctor` 里 `playwright: false` | 重新执行 `.venv/bin/python -m pip install -e .` |
| `auth status` 报登录失效 | 先在 Chrome 里重新登录闲鱼，再跑 `auth login --browser chrome`。Cookie 文件存在不等于有效 |
| 「浏览器登录态注入失败」 | Chrome 未运行或登录态已过期；先手动打开 Chrome 确认能进闲鱼 |
| 429 / `Retry-After` | 等满剩余秒数。限额是全局共享的，不是单条命令 |
| 提示风控 / `RGV587` / `FAIL_SYS_USER_VALIDATE` | 已进入熔断，硬风控 30 分钟。看 `doctor` 的 `circuit.remaining_seconds`，期间继续请求只会加重 |
| 搜索一直加载中或结果为空 | 很可能被跳到登录页。重跑 `auth login --browser chrome`；首页能出商品不代表翻页也通过校验 |
| 无桌面的 Linux / 容器 | 默认 headful 需要 `DISPLAY`。用 `GOOFISH_HEADLESS=1` 切 headless，或起 Xvfb 提供虚拟显示；headless 更易被风控。纯离线测试不需要浏览器 |

无桌面容器的常见做法是用 `xvfb-run` 包一层，仍然以 headful 启动 Chrome，指纹与桌面一致：

```ini
[Unit]
Description=Goofish-Z HTTP API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/Goofish-Z
Environment="PATH=/opt/Goofish-Z/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1920x1080x24" \
  /opt/Goofish-Z/.venv/bin/uvicorn goofish_z.api.app:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

自动恢复策略建议放 drop-in，主 unit 保持干净（改策略时不用动 ExecStart）：

```ini
# /etc/systemd/system/goofish-z-api.service.d/40-session-reliability.conf
[Service]
Environment=GOOFISH_AUTO_REFRESH_TOKEN=0
Environment=GOOFISH_NO_CHROME_BOOTSTRAP=1
```

改完 `systemctl daemon-reload && systemctl restart goofish-z-api`，用 `systemctl show goofish-z-api -p DropInPaths` 确认 drop-in 已加载。

容器内需要 `xvfb` 和 `google-chrome` 两个包。`--host 127.0.0.1` 表示只在本机监听；要从局域网访问，用反向代理或端口转发，不要直接把 API 暴露出去。

## 代码与运行数据边界

公开仓库只保存通用代码、文档和合成测试。账号、Cookie、Token、设备状态、浏览器临时 profile、限流/熔断状态、商品缓存、价格历史、SQLite 数据库都使用仓库外的 `~/.goofish-z/`，或 `GOOFISH_Z_DATA` 指定的外部目录。源码目录作为运行目录会被拒绝。

旧数据库首次使用时增补告警状态与已读字段，保留既有监控、价格历史和告警。升级前可备份外部运行目录，使用 `pip install -e .` 安装新增依赖后再重启服务。

本机网站等消费者通过 localhost HTTP API 读取商品，详情接口只返回白名单字段。实际发布、删除、回复、改价与交易必须按操作者明确指令执行。当前未提供经验证的编辑、改价或上下架命令；不应以发布新商品替代编辑。

## 验证

当前源码已在 Python 3.14 环境通过 **201 项 Python 测试、33 项网页测试**。CI 配置覆盖 Python 3.11 和 3.14。可在本地复现离线测试：

```bash
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
node --test tests/gui.test.js
.venv/bin/python scripts/check_public_repo.py
```

测试使用临时目录与合成数据，覆盖 API/MCP 调用、限流竞争、熔断、取消、告警去重、旧数据库迁移、网页文本渲染，以及 MCP 新旧协议和命令/子进程输出隔离。搜索回归覆盖单位换算、监控阈值、分页参数、翻页时的登录/风控检查、当前页筛选、数值排序和翻页缓存；另以隔离 Chrome 的合成页面验证实际 DOM 提取及桌面/手机布局。登录态回归覆盖同名跨域 Cookie、路径/有效期/HttpOnly 保留、浏览器和文件导入、HTTP 凭证选择、刷新合并，以及登录跳转期间的 DOM 读取。Android 请求契约测试见 `app-android/`。

离线测试不等同于真实闲鱼登录、搜索或交易验证；平台 DOM 与登录流程变化仍需实测。

2026-09-23 原生排序实测：CLI 的 `4090 / price_asc` 第一、二页和 `90HX / newest` 均通过实际请求、返回商品 ID 与页面次序核对。普通 Chrome 中也确认价格降序发送 `price / desc`。工作台的切换、翻页和缓存隔离另用合成服务验证，浏览已读页与本地筛选不会重新搜索。真实条目原文及账号数据保留在仓库之外。

### PC + Agent 实测记录

2026-09-04，在 macOS、系统 Chrome、Python 3.14 与 MCP SDK 2.1.1 环境下，导入已登录的 Chrome 会话，对 0.2.0 做了一次只读抽样验证：

| 检查项 | 结果 |
| --- | --- |
| Chrome 登录态导入与账号验证 | 通过 |
| PC HTTP 搜索 | 限量返回 3 条商品，标题与价格字段完整 |
| 搜索翻页 | 修复登录态域名丢失后，真实第二页读取通过，价格单位正确 |
| MCP 连接与本地诊断 | 加载 37 个工具，返回结构化诊断与最近验证状态 |
| Agent 商品详情 | 成功读取搜索所得商品，商品标识一致，标题与价格完整 |
| PC / Agent 任务查询 | 两端返回相同任务列表 |

本次查询未触发登录错误或风控提示。真实后台监控长时间运行、交易与商品写入不在本次实测范围内；账号、Cookie、商品返回内容及浏览器缓存不随代码发布。

## 更新记录

0.2.0 带来后台监控任务、原生分页排序、共享写限额与熔断、告警去重、MCP 2.1.1+ 迁移等变化，逐条明细见 [CHANGELOG.md](CHANGELOG.md)。

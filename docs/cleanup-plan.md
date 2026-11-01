# 分阶段瘦身与解耦

审计基线：`b3c3aed8e45c6cf177ebc004dd47de95e904ee3f`（PR #19 合并后）。
范围仅 Goofish-Z，不涉及 PriceRadar、私人运行数据、账号操作或部署。

## 现状与证据

基线共 156 个 Git 跟踪文件，Python 源码 9,825 行。CLI / MCP / HTTP
共享 `core.registry`，应保留这一入口契约，而不是另起一套业务实现。

| 发现 | 证据与影响 | 决策 |
| --- | --- | --- |
| 重复签名资源 | `static/goofish_js_version_2.js` 与 `src/goofish_z/static/goofish_js_version_2.js` 的 Git blob 均为 `7c8b7d7917887fab3ee5266dd9907ccc7423ab3f`，各 20,424 字节；`core/sign.py` 只用包资源，wheel 只包含 `src/goofish_z`，README 也指向包资源 | 删除根目录副本，保持包内字节完全不变，收紧隐私扫描例外 |
| 无调用依赖 | `tenacity` 只出现在 `pyproject.toml` 声明中；源码、测试、脚本无导入或动态加载；实际限流等待见 search/watch，认证恢复见 mtop/session | 只移除直接依赖声明，不改重试策略 |
| 核心包隐式加载认证 | `core/__init__.py` 无条件导入 Session，任何 `core.errors` / registry / price 导入都连带载入 requests、日志和 JS 桥 | 保留真实 Session 类公共导出，改按需导入，用新进程测试隔离导入缓存 |
| 命令全量发现 | `registry.discover()` 递归导入所有命令；CLI 在模块导入时构建整棵命令树，MCP/API 同样依赖发现 | 暂不改注册语义；先冻结完整命令名称、签名、策略、write 标记和输出契约，再优化启动加载 |
| 命令与 HTTP 模型反向依赖 | `commands/watch/jobs.py` 导入 `api.models.WatchRun`；`api/jobs.py` 又调用 watch 命令 | 下一批提取共享请求模型，旧路径兼容导出；保留后台任务单实例、取消与进度语义 |
| 大模块职责混合 | ws 716 行、search 699 行、price_value_engine 618 行、browser 550 行、session 381 行 | 按协议解析/纯转换与 I/O 分离，不按文件大小机械拆分 |
| 上游安装残留 | `commands/skills/install.py` 仍提示安装 goofish-cli，描述 force-include；当前树没有 `skills/` 或 `_bundled_skills/`，构建配置也无该规则 | 这是已公开命令，不以“没资源”为由直接删；另批决定提供资源或明确兼容错误并补测试 |

计数可复现：`git ls-files | wc -l`，`find src -name '*.py' -exec wc -l {} +`。
删除候选依据是全仓引用与构建配置检查，不把无静态引用等同于任意模块可删。

## 上游来源与许可证边界

现有文件注释说明了以下来源/参考关系，但不足以证明逐文件许可证：

- `core/session.py`、`core/item_page.py`：goofish-cli；session/mtop 还标注 goofish-omni 增强。
- `blacklist.py`：ai-goofish-monitor 的 result_blacklist_service；limiter 也标注参考其参数。
- registry、strategy、errors、browser_cookie 注释提到 opencli，可能是设计参考而非逐字复制。
- 包内签名 JS 有压缩协议解码逻辑，但无可核验的来源版本/许可证头。

基线树未含 LICENSE / NOTICE。此结论不是“无许可证即可自由复制”，也不在本次凭猜测
补一个许可证。保留所有已有归属注释及唯一签名资源；后续必须从已确认上游仓库和提交
比对来源、授权条件与修改范围，再补第三方清单/通知。不要在来源未确认时整块删除或重写。

## 执行顺序与验收

1. **本批：可证明冗余 + 最小导入边界。** 先加包资源完整性与公共 Session 身份测试、
   导入隔离回归，再删重复资源/闲置依赖，惰性导出 Session。减少一个维护副本（20,424
   字节）和一个直接依赖（15 → 14）；不声称 wheel 体积减少，根副本原本就不入 wheel。
2. **冻结公共契约。** 快照 CLI/MCP 注册名称、函数参数、策略/write 标记及 HTTP schema；
   补 skills.install 当前缺资源路径测试；确认依赖实际用途和上游来源后再设计 extras。
3. **分离模型和纯逻辑。** 先解除 watch 命令对 HTTP 层的模型依赖；后把 search 的结果转换、
   富化策略与浏览器 I/O 分开。旧导入路径提供兼容，逐批独立 PR；不能改变响应字段和排序。
4. **协议与运行时边界。** 将 WS 帧解析从连接、ack、心跳和账号版本校验中分离；认证刷新、
   跨进程锁、限流与熔断最后处理。保留异常类型、取消点、账号变更中断、冷却不缩短语义。
5. **安装体积再评估。** 有 CLI/MCP/API 各模式的干净安装测试后才考虑可选 browser/server
   extras；不让当前默认安装失去功能。浏览器、Node/JS 和 mcp 不能因体积大直接移除。

每一批必须通过：公共仓库边界检查、ruff、Python 全套单测、GUI 测试、覆盖率门槛；
触及浏览器/认证/WS 时另跑隔离真实 Chrome 回归。安装验收检查 wheel 包资源与入口。
只用合成数据、临时目录；测试通过不代表真实平台登录/交易验证。独立草稿 PR，无合并或部署。

# 电商价格采集与分析（开发中）

目标是从淘宝／天猫、京东、拼多多读取商品价格，并通过工作台、CLI、MCP、HTTP
共用同一套采集和分析逻辑。只执行搜索、打开详情和读取页面；没有下单、加购物车、
领取优惠券、联系卖家或修改商品的实现。

## 当前验证边界

已有真实浏览器采集路径、独立平台登录态、价格归一化、条件分类、相关性检查、
本地历史和工作台入口。离线测试覆盖金额单位、条件价格、型号和容量、失败隔离、
连续换词不残留结果、HTTP 命令分发、MCP 输入输出结构与本地数据权限。

**三个平台的登录后价格采集验收尚未完成，不能视为可用性已验证。**
现场普通 Chrome 中能看到京东新版卡片；淘宝和拼多多搜索要求登录。
新模块的独立采集窗口实际返回：淘宝 `login_required`、京东 `parse_failed`、
拼多多 `login_required`。这些失败被真实保存和显示，没有补入示例价格或旧结果。
拼多多新版列表没有普通商品链接，商品标识和登录后卡片解析仍需实测完善。
进一步通过实际 CLI 诊断确认：京东页面 `ready_state=complete`，商品节点和价格节点
均为 0，只出现普通「请登录」入口；不足以单凭这条提示断言必须登录，保留
`parse_failed`，待有效登录态下继续验证。结构化诊断只包含节点数量和固定登录提示，
不返回原始页面、账号信息或 Cookie。

拼多多公开首页的服务端商品数据能对应到商品 ID、普通价格和拼单价；搜索及详情页
仍要求登录。公开首页推荐没有替代用户指定关键词的搜索结果。

## 使用

启动项目 HTTP 服务后，工作台中的「电商价格采集与分析」可选择平台、关键词和条数。
默认每个平台读取当前搜索页最多 20 条；不自动翻页或无限滚动。
也可以仅选择一个平台，输入完整 HTTPS 商品详情链接读取详情页。
分享短链、非商品页面、自定义主机、端口和任意重定向地址会被拒绝。

```bash
goofish-z price collect "RTX 5060 8G" --platforms "taobao,jd,pdd" --limit 20 --format json
goofish-z price collect "RTX 5060 8G" --platforms jd --url "https://item.jd.com/100.html" --format json
goofish-z price history "RTX 5060 8G" --limit 20 --format json
```

上例商品 ID `100` 是占位值，使用时替换成实际商品链接。
MCP 工具为 `price.collect` 和 `price.history`，参数与 CLI 一致。

```http
POST /api/price/collect
Content-Type: application/json

{"query":"RTX 5060 8G","platforms":"taobao,jd,pdd","limit":20}
```

`GET /api/price/history?q=RTX%205060%208G&limit=20` 只读取本机历史。
采集可能持续约一分钟；各平台分别报告 `ok`、`login_required`、
`verification_required`、`rate_limited`、`timeout`、`parse_failed` 等状态。
不自动重试登录、安全验证或解析失败，每个平台两次读取至少间隔 30 秒。

商品卡片出现后会继续等待价格加载（最多 40 次检查，每次间隔 0.5 秒）。到达上限后，
已识别但缺价的商品仍保留价格原文并标为 `unknown`；部分有价、部分缺价返回
`partial`，全部缺价返回 `parse_failed`。平台结果的 `count` 是识别商品数，
`priced_count` 是有效价格数，`missing_prices` 是缺价数；工作台分别展示前两者。
这些计数在页面成功读取后提供，登录或访问失败时不假定已经识别到商品。

## 登录与私有数据

采集使用系统 Chrome 的独立 profile，默认不读取日常 Chrome 登录态。
用户可在自己 Chrome 登录后，明确选择单个平台导入：

```bash
goofish-z price import-chrome jd --format json
goofish-z price import-chrome taobao --format json
goofish-z price import-chrome pdd --format json
```

也可选择独立采集窗口，手工登录后关闭该标签页：

```bash
goofish-z price login jd --timeout-seconds 300 --format json
```

代码不自动输入账号、密码、短信或处理验证码。导入成功不等于登录有效，
以随后真实采集的返回为准。导入仅允许对应平台 Cookie 域；导入文件、独立
profile 和价格数据库均位于仓库外的 `GOOFISH_Z_DATA`（默认 `~/.goofish-z`）。
平台目录为 `price-sessions/<platform>`，目录权限 0700，Cookie 文件及
`prices.db` 权限 0600。导入数据不会进入 HTTP 返回、日志或 GitHub。

HTTP 的 `POST /api/price/import-chrome` 只接受单个 `platform`。工作台在该动作前
明确说明范围并要求用户确认；Agent 调用 `price.import-chrome` 前也应取得相应平台
登录态导入授权。服务维持本机绑定，不应直接暴露到公网。

## 分析口径

- `price_text` 保留页面价格原文；`price_low` / `price_high` 为人民币元。
- `listed` 是页面标价，`conditional` 是附优惠或资格条件的价格，`range` 是区间／
  起价，`unknown` 是未能可靠解析的金额。不会计算未核实的券后价，也不会重复减券。
- 补贴、首购、会员、拼单、定金、参考报价、批发条件单独标注。拼多多列表价格默认
  保留拼单及优惠资格待确认说明。
- `title_match` 仅表示标题相关；多个型号、多个容量、可能的整机或配件进入
  `uncertain`，明确不同型号／容量为 `mismatch`。它们不计入标题相关的价格统计。
- 型号和容量比较统一 G/GB、TiS/Ti SUPER 及全角写法；品牌等其他搜索条件仍需匹配。
  显卡查询中的电脑整机、游戏主机和水冷头保留为待确认，明确查询整机时按该查询判断。
- 不假定搜索卡片价格对应某个已选 SKU。`specification_confirmed` 当前为 false；
  不声称同款最低价，也不把页面标价当作成交价。
- 按平台和价格类型分别列出数量、最小值、最大值及中位数；区间组统计起价范围，
  不计算中位数。每个条目和每次采集都带采集时间。
- 每次采集（包括失败）独立保存为一个 run。新查询从空结果开始，不以旧记录
  冒充本次成功；查看历史需要用户显式点击，页面标注「历史记录」。

## 验收要求

1. 三个平台分别在实际登录态下读取到有商品链接、标题、规格线索、价格原文和
   采集时间的记录，并逐项与浏览器可见卡片核对。
2. 连续切换两个关键词，分别产生新请求与新 run，不残留旧结果。
3. 无登录、验证码、超时、解析变更时明确报错；其余平台继续，不填充示例数据。
4. 券后／补贴／拼单／多 SKU 条目不污染普通标价统计。
5. 工作台、HTTP、CLI 与 MCP 的实际调用返回一致，历史在服务重启后保留。

官方开放平台可作为后续另一条采集路径，但需要应用凭证及对应权限；当前没有伪造
这些授权，也没有将未调用成功的 API 适配器算作已接入。
参考：[淘宝商品查询接口](https://jaq-doc.alibaba.com/docs/api.htm?apiId=24518&scopeId=12677)、
[京东联盟开放平台](https://jos.jd.com/jdunion)、[拼多多开放平台](https://open.pinduoduo.com/)。

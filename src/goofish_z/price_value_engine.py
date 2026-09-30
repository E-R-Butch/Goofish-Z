"""Goofish-Z 价格与价值动态匹配引擎 (Price-Value Matching Engine)
核心理念：
低价值不是由单一标签决定，而是由「价格与真实使用价值的匹配度」决定。
- 带有折价属性（如 MDM、成色磨损）但价格极其优惠（如 256G 插卡版仅售 ¥1499）：判定为高性价比优质好物。
- 具有暗病、残次、单根偷标等缺陷，却标出接近正常全原价甚至溢价：判定为「价格与价值严重不匹配」予以拦截拉黑。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from goofish_z.llm_arbiter import LLMArbiter
from goofish_z.search_quality import extract_gpu_models, wants_parts, wants_service

# 1. 纯展示 / 占位 / 不出 (价值归零) / 小作文贴
PAT_DISPLAY_ONLY = re.compile(
    r"(?:仅展示|只展示|仅供欣赏|不[出卖](?!外地|外省|省外|本市|省内|同城|快递|邮寄|物流|邮费|运费|包邮|偏远|海外|港澳台|新疆|西藏)|非卖[品贴]|暂不出|勿拍|请勿拍下|拍下不发|谁拍谁傻|抵制奸商|科普贴|曝光帖|挂人|避坑指南)",
    re.IGNORECASE,
)

# 虚拟商品 / 教程 / 脚本 / 刷机服务 / 知识付费 / 代工服务 / 算力出租
PAT_VIRTUAL_SERVICE = re.compile(
    r"(?:魔改驱动|驱动技术|解锁命令|hiveos.*?解锁|算力珍珠|一键解锁|远程协助|代调|提供驱动|代刷|刷bios|bios修改|"
    r"出教程|算力节点搭建|代搭建|知识付费|方案服务|飞行表|算力解锁|代焊|代补电容|代改|资源分享|远程安装|解锁工具|"
    r"破解驱动|最新破解|解除.*?限制|补到x16|来回运费自己出|部署指南|部署教程|大模型教程|代部署|环境配置代搭|算力出租|远程出租|GPU出租|显卡出租|服务器租赁|代跑|远程租赁|按天出租)",
    re.IGNORECASE,
)

# 纯外壳 / 散热套件 / 延长线 / 拆件无核心料板 / 显卡周边零配件
PAT_ACCESSORY_GPU = re.compile(
    r"(?:散热(?:风扇|器|模组|套件)|风扇支架|水冷头|一体水冷|改水冷|显卡伴侣|显卡支架|背板|挡板|空盒|包装盒|"
    r"转接线|延长线|延长套件|转接套件|转接板|转接卡|降温神器|降温插件|导热贴|铜片|"
    r"显卡风扇|无芯片|无显存|剩下的pcb)",
    re.IGNORECASE,
)

# 搜显卡单卡时整机混淆引流
PAT_HOST_MACHINE = re.compile(
    r"(?:台式主机|游戏备用机|电脑主机|台式电脑整机|海景房台式主机|高端主机|AI\s*主机)",
    re.IGNORECASE,
)

# 2. 标单价引流套路 (套条写单根、批量写单张)
PAT_UNIT_PRICE_TRAP = re.compile(
    r"(?:标价[是为即](?:单[个片条盒张根]|单价)|单[个片条盒张根][标卖售价]|单[根条张]价|单片不包邮|单条出[，,]标价为单条价格|标价即单张价)",
    re.IGNORECASE,
)

# 3. 硬件重大故障 / 尸体 / 点不亮
PAT_SEVERE_DEFECT = re.compile(
    r"(?:点不亮|短路|烧毁|摔坏|开不开机|内屏摔坏|进水|打架卡|尸体|配件机|无视频输出|"
    r"花屏|炸管|掉电|盲盒|dos测试不通过)",
    re.IGNORECASE,
)

# 4. 硬件轻中度缺陷 / 暗病
PAT_MODERATE_DEFECT = re.compile(
    r"(?:缺[少了个]金手指|金手指.*?(?:断|缺|掉)|不识别|接口.*?(?:不亮|坏|缺失)|(?:dp|hdmi).*?不亮|"
    r"指纹坏|指纹不灵|无指纹|wifi[是为]坏的|无法连wifi|屏幕按压会出现黑点|屏幕.*?有色差|换屏有色差|外屏维修过)",
    re.IGNORECASE,
)

# 5. 霸王条款与高危交易
PAT_ABUSIVE_TERMS = re.compile(
    r"(?:退货扣(?:[1-9]\d{2,})|拒收扣(?:[1-9]\d{2,}))",
    re.IGNORECASE,
)

# 绝对拒绝零售 (单买买家不可获得)
PAT_STRICT_NO_RETAIL = re.compile(
    r"(?:不单[卖出]|不零售|不散[卖出]|单买勿扰|拆卖勿扰|单张不卖)",
    re.IGNORECASE,
)

# 偏好打包 / 大批量库存 (偏好整批但通常可私聊协商拆卖，绝不直接拦截)
PAT_BATCH_PREFERRED = re.compile(
    r"(?:能打包的来|打包优先|优先打包|打包出|共\d{2,4}张|出\d{2,4}片)",
    re.IGNORECASE,
)

# 跨品类完全无关污染 (如搜显卡时闲鱼召回了数码相机、相机电池、衣服鞋帽、相机主板)
PAT_IRRELEVANT_CATEGORY = re.compile(
    r"(?:数码相机|微单|单反|相机电池|锂离子电池|拆机电池|锂电池|充电器|电池|相机主板|主板|耳机套|手机壳|保护套|衣服|鞋子|男装|女装|羽绒服|包包|口红)",
    re.IGNORECASE,
)

# 严重硬件致命缺陷 / 尸体料板 (核心烧毁/不通电/虚焊/短路)
PAT_FATAL_DEFECT = re.compile(
    r"(?:不通电|上机冰凉|点不亮|不发热|黑屏|进水|烧毁|核心坏|掉卡|代码43|尸体|报废|料板|仅供配件)",
    re.IGNORECASE,
)

# 标题自述的非完整售价信号：只有这类确证信息才允许据低价拦截 (README: 低价本身仅观察提示)
PAT_PRICE_DISCLAIMER = re.compile(
    r"(?:标价|价格|售价)(?:为|是|仅为|只是|仅是)?\s*(?:定金|订金|押金|占位|引流|非实价)"
    r"|(?:仅|只)(?:收|拍|付|售)\s*(?:定金|订金|押金)"
    r"|(?:定金|订金|押金)(?:链接|专拍|勿当全款)"
    r"|(?:非实价|虚标价|引流价|拍前问价|勿直接拍|不要直接拍|补差价|尾款|拍下改价)",
    re.IGNORECASE,
)

# 6. MDM / 企业监管机
PAT_MDM = re.compile(r"(?:企业管理机|配置锁|监管锁|MDM|绕过ID|屏蔽更新|防抹除|点抹除返回)", re.IGNORECASE)

# 7. 真正不可用的激活锁 (有ID锁)
PAT_ICLOUD_LOCKED = re.compile(r"(?:(?:有|带)ID锁|ID锁机)", re.IGNORECASE)

# 蜂窝网络属性 (独立匹配，避免把 64G/24G 这类容量数字误认为 4G/5G)
PAT_CELLULAR = re.compile(r"(?<![0-9A-Za-z])(?:4G|5G)(?![0-9A-Za-z]|显存|内存)", re.IGNORECASE)


def _targets_gpu(query: str) -> bool:
    """查询是否面向显卡单卡；跨品类与配件规则只允许在显卡搜索里生效。"""
    q = str(query or "")
    return bool(extract_gpu_models(q)) or any(k in q.upper() for k in ("显卡", "GPU", "HX"))


def _requested_capacity_gb(query: str) -> int:
    """查询中明确要求的最大存储容量 (GB)；用于避免对查询已要求的容量重复加成。"""
    q = str(query or "").upper()
    values = [
        int(x)
        for x in re.findall(r"(\d{2,4})\s*G(?:B)?\b", q)
        if int(x) in (16, 32, 64, 128, 256, 512)
    ]
    values += [int(x) * 1024 for x in re.findall(r"(\d+)\s*T(?:B)?\b", q)]
    return max(values) if values else 0


@dataclass
class ValueAssessment:
    effective_price: float
    fair_value: float
    vmi: float  # Value Matching Index = fair_value / effective_price
    tier: str   # GREAT_VALUE / FAIR / OVERPRICED_LOW_VALUE / BAIT_TRAP / ZERO_VALUE
    is_blocked: bool
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class PriceValueEngine:
    """价格与价值匹配评估引擎 (支持规则初筛 + LLM 智能裁判双阶段仲裁)"""

    def __init__(
        self,
        baseline_price: float | None = None,
        query: str = "",
        enable_llm: bool | None = None,
        llm_model: str | None = None,
    ):
        import os

        self.baseline_price = baseline_price
        self.query = query
        if enable_llm is None:
            self.enable_llm = os.getenv("GOOFISH_ENABLE_LLM", "0") in ("1", "true", "True") or bool(
                os.getenv("GOOFISH_LLM_API_KEY")
            )
        else:
            self.enable_llm = enable_llm
        self.arbiter = LLMArbiter(model=llm_model) if self.enable_llm else None

    def assess(
        self,
        item: dict[str, Any],
        batch_median: float | None = None,
        query: str | None = None,
    ) -> ValueAssessment:
        active_query = query if query is not None else self.query
        has_baseline = (self.baseline_price is not None) or (batch_median is not None)
        base = self.baseline_price or batch_median or 1000.0

        title = str(item.get("title", ""))
        desc = str(item.get("desc", ""))
        text = f"{title} {desc}"

        raw_p = item.get("price")
        price: float | None = None
        if raw_p is not None:
            clean_p = re.sub(r"[^\d.]", "", str(raw_p))
            if clean_p:
                try:
                    price = float(clean_p)
                except ValueError:
                    price = None

        effective_price: float | None = price
        reasons = []
        tags = []

        # -------------------------------------------------------------
        # 1. 虚假标价换算与多 SKU 真实对齐 (真实有效成本还原)
        # -------------------------------------------------------------
        skus = item.get("skus") or item.get("skuList") or []
        has_multi_sku = len(skus) > 1
        matched_sku = None

        if has_multi_sku:
            for s in skus:
                spec_name = s.get("name") or s.get("spec") or ""
                if "20G" in active_query.upper() and "20G" in spec_name.upper():
                    matched_sku = s
                    break

            if matched_sku:
                fallback_p = price if price is not None else 0.0
                real_sku_price = float(matched_sku.get("price", fallback_p))
                if price is not None and real_sku_price > price:
                    effective_price = real_sku_price
                    tags.append(f"多SKU真实到手价:¥{real_sku_price:.0f}({matched_sku.get('name')})")
                    reasons.append(f"多SKU引流陷阱: 列表标¥{price:.0f}实为低配，目标20G实际到手价为¥{real_sku_price:.0f}")

        if PAT_UNIT_PRICE_TRAP.search(text):
            if any(k in text for k in ("32G", "32g", "16gx2", "16G*2", "16x2", "套条", "套装", "共32g", "共32G")):
                if price is not None:
                    effective_price = price * 2.0
                    tags.append(f"单根引流(实际¥{effective_price:.0f})")
            elif any(k in text for k in ("出货", "一共", "200", "批量")):
                tags.append("批量单张标价")

        # -------------------------------------------------------------
        # 2. 免费保守正则快速判定 (高确定性直接处理，绝不消耗 LLM 费用与耗时)
        # -------------------------------------------------------------
        is_definitive_blocked = False
        parts_or_service_intent = wants_parts(active_query) or wants_service(active_query)
        clean_display_text = re.sub(r"不[出卖](?:假货|山寨|翻新|劣质|仿品|瑕疵品)", "", text)
        clean_defect_text = re.sub(r"(?:无|没|没有|不|并非|杜绝|告别)(?:黑屏|花屏|短路|烧毁|进水|掉电|死机|暗病|暗伤|修|维修)", "", text)
        clean_lock_text = re.sub(r"(?:没有|没|无|不带|不含)ID锁", "", text)
        if PAT_DISPLAY_ONLY.search(clean_display_text):
            reasons.append("纯展示/小作文贴/引流不出")
            is_definitive_blocked = True
        elif (
            PAT_FATAL_DEFECT.search(clean_defect_text)
            and not parts_or_service_intent
            # 残值（base×25%）以内或面议的练手件不预拦，交由下方价格敏感判定：
            # 致命故障只有在标价超过料板残值时才做确定性拦截。
            and price is not None
            and price > base * 0.25
        ):
            reasons.append("严重硬件暗病/无法点亮/代码43/报废板")
            is_definitive_blocked = True
        elif PAT_IRRELEVANT_CATEGORY.search(text) and "相机" in text and _targets_gpu(active_query):
            reasons.append("跨品类杂质(相机配件电池混入)")
            is_definitive_blocked = True

        # -------------------------------------------------------------
        # 3. 混合协同仲裁 (Hybrid Escalation to LLM):
        # 仅在免费正则出现「存疑/争议/深水大漏/多SKU复杂」时才调用 LLM
        # -------------------------------------------------------------
        if (
            self.enable_llm
            and self.arbiter
            and self.arbiter.configured
            and not is_definitive_blocked
            and _targets_gpu(active_query)  # JEV 状态机为显卡语义；非显卡搜索不做 LLM 升级
        ):
            escalate = False
            escalate_reason = ""

            # 存疑条件 A: 存在多规格且未被基础规则命中的复杂 SKU
            if has_multi_sku and not matched_sku:
                escalate = True
                escalate_reason = "多SKU规格复杂，交由LLM精准对齐真实目标价格"

            # 存疑条件 B: 价格异常偏低(低于中位数78%)且在硬件区间(>=500)，疑似大漏或精妙话术陷阱
            elif effective_price is not None and 500.0 <= effective_price < base * 0.78:
                escalate = True
                escalate_reason = f"深水高性价比捡漏(标价¥{effective_price:.0f}远低于大盘¥{base:.0f})，LLM防伪与暗病复核"

            # 存疑条件 C: 包含复杂专业魔改或服务争议词 (防止正则误杀，交由LLM上下文语义判决)
            elif any(w in text for w in ("改4090", "改3090", "换皮", "代改", "魔改", "双显卡", "租赁", "出租", "教程")):
                escalate = True
                escalate_reason = "涉及深度改装/复杂硬件做工/争议词汇，LLM语义定性与精准做工估值"

            if escalate:
                logger.info(f"触发 JEV 极简仲裁 [{escalate_reason}]: {title[:30]}")
                verdict = self.arbiter.judge_jev(item=item, query=active_query, baseline_price=base)
                if verdict is not None:
                    verdict_is_blocked = verdict.is_blocked
                    verdict_reasons = list(verdict.reasons)
                    verdict_tags = list(verdict.tags)
                    verdict_tags.append("JEV极简仲裁")

                    # 本地确定性致命锁与霸王条款兜底检查 (绝不让带ID锁或恶意霸王条款漏网)
                    if PAT_ICLOUD_LOCKED.search(clean_lock_text):
                        verdict_is_blocked = True
                        verdict_reasons.append("激活锁死/不可用砖头机(带ID锁)")
                    if PAT_ABUSIVE_TERMS.search(text):
                        verdict_is_blocked = True
                        verdict_reasons.append("高危扣款霸王条款")

                    return ValueAssessment(
                        effective_price=verdict.effective_price,
                        fair_value=verdict.fair_market_value,
                        vmi=verdict.vmi,
                        tier="BLOCKED_SPECIAL" if verdict_is_blocked else verdict.tier,
                        is_blocked=verdict_is_blocked,
                        reasons=verdict_reasons,
                        tags=verdict_tags,
                    )

        # -------------------------------------------------------------
        # 4. 常规保守正则估值与物理加成 (覆盖 85%+ 普通样本，0 耗时 0 费用)
        # -------------------------------------------------------------

        # -------------------------------------------------------------
        # 2. 价值折损与增益因子推导 (Value Modifiers)
        # -------------------------------------------------------------
        # 初始公允价值对齐基准
        fair_value = base
        physical_adjustments = 0.0  # 绝对物理工序/物料增减金额 (元)

        # ---------------- 绝对物理工序/物料项 (智能动态评估工序价值，告别写死值) ----------------
        # 补电容智能估值 (主要针对 90HX 等矿卡改装)
        if any(k in text for k in ("补好电容", "已补电容", "补过电容", "可补好电容")) and (price is not None and price >= 300.0):
            mod_val = 40.0
            mod_notes = ["满血x16"]
            m_cost = re.search(r"(?:加|花|收|费用|补电容)[¥￥]?(\d{2})(?:元)?(?:可?补|焊)?", text)
            if m_cost and 20 <= float(m_cost.group(1)) <= 80:
                mod_val = float(m_cost.group(1))
                mod_notes = [f"卖家实标工费¥{mod_val:.0f}"]
            else:
                if any(k in text for k in ("换好硅脂", "更换硅脂", "7921", "信越", "导热垫")):
                    mod_val += 10.0
                    mod_notes.append("换硅脂保养")
            physical_adjustments += mod_val
            tags.append(f"已补电容(+¥{mod_val:.0f}:{'&'.join(mod_notes)})")

        # ---------------- 3080 20G / GA102 魔改卡专属工序与硬件层级智能推导 ----------------
        if "3080" in active_query.upper() and ("20G" in active_query.upper() or "20G" in text.upper()):
            # 1. 散热总成魔改 (如改 4090 TUF / 4090 散热器，用料成本约 ¥150)
            if any(k in text for k in ("4090风扇", "4090散热", "改4090", "4090tuf")):
                physical_adjustments += 150.0
                tags.append("4090巨型散热总成(+¥150)")

            # 2. PCB与供电底板层级 (3090底板/全新高规格PCB供电强，稳定性高)
            if any(k in text for k in ("3090底板", "3090 pcb", "3090pcb", "新pcb")):
                physical_adjustments += 120.0
                tags.append("3090级豪华供电PCB(+¥120)")

            # 3. 旗舰一线非公散热与用料 (微星魔龙/超龙/ROG/猛禽/火神)
            if any(k in text for k in ("魔龙", "超龙", "猛禽", "STRIX", "火神", "TUF")):
                physical_adjustments += 80.0
                tags.append("一线旗舰做工用料(+¥80)")

            # 4. 全新/充新原包装在 (箱说全/几乎未用)
            if any(k in text for k in ("全新未拆封", "全套包装", "箱说全", "几乎没用过")):
                physical_adjustments += 60.0
                tags.append("全新/箱说全充新(+¥60)")

            # 5. 涡轮散热器 (服务器多卡堆叠刚需，但普通用户噪音大，市场普遍折价 ¥100)
            if "涡轮" in text:
                physical_adjustments -= 100.0
                tags.append("涡轮噪音折价(-¥100)")

        # 风扇故障/异响: 更换万丽/公版三风扇总成的自购固定成本约 -¥40
        if any(k in text for k in ("需要更换风扇", "风扇有点震动", "换风扇", "风扇异响")):
            physical_adjustments -= 40.0
            tags.append("风扇异响(-¥40更换成本)")

        # ---------------- 相对功能/流通性比例项 (决定设备基础效用) ----------------
        # 正向增益 (大容量 / 高配加成)；查询已明确要求同档容量时不再重复加成 (基准已是同档样本)
        requested_capacity = _requested_capacity_gb(active_query)
        if "256G" in text.upper():
            if requested_capacity < 256 and ("64G" in active_query.upper() or base <= 1600):
                fair_value *= 1.35  # 256G 相比 64G 价值增益 +35%
                tags.append("256G高配")
        elif "512G" in text.upper():
            if requested_capacity < 512:
                fair_value *= 1.50
                tags.append("512G超大容量")

        # 蜂窝加成仅对「可选加装蜂窝」的产品线生效（平板／二合一）；手机等蜂窝标配产品线不构成增值
        _low_text = text.lower()
        _optional_cellular = any(
            k in _low_text for k in ("ipad", "平板", "tablet", "matepad", "surface", "galaxy tab")
        ) or any(
            k in str(active_query).lower()
            for k in ("ipad", "平板", "tablet", "matepad", "surface", "galaxy tab")
        )
        if _optional_cellular and (
            any(k in text for k in ("插卡", "蜂窝", "LTE")) or PAT_CELLULAR.search(text)
        ):
            fair_value *= 1.15  # 蜂窝版加成 +15%
            tags.append("蜂窝插卡版")

        # 闲鱼口头质保防忽悠规则:
        # 闲鱼个人交易无保证金兜底，收货后拉黑无门。店保只在卖家一念之间，空气承诺严格不给予任何估值加成！
        if any(k in text for k in ("店保", "质保一个月", "质保三个月", "个人质保", "保修一个月")):
            tags.append("口头店保(不计入估值)")

        # 负向折损 (合理折价预期)
        # 严重致命故障 / 尸体卡 (不通电/核心坏/上机冰凉，仅剩料板拆颗粒残值 ~15%)
        fatal_discounted = False
        if PAT_FATAL_DEFECT.search(clean_defect_text) and not parts_or_service_intent:
            fair_value *= 0.15
            fatal_discounted = True
            tags.append("严重硬件故障/料板尸体")
            if price is not None and price > base * 0.25:
                reasons.append(f"严重致命故障(不通电/尸体卡)，但标价¥{price:.0f}远超料板残值")

        # 低价处理原则 (README)：低价本身只是观察信号，不能据此认定引流；
        # 只有标题自述「定金/非实价/拍前问价」等确证信息时，才允许按低价拦截。
        if price is not None and has_baseline and base >= 500.0 and price < base * 0.45:
            clean_price_text = re.sub(r"(?:不是|非|不收|无)(?:引流价|定金|订金|押金)", "", text)
            if PAT_PRICE_DISCLAIMER.search(clean_price_text):
                reasons.append(f"虚标低价/定金引流贴(标题自述非完整售价，标价¥{price:.0f}，大盘¥{base:.0f})")
                fair_value = 0.0
            elif not PAT_FATAL_DEFECT.search(clean_defect_text) and not PAT_STRICT_NO_RETAIL.search(text):
                tags.append(f"观察:标价¥{price:.0f}远低于大盘¥{base:.0f}(未核实)")

        # 批发与大批量货源判定:
        # 实战原则——不要因打包而完全拦截！只要是真实正确的硬件商品，把选择权交给使用者。
        # 算法只负责清晰打标透出真实交易门槛与库存属性。
        if PAT_STRICT_NO_RETAIL.search(text):
            tags.append("限制整批打包(卖家声明不单卖)")
        elif PAT_BATCH_PREFERRED.search(text):
            tags.append("大批量库存/偏好打包")

        # MDM / 企业管理机：折价 ~20%
        if PAT_MDM.search(text):
            fair_value *= 0.80
            tags.append("MDM/企业监管")

        # 扩容机：折价 ~15%
        if "扩容" in text or "拖容" in text:
            fair_value *= 0.85
            tags.append("扩容机")

        # 轻中度暗病 (指纹坏 / 接口不亮 / 缺金手指 / 屏幕色差 / 换外屏)：折价 ~40%
        m_mod = PAT_MODERATE_DEFECT.search(clean_defect_text)
        if m_mod:
            fair_value *= 0.60
            defect_desc = m_mod.group(0)
            tags.append(f"暗病缺陷({defect_desc})")

        # 严重致命缺陷 (点不亮 / 短路 / 烧毁 / 摔坏 / 尸体 / 纯展示 / ID锁)：折价 ~85%
        # 已按料板残值(15%)处理过的致命故障不重复打折，避免同一损伤双重折旧。
        m_sev = PAT_SEVERE_DEFECT.search(clean_defect_text)
        if m_sev and not fatal_discounted:
            fair_value *= 0.15
            defect_desc = m_sev.group(0)
            tags.append(f"严重故障({defect_desc})")

        if PAT_ICLOUD_LOCKED.search(clean_lock_text):
            fair_value = 0.0
            reasons.append("激活锁死/不可用砖头机")

        if PAT_DISPLAY_ONLY.search(clean_display_text):
            fair_value = 0.0
            if "纯展示/小作文贴/引流不出" not in reasons:
                reasons.append("纯展示/小作文贴/引流不出")

        if PAT_ABUSIVE_TERMS.search(text):
            reasons.append("高危扣款霸王条款")

        # 虚拟技术服务 / 驱动代刷 / 解锁教程 / 飞行表 / 代工焊电容
        if not wants_service(active_query) and PAT_VIRTUAL_SERVICE.search(text):
            # 真实整卡硬件(标价>=400或面议，且含显卡/单片/显存容量等整卡特征)，卖家附送驱动/技术支持属正常赠品，绝不误杀！
            is_hardware_card = (price is None or price >= 400.0) and (
                any(
                    k in text
                    for k in (
                        "显卡",
                        "单卡",
                        "整卡",
                        "原装",
                        "功能正常",
                        "包好",
                        "成色",
                        "箱说",
                        "三风扇",
                        "双风扇",
                        "单片",
                        "单张",
                        "现货",
                        "换好硅脂",
                        "测试好发货",
                        "顺丰到付",
                        "包邮",
                    )
                )
                or any(f"{c}G" in text.upper() for c in (8, 10, 11, 12, 16, 20, 24, 48))
            )
            if not is_hardware_card:
                if (price is not None and price <= 300) or not any(k in text for k in ("成色", "单卡", "整卡", "箱说")):
                    reasons.append("虚拟服务/驱动教程/飞行表/代工焊(非整卡硬件)")
                    fair_value = 0.0

        # 跨品类完全无关商品污染 (如搜显卡出相机、电池、充电器)
        # 例外：查询本身就要整机/主机，且标题就是整机（常带「华硕主板」等配置描述）。
        query_wants_host = bool(re.search(r"主机|整机|台式|工作站|全套", active_query))
        host_listing = query_wants_host and bool(re.search(r"主机|整机|台式|电脑|服务器|网吧", title))
        if _targets_gpu(active_query) and not host_listing:
            if PAT_IRRELEVANT_CATEGORY.search(title) or (
                PAT_IRRELEVANT_CATEGORY.search(text)
                and not any(k in text for k in ("显卡", "显存", "算力", "PCI", "GA102", "核芯", "风扇"))
            ):
                reasons.append("跨品类无关商品污染(相机/电池/数码外设混入显卡搜索)")
                fair_value = 0.0

        # 配件混淆 (显卡风扇/支架/散热套件混进整卡搜索)
        if _targets_gpu(active_query) and not wants_parts(active_query) and PAT_ACCESSORY_GPU.search(title):
            looks_like_card = any(k in text for k in ("带卡", "整卡", "原装显卡"))
            has_card_body = looks_like_card or any(
                f"{c}G" in title.upper() for c in (8, 10, 11, 12, 16, 20, 24, 48)
            )
            cheap_candidate = price is not None and price < 250
            confirmed_accessory = bool(re.search(r"(?<!不)(?:单卖|单出)|不含显卡|无显卡", title))
            if (cheap_candidate and not looks_like_card) or (confirmed_accessory and not has_card_body):
                reasons.append("周边配件/散热器/风扇(非整卡硬件)")
                fair_value = 0.0

        # (极端超低价不再单独硬拦；与上方「低价处理原则」统一：无确证信息只做观察提示)

        # 搜显卡单卡时整机混入引流；查询本身就在找主机/整机时保留整机结果
        if not query_wants_host and PAT_HOST_MACHINE.search(title) and any(
            k in active_query.upper()
            for k in ("HX", "3060", "3070", "3080", "3090", "4060", "4070", "4080", "4090", "5080", "5090", "显卡", "GPU")
        ):
            reasons.append("整机/台式电脑混入显卡单卡搜索")
            fair_value = 0.0

        # 型号降级混淆判定 (如搜 90HX 出 70HX/50HX/40HX/30HX/1660)
        if active_query:
            q_clean = active_query.upper().replace(" ", "")
            if "90HX" in q_clean and not re.search(r"90\s*HX", title, re.IGNORECASE):
                for other in ("70HX", "50HX", "40HX", "30HX", "20HX", "10HX", "1660", "1080", "2060", "2070", "1070", "1060"):
                    if other in title.upper():
                        reasons.append(f"借词引流低配型号:「{other}」")
                        fair_value *= 0.50
                        break

        # 3. 价值匹配度指数 (VMI) 与判决
        fair_value += physical_adjustments
        fair_value = max(0.0, fair_value)

        if effective_price is not None and effective_price > 0:
            vmi = fair_value / effective_price
        else:
            # 面议或未知价格：不确定实际标价，默认中性 VMI=1.0，不因无标价而判定为零价值拦截
            vmi = 1.0
            if effective_price is None:
                tags.append("价格面议/待议")

        # 分层判定
        is_blocked = False
        if reasons:
            tier = "BLOCKED_SPECIAL"
            is_blocked = True
        elif not has_baseline:
            # 宽泛跨型号搜索，无统一定价基准，不根据单一 VMI 阈值强卡高档正常商品
            tier = "FAIR_VALUE"
        elif vmi >= 1.15:
            tier = "GREAT_VALUE"      # 高性价比 / 价格超值
        elif vmi >= 0.85:
            tier = "FAIR_VALUE"       # 价格与价值匹配
        elif vmi >= 0.65:
            tier = "SLIGHTLY_OVERPRICED" # 略微溢价，放行但不推荐
        else:
            tier = "OVERPRICED_LOW_VALUE" # 价格与价值严重不匹配 (低价值高价)
            is_blocked = True
            reasons.append(f"价格与价值严重不匹配(真实价值估约¥{fair_value:.0f}，实际标价¥{effective_price:.0f}，VMI={vmi:.2f})")

        return ValueAssessment(
            effective_price=effective_price or 0.0,
            fair_value=fair_value,
            vmi=vmi,
            tier=tier,
            is_blocked=is_blocked,
            reasons=reasons,
            tags=tags,
        )

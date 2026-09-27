"""Goofish-Z 价格与价值动态匹配判定器 (Price-Value Classifier)
核心理念：
低价值不取决于某个静态名词，而是取决于「价格与真实使用价值的匹配度」：
1. 具有折价特征（如成色微瑕、补电容、改卡、正常拆机保养）：如果折价充分甚至高配低价，属于高性价比好物，必须放行并打标。
2. 具有暗病缺陷或单根引流虚标：如果折价严重不足、甚至总成本赶超正常好货（VMI < 0.65），判定为「价格与价值严重不匹配」予以拦截。
3. 虚拟服务、教程、驱动代刷、周边配件混入、纯展示小作文贴：价值归零，直接拦截。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from goofish_z.search_quality import wants_parts, wants_service

# 纯展示 / 占位 / 不出 / 小作文贴
PAT_DISPLAY_ONLY = re.compile(
    r"(?:仅展示|只展示|仅供欣赏|不[出卖]|非卖[品贴]|暂不出|勿拍|请勿拍下|拍下不发|谁拍谁傻|抵制奸商|科普贴|曝光帖|挂人|避坑指南)",
    re.IGNORECASE,
)

# 标单价引流 (搜套条写单根单价，搜批量写单张单价)
PAT_UNIT_PRICE_TRAP = re.compile(
    r"(?:标价[是为即](?:单[个片条盒张根]|单价)|单[个片条盒张根][标卖售价]|单[根条张]价|单片不包邮|单条出[，,]标价为单条价格|标价即单张价)",
    re.IGNORECASE,
)

# 虚拟技术服务 / 驱动代刷 / 解锁教程
PAT_VIRTUAL_SERVICE = re.compile(
    r"(?:魔改驱动|驱动技术|解锁命令|hiveos.*?解锁|算力珍珠|一键解锁|远程协助|代调|提供驱动|代刷|刷bios|bios修改|出教程)",
    re.IGNORECASE,
)

# 显卡周边零配件 (风扇/支架/散热模组/水冷头)
PAT_ACCESSORY_GPU = re.compile(
    r"(?:散热(?:风扇|器|模组|套件)|风扇支架|水冷头|改水冷|显卡伴侣|显卡支架|背板|挡板|空盒|包装盒|转接线|延长线|导热贴|铜片)",
    re.IGNORECASE,
)

# 严重致命故障 / 尸体
PAT_SEVERE_DEFECT = re.compile(
    r"(?:点不亮|短路|烧毁|摔坏|开不开机|内屏摔坏|进水|打架卡|尸体|配件机|无视频输出|"
    r"花屏|炸管|掉电|盲盒|dos测试不通过)",
    re.IGNORECASE,
)

# 轻中度功能缺陷与暗病
PAT_MODERATE_DEFECT = re.compile(
    r"(?:缺[少了个]金手指|金手指.*?(?:断|缺|掉)|不识别|接口.*?(?:不亮|坏|缺失)|(?:dp|hdmi).*?不亮|"
    r"指纹坏|指纹不灵|无指纹|wifi[是为]坏的|无法连wifi|屏幕按压会出现黑点|屏幕.*?有色差|换屏有色差|外屏维修过)",
    re.IGNORECASE,
)

# 霸王条款
PAT_ABUSIVE_TERMS = re.compile(
    r"(?:退货扣(?:[1-9]\d{2,})|拒收扣(?:[1-9]\d{2,}))",
    re.IGNORECASE,
)

# 跨品类完全无关污染 (如搜显卡时闲鱼召回了数码相机、相机电池、衣服鞋帽)
PAT_IRRELEVANT_CATEGORY = re.compile(
    r"(?:数码相机|微单|单反|相机电池|锂离子电池|充电器套装|耳机套|手机壳|保护套|衣服|鞋子|男装|女装|羽绒服|包包|口红)",
    re.IGNORECASE,
)

# 6. MDM / 企业监管机
PAT_MDM = re.compile(r"(?:企业管理机|配置锁|监管锁|MDM|绕过ID|屏蔽更新|防抹除|点抹除返回)", re.IGNORECASE)

# iCloud 真正不可用激活锁 (带ID锁/有ID锁)
PAT_ICLOUD_LOCKED = re.compile(r"(?:(?:有|带)ID锁|ID锁机)", re.IGNORECASE)


@dataclass
class ClassificationResult:
    is_low_value: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    effective_price: float = 0.0
    fair_value: float = 0.0
    vmi: float = 1.0


class LowValueClassifier:
    """基于价格-价值匹配的低价值分类器"""

    def __init__(self, strict_model_match: bool = True):
        self.strict_model_match = strict_model_match

    def evaluate(
        self,
        item: dict[str, Any],
        query: str = "",
        batch_median: float | None = None,
    ) -> ClassificationResult:
        title = str(item.get("title", ""))
        desc = str(item.get("desc", ""))
        text = f"{title} {desc}"

        raw_p = item.get("price")
        price = 0.0
        if raw_p:
            clean_p = re.sub(r"[^\d.]", "", str(raw_p))
            try:
                price = float(clean_p)
            except ValueError:
                pass

        effective_price = price
        reasons: list[str] = []
        tags: list[str] = []

        base = batch_median or price or 1000.0

        # 1. 真实成本还原 (单价陷阱与多 SKU 真实对齐)
        skus = item.get("skus") or item.get("skuList") or []
        if skus and len(skus) > 1:
            matched_sku = None
            for s in skus:
                spec_name = s.get("name") or s.get("spec") or ""
                if "20G" in query.upper() and "20G" in spec_name.upper():
                    matched_sku = s
                    break
                elif "32G" in query.upper() and any(k in spec_name.upper() for k in ("32G", "16GX2", "16G*2")):
                    matched_sku = s
                    break
                elif "256G" in query.upper() and "256G" in spec_name.upper():
                    matched_sku = s
                    break

            if matched_sku:
                real_sku_price = float(matched_sku.get("price", price))
                if real_sku_price > price:
                    effective_price = real_sku_price
                    tags.append(f"多SKU真实到手价:¥{real_sku_price:.0f}({matched_sku.get('name')})")
                    reasons.append(f"多SKU引流陷阱: 列表标¥{price:.0f}实为低配，目标规格实际到手价为¥{real_sku_price:.0f}")

        if PAT_UNIT_PRICE_TRAP.search(text):
            if any(k in text for k in ("32G", "32g", "16gx2", "16G*2", "16x2", "套条", "套装", "共32g", "共32G")):
                effective_price = price * 2.0
                tags.append(f"单根引流(实际¥{effective_price:.0f})")
                reasons.append("标价为单件/单根虚假引流")
            elif any(k in text for k in ("出货", "一共", "200", "批量")):
                tags.append("批量单张标价")

        # 2. 公允价值推导 (混合模型: 基础功能比例项 + 绝对物理改装/物料项)
        fair_value = base
        physical_adjustments = 0.0

        # 绝对物理改装项 (实战铁律: 补了就是补满到 x16，基准约 ¥40，换硅脂追加 ¥10)
        if any(k in text for k in ("补好电容", "已补电容", "补过电容", "可补好电容")) and price >= 300.0:
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

        if any(k in text for k in ("需要更换风扇", "风扇有点震动", "换风扇", "风扇异响")):
            physical_adjustments -= 40.0
            tags.append("风扇异响(-¥40更换成本)")

        # 相对功能/流通性比例项
        if "256G" in text.upper():
            if "64G" in query.upper() or base <= 1600:
                fair_value *= 1.35
                tags.append("256G高配")
        elif "512G" in text.upper():
            fair_value *= 1.50
            tags.append("512G超大容量")

        if any(k in text for k in ("插卡", "蜂窝", "4G", "5G", "LTE")):
            fair_value *= 1.15
            tags.append("蜂窝插卡版")

        # 闲鱼口头店保/个人质保防忽悠 (空气承诺严格不计入估值加成)
        if any(k in text for k in ("店保", "质保一个月", "质保三个月", "个人质保", "保修一个月")):
            tags.append("口头店保(不计入估值)")

        if PAT_MDM.search(text):
            fair_value *= 0.80
            tags.append("MDM/企业监管")

        if "扩容" in text or "拖容" in text:
            fair_value *= 0.85
            tags.append("扩容机")

        m_mod = PAT_MODERATE_DEFECT.search(text)
        if m_mod:
            fair_value *= 0.60
            defect_desc = m_mod.group(0)
            tags.append(f"暗病缺陷({defect_desc})")
            reasons.append(f"硬件缺陷/暗病: 命中「{defect_desc}」")

        m_sev = PAT_SEVERE_DEFECT.search(text)
        if m_sev:
            fair_value *= 0.15
            defect_desc = m_sev.group(0)
            tags.append(f"严重故障({defect_desc})")
            reasons.append(f"硬件缺陷/严重故障: 命中「{defect_desc}」")

        if PAT_ICLOUD_LOCKED.search(text):
            fair_value = 0.0
            reasons.append("激活锁死/不可用砖头机")

        clean_display_text = re.sub(r"不[出卖](?:假货|山寨|翻新|劣质|仿品|瑕疵品)", "", text)
        if PAT_DISPLAY_ONLY.search(clean_display_text):
            fair_value = 0.0
            reasons.append("纯展示/小作文贴/引流不出")

        if PAT_ABUSIVE_TERMS.search(text):
            reasons.append("高危扣款霸王条款")

        if not wants_service(query) and PAT_VIRTUAL_SERVICE.search(text):
            is_hardware_card = (price >= 400.0) and (
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
                if price <= 300 or not any(k in text for k in ("成色", "单卡", "整卡", "箱说")):
                    reasons.append("虚拟服务/驱动教程/代刷脚本(非整卡硬件)")
                    fair_value = 0.0

        # 跨品类完全无关商品污染 (如搜显卡出相机、电池、充电器)
        if any(k in query.upper() for k in ("HX", "3080", "3090", "显卡", "GPU")):
            if PAT_IRRELEVANT_CATEGORY.search(title) or (
                PAT_IRRELEVANT_CATEGORY.search(text)
                and not any(k in text for k in ("显卡", "显存", "算力", "PCI", "GA102", "核芯", "风扇"))
            ):
                reasons.append("跨品类无关商品污染(相机/电池/数码外设混入显卡搜索)")
                fair_value = 0.0

        if not wants_parts(query) and PAT_ACCESSORY_GPU.search(title):
            if price < 250 and not any(k in text for k in ("带卡", "整卡", "原装显卡")):
                reasons.append("周边配件/散热器/风扇(非整卡硬件)")
                fair_value = 0.0

        if price <= 10.0 and base >= 500.0:
            reasons.append(f"超低价引流定金贴(标价¥{price:.0f}远低于基准¥{base:.0f})")
            fair_value = 0.0

        if self.strict_model_match and query:
            q_clean = query.upper().replace(" ", "")
            if "90HX" in q_clean and not re.search(r"90\s*HX", title, re.IGNORECASE):
                for other in ("50HX", "70HX", "30HX", "1660", "1080", "2060", "2070"):
                    if other in title.upper():
                        reasons.append(f"型号不符/借词引流: 实际为「{other}」")
                        fair_value *= 0.50
                        break
            elif "3080" in q_clean and not re.search(r"3080", title):
                for other in ("1080", "2080", "3070", "3060", "3090"):
                    if other in title:
                        reasons.append(f"型号不符/借词引流: 实际为「{other}」")
                        fair_value *= 0.50
                        break

        # 3. 价值匹配度指数 (VMI) 与判决
        fair_value += physical_adjustments
        fair_value = max(0.0, fair_value)

        vmi = (fair_value / effective_price) if effective_price > 0 else 0.0
        is_low_value = False

        if reasons:
            is_low_value = True
        elif vmi < 0.65:
            is_low_value = True
            reasons.append(
                f"价格与价值严重不匹配(估算公允价值¥{fair_value:.0f}，实际到手¥{effective_price:.0f}，匹配度VMI={vmi:.2f})"
            )

        return ClassificationResult(
            is_low_value=is_low_value,
            score=round(1.0 / (vmi + 0.01), 2),
            reasons=reasons,
            tags=tags,
            effective_price=round(effective_price, 2),
            fair_value=round(fair_value, 2),
            vmi=round(vmi, 2),
        )

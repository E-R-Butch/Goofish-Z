"""
JEV 极简约束型 LLM 仲裁器 (JEV-Style Constrained LLM Arbiter)
极限裁剪 LLM 的生成空间，严禁长篇大论，仅输出极简决策代码(15~25 tokens)。
后续所有估值数学运算、标签生成与文案组装全部交由本地 Python 纳秒级完成。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from goofish_z.search_quality import wants_parts, wants_service

logger = logging.getLogger(__name__)

# 做工档位映射 (本地 Python 极速计算加成)
HARDWARE_MOD_MAP = {
    0: ("标准做工", 0.0, "标准风冷三风扇"),
    1: ("3090豪华PCB", 120.0, "3090级豪华PCB加持(+¥120)"),
    2: ("4090巨型散热改装", 150.0, "4090级散热模组移植(+¥150)"),
    3: ("涡轮公版", -100.0, "服务器涡轮噪音折价(-¥100)"),
    4: ("一线旗舰做工", 80.0, "魔龙/TUF等旗舰做工(+¥80)"),
}

BLOCK_TYPE_MAP = {
    0: (False, "目标型号正品整卡"),
    1: (True, "周边零配件/散热器/延长线/空板(非整卡)"),
    2: (True, "虚拟教程/驱动软件/算力租赁/代工服务"),
    3: (True, "纯展示/小作文/已出勿拍留念"),
    4: (True, "型号不符/货不对板/借词引流的其它非目标型号"),
}


@dataclass
class JEVVerdict:
    block_type: int  # 0=整卡, 1=配件, 2=租赁/教程, 3=展示
    real_price: float  # 真实目标到手价 (0 表示直接取标价)
    mod_tier: int  # 硬件做工代号 (0~4)


@dataclass
class LLMVerdict:
    is_genuine_target: bool
    effective_price: float
    fair_market_value: float
    vmi: float
    tier: str
    is_blocked: bool
    reasons: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


SYSTEM_PROMPT = """你是一个硬件分类状态机。严格依据商品信息输出仅包含三个字段的极简 JSON，严禁输出任何分析理由或多余文字。
字段定义：
- "t": 标的分类代号(严格限0~4共5个互斥状态):
  0 = 买家搜索的目标型号整卡硬件
  1 = 周边配件/延长线/散热器/空板
  2 = 算力租赁/部署教程/驱动软件
  3 = 纯展示/已出勿拍留念
  4 = 型号不符/货不对板/借词引流的其它非目标型号(如搜90HX出1660S、笔记本等)
- "p": 买家购买目标规格真实所需支付金额(数字)。若标价真实单一或无多SKU套路填0；若存在多SKU阴阳引流填目标规格实际价格
- "m": 硬件档位代号(严格限0~4共5个状态):
  0 = 标准做工三风扇
  1 = 豪华底板(3090底板/90HX补电容满血)
  2 = 巨型散热改装(4090散热模组/水冷)
  3 = 涡轮公版(服务器高噪音)
  4 = 一线非公旗舰(火神/魔龙/TUF)

输出示例：{"t":0,"p":0,"m":1}"""


SYSTEM_PROMPT_FULL = """你是一个二手显卡交易专家分析师。
请审查商品并输出完整 JSON:
{
  "is_genuine_target": true or false,
  "effective_price": float (真实买到目标规格所需总金额),
  "fair_market_value": float (公允估值),
  "vmi": float,
  "tier": "GREAT_VALUE" | "FAIR_VALUE" | "SLIGHTLY_HIGH" | "BLOCKED_SPECIAL",
  "is_blocked": true or false,
  "reasons": ["拦截或做工分析理由"],
  "tags": ["标签"]
}
遇到教程、算力租赁、延长线配件、纯展示贴，is_blocked 必须为 true。"""


class LLMArbiter:
    def judge_full(
        self,
        item: dict[str, Any],
        query: str,
        baseline_price: float,
    ) -> LLMVerdict | None:
        """全量自由发挥模式: 深度语义分析、细致理由阐述、多维考量。"""
        title = item.get("title", "")
        desc = item.get("desc", "")
        price_raw = item.get("price", "0")
        skus = item.get("skus") or item.get("skuList") or []

        user_content = f"""【买家目标搜索】：{query}
【市场大盘中位数基准】：¥{baseline_price:.1f}
【商品标题】：{title}
【商品展示标价】：{price_raw}
【商品详细描述】：{desc if desc else "(无详细描述，见标题)"}
【多 SKU 规格列表】：{json.dumps(skus, ensure_ascii=False) if skus else "单一规格"}

请深入分析该商品，识别是否存在假标价、多SKU引流、虚假服务、改装用料加成，并输出以下 JSON 结构：
{{
  "is_genuine_target": true or false,
  "effective_price": float (买家真实购买所需成本),
  "fair_market_value": float (公允价值估算),
  "vmi": float (fair_market_value / effective_price),
  "tier": "GREAT_VALUE" | "FAIR_VALUE" | "SLIGHTLY_HIGH" | "BLOCKED_SPECIAL",
  "is_blocked": true or false,
  "reasons": ["详细分析理由1", "详细分析理由2"],
  "tags": ["特征标签1", "特征标签2"]
}}"""

        req_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_FULL},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,  # 全量模式不压缩 Token 预算，保全最高智力与完整思维链
        }

        try:
            req = urllib.request.Request(
                f"{self.base_url.rstrip('/')}/chat/completions",
                data=json.dumps(req_body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choice = result.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content") or ""
                
                # 提取 JSON (先脱去 markdown codeblock)
                clean_content = content.strip()
                if "```json" in clean_content:
                    clean_content = clean_content.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in clean_content:
                    clean_content = clean_content.split("```", 1)[1].split("```", 1)[0].strip()

                m = re.search(r"\{[\s\S]*\}", clean_content)
                if not m:
                    logger.warning(f"全量 LLM 未提取出有效 JSON: {content[:100]}")
                    return None
                parsed = json.loads(m.group(0))
                
                eff_p = float(parsed.get("effective_price", 0.0))
                # 若模型返回 0 或未给有效价格，以标价保底
                if eff_p <= 0 and price_raw:
                    try:
                        eff_p = float(re.sub(r"[^\d.]", "", str(price_raw)))
                    except ValueError:
                        pass

                fair_v = float(parsed.get("fair_market_value", 0.0))
                if fair_v <= 0:
                    fair_v = baseline_price

                vmi_val = float(parsed.get("vmi", 0.0))
                if vmi_val <= 0 and eff_p > 0:
                    vmi_val = fair_v / eff_p

                return LLMVerdict(
                    is_genuine_target=bool(parsed.get("is_genuine_target", True)),
                    effective_price=eff_p,
                    fair_market_value=fair_v,
                    vmi=vmi_val,
                    tier=str(parsed.get("tier", "FAIR_VALUE")),
                    is_blocked=bool(parsed.get("is_blocked", False)),
                    reasons=list(parsed.get("reasons", [])),
                    tags=list(parsed.get("tags", [])),
                )
        except Exception as e:
            logger.warning(f"全量 LLM 调用异常: {e}")
            return None

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("GOOFISH_LLM_BASE_URL")
            or "http://192.168.31.66:4000/v1"
        )
        self.api_key = (
            api_key
            or os.environ.get("GOOFISH_LLM_API_KEY")
            or os.environ.get("HERMES_CUSTOM_192_168_31_66_4000_API_KEY")
            or ""
        )
        self.model = (
            model
            or os.environ.get("GOOFISH_LLM_MODEL")
            or "antigravity-gemini-3.8-flash"
        )

    def judge_jev(
        self,
        item: dict[str, Any],
        query: str,
        baseline_price: float,
    ) -> LLMVerdict | None:
        """JEV 极简调用: 单次生成仅 ~15 个 token，本地 Python 负责所有加减与文案。"""
        title = item.get("title", "")
        desc = item.get("desc", "")
        price_raw = item.get("price", "0")
        skus = item.get("skus") or item.get("skuList") or []

        sku_summary = ""
        if skus:
            sku_items = [f"{s.get('name')}:{s.get('price')}" for s in skus[:4]]
            sku_summary = f"SKUs:[{', '.join(sku_items)}]"

        user_content = f"目标:{query} | 标价:{price_raw} | 标题:{title} | {sku_summary} | 描述:{desc[:80] if desc else ''}"

        req_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": 600,  # 确保 Gemini 思考链(约150~250tokens)不被截断
        }

        try:
            req = urllib.request.Request(
                f"{self.base_url.rstrip('/')}/chat/completions",
                data=json.dumps(req_body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choice = result.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content") or ""
                # print("JEV content received:", content)

                m = re.search(r"\{.*?\}", content.replace("\n", ""))
                if not m:
                    logger.warning(f"JEV 未能解析出 JSON: {content}")
                    return None
                parsed = json.loads(m.group(0))

                b_type = int(parsed.get("t", 0))
                real_p = float(parsed.get("p", 0.0))
                m_tier = int(parsed.get("m", 0))

                # 校验状态代号是否在合法 0~4 范围内，非法则拒绝并回退本地引擎
                if b_type not in BLOCK_TYPE_MAP:
                    logger.warning(f"JEV 状态代号超出范围: {b_type} (合法为 0~4)，降级回退本地规则")
                    return None

                # --- 本地 Python 纳秒级完成数学运算与文案组装 ---
                is_blocked, block_reason = BLOCK_TYPE_MAP[b_type]
                if b_type == 1 and wants_parts(query):
                    is_blocked = False
                elif b_type == 2 and wants_service(query):
                    is_blocked = False

                # 真实价格
                raw_p_num = 0.0
                if price_raw:
                    try:
                        raw_p_num = float(re.sub(r"[^\d.]", "", str(price_raw)))
                    except ValueError:
                        pass
                effective_p = real_p if real_p > 0 else raw_p_num

                # 估值加成 (兼容 90HX 补电容与 3080 魔改)
                is_90hx = "90HX" in query.upper()
                if m_tier == 1:
                    mod_name = "已补电容(满血x16)" if is_90hx else "3090豪华PCB"
                    mod_delta = 40.0 if is_90hx else 120.0
                    mod_desc = "已补满血x16电容(+¥40)" if is_90hx else "3090级豪华PCB加持(+¥120)"
                else:
                    mod_name, mod_delta, mod_desc = HARDWARE_MOD_MAP.get(m_tier, ("标准", 0.0, ""))
                fair_val = (baseline_price + mod_delta) if not is_blocked else 0.0
                vmi = (fair_val / effective_p) if effective_p > 0 else 0.0

                reasons = []
                tags = []
                tier_str = "FAIR_VALUE"
                if is_blocked:
                    tier_str = "BLOCKED_SPECIAL"
                    reasons.append(block_reason)
                elif vmi < 0.65 and effective_p > 0:
                    tier_str = "OVERPRICED_LOW_VALUE"
                    is_blocked = True
                    reasons.append(f"价格与价值严重不匹配(VMI={vmi:.2f}<0.65)")
                elif vmi >= 1.05:
                    tier_str = "GREAT_VALUE"
                elif vmi < 0.90:
                    tier_str = "SLIGHTLY_HIGH"

                if not is_blocked:
                    if real_p > 0 and real_p > raw_p_num:
                        tags.append(f"多SKU真实到手价:¥{real_p:.0f}")
                        reasons.append(f"多SKU引流还原: 实际到手价为¥{real_p:.0f}")
                    if mod_desc:
                        tags.append(mod_name)
                        if mod_delta != 0:
                            reasons.append(mod_desc)

                return LLMVerdict(
                    is_genuine_target=not is_blocked,
                    effective_price=effective_p,
                    fair_market_value=fair_val,
                    vmi=vmi,
                    tier=tier_str,
                    is_blocked=is_blocked,
                    reasons=reasons,
                    tags=tags,
                )
        except Exception as e:
            logger.warning(f"JEV 仲裁调用失败: {e}")
            return None

"""Explain evidence-backed search exclusions without guessing from low prices."""
from __future__ import annotations

import re
from typing import Any

from .blacklist import (
    extract_capacities, extract_gpu_models, is_system_unit, normalize_search_text,
)

# Ignore a bundled extra or a denial before classifying the offered product.
_BUNDLE = re.compile(r"(?:附赠|赠送|附送|送|附带|自带)(?:最新的)?[^，。；\n]{0,14}(?:教程|驱动)(?:及驱动)?")
_SERVICE_INTENT = re.compile(r"服务|维修|安装|代装|代刷|刷机|刷\s*BIOS|清灰|清洗|除尘|扩容|教程|驱动|租赁|出租", re.I)
_SERVICE = re.compile(
    r"上门(?:服务|维修|安装|清灰|清洗|除尘|装机|组装|重装|修电脑)"
    r"|(?:专业|承接|提供|接单|代客)(?:显卡|电脑|笔记本)?(?:维修|装机|清灰|刷机|扩容)"
    r"|(?:代装|代刷|代修)(?:系统|电脑|显卡|驱动|BIOS)"
    r"|(?:维修|安装|清灰|扩容|刷机|刷\s*BIOS)(?:服务|接单|代工)"
    r"|(?:远程|同城)(?:装机|安装|维修|重装|刷机)"
    r"|(?:显卡|电脑|笔记本)维修(?!过|史|记录|了|好|完|后|痕迹|练手|用途)"
    r"|(?:租赁|出租|按天租)", re.I,
)
_PART = r"(?:外壳|空壳|空盒|包装盒|散热器|散热模组|风扇|水冷头|背板|支架|挡板|延长线|转接线)"
_PART_ONLY = re.compile(r"(?:仅售|只售|只卖|单卖|单出|只有)\s*" + _PART + r"|" + _PART + r"\s*(?:单卖|单出|不含显卡|无显卡)")
_PART_OFFER = re.compile(r"显卡支架|显卡延长线|显卡转接线|水冷头|空壳|空盒|包装盒")
_BARE_CARD = re.compile(
    r"(?:无|没有|没|缺少)(?:GPU)?核心(?!故障|损伤|问题|损坏)"
    r"|(?:无|没有|没)显存(?!故障|损伤|问题|损坏)"
    r"|核心[、，\s/]*(?:显存)?(?:均无|没了|已拆)"
    r"|芯片没了.{0,12}(?:报废|无维修价值)"
    r"|料板|料版|显卡模型|模型显卡", re.I,
)
_PART_QUERY = re.compile(_PART + r"|PCB|料板|料版|模型|无核心|拆件", re.I)
_PRICE_DISCLAIMER = re.compile(
    r"(?:标价|价格|售价)(?:为|是|仅为|只是|仅是)?\s*(?:定金|订金|押金|占位|引流|非实价)"
    r"|(?:仅|只)(?:收|拍|付|售)\s*(?:定金|订金|押金)"
    r"|(?:定金|订金|押金)(?:链接|专拍|勿当全款)"
    r"|(?:非实价|虚标价|引流价|拍前问价|勿直接拍|不要直接拍)", re.I,
)
_NOT_FOR_SALE = re.compile(r"非卖[帖贴]|不卖[仅只]?展示|仅供展示|只展示不卖|不是卖货|勿拍[仅只]?展示|展示[帖贴]")


def offering_text(title: str) -> str:
    text = normalize_search_text(title)
    text = _BUNDLE.sub("", text)
    if re.search(r"显卡|图形卡", text) and re.search(r"成色|功能|无拆|拆机|风扇|单片|包算力", text):
        text = re.sub(r"提供(?:最新的)?[^，。；\n]{0,14}(?:教程|驱动)(?:及驱动)?", "", text)
    text = re.sub(r"(?:支持|可|免费|附送|赠送)上门(?:安装|送货|自提)", "", text)
    text = re.sub(r"(?:不支持|不提供|不做|没有)上门(?:服务|维修|安装|清灰|清洗|装机)", "", text)
    return text


def wants_service(query: str) -> bool:
    query = re.sub(r"免驱动|无需驱动|不用驱动|无维修|没维修", "", normalize_search_text(query))
    return bool(_SERVICE_INTENT.search(query))


def listing_reasons(query: str, item: dict[str, Any]) -> list[str]:
    title = normalize_search_text(item.get("title", ""))
    query = normalize_search_text(query)
    offering = offering_text(title)
    reasons = []
    if _NOT_FOR_SALE.search(title):
        reasons.append("非售卖条目：标题明确为展示或非卖帖")
    price_text = re.sub(r"(?:不是|非|不收|无)(?:引流价|定金|订金|押金)", "", title)
    if _PRICE_DISCLAIMER.search(price_text):
        reasons.append("非完整售价：标题注明定金、押金、引流或需另行询价")
    if not wants_service(query) and _SERVICE.search(offering):
        reasons.append("服务条目：标题提供上门、维修、安装、代工或租赁")
    models = extract_gpu_models(query)
    parts = re.sub(r"(?:附赠|赠送|附送|送|附带|带|含|自带|配有|配备|采用)(?:原装|全新|金属)?\s*" + _PART, "", offering)
    parts = re.sub(r"(?:无|没|没有|缺少|不含|不带)(?:原装)?\s*" + _PART, "", parts)
    physical_card = re.search(r"显卡|图形卡", title) and (
        extract_capacities(title) or re.search(r"成色|功能正常|无拆|自用|拆机", title)
    )
    cooler = re.search(r"(?:显卡散热器|[a-z0-9][\w\s+/-]{0,20}散热器)(?!换新|已换|更换)", parts, re.I)
    if models and not _PART_QUERY.search(query) and (_PART_ONLY.search(offering) or cooler or (_PART_OFFER.search(parts) and not physical_card)):
        reasons.append("配件条目：标题明确只售配件，不是完整显卡")
    bare = re.sub(r"(?:不是|并非)无核心", "", title)
    if models and not _PART_QUERY.search(query) and _BARE_CARD.search(bare):
        reasons.append("不完整显卡：标题说明缺核心／显存、料板或模型")
    return reasons


def model_reasons(query: str, item: dict[str, Any]) -> list[str]:
    requested = extract_gpu_models(query)
    if not requested:
        return []
    title = normalize_search_text(item.get("title", ""))
    # A performance comparison does not make the compared model the product.
    offered = re.sub(r"(?:性能)?(?:对标|相当于|媲美|堪比|接近|比肩|秒杀)[^，。；\n]*", "", title)
    actual = extract_gpu_models(offered)
    if actual and not requested.intersection(actual):
        return [f"型号不匹配：搜索 {' / '.join(sorted(requested))}，标题型号为 {' / '.join(sorted(actual))}"]
    # Known foreign product types are evidence of irrelevance. An unspecified
    # GPU or a whole-system bundle remains inspectable instead of being guessed.
    if not actual and not is_system_unit(item) and not re.search(r"显卡|图形卡|GPU|板卡", title, re.I):
        if re.search(r"相机|镜头|主板|摄像机|相机配件", title):
            return ["商品类型不符：未出现搜索显卡型号，标题为相机、镜头或主板"]
    return []


def capacity_reasons(query: str, title: str) -> list[str]:
    requested, actual = extract_capacities(query), extract_capacities(title)
    if requested and actual and not requested.intersection(actual):
        return [f"搜索{' / '.join(str(n) + 'G' for n in sorted(requested))}但商品容量不匹配"]
    return []

"""Canonical marketplace URLs. No caller-controlled hosts, ports or redirects."""
from urllib.parse import parse_qs, urlencode, urlsplit
import re

from .models import PLATFORMS

DOMAINS = {"taobao": ("taobao.com", "tmall.com"), "jd": ("jd.com",), "pdd": ("yangkeduo.com", "pinduoduo.com")}
HOME = {"taobao": "https://www.taobao.com/", "jd": "https://www.jd.com/", "pdd": "https://mobile.yangkeduo.com/"}


def check_platform(platform: str) -> str:
    if platform not in PLATFORMS:
        raise ValueError("平台必须为 taobao、jd 或 pdd")
    return platform


def search_url(platform: str, query: str) -> str:
    check_platform(platform)
    query = query.strip()
    if not query or len(query) > 200:
        raise ValueError("搜索关键词长度须为 1 到 200")
    if platform == "taobao":
        return "https://s.taobao.com/search?" + urlencode({"q": query})
    if platform == "jd":
        return "https://search.jd.com/Search?" + urlencode({"keyword": query, "enc": "utf-8"})
    return "https://mobile.yangkeduo.com/search_result.html?" + urlencode({"search_key": query, "search_type": "goods"})


def allowed_document(platform: str, url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password and
            parsed.port in (None, 443) and any(parsed.hostname == d or (parsed.hostname or "").endswith("." + d) for d in DOMAINS[platform]))


def product_url(platform: str, url: str) -> tuple[str, str]:
    check_platform(platform)
    parsed = urlsplit(str(url))
    if not allowed_document(platform, str(url)):
        raise ValueError("请提供对应平台的 HTTPS 商品详情链接")
    params = parse_qs(parsed.query)
    if platform == "jd" and parsed.hostname == "item.jd.com":
        match = re.fullmatch(r"/(\d+)\.html", parsed.path)
        if match:
            return f"https://item.jd.com/{match[1]}.html", match[1]
    if platform == "taobao" and parsed.hostname in ("item.taobao.com", "detail.tmall.com") and parsed.path == "/item.htm":
        value = params.get("id", [""])[0]
        if re.fullmatch(r"\d{1,30}", value):
            return f"https://{parsed.hostname}/item.htm?id={value}", value
    if platform == "pdd" and parsed.hostname == "mobile.yangkeduo.com" and parsed.path in ("/goods.html", "/goods1.html", "/goods2.html"):
        value = params.get("goods_id", [""])[0]
        if re.fullmatch(r"\d{1,30}", value):
            return f"https://mobile.yangkeduo.com/goods.html?goods_id={value}", value
    raise ValueError("无法识别商品链接，请粘贴完整商品详情链接，不能使用分享短链或任意页面")

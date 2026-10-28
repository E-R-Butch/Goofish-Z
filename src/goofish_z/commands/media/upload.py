"""media upload — 上传图片到闲鱼 CDN。

图片上传打的是 CDN（stream-upload.goofish.com），吃独立媒体预算（`media` 桶），
不占商品发布/发消息的经营类写预算。

**只有服务端明确确认过的完整图片收据才返回**：`success is not True`、缺 `object`、
尺寸解析失败、CDN 地址不是闲鱼域，一律报错。之前直接读 `raw["object"]`，上传失败会
返回 `url: ""` 和 `0x0` 静默通过，发布时拿空图，而调用方以为成功了。
"""

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from requests.exceptions import RequestException

from goofish_z.core import Session, Strategy, command
from goofish_z.core.errors import GoofishError, RiskControlError
from goofish_z.core.limiter import acquire
from goofish_z.core.session import USER_AGENT

UPLOAD_URL = "https://stream-upload.goofish.com/api/upload.api"

# 上传响应里出现这些标记按风控处理，与 mtop 的 _RISK_KEYWORDS 口径一致。
_RISK_MARKERS = ("RGV587", "USER_VALIDATE", "ILLEGAL_ACCESS")
# 收据 URL 必须落在闲鱼/CDN 域内。
_RECEIPT_HOSTS = ("alicdn.com", "goofish.com")


def validate_image_path(path: str) -> Path:
    source = Path(path).expanduser()
    if not source.is_file() or source.stat().st_size == 0:
        raise GoofishError("图片文件不存在或为空")
    mime = mimetypes.guess_type(str(source))[0]
    if not mime or not mime.startswith("image/"):
        raise GoofishError("无法识别图片格式，请使用有正确扩展名的图片")
    return source


def validate_image_receipt(value: Any) -> dict[str, Any]:
    """上传收据必须是闲鱼 CDN 的 https 地址 + 正整数宽高。"""
    if not isinstance(value, dict):
        raise GoofishError("已上传图片必须包含 url、width、height")
    url = value.get("url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    host = (parsed.hostname or "") if parsed else ""
    trusted = bool(host) and any(
        host == domain or host.endswith("." + domain) for domain in _RECEIPT_HOSTS
    )
    if not parsed or parsed.scheme != "https" or parsed.username or not trusted:
        raise GoofishError("图片 URL 必须是闲鱼上传返回的 HTTPS CDN 地址")
    dimensions: list[int] = []
    for key in ("width", "height"):
        raw = value.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).isdigit():
            raise GoofishError("图片宽高必须是正整数")
        if int(raw) <= 0:
            raise GoofishError("图片宽高必须是正整数")
        dimensions.append(int(raw))
    return {"url": url, "width": dimensions[0], "height": dimensions[1]}


@command(
    namespace="media",
    name="upload",
    description="上传图片到闲鱼 CDN（独立媒体预算，返回服务端确认过的图片收据）",
    strategy=Strategy.COOKIE,
    columns=["url", "width", "height", "size"],
    write=True,
)
def upload(path: str) -> dict[str, object]:
    from goofish_z.core.guard import check as guard_check

    guard_check()
    source = validate_image_path(path)
    mime = mimetypes.guess_type(str(source))[0]
    if mime is None:  # pragma: no cover - validate_image_path 已拦过，这里只为类型收敛
        raise GoofishError("无法识别图片格式，请使用有正确扩展名的图片")
    session = Session.load()
    headers = {
        "accept": "*/*",
        "origin": "https://www.goofish.com",
        "referer": "https://www.goofish.com/",
        "user-agent": USER_AGENT,
    }
    params = {"floderId": "0", "appkey": "xy_chat", "_input_charset": "utf-8"}

    try:
        with source.open("rb") as stream, acquire("media.write"):
            resp = session.http.post(
                UPLOAD_URL,
                headers=headers,
                params=params,
                files={"file": (source.name, stream, mime)},
                timeout=60,
            )
            resp.raise_for_status()
            raw = resp.json()
    except (RequestException, OSError, ValueError):
        raise GoofishError("图片上传连接或响应失败；未确认成功，不自动重传") from None

    if not isinstance(raw, dict):
        raise GoofishError("上传响应结构无效；未确认成功")
    if raw.get("success") is not True:
        marker = str(raw.get("status", ""))
        if any(key in marker for key in _RISK_MARKERS):
            raise RiskControlError(f"图片上传被平台风控拒绝：{marker}")
        raise GoofishError("图片上传未被服务端确认成功")
    obj = raw.get("object")
    if not isinstance(obj, dict):
        raise GoofishError("上传响应缺少图片对象")
    try:
        width, height = map(int, str(obj.get("pix", "")).split("x"))
    except (ValueError, TypeError):
        raise GoofishError("上传响应缺少有效图片尺寸") from None

    receipt = validate_image_receipt({"url": obj.get("url"), "width": width, "height": height})
    return {**receipt, "size": obj.get("size", 0)}


__test__ = {
    "validate_image_path": validate_image_path,
    "validate_image_receipt": validate_image_receipt,
}

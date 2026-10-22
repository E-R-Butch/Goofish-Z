"""message send — 向指定会话发送一条消息（文本/图片）。

写操作，走限流 + 熔断。未知 cid 时可传 --item-id 自动创建单聊。
"""

import asyncio
from typing import Any, Literal

from goofish_z.core import Session, Strategy, command
from goofish_z.core.errors import GoofishError
from goofish_z.core.guard import watch
from goofish_z.core.limiter import acquire
from goofish_z.core.token import get_access_token
from goofish_z.core.ws import (
    connect,
    create_chat,
    heartbeat_loop,
    register,
    send_image,
    send_text,
    wait_send_ack,
)

# 发送回执等待上限（秒）。超时即视为未确认送达，不再无条件报成功。
ACK_TIMEOUT = 5.0


@command(
    namespace="message",
    name="send",
    description="向会话发送消息（text/image）。text 必填，image 走 url+wh",
    strategy=Strategy.COOKIE,
    columns=["cid", "toid", "kind", "ok", "mid"],
    write=True,
)
def send(
    cid: str,
    toid: str,
    text: str = "",
    *,
    kind: Literal["text", "image"] = "text",
    image_url: str = "",
    image_width: int = 0,
    image_height: int = 0,
    item_id: str = "",
) -> dict[str, Any]:
    session = Session.load()
    with acquire("write"), watch():
        result = asyncio.run(_send(
            session,
            cid=cid,
            toid=toid,
            text=text,
            kind=kind,
            image_url=image_url,
            image_width=image_width,
            image_height=image_height,
            item_id=item_id,
        ))
        # _send/_apply_ack 保留结构化结果供内部使用；公开命令必须用异常传递失败，
        # 否则 CLI 会退出 0，MCP 也会把 ok=False 当成成功工具结果。
        if result.get("ok") is not True:
            raise GoofishError(
                f"消息发送未确认：{result.get('error', 'unknown')} (mid={result.get('mid', '')})",
                raw=result,
            )
        return result


async def _send(
    session: Session,
    *,
    cid: str,
    toid: str,
    text: str,
    kind: str,
    image_url: str,
    image_width: int,
    image_height: int,
    item_id: str,
) -> dict[str, Any]:
    token = get_access_token(session)
    async with connect(session) as ws:
        await register(ws, session, token)
        hb = asyncio.create_task(heartbeat_loop(ws))
        try:
            if item_id:
                await create_chat(ws, myid=session.unb, toid=toid, item_id=item_id)
                await asyncio.sleep(0.5)

            if kind == "text":
                if not text:
                    raise ValueError("kind=text 需要 --text")
                mid = await send_text(
                    ws, myid=session.unb, cid=cid, toid=toid, text=text
                )
            elif kind == "image":
                if not (image_url and image_width and image_height):
                    raise ValueError("kind=image 需要 --image-url/--image-width/--image-height")
                mid = await send_image(
                    ws,
                    myid=session.unb,
                    cid=cid,
                    toid=toid,
                    url=image_url,
                    width=image_width,
                    height=image_height,
                )
            else:
                raise ValueError(f"不支持的 kind: {kind}")
            ack = await wait_send_ack(ws, mid, timeout=ACK_TIMEOUT)
        finally:
            hb.cancel()

    result: dict[str, Any] = {"cid": cid, "toid": toid, "kind": kind, "ok": False, "mid": mid}
    return _apply_ack(result, ack)


def _apply_ack(result: dict[str, Any], ack: dict[str, Any] | None) -> dict[str, Any]:
    """把下行回执映射到发送结果。

    没有回执（超时）或 code 非 200 都算未确认送达——过去是 sleep 一秒后无条件
    返回 ok=True，拒收和限流都看不出来。
    """
    if ack is None:
        result["error"] = "ack_timeout"
        return result
    try:
        code = int(ack.get("code", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    result["ok"] = code == 200
    if not result["ok"]:
        result["error"] = f"ack_code={ack.get('code')!r}"
    return result

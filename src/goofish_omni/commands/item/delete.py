"""item delete — 下架商品。接口 com.taobao.idle.item.delete v1.1（写操作）"""

from goofish_omni.core import Session, Strategy, command
from goofish_omni.core.guard import watch
from goofish_omni.core.limiter import acquire
from goofish_omni.core.mtop import call


@command(
    namespace="item",
    name="delete",
    description="下架/删除商品（写操作，受限流和风控护栏保护）",
    strategy=Strategy.COOKIE,
    columns=["item_id", "ok", "message"],
    write=True,
)
def delete(item_id: str) -> dict[str, object]:
    session = Session.load()
    with acquire("item.write"), watch():
        raw = call(
            session,
            api="com.taobao.idle.item.delete",
            data={"itemId": str(item_id)},
            version="1.1",
            spm_cnt="a21ybx.item.0.0",
        )
    ret = raw.get("ret") or []
    return {
        "item_id": str(item_id),
        "ok": any("SUCCESS" in r for r in ret),
        "message": " | ".join(ret),
    }

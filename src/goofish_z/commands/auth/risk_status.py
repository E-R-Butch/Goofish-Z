"""auth risk-status — 查看风控熔断 + 限流状态全景。"""
from __future__ import annotations

from goofish_z.core import Strategy, command
from goofish_z.core.guard import status as guard_status
from goofish_z.core.limiter import status as limiter_status


@command(
    namespace="auth",
    name="risk-status",
    description="查看风控熔断 + 限流状态（监控面板/排查用）",
    strategy=Strategy.PUBLIC,
    columns=["tripped", "level", "reason", "remaining_seconds", "total_hits"],
)
def risk_status() -> dict:
    return {
        "circuit": guard_status(),
        "limiters": limiter_status(),
    }

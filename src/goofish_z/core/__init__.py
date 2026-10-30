"""Shared command metadata; authentication is loaded only when requested."""
from typing import TYPE_CHECKING

from goofish_z.core.errors import (
    AuthRequiredError,
    BlockedError,
    EmptyResultError,
    GoofishError,
    NotFoundError,
    RateLimitedError,
    RiskControlError,
    SignError,
)
from goofish_z.core.registry import Command, command, iter_commands, registry
from goofish_z.core.strategy import Strategy

if TYPE_CHECKING:
    from goofish_z.core.session import Session


def __getattr__(name: str):
    # Keep the historical export as the actual class, not a proxy.  Do not
    # catch import failures: requesting authentication must fail transparently.
    if name == "Session":
        from goofish_z.core.session import Session
        return Session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "AuthRequiredError",
    "BlockedError",
    "Command",
    "EmptyResultError",
    "GoofishError",
    "NotFoundError",
    "RateLimitedError",
    "RiskControlError",
    "Session",
    "SignError",
    "Strategy",
    "command",
    "iter_commands",
    "registry",
]

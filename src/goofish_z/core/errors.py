"""统一异常体系。driver 层根据响应体 ret 或状态自动抛对应异常。

退出码参照 sysexits.h（与 opencli 对齐）：
- 1   GENERIC
- 65  DATAERR    —— UpstreamError（响应不符合契约：缺 ret／非 JSON 对象）
- 75  TEMPFAIL   —— RateLimitedError
- 76  RISK       —— RiskControlError（项目特有，对应 RGV587 / punish）
- 77  NOPERM     —— AuthRequiredError
- 78  CONFIG     —— SignError
- 79  NOINPUT    —— NotFoundError / EmptyResultError / BlockedError
"""
from __future__ import annotations


class GoofishError(Exception):
    exit_code = 1

    def __init__(self, message: str, *, raw: dict | None = None, hint: str | None = None):
        super().__init__(message)
        self.raw = raw
        self.hint = hint


class AuthRequiredError(GoofishError):
    exit_code = 77


class UpstreamError(GoofishError):
    """上游响应不符合契约：缺 ret、ret 为空、不是 JSON 对象。

    单独成类是为了让这条路径可测、可区分——把契约破坏当成功放行，
    一次协议变更就会让所有失败静默通过。
    """

    exit_code = 65


class SignError(GoofishError):
    exit_code = 78


class RateLimitedError(GoofishError):
    exit_code = 75

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        raw: dict | None = None,
        hint: str | None = None,
    ):
        super().__init__(message, raw=raw, hint=hint)
        self.retry_after = retry_after


class RiskControlError(GoofishError):
    """触发风控：RGV587 / punish / FAIL_SYS_USER_VALIDATE 等。"""
    exit_code = 76


class NotFoundError(GoofishError):
    exit_code = 79


class EmptyResultError(GoofishError):
    """查询无命中。对标 opencli EmptyResultError。"""
    exit_code = 79


class BlockedError(GoofishError):
    """请求被拦截（验证码页 / 安全验证 / 异常访问）。对标 opencli 的 blocked 分支。"""
    exit_code = 79

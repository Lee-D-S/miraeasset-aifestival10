"""Process-local QPM/TPM guard for CLOVA requests."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
import re
import threading
import time
from collections.abc import Mapping


_PROVIDER_RATE_LIMIT_RE = re.compile(
    r"RateLimitBlocked|rate[_ -]?limit|remaining[_ ]tokens|(?:HTTP|status)[ _-]*429",
    re.IGNORECASE,
)
_QUESTION_DEADLINE: ContextVar[float | None] = ContextVar("dis164_question_deadline", default=None)


def estimate_tokens(value: object) -> int:
    """Conservative token estimate used only for local admission control."""
    return max(1, (len(str(value)) + 3) // 4)


def parse_reset_seconds(value: object) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return 0.0
    return max(float(match.group(0)), 0.0)


class RateLimitBlocked(RuntimeError):
    """Raised before a request when the local/provider budget is exhausted."""

    def __init__(self, reason: str, retry_after: float = 0.0):
        super().__init__(reason)
        self.retry_after = retry_after


@contextmanager
def question_rate_limit_budget(seconds: float = 300.0):
    token = _QUESTION_DEADLINE.set(time.monotonic() + max(float(seconds), 0.1))
    try:
        yield
    finally:
        _QUESTION_DEADLINE.reset(token)


def remaining_question_seconds() -> float | None:
    deadline = _QUESTION_DEADLINE.get()
    return None if deadline is None else max(deadline - time.monotonic(), 0.0)


def is_rate_limit_error(error: BaseException) -> bool:
    """Return whether an error represents local or provider capacity limits."""

    return isinstance(error, RateLimitBlocked) or bool(_PROVIDER_RATE_LIMIT_RE.search(str(error)))


def rate_limit_event(error: BaseException, *, operation: str, client: object | None = None) -> dict[str, object]:
    """Build a non-secret provider-capacity event for the public trace."""

    event: dict[str, object] = {
        "status": "rate_limited",
        "operation": operation,
        "error_type": type(error).__name__,
        "message": str(error),
    }
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        event["retry_after_seconds"] = round(float(retry_after), 3)
    for key in ("status_code", "retryable"):
        value = getattr(error, key, None)
        if value is not None:
            event[key] = value
    if client is not None:
        headers = getattr(client, "last_rate_limit", {})
        if isinstance(headers, Mapping):
            event["rate_limit_headers"] = {str(key): str(value) for key, value in headers.items()}
        provider_status = getattr(client, "last_provider_status", {})
        if isinstance(provider_status, Mapping):
            for key in ("source", "status_code", "estimated_tokens"):
                if key in provider_status:
                    event[key] = provider_status[key]
        limiter = getattr(client, "rate_limiter", None)
        snapshot = getattr(limiter, "snapshot", None)
        if callable(snapshot):
            event["limiter"] = snapshot()
    return event


class ClovaRateLimiter:
    def __init__(self, *, default_qpm: int, default_tpm: int, min_interval: float = 0.0):
        self.default_qpm = default_qpm
        self.default_tpm = default_tpm
        self.min_interval = max(min_interval, 0.0)
        self._calls: deque[tuple[float, int]] = deque()
        self._last_call = 0.0
        self._blocked_until = 0.0
        self._remaining_requests: int | None = None
        self._remaining_tokens: int | None = None
        self._token_reset_until = 0.0
        self._lock = threading.Lock()

    def observe(self, headers: Mapping[str, object]) -> None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        with self._lock:
            self._remaining_requests = self._int_or_none(normalized.get("x-ratelimit-remaining-requests"))
            self._remaining_tokens = self._int_or_none(normalized.get("x-ratelimit-remaining-tokens"))
            reset_values = [
                parse_reset_seconds(normalized.get("x-ratelimit-reset-requests")),
                parse_reset_seconds(normalized.get("x-ratelimit-reset-tokens")),
            ]
            reset = max(reset_values, default=0.0)
            if reset > 0 and self._remaining_tokens is not None:
                self._token_reset_until = max(
                    self._token_reset_until,
                    time.monotonic() + reset,
                )
            if (self._remaining_requests == 0 or self._remaining_tokens == 0) and reset > 0:
                self._blocked_until = max(self._blocked_until, time.monotonic() + reset)

    def before_call(self, estimated_tokens: int) -> None:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0][0] >= 60.0:
                self._calls.popleft()
            if self._token_reset_until and now >= self._token_reset_until:
                # Provider remaining-token headers describe the previous
                # window. Clear them after reset so the next call can refresh
                # the budget instead of being blocked by stale state.
                self._remaining_tokens = None
                self._token_reset_until = 0.0
            if now < self._blocked_until:
                raise RateLimitBlocked("CLOVA rate-limit reset 전이라 호출을 차단했습니다.", self._blocked_until - now)
            if self._remaining_requests is not None and self._remaining_requests <= 0:
                raise RateLimitBlocked("CLOVA remaining requests가 0이라 호출을 차단했습니다.")
            if self._remaining_tokens is not None and self._remaining_tokens < estimated_tokens:
                retry_after = max(self._token_reset_until - now, 0.0)
                raise RateLimitBlocked(
                    "CLOVA remaining tokens가 요청 예산보다 작아 호출을 차단했습니다.",
                    retry_after,
                )
            if len(self._calls) >= self.default_qpm:
                retry_after = max(60.0 - (now - self._calls[0][0]), 0.0) if self._calls else 60.0
                raise RateLimitBlocked("로컬 QPM 예산을 초과해 CLOVA 호출을 차단했습니다.", retry_after)
            used_tokens = sum(tokens for _, tokens in self._calls)
            if used_tokens + estimated_tokens > self.default_tpm:
                retry_after = max(60.0 - (now - self._calls[0][0]), 0.0) if self._calls else 60.0
                raise RateLimitBlocked("로컬 TPM 예산을 초과해 CLOVA 호출을 차단했습니다.", retry_after)
            if self.min_interval and now - self._last_call < self.min_interval:
                raise RateLimitBlocked("CLOVA 호출 간 최소 간격 전이라 호출을 차단했습니다.", self.min_interval - (now - self._last_call))
            self._calls.append((now, estimated_tokens))
            self._last_call = now

    def snapshot(self) -> dict[str, object]:
        """Return safe, non-secret limiter state for audit traces."""

        now = time.monotonic()
        with self._lock:
            window_tokens = sum(tokens for _, tokens in self._calls)
            reset_in = max(self._blocked_until - now, 0.0)
            return {
                "default_qpm": self.default_qpm,
                "default_tpm": self.default_tpm,
                "window_calls": len(self._calls),
                "window_tokens": window_tokens,
                "remaining_requests": self._remaining_requests,
                "remaining_tokens": self._remaining_tokens,
                "reset_in_seconds": round(reset_in, 3),
                "token_reset_in_seconds": round(
                    max(self._token_reset_until - now, 0.0),
                    3,
                ),
            }

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        try:
            return int(str(value)) if value is not None else None
        except ValueError:
            return None


__all__ = [
    "ClovaRateLimiter",
    "RateLimitBlocked",
    "estimate_tokens",
    "is_rate_limit_error",
    "parse_reset_seconds",
    "question_rate_limit_budget",
    "rate_limit_event",
    "remaining_question_seconds",
]

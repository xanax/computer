"""Retry policy for outbound provider calls.

One rule, applied everywhere a provider is called:

    4xx is the provider telling us *the request is wrong* -- retrying it
    burns the turn for an answer that will not change. Everything else is
    worth another attempt: 5xx, the three 4xx codes that mean "not right
    now" (408 timeout, 409 conflict, 425 too early) plus 429, and any
    transport failure -- connect/read/write timeout, connection reset,
    protocol error, unreadable body.

429 is the reason this module exists. A rate limited model used to fail the
whole turn on the first response, or (after the streaming retry landed) wait
0.5s, 1.0s and give up. OpenRouter's *shared* free pool stays drained for
minutes, so the wait has to be exponential and long enough to outlast a
burst, while still bounded -- a turn that hangs for five minutes is a worse
failure than one that says "429".

Callers pass a zero-argument async callable that performs exactly one
attempt and raises on failure. Streaming callers cannot use
`retry_async` for the whole call, because re-running it would duplicate the
text already emitted; they drive their own loop and only consult
`is_retryable_exception` / `retry_delay`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

from cptr.env import PROVIDER_RETRY_ATTEMPTS, PROVIDER_RETRY_MAX_DELAY_SECONDS

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Total attempts, not retries: 4 attempts = the first call plus 3 retries.
RETRY_ATTEMPTS = max(1, PROVIDER_RETRY_ATTEMPTS)
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = PROVIDER_RETRY_MAX_DELAY_SECONDS

# 4xx codes that mean "not right now" rather than "your request is wrong".
RETRYABLE_4XX = frozenset({408, 409, 425, 429})

# Transport-level failures: no response ever arrived, or it was unreadable.
RETRYABLE_TRANSPORT_ERRORS = (httpx.TransportError, httpx.DecodingError)


def is_retryable_status(status: int) -> bool:
    """True when a response status is worth another attempt."""
    return status >= 500 or status in RETRYABLE_4XX


def is_retryable_exception(
    exc: BaseException, *, extra: tuple[type[BaseException], ...] = ()
) -> bool:
    """True when `exc` is worth another attempt.

    `extra` lets a caller mark its own error types as retryable -- ai.py uses
    it for `EmptyCompletionError`, an empty 200 stream that a retry often
    fixes.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_status(exc.response.status_code)
    if extra and isinstance(exc, extra):
        return True
    return isinstance(exc, RETRYABLE_TRANSPORT_ERRORS)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Read `Retry-After` off an HTTP error, as seconds or an HTTP-date."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).timestamp() - time.time()
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(exc: BaseException, attempt: int) -> float:
    """Seconds to wait before retry `attempt` (0-based).

    A provider that says `Retry-After` is obeyed (clamped), because it knows
    how long its own window is. Otherwise the delay doubles per attempt with
    a little jitter, so several chats hitting the same drained pool do not
    wake up together and drain it again.
    """
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(max(retry_after, 0.0), RETRY_MAX_DELAY_SECONDS)

    delay = RETRY_BASE_DELAY_SECONDS * (2**attempt)
    delay += random.uniform(0, delay * 0.25)
    return min(delay, RETRY_MAX_DELAY_SECONDS)


def describe_exception(exc: BaseException) -> str:
    """One line naming the failure, for retry logs and error messages."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str,
    attempts: int = RETRY_ATTEMPTS,
    extra_retryable: tuple[type[BaseException], ...] = (),
) -> T:
    """Run `fn()` until it succeeds, a non-retryable error, or `attempts` runs out.

    The last failure is re-raised unchanged, so callers still see the real
    exception (and its stored provider error body) after the retries.
    """
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            retryable = is_retryable_exception(exc, extra=extra_retryable)
            if attempt == attempts - 1 or not retryable:
                raise
            delay = retry_delay(exc, attempt)
            logger.warning(
                "[retry] %s failed (%s); retrying in %.1fs (%s/%s)",
                label,
                describe_exception(exc),
                delay,
                attempt + 1,
                attempts - 1,
            )
            await asyncio.sleep(delay)
    raise AssertionError("retry_async exhausted its loop without returning or raising")

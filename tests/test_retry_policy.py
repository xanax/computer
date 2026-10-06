"""Retry policy: retry every provider failure except a real 4xx.

Design: ``cptr/utils/retry.py``. The trigger was a model
(``openrouter/inclusionai/ling-3.1-flash``) whose only serving provider sits
behind OpenRouter's *shared* free pool: the first call came back 429, the
streaming path retried twice at 0.5s/1.0s, and the turn failed anyway -- the
pool stays drained for minutes.

Two properties matter, and they pull in opposite directions:

* retry broadly (5xx, transport failures, empty streams) and wait long
  enough to outlast a rate-limit burst, and
* never retry a 4xx that is telling us the request itself is wrong, and never
  silently drop the provider's own error -- the last failure is re-raised.
"""

from __future__ import annotations

import asyncio
import time
from email.utils import formatdate
from types import SimpleNamespace

import httpx
import pytest

from cptr.utils import ai
from cptr.utils import retry as retrymod
from cptr.utils.retry import (
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    is_retryable_exception,
    is_retryable_status,
    retry_async,
    retry_delay,
)


def _run(coro):
    return asyncio.run(coro)


def _status_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


# ── The rule ──────────────────────────────────────────────────


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 422, 451])
def test_client_errors_are_not_retried(status):
    """The request is wrong; repeating it gets the same answer, only later."""
    assert is_retryable_status(status) is False
    assert is_retryable_exception(_status_error(status)) is False


@pytest.mark.parametrize("status", [408, 409, 425, 429])
def test_not_right_now_4xx_is_retried(status):
    assert is_retryable_status(status) is True
    assert is_retryable_exception(_status_error(status)) is True


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_server_errors_are_retried(status):
    assert is_retryable_exception(_status_error(status)) is True


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("no bytes"),
        httpx.PoolTimeout("no slot"),
        httpx.WriteError("reset"),
        httpx.RemoteProtocolError("disconnected"),
        httpx.DecodingError("garbled body"),
    ],
)
def test_transport_failures_are_retried(exc):
    assert is_retryable_exception(exc) is True


def test_unknown_exceptions_are_not_retried():
    """A bug in our own code must not be dressed up as a transient failure."""
    assert is_retryable_exception(ValueError("bad payload")) is False


def test_extra_retryable_types_are_honoured():
    class Empty(Exception):
        pass

    assert is_retryable_exception(Empty(), extra=(Empty,)) is True
    assert is_retryable_exception(Empty()) is False


# ── The wait ──────────────────────────────────────────────────


def test_backoff_grows_and_is_capped():
    exc = httpx.ReadTimeout("slow")
    delays = [retry_delay(exc, attempt) for attempt in range(4)]
    assert delays[0] >= RETRY_BASE_DELAY_SECONDS
    assert delays[1] > delays[0]
    assert delays[2] > delays[1]
    # Tall enough to outlast a rate-limit burst, bounded so a turn cannot hang.
    assert retry_delay(exc, 20) == RETRY_MAX_DELAY_SECONDS


def test_retry_after_seconds_is_obeyed():
    exc = _status_error(429, {"retry-after": "7"})
    assert retry_delay(exc, 0) == 7.0


def test_retry_after_http_date_is_obeyed():
    exc = _status_error(429, {"retry-after": formatdate(time.time() + 5, usegmt=True)})
    assert 3.0 <= retry_delay(exc, 0) <= 5.0


def test_retry_after_is_clamped():
    exc = _status_error(429, {"retry-after": "9999"})
    assert retry_delay(exc, 0) == RETRY_MAX_DELAY_SECONDS


def test_garbage_retry_after_falls_back_to_backoff():
    exc = _status_error(429, {"retry-after": "later"})
    delay = retry_delay(exc, 0)
    assert RETRY_BASE_DELAY_SECONDS <= delay <= RETRY_BASE_DELAY_SECONDS * 1.25


# ── The runner ────────────────────────────────────────────────


@pytest.fixture
def no_sleep(monkeypatch):
    """Collect the waits instead of taking them."""
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return slept


def test_retry_async_returns_the_first_success(no_sleep):
    calls = []

    async def _fn():
        calls.append(1)
        if len(calls) < 3:
            raise _status_error(503)
        return "ok"

    assert _run(retry_async(_fn, label="test")) == "ok"
    assert len(calls) == 3
    assert len(no_sleep) == 2


def test_retry_async_gives_up_and_reraises_the_provider_error(no_sleep):
    calls = []

    async def _fn():
        calls.append(1)
        raise _status_error(503)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        _run(retry_async(_fn, label="test"))
    assert len(calls) == RETRY_ATTEMPTS
    assert caught.value.response.status_code == 503


def test_retry_async_does_not_repeat_a_client_error(no_sleep):
    calls = []

    async def _fn():
        calls.append(1)
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        _run(retry_async(_fn, label="test"))
    assert len(calls) == 1
    assert no_sleep == []


# ── The call sites ────────────────────────────────────────────


class _FakeResponse:
    """A scripted HTTP response, optionally failing part-way through a stream."""

    def __init__(self, status_code=200, payload=None, lines=None, fail_after=None, fail_with=None):
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.content = b""
        self.request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        self._payload = payload if payload is not None else {}
        self._lines = lines or []
        self._fail_after = fail_after
        self._fail_with = fail_with

    def json(self):
        return self._payload

    @property
    def text(self) -> str:
        return '{"error":{"message":"nope"}}'

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=response
            )

    async def aread(self) -> bytes:
        return b'{"error":{"message":"slow down","code":429}}'

    async def aiter_lines(self):
        for index, line in enumerate(self._lines):
            if self._fail_after is not None and index == self._fail_after:
                raise self._fail_with
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ScriptedClient:
    """Stands in for httpx.AsyncClient, replaying a script of responses."""

    script: list = []
    calls: int = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @classmethod
    def _next(cls):
        response = cls.script[min(cls.calls, len(cls.script) - 1)]
        cls.calls += 1
        return response

    def stream(self, *a, **k):
        return self._next()

    async def post(self, *a, **k):
        return self._next()


@pytest.fixture
def scripted_http(monkeypatch, no_sleep):
    monkeypatch.setattr(ai.httpx, "AsyncClient", _ScriptedClient)
    _ScriptedClient.calls = 0
    _ScriptedClient.script = []
    return _ScriptedClient


def _form():
    return SimpleNamespace(
        model="inclusionai/ling-3.1-flash",
        messages=[{"role": "user", "content": "hi"}],
        instructions=None,
        tools=[],
    )


def _drain_stream():
    async def _go():
        events = []
        async for event in ai.stream_openai_completions(
            form_data=_form(), url="http://example.invalid/v1", key="k", request_params={}
        ):
            events.append(event)
        return events

    return _run(_go())


_OK_LINES = [
    'data: {"choices":[{"delta":{"content":"hello"}}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    "data: [DONE]",
]


def test_stream_retries_a_429_before_the_first_event(scripted_http):
    scripted_http.script = [_FakeResponse(429), _FakeResponse(200, lines=_OK_LINES)]
    events = _drain_stream()
    assert scripted_http.calls == 2
    assert [e for e in events if e.get("type") == "done"]


def test_stream_stops_at_a_client_error(scripted_http):
    scripted_http.script = [_FakeResponse(404)]
    with pytest.raises(httpx.HTTPStatusError):
        _drain_stream()
    assert scripted_http.calls == 1


def test_stream_never_replays_after_emitting_text(scripted_http):
    """Re-running the request would duplicate the text already shown."""
    scripted_http.script = [
        _FakeResponse(
            200,
            lines=[
                'data: {"choices":[{"delta":{"content":"half"}}]}',
                'data: {"choices":[{"delta":{"content":" a sentence"}}]}',
            ],
            fail_after=1,
            fail_with=httpx.ReadTimeout("dropped"),
        )
    ]
    with pytest.raises(httpx.ReadTimeout):
        _drain_stream()
    assert scripted_http.calls == 1


def test_chat_completion_retries_and_returns_text(scripted_http):
    scripted_http.script = [
        _FakeResponse(502),
        _FakeResponse(200, payload={"choices": [{"message": {"content": "Title"}}]}),
    ]
    text = _run(
        ai.chat_completion(
            provider="openai",
            base_url="http://example.invalid/v1",
            api_key="k",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert text == "Title"
    assert scripted_http.calls == 2


def test_chat_completion_does_not_retry_a_401(scripted_http):
    """A bad key is not transient; retrying it three more times is pure noise."""
    scripted_http.script = [_FakeResponse(401)]
    with pytest.raises(httpx.HTTPStatusError):
        _run(
            ai.chat_completion(
                provider="openai",
                base_url="http://example.invalid/v1",
                api_key="bad",
                model="m",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert scripted_http.calls == 1


def test_every_provider_call_site_is_wrapped():
    """Structural guard: the streaming paths, the non-streaming path, image
    generation/editing and STT/TTS all go through the shared policy. The bug
    was a *missing* retry, so a behavioural test on one call site cannot catch
    the others."""
    import pathlib

    expected = {
        "cptr/utils/ai.py": 1,  # chat_completion's retry_async
        "cptr/utils/images.py": 2,
        "cptr/routers/audio.py": 2,
    }
    for path, count in expected.items():
        source = pathlib.Path(path).read_text()
        assert source.count("retry_async(") == count, f"{path} lost its retry wrapper"

    ai_source = pathlib.Path("cptr/utils/ai.py").read_text()
    # The three streaming transports drive their own loop, so they have to
    # consult the policy directly rather than through retry_async.
    assert ai_source.count("is_retryable_exception(") == 3
    assert ai_source.count("retry_delay(") == 3
    assert "retrymod" not in ai_source


def test_retry_max_attempts_is_left_configurable():
    assert RETRY_ATTEMPTS >= 2
    assert retrymod.RETRY_MAX_DELAY_SECONDS == RETRY_MAX_DELAY_SECONDS

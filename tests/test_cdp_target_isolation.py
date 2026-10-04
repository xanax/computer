"""The agent's CDP client must not take over a page the user can see.

``browser_navigate`` and the rest of the browser_* tools drive one Chrome over
CDP -- the same Chrome the user's Browser tab may be showing. Adopting "the
first page target" (the old behaviour) meant a tool call navigated whichever page
happened to be first in the list, which reads to the user as "my tab keeps
changing itself".

These tests pin the two properties that fix it: an unmarked page is never
adopted, and a target we did create is found again on reconnect (so repeated
calls do not pile up tabs). No Chrome required -- ``/json/list`` and
``/json/new`` are stubbed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cptr.utils.browser.cdp import CDPClient


class FakeHTTP:
    """Stands in for ``httpx.AsyncClient`` over the two CDP endpoints we use."""

    def __init__(self, targets: list[dict[str, Any]] | None = None) -> None:
        self.targets: list[dict[str, Any]] = list(targets or [])
        self.created: list[str] = []
        self.list_calls = 0
        self.new_calls = 0
        self._next_id = 1

    async def __aenter__(self) -> "FakeHTTP":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, url: str, **_kwargs: object) -> "FakeResponse":
        self.list_calls += 1
        return FakeResponse(200, list(self.targets))

    async def put(self, url: str, **_kwargs: object) -> "FakeResponse":
        self.new_calls += 1
        target_id = f"NEW{self._next_id}"
        self._next_id += 1
        raw = url.partition("?")[2]
        # The client percent-encodes the marker: a literal '#' would be parsed
        # as a fragment of the request URL and silently dropped.
        from urllib.parse import unquote

        opened = unquote(raw)
        target = {
            "id": target_id,
            "type": "page",
            "url": opened,
            "webSocketDebuggerUrl": f"ws://fake/{target_id}",
        }
        self.targets.insert(0, target)
        self.created.append(opened)
        return FakeResponse(200, target)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeWebSocket:
    """Accepts any URL and replies to every CDP command with an empty result.

    ``websockets.connect`` is awaited, so the factory returns an awaitable that
    yields the socket.
    """

    def __init__(self, url: str, **_kwargs: object) -> None:
        self.url = url
        self.sent: list[str] = []
        self._id = 0

    def __await__(self):
        async def _ready() -> "FakeWebSocket":
            return self

        return _ready().__await__()

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        import json

        self._id += 1
        return json.dumps({"id": self._id, "result": {}})

    async def close(self) -> None:
        return None


@pytest.fixture
def stub_cdp(monkeypatch: pytest.MonkeyPatch) -> FakeHTTP:
    """Replace the network and websocket halves of ``connect()``.

    ``connect()`` does ``import httpx`` inside the function body, which resolves
    through ``sys.modules`` -- so patching a module-level attribute on
    ``cdp`` would not take effect and the test would quietly drive a real
    Chrome. Patch ``httpx.AsyncClient`` itself.
    """
    import httpx

    import cptr.utils.browser.cdp as cdp_mod

    fake = FakeHTTP()

    def AsyncClient(**_kwargs: object) -> FakeHTTP:
        return fake

    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient, raising=True)
    monkeypatch.setattr(cdp_mod.websockets, "connect", FakeWebSocket, raising=False)
    return fake


def _page(target_id: str, url: str) -> dict[str, Any]:
    return {
        "id": target_id,
        "type": "page",
        "url": url,
        "webSocketDebuggerUrl": f"ws://fake/{target_id}",
    }


def test_connect_never_adopts_a_page_the_user_can_see(stub_cdp: FakeHTTP) -> None:
    """A user's page must be left alone even when it is the only one there."""
    user_page = _page("USER", "http://localhost:5173/")
    stub_cdp.targets = [user_page]

    client = asyncio.run(CDPClient.connect("http://localhost:9222"))
    try:
        assert client._target_id != "USER", "attached to the user's page"
        assert stub_cdp.new_calls == 1, "should open a target of its own"
        assert [t["id"] for t in stub_cdp.targets][:2] == ["NEW1", "USER"]
    finally:
        asyncio.run(client.close())


def test_connect_skips_the_first_page_in_favour_of_the_first_user_page_ordering(
    stub_cdp: FakeHTTP,
) -> None:
    """Ordering must not decide ownership, even with several pages open."""
    stub_cdp.targets = [
        _page("A", "http://127.0.0.1:3070/"),
        _page("B", "http://localhost:5173/"),
        _page("C", "https://example.com/"),
    ]

    client = asyncio.run(CDPClient.connect("http://localhost:9222"))
    try:
        assert client._target_id == "NEW1"
        assert {t["id"] for t in stub_cdp.targets} == {"A", "B", "C", "NEW1"}
    finally:
        asyncio.run(client.close())


def test_connect_reuses_the_target_it_created(stub_cdp: FakeHTTP) -> None:
    """Reconnect finds our own target instead of opening a tab per call."""
    stub_cdp.targets = [_page("USER", "http://localhost:5173/")]

    first = asyncio.run(CDPClient.connect("http://localhost:9222"))
    first_id = first._target_id
    asyncio.run(first.close())

    second = asyncio.run(CDPClient.connect("http://localhost:9222"))
    try:
        assert second._target_id == first_id, "opened a second target"
        assert stub_cdp.new_calls == 1
    finally:
        asyncio.run(second.close())


def test_marker_survives_url_encoding(stub_cdp: FakeHTTP) -> None:
    """The marker must reach Chrome encoded, or the reuse check never matches.

    A literal ``#`` is parsed as a fragment of the *request* URL and dropped
    before it is sent, which silently leaves every target as plain about:blank.
    """
    client = asyncio.run(CDPClient.connect("http://localhost:9222"))
    try:
        assert stub_cdp.created == [CDPClient.OWNED_TARGET_URL]
        assert stub_cdp.targets[0]["url"].startswith(CDPClient.OWNED_TARGET_URL)
    finally:
        asyncio.run(client.close())


def test_a_blank_user_page_is_not_mistaken_for_ours(stub_cdp: FakeHTTP) -> None:
    """Our own blank tab is marked; the user's blank tab is not."""
    stub_cdp.targets = [_page("USER", "about:blank")]

    client = asyncio.run(CDPClient.connect("http://localhost:9222"))
    try:
        assert client._target_id == "NEW1"
        assert stub_cdp.new_calls == 1
    finally:
        asyncio.run(client.close())

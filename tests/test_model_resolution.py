"""A connection that cannot be reached must not turn model resolution into a 500.

``_fetch_provider_models`` is typed ``list[str] | None`` and returns ``None`` when
a provider's ``/models`` endpoint is down (or answers non-200). The scan in
``_resolve_connection`` iterated that result directly, so ``for mid in None``
raised ``TypeError`` — and the caller saw HTTP 500 instead of the honest
``400 no connection found for model: …``. Ledger: B-014.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from cptr.routers import chat

GOOD = {
    "id": "conn-good",
    "prefix_id": "openrouter",
    "provider": "openrouter",
    "enabled": True,
    "data": {"models": ["gpt-4o", "claude-sonnet-4-20250514"]},
}
# No ``data.models``, so resolution has to ask the provider — and the provider
# is unreachable.
DEAD = {"id": "conn-dead", "prefix_id": "dead", "provider": "openai", "enabled": True}


@pytest.fixture
def connections(monkeypatch):
    async def fake_get_connections():
        return [DEAD, GOOD]

    async def fake_fetch(conn):
        return None  # unreachable provider

    monkeypatch.setattr(chat, "_get_connections", fake_get_connections)
    monkeypatch.setattr(chat, "_fetch_provider_models", fake_fetch)


def test_unreachable_connection_is_skipped_not_fatal(connections):
    conn, model = asyncio.run(chat._resolve_connection("gpt-4o"))
    assert conn["id"] == "conn-good"
    assert model == "gpt-4o"


def test_unreachable_connection_still_reports_not_found(connections):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat._resolve_connection("no-such-model"))
    assert exc.value.status_code == 400
    assert "no connection found" in exc.value.detail


def test_prefix_match_does_not_need_discovery(monkeypatch):
    async def fake_get_connections():
        return [DEAD]

    async def fake_fetch(conn):  # pragma: no cover - must not be reached
        raise AssertionError("prefix match should not need discovery")

    monkeypatch.setattr(chat, "_get_connections", fake_get_connections)
    monkeypatch.setattr(chat, "_fetch_provider_models", fake_fetch)
    conn, model = asyncio.run(chat._resolve_connection("dead/gpt-4o"))
    assert conn["id"] == "conn-dead"
    assert model == "gpt-4o"

"""Tests for cptr's SQLite engine configuration.

The failure these guard against, from chat 7ceb3817 (2026-10-04):
``sqlite3.OperationalError: database is locked`` raised out of
``ChatMessage.update`` mid-turn, so the assistant's text and all 166 tool calls
in that turn were lost.

Every turn persists its whole ``output`` JSON blob in one commit, and SQLite
allows exactly one writer at a time. A save that takes 5s or more therefore
kills every other turn saving alongside it -- unless the waiting writer is
patient. SQLite's default lock timeout is 5000ms; cptr's engine left it there.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.env import DB_BUSY_TIMEOUT_S
from cptr.utils import db as dbmod


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """A throwaway engine built exactly as ``get_engine`` builds the real one."""
    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "_engine", None)
    monkeypatch.setattr(dbmod, "_async_session", None)
    try:
        yield dbmod.get_engine()
    finally:
        dbmod._engine = None
        dbmod._async_session = None


def test_busy_timeout_is_above_sqlites_default(engine):
    """The whole bug in one assertion: 5000ms is not enough for cptr's writes."""
    default = sqlite3.connect(":memory:").execute("PRAGMA busy_timeout").fetchone()[0]
    assert DB_BUSY_TIMEOUT_S * 1000 > default, (
        f"busy timeout {DB_BUSY_TIMEOUT_S}s does not exceed SQLite's {default}ms "
        "default, so a slow save still kills concurrent turns"
    )


def test_every_connection_gets_the_busy_timeout_and_sync_mode(engine):
    """Both are per-connection, so the startup-only PRAGMA never reached them."""

    async def check():
        # pool_size connections, so a fresh one is drawn each time
        for _ in range(4):
            async with engine.connect() as conn:
                busy = (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar()
                sync = (await conn.exec_driver_sql("PRAGMA synchronous")).fetchone()[0]
                # NORMAL is 1; FULL (2) is an fsync per commit and cptr's cost.
                assert busy == int(DB_BUSY_TIMEOUT_S * 1000)
                assert sync == 1, "synchronous=NORMAL (1) expected, fsync-per-commit back"

    _run(check())


def test_a_writer_waits_past_five_seconds_instead_of_failing(engine):
    """The regression itself: hold the write lock longer than SQLite's default
    timeout, and the saving turn must still persist rather than raise."""
    db_file = engine.url.database

    async def check():
        # WAL must be on for concurrent readers during the held write txn.
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("CREATE TABLE m (id TEXT PRIMARY KEY, output TEXT)")

        hold_s = DB_BUSY_TIMEOUT_S / 4  # comfortably past the old 5s default

        def hog():
            # A blocking connection is the one thing that reproduces this:
            # aiosqlite runs every query on one worker thread per connection,
            # and a slow commit in it blocks the whole turn's saves.
            c = sqlite3.connect(db_file, isolation_level=None, timeout=DB_BUSY_TIMEOUT_S)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR REPLACE INTO m VALUES ('hog', ?)", ("x" * 2_000_000,))
            time.sleep(hold_s)
            c.execute("COMMIT")
            c.close()

        t = threading.Thread(target=hog)
        t.start()
        time.sleep(0.3)  # let it take the lock

        t0 = time.monotonic()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(
                text("INSERT OR REPLACE INTO m VALUES ('turn', :o)"),
                {"o": "y" * 1_300_000},  # a real chat_messages.output blob
            )
            await s.commit()
        waited = time.monotonic() - t0
        t.join()

        assert waited > 5.0, f"expected to wait out the hog, only waited {waited:.2f}s"
        async with factory() as s:
            got = (await s.execute(text("SELECT output FROM m WHERE id='turn'"))).scalar()
        assert got == "y" * 1_300_000, "the turn's output was not persisted"

    _run(check())


def test_init_db_passes_the_timeout_to_alembic(monkeypatch, tmp_path):
    """Migrations open their own sync engine, so the URL must carry the timeout."""
    monkeypatch.setattr(dbmod, "DB_FILE", tmp_path / "m.db")
    seen = {}

    class FakeCommand:
        @staticmethod
        def upgrade(cfg, target):
            seen["url"] = cfg.get_main_option("sqlalchemy.url")

    import alembic.command

    monkeypatch.setattr(alembic.command, "upgrade", FakeCommand.upgrade)
    monkeypatch.setattr(dbmod, "_engine", None)
    monkeypatch.setattr(dbmod, "_async_session", None)
    monkeypatch.setattr(dbmod, "_seed_config_from_toml", _noop)
    dbmod._engine = dbmod.get_engine()

    _run(dbmod.init_db())

    assert f"timeout={DB_BUSY_TIMEOUT_S}" in seen["url"], seen["url"]
    dbmod._engine = None
    dbmod._async_session = None


async def _noop():
    return None


def test_engine_url_and_pool_are_unchanged(engine):
    """Guard against the fix quietly changing how cptr stores data."""
    assert engine.url.database.endswith("test.db")
    # A queue pool is what lets concurrent turns each get their own connection.
    assert engine.pool.__class__.__name__ != "NullPool"


# create_async_engine is imported for the fixture's type only; NullPool documents
# why other test modules use it and get_engine deliberately does not.
_ = NullPool
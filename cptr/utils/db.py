"""Async database engine and session management."""

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from cptr.env import DATA_DIR, DB_BUSY_TIMEOUT_S, DB_FILE

_engine = None
_async_session = None


def get_engine():
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{DB_FILE}",
            echo=False,
            # SQLite's default is 5000ms, which a burst of concurrent turns saving
            # multi-megabyte output blobs exceeds (see DB_BUSY_TIMEOUT_S). Handing
            # the timeout to the driver also covers connections alembic and any
            # future raw engine make, which bypass the PRAGMA below.
            connect_args={"timeout": DB_BUSY_TIMEOUT_S},
        )
        # `synchronous` and the lock timeout are per-connection, so they are set
        # here on every new pooled connection rather than once at startup.
        # NORMAL is the documented safe setting for WAL (durable across app
        # crashes, only at risk from an OS-level crash) and removes an fsync per
        # commit -- the cost that decides how long a commit holds the write lock.
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):
            cur = dbapi_connection.cursor()
            try:
                cur.execute(f"PRAGMA busy_timeout={int(DB_BUSY_TIMEOUT_S * 1000)}")
                cur.execute("PRAGMA synchronous=NORMAL")
            finally:
                cur.close()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _async_session


async def get_db() -> AsyncSession:
    """Get an async DB session. Use as: async with await get_db() as db:"""
    factory = get_session_factory()
    return factory()


async def init_db():
    """Create tables and run Alembic migrations."""
    # Ensure WAL mode for concurrent reads
    async with get_engine().begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    # Run Alembic migrations (sync, one-time startup cost)
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    # Migrations open their own sync engine, so they need the same lock timeout:
    # a DDL migration that runs while turns are saving must wait, not abort boot.
    alembic_cfg.set_main_option(
        "sqlalchemy.url", f"sqlite:///{DB_FILE}?timeout={DB_BUSY_TIMEOUT_S}"
    )
    command.upgrade(alembic_cfg, "head")

    # Seed DB config from config.toml (file is source of truth on startup)
    await _seed_config_from_toml()


async def _seed_config_from_toml():
    """Load [app_config] from config.toml and upsert into the DB config table.

    Called on every startup. The file always wins — this ensures config
    survives DB loss and allows hand-editing the TOML file.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        from cptr.utils.config import load_app_config_from_toml

        app_config = load_app_config_from_toml()
        if not app_config:
            return

        # Direct DB upsert (not via Config.upsert to avoid re-syncing back to file)
        from cptr.models.config import Config as ConfigModel

        async with get_session_factory()() as db:
            for key, value in app_config.items():
                existing = await db.get(ConfigModel, key)
                if existing:
                    existing.value = value
                else:
                    db.add(ConfigModel(key=key, value=value))
            await db.commit()

        logger.info("Loaded %d config key(s) from config.toml", len(app_config))
    except Exception:
        logger.warning("Failed to seed config from config.toml", exc_info=True)

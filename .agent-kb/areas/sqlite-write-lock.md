# SQLite write-lock contention (`database is locked`)

**Status:** fixed · **Ledger:** B-024 · **Tests:** `tests/test_db_engine.py`

## What the symptom was

A turn in chat `7ceb3817` ("on a new branch create a donations page…") died
mid-way with

```
sqlite3.OperationalError: database is locked
[SQL: UPDATE chat_messages SET content=?, output=? WHERE chat_messages.id = ?]
```

and the reply carried `> **Error:** (sqlite3.OperationalError) database is
locked`. 166 tool calls' worth of work was lost with it. It looks like an
upstream/provider failure but nothing external was involved — it was purely
cptr refusing to wait for its own database.

## The mechanism

SQLite grants **one writer at a time**. cptr persists a chat turn's entire
`chat_messages.output` JSON blob in a single commit — measured **1,292,023
bytes** for the failing row, up to **5.9MB** in this install — so one turn's
commit holds the write lock for its whole duration, and every other in-flight
turn queues behind it.

`get_engine()` set *only* `journal_mode=WAL` (once, at startup). `busy_timeout`
was therefore left at **SQLite's default of 5000ms** — so any save slower than
5s turned every concurrent save into a hard failure. Waiting is always correct
here: the lock always clears.

### Evidence it is the wait, not the payload

`.cptr/probe/save-latency.py` over a real 135k-line log: **7596 saves**, median
17ms, p99 0.6s, but **max 40.17s**. Nine saves exceeded the 5s budget; the
slowest were `c2c6de0e` 40.2s, `d519b071` 33.0s, `7d25a814` 17.2s. The
production failure waited **5.886s** (13:18:46.625 save begins → 13:18:52.511
error) — the default 5000ms plus overhead. Deterministic reproduction
(`.cptr/probe/lock-repro.py`): fails at **5.05s** unfixed.

## Fix

All in `cptr/utils/db.py` + `cptr/env.py`, at the single choke point every
write already passes through (`get_engine()`); 118 `get_db()` /
`get_session_factory()` call sites and zero raw `sqlite3` writers in `cptr/`,
so no other module needed touching.

- `connect_args={"timeout": DB_BUSY_TIMEOUT_S}` (default **60s**) — also covers
  connections Alembic and any future raw engine make.
- An `@event.listens_for(sync_engine, "connect")` hook setting
  `busy_timeout` and `synchronous=NORMAL` on **every new pooled connection** —
  both pragmas are per-connection, so the existing startup-only
  `journal_mode=WAL` never reached them.
- `init_db()` appends `?timeout=` to the Alembic URL (Alembic opens its own
  sync engine via `engine_from_config`, which would otherwise inherit the 5s
  default and abort boot).
- Env override: `CPTR_DB_BUSY_TIMEOUT_S`.

`synchronous=NORMAL` is SQLite's documented safe setting for WAL (durable
across app crashes; only at risk from an OS crash) and removes an fsync per
commit. It is a secondary improvement, **not** the fix — see below.

## Traps that cost time

- **`/tmp` is tmpfs**, so every SQLite probe there under-measures (~10ms vs
  ~17ms fsync). Probes must run against the real filesystem
  (`/home/brendan/.cptr/`) or they mislead.
- **`synchronous` was a red herring.** It looked like the cause — an fsync per
  multi-MB commit on one write lock is a plausible story — but measured at
  0.02s/write with no difference between FULL and NORMAL
  (`.cptr/probe/sync-probe.py`). Don't re-derive it; the timeout is the fix.
- **Realistic contention alone doesn't reproduce.** Six tasks × ~1MB saves on a
  copy of the real 230MB DB lose nothing
  (`.cptr/probe/contention-probe.py`, `.cptr/probe/checkpoint-probe.py`). The
  >5s holder is intermittent, so a green probe does not clear the bug — only
  the `save-latency.py` histogram shows it happening in production.
- **A transient lock must not abort a turn.** Even with the timeout raised, a
  genuine long stall should still let the save succeed; the test holds the lock
  via a *separate blocking* connection because aiosqlite runs each connection's
  queries on one worker thread.

## Related

- B-021/B-022 (`stream-control-chars`) — same table, different failure.
- `notes/NOTES-sqlite-write-lock.md` — full log excerpt and timing analysis.
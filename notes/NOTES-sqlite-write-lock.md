# SQLite write-lock contention — `database is locked` (B-024)

Chat `7ceb3817`, 2026-10-04 13:18. The full diagnosis lives in
`.agent-kb/areas/sqlite-write-lock.md`; this is the evidence trail.

## The failure, in the log

```
2026-10-04 13:18:46.625 | ... [task a333ee7a] db save (...) begin:
    content=4312 chars output=412 items ...
2026-10-04 13:18:52.674 | ... [task a333ee7a] db save (error) begin: done=True ...
```

Between those two lines, **5.886s**, the save raised:

```
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
[SQL: UPDATE chat_messages SET content=?, output=? WHERE chat_messages.id = ?]
```

That is SQLite's default `busy_timeout` (5000ms) plus overhead — the writer
waited out its entire budget and gave up. The task handler then treated it as a
fatal turn error (`cptr/utils/chat_task.py:2972`), appended an inline
`Error: (sqlite3.OperationalError) database is locked` block to the reply, and
saved through the `error` path. The turn's text and all its tool calls were
gone.

Note the `└ functools.partial(<built-in method rollback of sqlite3.Connection
object ...>)` frame: the rollback itself could not get the lock either.

## Not a one-off

53 occurrences of `database is locked` in this log, against 12
`Chat task error for message` lines in total — i.e. **most task errors in the
window were lock failures**, and the rest are unrelated (provider errors).

## Save durations are bimodal

`.cptr/probe/save-latency.py /tmp/cptr.log.clean`:

```
7596 saves
  median    0.017s      p90   0.126s      p99   0.618s      max  40.170s
  saves slower than the 5000ms default busy_timeout: 9 (0.12%)
    c2c6de0e  40.170s     d519b071  32.981s   7d25a814  17.156s
    f2d3474f  11.949s     ae63a36e  11.738s   3012bf30  10.192s
    ebdd30bc   7.247s     068b5c8c   6.262s
```

The median save is 17ms. The tail is what matters: nine saves held SQLite's
single write lock for long enough to kill everything saving alongside them.

## Two probes that must not be trusted

**`/tmp` is tmpfs.** `df -hT /tmp` → `tmpfs`; the DB lives on `/dev/sdd ext4`.
Measured 4k fsync: **10ms on tmpfs vs 17ms on the real FS**. Every SQLite probe
written against `/tmp` under-measures disk cost. `lock-repro.py` was moved to
`/home/brendan/.cptr/` before its numbers meant anything.

**Realistic contention does not reproduce it.** Six concurrent tasks writing
the real 1.3MB blobs to a copy of the 230MB `app.db` lost nothing:

```
contention-probe.py   6 tasks x 3 saves x ~1MB:  0.76s  failed=0
checkpoint-probe.py   6 tasks x 8 saves x 1MB:   1.71s  failed=0
```

So a green contention probe does **not** clear this bug — the >5s holder is
intermittent (a long-running turn elsewhere, a checkpoint against a large WAL,
or a starved disk). Only the `save-latency.py` histogram over a real log shows
it happening.

## `synchronous` is a red herring

`sync-probe.py` compared FULL vs NORMAL: **0.13s vs 0.12s** for 6 × 1.3MB
writes, 0.02s per write either way. The fsync-per-commit theory was attractive
and wrong. NORMAL is kept as a cheap improvement, but `busy_timeout` is the fix.

## The fix, verified

`get_engine()` — the only place cptr builds its engine, reached by 118
`get_db()`/`get_session_factory()` call sites and zero raw `sqlite3` writers:

- `connect_args={"timeout": DB_BUSY_TIMEOUT_S}` (60s, `CPTR_DB_BUSY_TIMEOUT_S`)
- `@event.listens_for(sync_engine, "connect")` → `busy_timeout`,
  `synchronous=NORMAL` on **every pooled connection**
- Alembic URL gets `?timeout=` (`engine_from_config` opens its own engine)

Verified against the real 230MB database after the change:

```
journal_mode  wal     busy_timeout  60000     synchronous  1
```

`tests/test_db_engine.py` — 5 tests, and they were checked to fail against
`HEAD` with the exact `sqlite3.OperationalError: database is locked`, which is
the only evidence that they actually test the bug.

**Restart the cptr server to load this.** The engine is built once per process.
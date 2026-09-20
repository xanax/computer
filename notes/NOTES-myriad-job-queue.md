# NOTES — Myriad: one job queue

**Status:** design note, nothing implemented. 2026-09-20.
**Ask (Brendan, verbatim):** human tasks · defer a todo to an LLM at a certain time ·
run scheduled tasks out of hours when the LLM is cheaper · later, queues so changes on
the same codebase don't conflict.

---

## 1. Thesis

These are not four features. They are **one row type viewed through four different
columns**: who runs it (`executor`), when it starts (`trigger`), and what it may touch
at the same time (`resource`).

Today cptr implements three quarters of this as three unrelated mechanisms. The cost is
visible: work that should be one concept is scattered over two tables plus a chat's
`meta` JSON, and none of the three can express the other two.

**The fix is not to delete the clock.** The earlier instinct — "scheduled tasks with no
due date" — collapses the two questions a work list answers:

| Question | Needs |
|---|---|
| *What is the machine going to do, and when?* | a clock (`rrule` / `at`) |
| *What is outstanding?* | no clock, ever |

A scheduled task **is** its clock; strip the clock and the scheduler has nothing to
fire. Human tasks **already have no dates** (`workspace_todos.title` + `status` + `source`).
So: keep the clock, make it **optional per row**, and let the executor vary.

---

## 2. What exists today (verified, not recalled)

| Mechanism | Backing store | Trigger | Executor | Poll |
|---|---|---|---|---|
| Automations | `automations` + `automation_runs` | `rrule` → `next_run_at` | a model, in a **new chat** | 10s + 0–2s jitter |
| Timers | **a `Chat` row** with `meta.status='pending'` | `meta.timer_at` (ns) | a model, in the **parent chat** | 1s |
| Todos | `workspace_todos` + `todo_requests` | manual | a human | — |

Evidence:

- `cptr/utils/automations.py:99` — `Automation.claim_due(now, limit=10)`, then
  `:103` `asyncio.create_task(execute_automation(...))` for **every** claimed row.
- `cptr/utils/automations.py:115` — `execute_automation` creates a real `Chat` with
  `meta.params.tool_approval_mode = "full"`, then calls the same `start_task` loop.
- `cptr/utils/timers.py:1` — *"Durable one-shot timers backed by dormant internal child
  chats."* Timers are not a table; they are chats with `meta.status` ∈
  `pending|running|completed|error|cancelled`, plus `meta.timer_at`, `parent_chat_id`,
  `timer_model_id`, `cancel_on`, `workspace`.
- `cptr/env.py:121-122` — `AUTOMATION_POLL_INTERVAL=10`, `TIMER_POLL_INTERVAL=1`.

### 2.1 **"Defer a todo to an LLM at a time" is already built**

`timers.py` does exactly this: `Chat.get_due_timers(now)` → `_launch_timer()` → creates a
user message **and** an assistant placeholder in the parent chat → `start_task(...)`.
It survives restart (`recover_timers()`, `:213`) and cancels on events
(`cancel_timers_for_event()`, `:60`).

What it *cannot* do is attach to a todo, because it hangs off `Chat.meta` rather than
being a first-class row. That is a storage-shape problem, not a missing-feature problem.
This is why Phase 2 below is nearly free.

### 2.2 Three gaps that the queue makes urgent

1. **Unbounded concurrency on one tree.** `claim_due(limit=10)` claims up to ten rows and
   `create_task`s all of them. Ten rrules landing at 09:00 = ten agents in one working
   tree. There is no cap: unlike subagents (which at least have
   `subagents.max_concurrent=20`), automations have **no** concurrency limit at all.
2. **No locking whatsoever.** `flock`/`fcntl` appear only as `TIOCSWINSZ` ioctls
   (`utils/terminal.py:81,132,197,212`; `utils/tools.py:42,449`) — never as a lock.
   Atomic writes *do* exist, but as two duplicated private helpers:
   `_atomic_write_text` (`utils/memory.py:213`) and `_write_text_atomic`
   (`utils/skills.py:564`).
3. **No price data anywhere.** Grep for `price|pricing|cost|off-peak|usd` across
   `models/`, `routers/`, `utils/` returns nothing. We *do* track
   `chat_messages.usage = {input_tokens, output_tokens, ...}` (`models/chats.py:588`),
   so tokens are counted but never valued.

`claim_due` is also only *single-process* atomic (select, then mutate, then commit);
there is no `UPDATE ... WHERE status='queued' RETURNING`, so it cannot arbitrate a
queue. Fine today, insufficient for Phase 4.

---

## 3. The primitive: `jobs`

```sql
jobs
  id           TEXT PK
  user_id      TEXT NOT NULL
  workspace    TEXT NOT NULL
  resource     TEXT NOT NULL DEFAULT ''   -- serialization lane; '' = free-running
  title        TEXT NOT NULL
  kind         TEXT NOT NULL DEFAULT 'task'   -- 'task' | 'note'
  executor     TEXT NOT NULL              -- 'human' | <model_id>
  trigger      TEXT NOT NULL              -- 'manual' | 'at' | 'rrule' | 'window'
  trigger_at   INTEGER                    -- ns, for 'at'
  rrule        TEXT                       -- for 'rrule'
  deadline     INTEGER                    -- ns, latest acceptable start, for 'window'
  payload      TEXT                       -- prompt handed to the agent
  status       TEXT NOT NULL              -- see §3.2
  priority     INTEGER NOT NULL DEFAULT 0
  depends_on   TEXT                       -- job id, nullable
  attempts     INTEGER NOT NULL DEFAULT 0
  last_error   TEXT
  parent_chat  TEXT                       -- chat to wake (timer semantics)
  source       TEXT NOT NULL DEFAULT 'human'  -- 'human' | 'chat'
  origin_chat  TEXT                       -- chat that proposed it, if source='chat'
  meta         JSON
  created_at   INTEGER NOT NULL
  updated_at   INTEGER NOT NULL
```

Indexes: `ix_jobs_user_ws_status (user_id, workspace, status)`,
`ix_jobs_due (status, trigger_at)`, `ix_jobs_resource (resource, status, priority, created_at)`.

### 3.1 Field notes

- **`executor`** is the axis that kills the todos/scheduled split. `'human'` → it waits
  for a person. Any other value → it is a model id, and the scheduler will run it.
- **`resource`** is the axis that makes your queue ask real. See §5.
- **`trigger`** collapses three existing things: `'manual'` (today's todo), `'at'`
  (today's timer), `'rrule'` (today's automation). `'window'` is new.
- **`kind='note'`** is a non-actionable line: it renders in the list, executes never.
  Useful for "remember this" items that shouldn't pretend to be completable.
- **`parent_chat`** preserves timer semantics (wake *this* conversation). Null =
  automation semantics (start a fresh chat).

### 3.2 Status machine

```
open ──(trigger fires / human starts)──> queued ──(claim)──> running
  │                                                          │
  │                                             ┌────────────┼────────────┐
  │                                             ▼            ▼            ▼
  │                                          done      needs_review    failed
  │                                                          │            │
  └───────────────── blocked (depends_on) ───────────────────┘       retry?
```

- `open` — visible, not yet runnable (human task, or future-dated).
- `queued` — trigger fired, waiting for its `resource` lane.
- `needs_review` — **the load-bearing status.** See §7.
- `failed` — carries `last_error`; `attempts` bounds retries.

`needs_review` is the state that stops overnight agent work from silently becoming
overnight agent *commits*.

---

## 4. Mapping: every existing thing becomes a row

| Today | Becomes |
|---|---|
| `workspace_todos` row, open | `executor='human'`, `trigger='manual'`, `status='open'` |
| `workspace_todos` row, done | same, `status='done'` |
| `todo_requests` (chat-proposed) | `source='chat'`, `origin_chat=<id>`, and **not yet materialised as a job** — see §8 |
| `Chat` with `meta.timer_at` | `executor=<timer_model_id>`, `trigger='at'`, `trigger_at=<ns>`, `parent_chat=<parent>` |
| `automations` row | `executor=<model_id>`, `trigger='rrule'`, `rrule=<...>` |
| `automation_runs` row | the `running`→terminal transition, with history in `meta` |

---

## 5. Why `resource` is the whole point

`resource` is a **lock key**, not a label. Jobs sharing a non-empty `resource` run
strictly one at a time, FIFO within `priority`.

- `resource=''` → free-running (independent research tasks, web lookups).
- `resource='repo:/home/brendan/computer'` → serialize everything that writes that tree.
- `resource='file:cptr/utils/tools.py'` → finer grain, if the scheduler grows
  path-level awareness later.

This is what turns "schedule changes on the same codebase without conflicting" from a
policy you have to remember into an invariant the queue enforces, and it retro-fits the
09:00-ten-agents problem in §2.2 with zero user effort.

Two viable granularities for `resource='repo:...'`:

1. **Global lock per repo** — one writer at a time, no merge work, lower throughput.
2. **Git worktree per job + serial merge** — jobs run in parallel in isolated
   worktrees; the *merge* is what serializes on the resource.
   `utils/git.py` currently has no worktree support (`git worktree` appears nowhere),
   so (2) is real work but is the same trick the lanes already use at server level.

Recommendation: ship (1) — it is safe and trivial. Add (2) only once the queue is
actually the bottleneck.

---

## 6. New machinery #1: pricing

Required for `trigger='window'`: "start by `deadline`, but prefer the cheapest hour
inside it."

Needs a small static table, not a live API:

```
model_prices
  model_id, input_per_mtok, output_per_mtok, updated_at
price_windows                 -- recurring off-peak discounts
  model_id, rrule-like expr, multiplier   -- e.g. 0.5 during 16:30-00:30 UTC
```

Scheduler logic for `window`: enumerate candidate slots between `now` and `deadline`,
value each with `tokens × price × multiplier`, pick the cheapest that still starts in
time. `chat_messages.usage` already gives us the token estimate basis — a job can
borrow the median token cost of prior runs to size itself.

This is genuinely testable on your own setup: **DeepSeek — the model this workspace runs
on — already has a documented off-peak discount window**, so there is a real,
verifiable target rather than a hypothetical one.

Keep it honest: prices are configuration, they go stale, and the table must be
user-editable. Do **not** hardcode vendor prices in code.

---

## 7. The invariant

> **Agents never land writes. Agents land proposed changes.**

Everything follows from this one rule:

- satisfies your standing requirement that todo adds/removes need human verification;
- makes overnight work safe — the morning's deliverable is a **review queue**, not a
  done deal;
- gives the queue a natural landing zone (`needs_review`);
- means the *queue* touches the tree (under `resource`), while *agents* only produce
  diffs into it.

Corollary to state plainly: out-of-hours agent work creates an
**unreviewed-diff backlog** by construction. That is the real cost of this feature, and
it is a human-throughput problem, not a compute one. Worth sizing before Phase 4: if a
night produces more diff than a morning can review, the queue is too fast.

---

## 8. Migration path

Deliberately **non-destructive**. Nothing is dropped in Phase 1.

1. **Create `jobs`** (migration `0009_create_jobs.py`), with both indexes.
2. **Backfill** `workspace_todos` → `jobs` (`executor='human'`, `trigger='manual'`;
   `source` preserved). Ids preserved so URLs/links survive.
3. **Dual-write.** `cptr/routers/todos.py` keeps its routes but becomes a thin shim
   writing `jobs`. The built dashboard keeps working untouched.
4. **Leave `workspace_todos` / `todo_requests` in place** for one release. Drop in a
   later migration once nothing reads them.
5. **Timers last.** Folding `Chat.meta` timers into `jobs.trigger='at'` touches working
   code (`timers.py` is subtle: `recover_timers`, `cancel_on` event wiring,
   `get_pending_input_lock`). Do it as its own change with its own tests.
6. **Automations last of all.** `automations` has a UI, an API, a webhook path and a
   run-history table. Migrate only after `jobs` has proven itself as the spine.

`todo_requests` stays as-is through all of Phase 1: the *approval* concept is
independent of the *job* concept, and conflating them now would couple two migrations.

---

## 9. Phases

| Phase | Deliverable | Risk | Notes |
|---|---|---|---|
| **1** | `jobs` table + backfill + todo routes as shim | low | no behaviour change; scoped below |
| **2** | `executor='at'` — defer a todo to a model at a time | low | `timers.py` already does the hard part |
| **3** | `model_prices` + `trigger='window'` | medium | needs real price data; staleness to manage |
| **4** | `resource` serialization + claim atomicity | **high** | the queue proper; worktrees optional |

### Phase 1 scope (concrete)

- `cptr/migrations/versions/0009_create_jobs.py` — table + indexes; backfill
  `workspace_todos`; **no drops**.
- `cptr/models/jobs.py` — `Job` + class methods: `get_by_id`, `list_for_workspace`,
  `create`, `update_status`, `claim_due`, `count_by_status`. Same shape as
  `models/automations.py` / `models/todos.py` for consistency.
- `cptr/models/__init__.py` — export `Job`.
- `cptr/routers/jobs.py` — `GET/POST/PATCH/DELETE /api/jobs` (mirrors `/api/todos`).
- `cptr/routers/todos.py` — becomes a shim over `jobs`; response shape unchanged.
- `cptr/socket/main.py` — emit `jobs_changed` (keep `todos_changed` emitting too).
- `tests/test_jobs.py` — backfill parity (`count(jobs where executor='human') ==
  count(workspace_todos)`), status transitions, `claim_due` respects `trigger_at`.

**Acceptance:** the dashboard renders identically, all existing tests pass, and
`git diff` shows no change to any response schema. Phase 1 is a refactor, not a feature.

**Explicitly out of scope for Phase 1:** pricing, window triggers, locking, worktrees,
folding timers, folding automations, any UI change.

---

## 10. Risks

1. **Phase 1 touches working code** (the todo routes and the live dashboard). Mitigation:
   shim, don't rewrite; assert response-shape parity in tests.
2. **`claim_due` is not queue-safe** (see §2.2). Do not build Phase 4 on it; replace with
   a single conditional UPDATE … RETURNING, or `BEGIN IMMEDIATE` on SQLite.
3. **SQLite single-writer.** A queue on SQLite is fine at this scale, but the lock and
   the claim must be short. Don't hold a transaction across an agent run.
4. **Review backlog** (§7) is the true cost of Phase 3–4.
5. **Price staleness** makes `window` pick a slot that is no longer cheap, silently.
   Needs a visible "prices last updated" date in the UI.

---

## 11. Open questions for Brendan

1. **`resource` default.** Should a job on a git repo default to
   `resource='repo:<path>'` (safe, serial) or `''` (fast, racy)? Safety-first is my
   recommendation.
2. **`needs_review` landing zone.** Review where — the dashboard, the Git bar, or a new
   Review section? This decides a lot of Phase 4's UI.
3. **Human tasks: one list or two?** Does `executor='human'` still show ticking
   checkboxes (current behaviour), or is `kind='note'` closer to what you want for
   non-actionable items?
4. **Retry policy on `failed`.** Silent retry next tick, or surface it and wait?
5. **Does the dashboard become the *only* surface** for automations too (folding
   `/scheduled` in), or do automations keep their own page? Answering this early avoids
   duplicating UI twice.

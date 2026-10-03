---
area: dot-cptr-in-git
title: What of .cptr belongs in git
aliases: [.cptr, gitignore, auto_gitignore_dot_cptr, ensure_cptr_gitignored, .cptr/*, negation, allowlist, re-include, gitignore last match wins]
updated: 2026-10-03
---

# What of .cptr belongs in git

## Map
- `.gitignore` (this repo) — the curated `.cptr` block, lines ~29-45.
- `cptr/utils/workspace.py:26` — `ensure_cptr_gitignored(workspace)`, the single
  source of truth for the auto-entry.
- `cptr/utils/runtime.py:804` — `_ensure_cptr_gitignored_for(path)`, called on
  every authenticated file write. Now delegates to the above (was a duplicate).
- `cptr/utils/memory.py:1172` — calls it on workspace-scope memory writes, i.e.
  on prompt builds. This is the caller that fires most often in practice.
- `cptr/env.py:104-105` — `CPTR_AUTO_GITIGNORE_DOT_CPTR` (env) /
  `workspace.auto_gitignore_dot_cptr` (config), default **true**.
- `scripts/cptr-backup.sh` — globs `~/**/.cptr` into the nightly tarball, so
  everything here is backed up whether or not git tracks it.

## Facts
- [ran 2026-10-03 `du` on `.cptr`] 263M total: `cache/` 149M (regenerable TTS),
  `chats/` 67M (regenerable from the DB via `utils/chat_export.py`),
  `task_logs/` 47M, `harness/` 684K, `artifacts/` 64K, `screenshots/` 52K,
  `memory/` 16K, `skills/` 12K. Only the last four are small and hand-made.
- [ran 2026-10-03 secret scan over `.cptr`] No high-entropy credentials
  (`sk-`/`ghp_`/`AKIA`/`PRIVATE KEY` = 0 files), but `.cptr/task_logs/` holds
  **live session JWTs**, including an `admin` one valid to 2026-10-14, plus ~210
  files whose credential-shaped text is mostly *about* secrets rather than
  secret. `harness/`, `memory/`, `artifacts/`, `skills/` had none.
- [verified 2026-10-03 `git check-ignore` in a throwaway repo] Git **cannot
  re-include a path whose parent *directory* is excluded**. A bare `.cptr` plus
  `!.cptr/skills/` fails silently: `skills/` stays ignored.
- [verified 2026-10-03 same harness] `.cptr/*` excludes the *contents* without
  excluding the parent, so `!.cptr/skills/` then works. This is the only form
  that supports an allowlist.
- [verified 2026-10-03 same harness] Git is **last-match-wins**. An entry
  appended to the *end* of `.gitignore` overrides every `!` above it — so a
  curated block placed before an appended bare `.cptr` is dead, and looks fine.
- [verified 2026-10-03 `cptr/utils/workspace.py:40-43` at HEAD~] The auto-check
  only accepted a line that was *exactly* `.cptr` or `.cptr/`, so `.cptr/*`
  counted as "absent" and a bare `.cptr` was appended — the block above got
  silently voided on the next *prompt build*, not on a git operation.
- [ran 2026-10-03, live server] The running server re-appended `.cptr` within
  minutes of a manual `.gitignore` edit. Editing the file alone is not durable;
  the *code* has to be fixed or the feature disabled.

## Built
- `.gitignore` now carries a curated block: ignore `.cptr` contents, re-include
  `.cptr/skills/` and `.cptr/artifacts/`. 6 files, 64K, no credential-shaped
  strings. `cache/`, `chats/`, `task_logs/`, `screenshots/`, `memory/`,
  `harness/`, `usage.json` stay ignored.
- The block keeps a leading bare `.cptr` (satisfies the old check) followed by
  `!.cptr/` (re-include the dir, or git never descends), then `.cptr/*` (exclude
  contents), then the two negations. This is **working around the old code
  path** so the allowlist holds even before a restart; a comment in the file
  says which line is load-bearing.
- Fixed the real bug: `ensure_cptr_gitignored` now recognises `.cptr`,
  `.cptr/`, `/.cptr`, `/.cptr/`, `.cptr/*`, `/.cptr/*` as equivalent
  (`_CPTR_IGNORED_FORMS` + `_already_ignores_cptr`). `runtime.py`'s duplicate
  collapsed into a delegation, so the two cannot drift.
- `tests/test_workspace_gitignore.py` — 13 cases, including byte-identical
  leave-alone of a curated block. Full suite: 129 passed.
- `cptr/utils/runtime.py` gained the house `logger = logging.getLogger(__name__)`;
  it had no logging at all, and the new except-handler would have raised
  `NameError` instead of logging.

## Decisions
- **Selective, not blanket.** The ask was to un-ignore `.cptr` because it holds
  valuable data. Most of it is churn, 216M of the 263M is regenerable, and one
  directory carries live admin tokens — so commit the small hand-written parts
  and keep the rest out. Rejected: un-ignoring everything (a JWT in git history
  is forever); rejected: leaving it fully ignored (loses the two real assets).
- Rejected: setting `workspace.auto_gitignore_dot_cptr = false` instead of
  fixing the check. That disables the protection *globally*, so any repo without
  a `.cptr` line would start showing 263M of untracked state.
- Rejected: `runtime.py` keeping its own copy of the check. Duplication is what
  let the bug survive in two places; delegation is the fix.

## Dead ends
- Adding only `!.cptr/skills/` under a bare `.cptr`: silently no-op (parent
  excluded). Verified.
- Removing the appended `.cptr` by hand: it comes back on the next memory write
  or file write while the old code is in memory.
- `git check-ignore -q` on a *directory* reports on the directory, not its
  contents — check the actual file when validating an allowlist.

## Do not redo
- Before proposing "un-ignore `.cptr`", read this file. The interesting part is
  not the `.gitignore` edit; it is that cptr *writes* to `.gitignore` at runtime
  and that git's last-match-wins makes the append silently lethal.

## Open
- Two dirs are defensible additions and were deliberately left out pending a
  decision: `.cptr/harness/` (the cdp probe harness referenced by cross-workspace
  memory — but ~100 one-off probes, a 195K PNG, `__pycache__`, and
  `mint-cookie.py`) and `.cptr/memory/` (16K of per-user workspace memory, clean
  but arguably personal). Each is a one-line `!` addition.
- **The server must be restarted** for the `ensure_cptr_gitignored` fix to take
  effect. Until then the file-level workaround is what keeps the allowlist
  intact.

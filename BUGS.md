# Bug ledger

Bugs found while working in this fork, and whether they are upstream's or ours.

**"Is it in upstream?" is a fact about a commit, not a permanent truth.** Upstream moves, so every answer here is stamped with the commit and date it was checked at, and the machine-checked ones are re-verified by [`scripts/fork-status.sh`](scripts/fork-status.sh) (probe definitions in [`scripts/fork-probes.tsv`](scripts/fork-probes.tsv)) rather than left to memory. The latest run is in [`notes/fork-probe-report.md`](notes/fork-probe-report.md).

## How to read the "upstream" column

| State | Meaning | What to do |
| --- | --- | --- |
| `PRESENT-UPSTREAM` | Upstream code still shows the bug. | Worth filing upstream; treat the local patch as temporary. |
| `GONE-UPSTREAM` | Upstream no longer shows it. | Drop the local patch at the next sync. |
| `FORK-ONLY` | The bug is in this fork's own code, not upstream's. | Ours to fix; nothing to report. |
| `BY-DESIGN` | Upstream behaviour, deliberate there. | Do not "fix" it; document it instead. |
| `UNVERIFIED` | Not checked against upstream code yet. | Run the probe, or reproduce on the upstream lane. |

The fork's own patches are tracked the same way, from the other direction — a `fix` probe answers *"has upstream adopted my patch yet?"*, which is the state that quietly rots a fork.

## Ledger

| ID | Summary | Severity | Layer | Where | Upstream status | Local patch | Reported |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B-001 | Commit fails with HTTP 400 whenever a *staged deletion* is in the list; `GitBar.doCommit()` re-stages paths, a staged deletion matches neither worktree nor index, and one unmatchable path aborts the whole `git add` batch — so `/api/git/commit` is never reached | high | backend | `cptr/utils/git.py::stage()` | `FORK-ONLY` — fork fix, not in upstream @ `f9d1d8c` 2026-09-19 | `bb78992` | none |
| B-002 | Soft keyboard covers the terminal prompt instead of resizing it (no `visualViewport`/`geometrychange` handling) | medium | frontend | `Terminal.svelte` | `FORK-ONLY` — fork fix @ `f9d1d8c` 2026-09-19 | `d6d7787` | none |
| B-003 | Idle send button: `text-white` arrow on `bg-gray-200` = **1.24:1** contrast, measured. Broken in the *default light theme*, not just the mono palettes; present identically before and after the mono work, so it is a component bug rather than a palette one | low (a11y) | frontend | `chat/SendButton.svelte:64` | `PRESENT-UPSTREAM` @ `f9d1d8c` 2026-09-19 (`text-white bg-gray-200` still there) | none (one-token fix, unapplied) | none |
| B-004 | Tree listing `stat`s every entry just to decide whether it is a file, and descends `.git`/`node_modules` that the listing itself hides. On `/mnt/c` ~1–5 ms per `stat` makes this minutes, and it silently inflates every count reported to the agent | high (on WSL) | backend | `cptr/utils/runtime.py` | `PRESENT-UPSTREAM` @ `f9d1d8c` 2026-09-19 — upstream still uses `rglob` here (coarse probe; re-verify after each sync) | fixed in fork (`_TREE_IGNORE` + `_ScanBudget` + dirent classification) | none |
| B-005 | Directories on `/mnt/c` are pathologically slow to list: ~5 ms to open one (vs ~0.04 ms on ext4), ~1–5 ms per `stat`, so cost scales with directories visited rather than entries returned | medium | perf | UI file picker + backend listing | `BY-DESIGN` — upstream targets native filesystems, where it is a non-issue; not a bug to file | `scandir` + `dirs_only` + 10 s TTL cache + hover prefetch: **820 ms → 302 ms** | n/a |
| B-006 | Mono (e-ink) themes wiped solid fills: a rule matched `[class*='bg-white/']` by *substring*, so any element merely mentioning an alpha utility lost its background — an invisible "Open" button with a paper label on paper. The rule was unlayered, so it also beat the element's own `bg-gray-900` | high | frontend | `frontend/src/app.css` | `FORK-ONLY` — consequence of a fork-only feature @ `f9d1d8c` 2026-09-19 | fixed: two blocks after the wipe re-assert the two real surfaces (9/33 wiped surfaces → 0/0 measured) | none |
| B-007 | Perf data defects found in the first real sample (766 rows / 5 sessions): `ts` held page-relative `performance.now()` so rows reset on reload and could not be ordered across sessions; `mount` events (the bulk of the volume) carried no context while `tab_switch` did; 574 of 766 rows were `long_task`/`label:"self"` attributing nothing; `measureToPaint` readings from hidden tabs were silently inflated by rAF throttling | medium | both | fork's own telemetry | `FORK-ONLY` — fork-only instrumentation, no upstream equivalent @ `f9d1d8c` 2026-09-19 | `9eb2238` (`ts` = epoch-ms, `perf_ms`, `setPerfContext`, `during`/`since_ms`, `started_hidden`) | none |
| B-008 | Workspace sidebar headings render as grey washes in the mono palettes — mid-tone washes are exactly what a 1-bit display turns into noise, and unread counts were near-invisible | low | frontend | `SidebarWorkspaceList.svelte` | `FORK-ONLY` — only visible in a fork-only palette @ `f9d1d8c` 2026-09-19 | inverted ink plates: ink background, paper label/icons, unread = paper chip | none |
| B-009 | Every mounted chat tab polls `/api/terminal/sessions` on a 5 s interval, hidden or not — a headless tab (and one drawn on e-ink) keeps waking the server for a list it is not showing | low (background work) | frontend | `chat/ChatPanel.svelte` | `FORK-ONLY` — fork fix, upstream polls unconditionally @ `f9d1d8c` 2026-09-19 | `7b96987` — poll only while the tab is active *and* `document.visibilityState === 'visible'`, re-armed on `visibilitychange` | none |
| B-010 | Context compaction resumes from the **oldest** checkpoint on a branch: `_load_message_history` walks root→leaf and stops at the *first* `chat_summary`, so on a repeatedly compacted branch the loosest summary wins and messages that later summaries already cover are re-sent — and re-summarized — every turn | high (context budget) | backend | `cptr/utils/chat_task.py::_load_message_history` | `PRESENT-UPSTREAM` @ `f9d1d8c` 2026-09-19 (the `break` is still there) | fixed in fork — newest checkpoint on the branch wins, with 5 regression tests ([`tests/test_context_compaction.py`](tests/test_context_compaction.py)); replayed messages across the 158 chats in this install's DB: **17,236 → 8,119** | none |

| B-011 | Probe results were unreliable in two independent ways. (1) The pattern test was `grep -Fq` inside a `set -o pipefail` script: `-q` exits at the first match, which can kill `git show` with SIGPIPE while it is still writing, and `pipefail` then reports the *pipeline* as failed — so a pattern that is plainly present reads as absent (**9/40 false negatives**, non-deterministically, on the 119 KB `chat_task.py`). A false `GONE-UPSTREAM` advises *deleting a local patch that is still needed*. (2) The `file` kind relied on an empty pattern field, but a tab is IFS *whitespace* and `read` collapses runs of it, so the field is unrepresentable: the note landed in `$pattern` and the probe grepped prose instead of checking existence | medium (tooling — it silently misinformed every probe run) | tooling | `scripts/fork-status.sh`, `scripts/fork-probes.tsv` | `FORK-ONLY` — fork-only tooling, nothing upstream to compare @ `f9d1d8c` 2026-09-19 | fixed: `grep -Fc` (drains the input, so the writer always finishes); `file` rows answered by `git cat-file -e`; probe-only rows moved to a `P-*` id namespace so they cannot collide with ledger ids | none |

## Notes on method

Two of these are only "upstream status" questions in a loose sense: B-005 is upstream behaviour that is correct for its target (native filesystems) and merely expensive on WSL, and B-006/B-007/B-008 are consequences of fork-only features, so they cannot exist upstream. They are in the ledger anyway, marked `BY-DESIGN`/`FORK-ONLY`, because the useful question for those is different — *should this go upstream?* — and the answer for B-005 is no.

Verify rather than recall. For a UI bug the cheapest check is the upstream lane: `cptr-upstream` is a stock upstream checkout on **:4201** (`cptr-lanes.sh restart cptr-upstream`). If it reproduces there, it is upstream's. For a code-level claim, probe the source instead of the running app:

```bash
./scripts/fork-status.sh                                    # all probes, refreshed
git show upstream/main:cptr/utils/git.py | grep -F '_PATHSPEC_NO_MATCH'
git log --oneline -S'<pattern>' upstream/main               # did upstream ever have it?
```

Filing upstream: issues go to [`open-webui/computer`](https://github.com/open-webui/computer/issues), not to this fork. Re-check the top of the ledger before filing — the probe report may already show a fix landed.

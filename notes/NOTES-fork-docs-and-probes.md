# Fork documentation + upstream bug probes

Date: 2026-09-19

## Why

This repo is a fork (`origin` = `xanax/computer`, `upstream` = `open-webui/computer`). Two questions recur and both were being answered from memory:

1. *What has this fork changed, and why?* — for a visitor arriving from a fork link.
2. *Is this bug ours or upstream's, and has upstream fixed it yet?*

Question 2 is the one that matters, because the answer is **a fact about a commit, not a permanent truth**. Upstream moves; a remembered "yes, still broken upstream" decays silently. And a fork rots in a specific direction: when upstream adopts one of your patches, that patch becomes dead weight that will conflict forever, and nobody notices because nothing tells them.

## Layout

| File | Owner | Purpose |
| --- | --- | --- |
| `README.md` | upstream, **except one added `## Fork note` section** | the only thing a visitor sees without clicking. Fork identity is added as its own section; upstream's text is untouched. |
| `FORK.md` | fork | full write-up: what/why, status table, sync workflow, licence note |
| `CHANGELOG.fork.md` | fork | Keep-a-Changelog entries for fork changes, `0.9.21-fork.N` |
| `BUGS.md` | fork | the bug ledger + method |
| `notes/` | fork | per-topic reasoning and measurements (the real record) |
| `CHANGELOG.md` | **upstream — left pristine** | see below |
| `scripts/fork-probes.tsv` | fork | probe definitions, the machine-readable source of truth |
| `scripts/fork-status.sh` | fork | runs the probes, writes the report |
| `notes/fork-probe-report.md` | generated | latest results |
| `notes/fork-probe-state.tsv` | generated | previous run's statuses, for change detection |

## The CHANGELOG decision

Earlier fork commits (`ae85558`, `c8a021f`) wrote into upstream's `CHANGELOG.md` under `## [Unreleased]`. That is a conflict *every single sync*, because upstream edits that section constantly — it is the one part of the file guaranteed to be dirty. So upstream's changelog now stays pristine and fork changes go in `CHANGELOG.fork.md`. Same for the README body: the fork section is prepended as a distinct heading rather than woven through upstream prose, so a merge has one conflict site at worst and usually none.

## The probe model

A probe is `{id, kind, path, pattern}`. `pattern` is matched literally with `grep -F` against `git show upstream/main:<path>`. Four kinds, and the meaning of "found" flips per kind:

| kind | found upstream means | status |
| --- | --- | --- |
| `fix` | upstream adopted our patch → **delete the local patch** | `MERGED-UPSTREAM` / `FORK-ONLY` |
| `bug` | the bug is still upstream's → reportable | `PRESENT-UPSTREAM` / `GONE-UPSTREAM` |
| `file` | upstream now has something at our fork-only path → conflict | `IN-UPSTREAM` / `FORK-ONLY` |
| `feature` | upstream shipped the same feature → conflict | `ALSO-UPSTREAM` / `FORK-ONLY` |

`B-*` ids are shared with the ledger in `BUGS.md`: one row per ledger entry, and the same id means the same thing in both files. Rows that probe a fork feature/file with no ledger entry use `P-*`, so the two id spaces cannot collide — they did, until 2026-09-19: the `file` probes for the perf/TTS/sidebar files had taken `B-005…B-010`, numbers the ledger had already given to unrelated bugs, and three of those rows also carried their *note* in the `pattern` column (the empty field was omitted), so the probe grepped prose instead of checking that the file exists upstream. A `file` row has no pattern column at all: a tab is IFS *whitespace*, so `read` collapses a run of tabs and an empty field is unrepresentable — the row is `id<TAB>file<TAB>path<TAB>note` and the kind is answered by `git cat-file -e`, which is what it always meant.

The other half of that fix is in the probe loop: it used `grep -Fq`, whose first-match exit can kill `git show` with SIGPIPE mid-write, and `pipefail` then reports the *pipeline* as failed — so a plainly present pattern reads as absent. Measured on the 119 KB `chat_task.py`: **9/40 false negatives**, non-deterministically, which is how a `PRESENT-UPSTREAM` bug (B-010) first reported as `GONE-UPSTREAM` and how a file that exists upstream (P-006) reported as fork-only. `grep -Fc` drains the input so the writer always finishes: 0/40.

The script diffs this run's statuses against `notes/fork-probe-state.tsv` and prints `ACTION NEEDED` with the transitions. It exits 0 even when states change, so a scheduled run crossing a real transition does not look like a crash.

`--update` additionally rewrites the `<!-- fork-status:begin --> … <!-- fork-status:end -->` block in `FORK.md` (upstream sha/date/subject, commits ahead, commits behind, diffstat). Verified: a hand-edited wrong value is restored.

## Current findings (2026-09-19)

Base `f9d1d8c`, 27 commits ahead, 0 behind.

- **`PRESENT-UPSTREAM`, genuinely reportable:** B-003 (send button `text-white` on `bg-gray-200` = 1.24:1 — a *default-theme* a11y bug, not just a mono one) and B-004 (tree listing `stat`s every entry, descends `.git`/`node_modules`).
- **`FORK-ONLY`:** B-001 (git stage pathspec), B-002 (keyboard resize), B-006/B-007/B-008 (mono fill-wipe, telemetry data defects, sidebar headings — all consequences of fork-only features, so they cannot exist upstream), B-009 (hidden-tab session polling; fork patch, upstream still polls unconditionally), plus the `P-*` rows behind the perf telemetry, native TTS and sidebar-heading features.
- **B-010** (compaction replayed from the oldest checkpoint on a branch) is upstream's bug, still `PRESENT-UPSTREAM`; the fork patch and the before/after measurement are in [`NOTES-context-compaction-newest-checkpoint.md`](NOTES-context-compaction-newest-checkpoint.md).
- **Not bugs at all:** the `/mnt/c` slowness (B-005) is upstream behaviour that is correct for native filesystems. The answer there is "no, don't file it", which is why the ledger carries a `BY-DESIGN` state — the useful question for those is *should this go upstream?*, not *is it upstream?*

B-004 is a **coarse** probe (`rglob` in `runtime.py`) and will need re-verification after a sync; a precise probe would test for `_TREE_IGNORE`-equivalent handling, which is hard to grep for in code we did not write.

## Verification note

Visual check of the rendered README is worth doing before showing anyone: the fork section sits between "Start here" and `## Install`, so it is above the fold but below upstream's own intro.

## Maintenance

A weekly automation ("Fork upstream probe", Mondays 09:00) runs `fork-status.sh --update` and reports transitions without touching the server or committing anything. Probes must be re-checked whenever the upstream base moves: a probe whose pattern upstream has deleted reports `FORK-ONLY`/`GONE-UPSTREAM` for the wrong reason, and a silently rotted probe is worse than no probe.

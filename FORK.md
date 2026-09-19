<!--
SPDX note: this file is fork-added documentation, not upstream content.
Keep upstream-owned files (README body, CHANGELOG.md) free of fork narrative so
`git merge upstream/main` stays boring; this is the one place fork identity lives.
-->

# This is a fork

**`xanax/computer`** is a personal fork of **[`open-webui/computer`](https://github.com/open-webui/computer)** — "Open WebUI Computer" (`cptr`), which serves your whole machine to any browser.

Everything good here is upstream's. This fork exists because the maintainer runs `cptr` against a Windows filesystem from WSL and drives it from a phone and an e-ink display, which is a combination upstream does not optimise for. The changes below are the result.

| | |
| --- | --- |
| Fork | `xanax/computer` (`origin`) |
| Upstream | `open-webui/computer` (`upstream`) |
| Base branch | `main` tracks `upstream/main` |
| Upstream docs | https://docs.openwebui.com/ecosystem/computer/ |
| Licence | Upstream's, unchanged — see [Licence](#licence-and-attribution) below |

<!-- fork-status:begin -->
_Refreshed automatically by `scripts/fork-status.sh --update`; do not hand-edit._

| | |
| --- | --- |
| Upstream base | `open-webui/computer` @ `f9d1d8c` (2026-08-16) — "refac" |
| Fork commits ahead | 34 |
| Upstream commits we lack | 0 |
| Diff vs upstream | 157 files changed, 9990 insertions(+), 1720 deletions(-) |
| Probes last run | 2026-09-19 |
<!-- fork-status:end -->

Run `scripts/fork-status.sh` for the current numbers plus the bug/fix probes. **This fork carries 0 commits from upstream's future**: the base above is both the fork point and the freshest upstream commit, so a sync is always a fast-forward merge with no upstream work sitting unreviewed.

## What this fork adds

A short version. The reasoning, measurements, and verification for each item live in [`notes/`](notes/) and the ledger is [`BUGS.md`](BUGS.md).

### Speed — mostly the WSL `/mnt/c` cost model

Reading `/mnt/c` through the 9p bridge is expensive in a way that shapes every one of these: `stat(2)` on one path costs **~1–5 ms** (vs ~0.005 ms on ext4) and opening a directory costs **~5 ms** (vs ~0.04 ms). The unit of cost is *directories visited*, not entries listed. Fixes:

- **File-tree listing** (the agent's `list_directory` tool) no longer `stat`s every entry. It classifies from the directory entry — 9p supplies `d_type` for free — prunes `.git`/`node_modules` *inside* the count walk, never follows symlinked directories, and runs under a ~1 s budget with ceilings on dirs/files/stats/lines. Anything it cannot reach is marked `?`, any capped count is marked `+`, so an enormous tree degrades instead of hanging.
- **Directory browsing** (`use:scandir` + `dirs_only` + a 10 s TTL cache + hover prefetch): **820 ms → 302 ms** on a cold `/mnt/c` listing.
- **Perf telemetry that was missing entirely** — a `ui_events` table, `POST /api/ui-events`, and a client collector that measures tab switch → paint, component mount, and directory round-trips, on a real epoch-ms timeline. The first real sample (766 rows / 5 sessions) exposed four defects in the data itself, all fixed: `ts` wasn't a clock, `mount` events carried no context, 75% of rows were unattributable long tasks, and `requestAnimationFrame` readings from hidden tabs were silently inflated.

### Monitoring — seeing the machine from the outside

The same `ui_events` pipeline is deliberately best-effort: it buffers, flushes every 5 s or at 40 samples, sends with `sendBeacon` on unload, swallows its own failures, and is capped — a down backend cannot slow the UI. `GET /api/ui-events/summary` returns p50/p95/max per event kind, sorted slowest-first, which is the order worth fixing things in. Per-sample it records tab counts, group counts, viewport, and whether the tab was visible, so "slow because 40 tabs are open" is distinguishable from "slow on a cold cache".

### Fixes and tweaks

- **Commit button no longer fails with a 400** when a staged *deletion* is present. `GitBar` re-stages the files it is about to commit; a staged deletion matches neither worktree nor index, so one unmatchable path aborted the whole `git add` batch and the commit request was never sent. It already cost more than it saved to send the list in one call; now it keeps that fast path and, only on this specific failure, retries per path treating "nothing to stage" as a no-op.
- **On-screen keyboard:** terminals now handle `visualViewport`/`geometrychange`, so the keyboard on a phone resizes the terminal instead of hiding the prompt behind it.
- **Terminal font size** control.
- **Directory download as zip** in the file browser.
- **Tabs and sidebar:** workspace folders start expanded, chats close rather than being force-marked unread, active/muted font colours corrected.
- **Terminal shortcut bar** (Tab/Esc/Ctrl row) can be switched off in Settings → Appearance, for when you are typing on a real keyboard.

### Themes for e-ink displays

Two palettes, `bw` and `bw-dark`, render the UI as pure ink-on-paper: no greyscales and no dithering, because mid-tones on a 1-bit display become noise. Hover, active, and scrim states are **solid inversions** (ink surface with paper text, or an ink border) rather than grey washes; code and diagram surfaces stay paper with an ink border; translucent "veil" overlays become solid ink.

Building that in CSS over an existing Tailwind palette hit a real bug: a rule that wiped translucent fills matched on a *substring* of the class attribute, so any element that merely mentioned an alpha utility anywhere in its class list lost its solid background — most visibly an invisible "Open" button whose paper label sat on paper. Both fixes are after the wipe rule and re-assert the two real surfaces. A sweep of all 731 distinct class strings in the codebase, measured per element in both themes, went from 9 wiped ink surfaces / 33 wiped paper surfaces to **0 / 0**, and invisible-text cases from 13 → 5 (bw) and 64 → 5 (bw-dark); the remainder are false positives or the pre-existing component bug logged as **B-003**.

## Syncing with upstream

```bash
git fetch upstream
git log --oneline upstream/main..main        # what this fork adds
git rev-list --count main..upstream/main      # what upstream has that we don't
git diff --shortstat upstream/main...main     # scale of the delta
git merge upstream/main                       # sync
./scripts/fork-status.sh --update             # re-probe bugs/fixes, refresh the table above
```

After a sync, run `scripts/fork-status.sh` and look for `MERGED-UPSTREAM` rows: those are local patches upstream has since adopted and which should now be deleted. That state is how forks rot, so it is worth checking each time rather than discovering it during a conflict.

Fork work lands on `feat/*`/`fix/*` branches and is merged to `main`, so `git log upstream/main..main` is effectively the changelog.

## Bugs

[`BUGS.md`](BUGS.md) is the ledger: every bug found, whether it is in upstream, and whether it was reported. Two columns are machine-maintained — see [`scripts/fork-probes.tsv`](scripts/fork-probes.tsv). Every entry is checked against real upstream *code* and recorded with the commit and date it was checked at, because "present upstream" is a fact about a commit, not a permanent truth.

## Licence and attribution

Upstream is **source-available, not open source**: the Open Use License (Elastic License 2.0 plus an attribution-preservation condition). This fork redistributes modified source under those same terms and adds nothing to them.

In practice that means the attribution elements — the product name "Open WebUI Computer", `cptr`, the logo, copyright notices, the About/version screens — are left **exactly as upstream ships them**. No fork branding has been applied to them, and nothing here should be read as forking the *project*; this is one person's working copy of someone else's software. Fork narrative deliberately lives in this file rather than in the README body, which also happens to keep upstream merges clean.

The one exception is the README, where a short "This is a fork" note sits at the top so a visitor arriving from a fork link is not misled about what they are looking at. It adds a pointer; it modifies no attribution.

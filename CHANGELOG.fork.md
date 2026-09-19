# Fork changelog

Changes made in **this fork** (`xanax/computer`), as opposed to upstream's own [`CHANGELOG.md`](CHANGELOG.md), which is left untouched so that merging upstream stays boring and conflict-free.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions track the upstream release this fork is based on, with the fork build appended per [SemVer's fork convention](https://semver.org/#spec-item-9): upstream `0.9.21` + third fork release = `0.9.21-fork.3`.

For the *reasoning* behind each change, the per-topic documents in [`notes/`](notes/) are the real record. For bugs and whether they are upstream's, see [`BUGS.md`](BUGS.md).

## [Unreleased]

Based on upstream `f9d1d8c` (2026-08-16).

### Added

- **UI performance telemetry.** A `ui_events` table (migrations `0005`, `0006`), `POST /api/ui-events` batch ingest capped at 200 events, and `GET /api/ui-events/summary` / `/recent` / `POST /prune` / `DELETE`. Summary returns count/p50/p95/max/mean per event kind, sorted slowest p95 first. Client collector in `perf.ts`: buffers, flushes every 5 s or 40 samples, `sendBeacon` on unload, best-effort throughout, disabled via `localStorage['cptr.perf.disabled']`.
- **Instrumented paths:** tab switch → paint, mount → paint per tab type, directory list/navigate/paint/prefetch, and main-thread long tasks ≥50 ms attributed to the interaction that caused them.
- **On-device text-to-speech** (`audio.tts_provider = native`) using SAPI / macOS `say` / espeak — no API key required.
- **Directory download as zip** in the file browser.
- **Terminal font size** control.
- **Terminal shortcut bar toggle** (Settings → Appearance): hides the Tab/Esc/Ctrl row for hardware keyboards.
- **Chats close rather than being marked unread,** with an × on the chat row and a close action at the end of a conversation. Closed chats return automatically when new activity makes them unread.
- **e-ink themes `bw` and `bw-dark`:** pure ink-on-paper, no greyscales and no dithering. Mid-tones are solid inversions (ink surface with paper text) or an ink border; code and diagram surfaces stay paper.
- Workspace headings render as inverted ink plates in every theme — ink background, paper label/icons, unread as a paper chip.

### Changed

- **File-tree listing is bounded and stat-free.** `os.scandir` with dirent classification throughout, `_TREE_IGNORE` pruned *inside* the count walk, symlinked directories never followed, all under a shared `_ScanBudget` (~1 s plus ceilings). Unreached items are marked `?` and capped counts `+`.
- **Directory listing** uses `scandir` + `dirs_only` + a 10 s TTL cache + hover prefetch: **820 ms → 302 ms** on a cold `/mnt/c` listing.
- Workspace folders start expanded by default.

### Fixed

- **Commit button returned HTTP 400 whenever a staged deletion was present.** `GitBar.doCommit()` re-stages the files it is about to commit; a staged deletion matches neither worktree nor index, so one unmatchable path aborted the entire `git add` batch and `/api/git/commit` was never reached. `git.py::stage()` now keeps the single-call fast path and, only on the pathspec failure, retries per path treating "nothing to stage" as a no-op.
- **Soft keyboard covered the terminal prompt** instead of resizing it — `visualViewport` / `geometrychange` handling added.
- **Solid fills wiped in the mono themes.** An unlayered rule matched `[class*='bg-white/']` by *substring*, so any element merely mentioning an alpha utility in its class list lost its background — including an invisible "Open" button with a paper label on paper. Two blocks after the wipe re-assert the two real surfaces; a sweep of all 731 distinct class strings measured wiped surfaces at 9 ink / 33 paper → **0 / 0**, invisible-text cases 13 → 5 (bw) and 64 → 5 (bw-dark).
- **Perf data quality:** `ts` was page-relative rather than epoch-ms (rows reset on reload, unordered across sessions) — now epoch-ms with `perf_ms` retaining the precise intra-page clock; ambient context (tab counts, groups, viewport, visibility) now attached to every sample at *record* time rather than flush time; long tasks attributed to the preceding interaction, sub-80 ms dropped as noise; `started_hidden` / `active` flags so rAF-throttled readings from background tabs can be discounted instead of read as slowness.

### Removed

- **Mark as unread** — closing replaces it. The unread dot still clears on read; there is no longer a way to force it back.

## Probe status

Re-verified against upstream by `./scripts/fork-status.sh`. Latest report: [`notes/fork-probe-report.md`](notes/fork-probe-report.md).

[Unreleased]: https://github.com/open-webui/computer/compare/f9d1d8c...HEAD

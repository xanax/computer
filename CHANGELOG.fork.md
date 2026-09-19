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
- **Compaction is visible in the transcript.** A checkpoint now draws a labelled rule above the message it was stamped on ("Earlier messages summarized"), so a chat that silently lost its middle is legible. `_message_dict()` reports `summary_chars` per message (the length only — the text stays in the system prompt), and a mid-turn `chat:compacted` socket event stamps the marker without waiting for a reload. Previously compaction was invisible: the transcript just got shorter.
- **Tool servers attached per workspace.** An OpenAPI or MCP server can now be attached to a single workspace instead of every one: new servers default to workspace-only, existing global servers stay global, and the workspace editor preserves the attachment when it saves its other fields. Ids live in `workspaces.data.toolServers`; `GET /api/state/tool-servers` lists the registered servers without their secrets so the workspace editor can offer them.
- **Per-workspace time on screen.** Visible time is now measured rather than inferred from the gaps between events: a `dwell` span opens while a workspace's tab is visible and closes on workspace switch, visibility change or unload, clamped at 30 minutes so a sleeping laptop is not a session of work. `GET /api/ui-events/dwell` sums those spans and each sidebar heading shows its workspace's share of the tracked total, read once on mount and drawn as paper text on the heading's ink plate. Shares under 0.5% are dropped rather than printed as a meaningless 0%, and there is deliberately no gap-attribution fallback — the figure sits next to a workspace name, so it is measured or absent.
- **A question line in the chat header.** Under the title it carries the first line of the question whose answer is on screen, and clicking it scrolls that question to just under the header — which leaves the line naming the question before it, so repeated clicks walk back up the chat. In a short chat, or before the question's own text has scrolled away, nothing is shown.
- **Read-only `ui_metrics` tool** (Admin → Models, `telemetry` group) summarising the `ui_events` samples: measured dwell per workspace, the slowest event kinds, and an inventory of what is open.

### Changed

- **File-tree listing is bounded and stat-free.** `os.scandir` with dirent classification throughout, `_TREE_IGNORE` pruned *inside* the count walk, symlinked directories never followed, all under a shared `_ScanBudget` (~1 s plus ceilings). Unreached items are marked `?` and capped counts `+`.
- **Directory listing** uses `scandir` + `dirs_only` + a 10 s TTL cache + hover prefetch: **820 ms → 302 ms** on a cold `/mnt/c` listing.
- **Command-session polling is gated on visibility.** A chat tab polls `/api/terminal/sessions` every 5 s only while it is the active tab *and* `document.visibilityState === 'visible'`, re-arming on `visibilitychange`; previously every mounted tab polled forever, hidden or not.
- **A chat load carries only what the collapsed transcript draws.** Reasoning text and tool output were 48.6 MB of the 60.8 MB of stored messages, and none of it is visible until a row is expanded — the client truncates long tool output to 10k chars even then. `GET /api/chats/{id}` now sends placeholders carrying a char count instead, flags the affected messages `output_stripped`, and the full text arrives from `GET /api/chats/{id}/messages/{message_id}/output` the first time one of that message's rows is opened: the largest chat on this box went **6.60 MB → 0.66 MB (89.9%)** per open, same messages, same ids. `ask_user` calls and their answers are never trimmed, since that row renders itself expanded; a preference that keeps rows open takes `?full=1` and one response rather than a request per message.
- **A chat load reads the message table once instead of four times.** Three bookkeeping questions asked of the same chat — the newest message id, the newest non-null `model`, and the usage-checkpoint walk — each loaded every full ORM row, including the `output` column that is ~95% of the table's bytes. New header-only queries in `cptr/models/chats.py` answer them from a few columns, and `_get_chat_context_usage` checks the provider usage report before rebuilding history: full reads per load **4 → 2** (the response body, which needs the rows anyway, and the token estimate), their query time **418 ms → 204 ms**, against 20 ms for the new queries.
- Workspace folders start expanded by default.

### Fixed

- **Commit button returned HTTP 400 whenever a staged deletion was present.** `GitBar.doCommit()` re-stages the files it is about to commit; a staged deletion matches neither worktree nor index, so one unmatchable path aborted the entire `git add` batch and `/api/git/commit` was never reached. `git.py::stage()` now keeps the single-call fast path and, only on the pathspec failure, retries per path treating "nothing to stage" as a no-op.
- **Soft keyboard covered the terminal prompt** instead of resizing it — `visualViewport` / `geometrychange` handling added.
- **Solid fills wiped in the mono themes.** An unlayered rule matched `[class*='bg-white/']` by *substring*, so any element merely mentioning an alpha utility in its class list lost its background — including an invisible "Open" button with a paper label on paper. Two blocks after the wipe re-assert the two real surfaces; a sweep of all 731 distinct class strings measured wiped surfaces at 9 ink / 33 paper → **0 / 0**, invisible-text cases 13 → 5 (bw) and 64 → 5 (bw-dark).
- **Compaction reloaded from the wrong checkpoint**, so a repeatedly compacted chat re-sent history it had already summarized. `_load_message_history` scanned root→leaf and stopped at the *first* `chat_summary`, i.e. the oldest, loosest checkpoint on the branch; summaries stack, each absorbing the previous, so the newest is the one that bounds the replay. It now keeps the most recent checkpoint on the branch (which is also the point `_summary_checkpoint_message_id` stamps, so the two agree). Measured over the 158 chats in this install's DB: messages replayed per turn **17,236 → 8,119**, all 23 affected chats smaller, none larger. Regression tests in `tests/test_context_compaction.py`.
- **Perf data quality:** `ts` was page-relative rather than epoch-ms (rows reset on reload, unordered across sessions) — now epoch-ms with `perf_ms` retaining the precise intra-page clock; ambient context (tab counts, groups, viewport, visibility) now attached to every sample at *record* time rather than flush time; long tasks attributed to the preceding interaction, sub-80 ms dropped as noise; `started_hidden` / `active` flags so rAF-throttled readings from background tabs can be discounted instead of read as slowness.

### Removed

- **Mark as unread** — closing replaces it. The unread dot still clears on read; there is no longer a way to force it back.

## Probe status

Re-verified against upstream by `./scripts/fork-status.sh`. Latest report: [`notes/fork-probe-report.md`](notes/fork-probe-report.md).

[Unreleased]: https://github.com/open-webui/computer/compare/f9d1d8c...HEAD

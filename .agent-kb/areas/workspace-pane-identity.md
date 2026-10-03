---
area: workspace-pane-identity
title: Tab ids repeat across workspaces, so panes are keyed by workspace + id
aliases: [paneKey, CHAT_TAB, pane reuse, workspace switch leak, persisted-tab, B-020]
updated: 2026-10-03
---

# Tab ids repeat across workspaces, so panes are keyed by workspace + id

## Map
- `cptr/frontend/src/routes/+page.svelte` — the pane host. `paneKey(tab)` (~:887) and five keyed `{#each}` loops: home pane (:1070-1111, still `tab.id`), group panes — `files` :1462 (shared, id-keyed), `file`/`chat`/`terminal`/`browser` :1471-1511 (`paneKey`). Panes are `.persisted-tab`, hidden with `.persisted-tab-hidden`, never unmounted on a tab switch.
- `cptr/frontend/src/lib/stores.ts` — the pinned zone: `DASH_TAB`/`FILES_TAB`/`CHAT_TAB` (line ~180), `pinWorkspaceGroups` (~:235, re-pins on load, drops blank strays, demotes pinned conversations), `orderWorkspaceTabs` (~:192, `launcher.slice(0, 1)`).
- `cptr/frontend/src/lib/components/chat/ChatPanel.svelte` — `ontabupdate` → `updateTab(id, {label, path})` is a panel's only write back to a tab; `detachPinnedChatTab` moves a conversation out of the launcher **keeping its id**.
- `notes/NOTES-cross-workspace-chat-pane.md` — measurements, probes, the heal's weak point; `BUGS.md` B-020; probe `B-020` in `scripts/fork-probes.tsv`.

## Facts
- [ran 2026-10-03 cdp harness, live pair of workspaces] Before the fix, switching AIjly → general handed general's launcher pane the mounted `ChatPanel` (and the conversation) of AIjly's detached `chat` tab: `bodyHasForeignChat: true`, and general's DB row then read `['chat','chat','ae2580 Demo URLs','8366e702…',true]`.
- [verified 2026-10-03 cptr/frontend/src/routes/+page.svelte] Svelte reuses a keyed block whose key matches, so a key that is not *globally* unique is a per-workspace aliasing bug, not a cosmetic one: the reused block keeps component state (`chatId`) and DOM.
- [verified 2026-10-03 stores.ts] `CHAT_TAB`/`DASH_TAB`/`FILES_TAB` are module constants, so the pinned launcher id `chat` exists simultaneously in every workspace; `pinWorkspaceGroups` re-pins them from those constants on every load.
- [ran 2026-10-03 probes] After the fix the only pane that survives a workspace switch is the `files` (FileBrowser) wrapper — `fp.editable:false`, no editor — which is intended: `dash`/`files` are rendered outside the loop and read `$currentWorkspace`.
- [ran 2026-10-03 read-only sqlite] `workspaces.data['groups'][n].tabs` = `[id, type, label, path, permanent]`; the live DB is `~/.cptr/app.db` (`file:…?mode=ro` URI), `updated_at` is unix seconds. Query helper: `notes/_scratch/ws-rows.py`.

## Built
- `paneKey(tab)` + `const wsPath = $derived($currentWorkspace?.path ?? '')` in `+page.svelte`, applied to the four per-workspace pane loops (`file`/`chat`/`terminal`/`browser`). Identity is `workspace path \u0000 tab id`.
- Probe set: `notes/_scratch/probe-user-repro.mjs` (the user's repro: switch, then click the launcher — needs ~13 s for panes to paint), `probe-ws-switch-roundtrip.mjs` (marks panes with `dataset.probeA`; a mark that survives **is** a reused element), `probe-general-fresh.mjs`, `ws-rows.py`.

## Decisions
- Key panes by workspace **at the pane host**, not by making launcher ids unique. The id `chat` is load-bearing (the persisted `activeTabId`, `detachPinnedChatTab`, the sessionStorage draft key, and the pinned zone is rebuilt from constants each load), so uniqueness would have to be persisted anyway — and the *real* invariant is "a pane belongs to a workspace".
- Rows already hijacked are left to heal on load rather than migrated: `pinWorkspaceGroups` re-pins the blank launcher and `orderWorkspaceTabs`' `launcher.slice(0,1)` drops the path-carrying duplicate. A DB cleanup would race every live browser tab's autosave for no visible gain (the heal runs before first paint).

## Dead ends
- "The panel holds a stale `chatId`" is a symptom; the panel is not misconfigured — it was handed the wrong tab. Do not try to fix it inside `ChatPanel.svelte`.
- Clearing panel state on a workspace change cannot work: the panel has no workspace prop and no idea a switch happened.
- `git status` in this repo is not a reliable "what did I change" list — sibling agent sessions commit into the same tree (B-019 landed while this task ran).

## Do not redo
- Re-deriving upstream's status: `git show upstream/main:cptr/frontend/src/routes/+page.svelte | grep -n 'as tab (tab.id)'` shows upstream keys by id too, but upstream's `stores.ts` has **no** pinned launcher (`CHAT_TAB`/`pinWorkspaceGroups` absent) — so the bug is fork-only. The `B-020` probe answers this from now on (`scripts/fork-status.sh`).
- Re-probing "is the DB contaminated?" after a switch: the launcher row for a healed workspace is `['chat','chat','chat',None,True]` and stays byte-identical across a round trip (only `updated_at` moves).

## Open
- [2026-10-03] 12 other workspace rows still carry a hijacked launcher in the DB; each heals the first time it is opened, and the *currently open* browser tab needs a page reload to pick up the new build.
- A chat that is streaming while you switch workspaces now unmounts its panel (before, it accidentally stayed mounted). Server-side nothing is lost; the panel re-syncs through `needsResync` → `loadChat` on return — the path `sleepClosedChatTabs()` already used.
- The home pane's four loops (:1070-1111) still key on `tab.id` alone; safe today because `homePane` is a single store with `home-…` ids, but it is the same shape if home panes ever become per-workspace.

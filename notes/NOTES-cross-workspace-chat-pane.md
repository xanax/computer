# Panes belong to a workspace — the tab id is not unique

Reported 2026-10-03: in one workspace, opening the pinned **chat** tab showed the
conversation you had been reading in the workspace you just came from. Worse than
a wrong view: the mounted panel kept that other workspace's `chatId` as its own
state, so a send or a history pick wrote the foreign chat onto the new
workspace's launcher row (the launcher then carries the other chat's id and
label, and the message lands in a chat that workspace does not own).
`BUGS.md` B-020.

## Two facts that combine

1. **Tab ids repeat across workspaces.** The pinned zone is created by constant
   tab objects in `cptr/frontend/src/lib/stores.ts` — `DASH_TAB`, `FILES_TAB`
   and `CHAT_TAB` (id `chat`). `createDefaultGroup()` hands the *same*
   `CHAT_TAB` object to every workspace, and `pinWorkspaceGroups()` re-pins it on
   load. A conversation detached from that launcher (`detachPinnedChatTab` in
   `chat/ChatPanel.svelte`, called from the `ontabupdate` handler) keeps the id
   of the launcher it came from — the detached tab **is** the `chat` tab, now
   with a `path`. So `chat` is a valid tab id in every workspace at once.
2. **Svelte reuses a keyed block whose key matches.** `cptr/frontend/src/routes/+page.svelte`
   renders one `.persisted-tab` per tab inside `{#each group.tabs… as tab (tab.id)}`.
   Switching workspace replaces the whole tab list, but the key of the incoming
   workspace's launcher (`chat`) equals the key of the outgoing workspace's
   conversation tab (`chat`, detached earlier) — so the *DOM element and the
   mounted component* of the outgoing pane were handed to the incoming tab. The
   panel never learns the switch happened: `chatId` is component state, and the
   whole point of these panes is that they survive tab switches
   (`.persisted-tab-hidden`, not unmount).

The panel's only channel back to a tab is `ontabupdate` →
`updateTab(id, { label, path })`, and the id it passes is the id of the *tab it
was handed*, which is now a tab of the other workspace. Hence the leak writes
itself into the DB.

## Fix

`paneKey(tab)` in `+page.svelte`: identity is **workspace path + tab id**, applied
to the four pane types that can be per-workspace state (`file`, `chat`,
`terminal`, `browser`). `dash`/`files` keep their bare ids on purpose — both are
rendered outside the `{#each}` loop and are singletons that read
`$currentWorkspace`, so *sharing* them is the desired behaviour (that is what
makes the file browser follow the workspace you are in).

Rejected: making the launcher id unique per workspace. `chat` is load-bearing as
an id — `activeTabId`, `detachPinnedChatTab`, the sessionStorage draft key
(`pickUpChatDraft`), and the home-pane tabs (`home-…`) all address tabs by id,
and the pinned zone is re-asserted from constants on every load, so a fresh id
per workspace would have to be persisted or it would re-collide anyway.

## Evidence

Probes (`scripts/cdp.mjs` — see *Reproducing* below; screenshots in
`/tmp/fixed-*.png`, raw output in the cdp log for 2026-10-03):

| probe | before the fix | after |
| --- | --- | --- |
| fresh load of `/home/brendan/general`, its launcher row had been hijacked | `bodyHasForeignChat: true` (AIjly's chat "ae2580 Demo URLs" rendered inside general's launcher) | `false`; visible pane shows the landing view ("What can I help you with?") |
| user's repro: AIjly (whose `chat` pane holds a real conversation) → switch to general → click the launcher | visible pane = AIjly's conversation; general's row gained `label: "ae2580 Demo URLs"` | visible pane = landing view + general's own history; one AIjly pane survives the switch and it is the FileBrowser wrapper (`editable:false`, the shared `.files` pane) — no chat pane carries over |
| DB row before/after the round trip | launcher `['chat','chat','ae2580 Demo URLs', '8366e702…', True]` | `['chat','chat','chat',None,True]` both before and after the probes — the round trip now writes nothing foreign |

Reproduce the DB side read-only:

```bash
.venv/bin/python notes/_scratch/ws-rows.py /home/brendan/AIjly /home/brendan/general
```

```python
sqlite3.connect("file:/home/brendan/.cptr/app.db?mode=ro", uri=True)
# workspaces.data['groups'][n].tabs -> [id, type, label, path, permanent]
```

## Why the hijacked rows heal themselves (and the trap in that)

`pinWorkspaceGroups()` keeps the *blank* permanent chat tab as the launcher
(`!tab.path`) and re-pins the canonical `CHAT_TAB`; the hijacked tab has a
`path`, so it is not chosen. It is also not a "stray" (`isStray` compares ids and
its id *is* `chat`), so it survives into the list — where `orderWorkspaceTabs()`
puts it and the pinned launcher in the same `launcher` bucket and keeps
`launcher.slice(0, 1)`, i.e. **the pinned one, because it was placed first**.
Result: one blank launcher, hijacked one dropped. The foreign conversation is not
lost — it lives in the workspace that owns it.

So the heal is real but *incidental*: it depends on the doomed tab sharing the id
`chat`. If the launcher is ever given a fresh id (or `orderWorkspaceTabs` starts
preferring tabs with a path), the hijacked tab stops colliding, becomes a stray,
gets demoted to an ordinary chat tab (`{ ...tab, permanent: false }` — it keeps
its messages) and a *foreign conversation* is left sitting in the workspace. That
branch is intended for the genuine "a conversation ended up pinned" case; don't
extend it to the hijack without re-checking this note.

Rows already contaminated in the DB do **not** need a migration: the heal runs on
load, before the first paint (the fresh-load probe shows no foreign chat even on
the first frame). Writing a one-off cleanup into 13 workspace rows would have to
race every live browser tab's autosave for no visible gain.

## Blast radius to watch

- A chat that is **streaming** while you switch workspaces now unmounts its
  panel, where before it accidentally stayed mounted. The stream is unaffected
  server-side (the turn completes and persists), and the panel re-syncs on return
  (`needsResync` → `loadChat` when it becomes visible again) — the same path used
  by `sleepClosedChatTabs()`. Nothing is lost; the visible tokens may jump.
- The opposite is an improvement: switching workspaces now *destroys* the panes
  it used to keep mounted, so the multi-chat mount storm
  (`notes/NOTES-ui-multi-chat-slowdown.md`) no longer carries across a switch.
- Anyone changing the pane keying must keep the `{#each}` keys in step with the
  pane type list — `dash`/`files` are deliberately absent from `paneKey`.

## Reproducing

```bash
# cookie first (the harness browser has its own profile)
.venv/bin/python .cptr/harness/mint-cookie.py --user brendan > /tmp/verify-cookies.txt
node .cptr/harness/cdp.mjs \
  --url 'http://127.0.0.1:4200/?workspace=/home/brendan/AIjly' \
  --js notes/_scratch/probe-user-repro.mjs --wait 3000 --shot /tmp/repro.png
```

`probe-user-repro.mjs` needs ~13 s after load (panes paint late), clicks the
`.ws-item a` of the target workspace, then the tab-bar button labelled `chat`.
`probe-ws-switch-roundtrip.mjs` marks panes with `dataset.probeA` and reports
which marks survive a switch — an element that survives **is** a reused pane.

## Related

- `notes/NOTES-ui-multi-chat-slowdown.md` — the pane mount storm the reuse was
  hiding inside.
- `.agent-kb/areas/workspace-pane-identity.md` — the condensed version.
- `BUGS.md` B-020; probe row in `scripts/fork-probes.tsv`.

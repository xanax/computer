# Changelog

Newest first. One line per task. Current truth is in `areas/`, not here.

2026-10-03 — workspace-pane-identity — Switching workspace and clicking the pinned **chat** tab showed — and then wrote — the *other* workspace's conversation: the launcher id `chat` exists in every workspace and Svelte reuses a keyed block whose key matches, so the incoming pane inherited the outgoing `ChatPanel`. Panes are now keyed by workspace path + tab id; verified with the cdp harness on a live pair of workspaces and against the DB rows (B-020).
2026-10-02 — mono-codemirror-rendering — Mono editor read as ink-on-ink: the cursor line's text was never flipped and a selection's slab is painted behind the glyphs. Cursor line, selection (shared `editor-mono.ts` mark, both CM mounts) and the carets now invert, verified 0/255 in `bw` and `bw-dark` (B-018).
2026-10-03 — workspace-state-ownership — The workspace prompt "saved" and then reverted: the layout autosave echoes the whole object it read (prompt included) and `put_workspace` only guarded keys the body *omitted*. `prompt`/`services`/`toolServers` are now server-owned on the layout PUT and the store drops `prompt`/`services` at load; repro/verify over HTTP on an isolated instance (B-019).
2026-10-02 — frontend-reactivity — Dashboard clicks died: a no-op store notify rebuilt the board between pointerdown and pointerup; guarded both (B-017).

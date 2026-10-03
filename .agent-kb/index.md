# Index

Read this before exploring. Then open at most three area files whose Summary or Keywords match the task. Do not read the rest of `.agent-kb`.

Older, topic-per-file notes are in `notes/NOTES-*.md` and have **not** been folded in here yet — if the index row does not cover your topic, grep `notes/` before re-deriving it.

| Area | File | Summary | Keywords |
| --- | --- | --- | --- |
| mono-codemirror-rendering | areas/mono-codemirror-rendering.md | A mono selection was ink-on-ink because CodeMirror paints the slab in a layer *behind* the text; the cursor line, selections and carets are three surfaces to flip, and `:where()` makes a rule lose to the token colour. | cm-activeLine, cm-mono-selected, mono, e-ink, bw, bw-dark, CodeMirror, selection, caret, FileEditor, :where, persisted-tab |
| frontend-reactivity | areas/frontend-reactivity.md | A no-op store write still notifies; the pane re-render it causes can unmount the element you are clicking, and the dashboard's `{#if loading}` board is what ate those clicks. | svelte stores, setActiveGroup, dashboard, clicks, rerender, panes, services, B-017 |
| workspace-state-ownership | areas/workspace-state-ownership.md | The workspace read carries more than the store owns (prompt, services, toolServers) and the layout autosave echoes it all back — so a field with its own endpoint "saved" and then reverted, because `put_workspace` only guarded keys the body *omitted*. | workspace prompt, autosave, stale echo, put_workspace, toolServers, services, revert, B-019 |

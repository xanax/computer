# Changelog

Newest first. One line per task. Current truth is in `areas/`, not here.

2026-10-02 — mono-codemirror-rendering — Mono editor read as ink-on-ink: the cursor line's text was never flipped and a selection's slab is painted behind the glyphs. Cursor line, selection (shared `editor-mono.ts` mark, both CM mounts) and the carets now invert, verified 0/255 in `bw` and `bw-dark` (B-018).
2026-10-02 — frontend-reactivity — Dashboard clicks died: a no-op store notify rebuilt the board between pointerdown and pointerup; guarded both (B-017).

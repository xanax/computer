# Sibling fork triage log

Append-only record of what each scan judged, so a later run can tell "already
considered" from "new". Format, one section per scan:

    ## YYYY-MM-DD
    - `fork` (head sha) — IMPORT / MAYBE / REJECTED: one line of reasoning

A fork is only re-judged when the head sha in `notes/fork-scan-state.tsv` moves
past the one recorded here. The scan itself is regenerated and disposable
(`notes/fork-scan-report.md`); this file is the memory.

## 2026-09-19 — first scan (77 forks, 18 carry commits we lack)

Baseline judgements. None of these were acted on; they are candidate reading.

- `heidi-dang/computer` (2ff7ddc1) — MAYBE: 215 substantive commits, the largest excursion of any fork (live activity streams, steering provenance, scoped direct-coding API, resumable terminal events, lease lifecycle). Too big to skim; worth a deliberate read on a quiet day rather than a merge.
- `1-kabir/computer` (4b7dda20) — IMPORT: subagent visibility bar + cancel API (we have `async_subagents.py` cancel-all but no UI), provider-side stream truncation detection with a "Continue" affordance, tab rename via context menu, per-workspace memory char limits.
- `ql-isaac/computer` (e5b94df2) — MAYBE: the `monospaceFont` propagation batch (editor, mermaid, chat, ShortcutBar) is directly relevant to the e-ink/mono work; the OMP agent integration and `update.sh` rebase loop are not.
- `aaronbolton/computer` (see state tsv) — MAYBE: border-contrast ceiling raised from 16% to 100% — relevant to the `bw`/`bw-dark` palettes.
- `themrsung/secure-cptr` (0ed4428a) — MAYBE: TOTP 2FA, HTTPS, gated terminal, capability roles, audit-log redaction. A security posture, probably too opinionated to merge wholesale.
- `Limicrafter/computer` — IMPORT (small): token usage tracking — inline live counter + per-workspace usage panel. We have nothing similar.
- `cclecle/computer` — MAYBE (small): freeze system prompt per conversation + drift report; nudge-and-retry turns that end on reasoning alone.
- `satouriko/computer` — MAYBE (small): persistent file explorer on wide screens; more C++ extensions.
- `janhetzler/opencomputer` (c438358b) — REJECTED for code: HuggingFace Space packaging and a personal journal; nothing to merge, though the packaging notes are readable.
- `dincozdemir/computer` — REJECTED: managed remote model + GitHub tools, built on their own hosting assumptions.
- `paulorcf/computer`, `0x5t4l1n/computer`, `Georg311/computer`, `qjqqyy/computer`, `kondrondin/computer`, `naqerl/computer`, `espen96/computer`, `Perissosdigitals/kuma-executor` — REJECTED: single small local fixes or a branch named "push"; no evidence of a portable idea.
- `Abdul-mo-dev/computer` — UNSCANNABLE: no common ancestor with upstream (history rewritten).

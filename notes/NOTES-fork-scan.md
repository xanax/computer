# Scanning sibling forks for features worth importing

Date: 2026-09-19

## Why

`scripts/fork-status.sh` answers one question: *has upstream caught up with us?* It is blind to the other direction of travel. `open-webui/computer` has ~79 forks, several of them further ahead of upstream than this fork is (18 carry commits upstream does not have), and they are working the same codebase: the same `/mnt/c` cost model, the same mono-theme problems, the same agent loop. A feature one of them built in a weekend is a feature nobody has to build here.

So `scripts/fork-scan.sh` asks the opposite question: **who else has built something we do not have, and is it worth importing?**

## The model

GitHub's `compare` endpoint works across forks: `GET /repos/open-webui/computer/compare/open-webui:main...OWNER:main` returns `ahead_by`, `behind_by`, the commit list, and the changed files — one request per fork, no cloning. The script:

1. lists the forks (`sort=newest`), skipping our own and any whose last push is older than `MAX_AGE_DAYS` (default 240);
2. compares each one against upstream's default branch;
3. drops forks with `ahead_by = 0` (a mere sync);
4. ranks the rest by **substantive** commits — those whose subject survives a noise filter (`Merge`/`Sync`/`chore`/`Bump`/`dependabot`/`CI-file edits`), because 260 commits that are mostly upstream syncs are less interesting than 4 real ones;
5. prints the subjects and top-level areas touched, and diffs against `notes/fork-scan-state.tsv` to flag forks whose head moved since the last scan.

Cost: ~80 requests and ~80 s, against a 5000/hour authenticated rate limit. Exit status is always 0 unless the scan could not run at all.

**`ahead_by` is not a measure of "newness".** A fork that never rebases keeps the upstream commits it forked from, but those are in upstream too, so they do not count as ahead; a fork that rewrote history counts as ahead of everything. `janhetzler/opencomputer` (103 ahead, 82 behind) and `heidi-dang/computer` (260 ahead, 0 behind) are genuine excursions; the 1-2-ahead/300-500-behind tail is usually one small local patch against an ancient base.

## Three bugs found while building it, all of which made the report lie

**1. The base ref is `OWNER:BRANCH`, not `OWNER/REPO:BRANCH`.** Writing `"repos/$UPSTREAM/compare/$UPSTREAM:$BASE..."` produces `open-webui/computer:main` and GitHub answers **404 for every single fork** — the scan looked "empty but successful". The tell was that it failed uniformly: a real access problem is never 100% reproducible across unrelated repos.

**2. Bash treats tab as IFS *whitespace*.** `IFS=$'\t' read -r a b c d <<<"1\t371\tpush\t\tdeadbeef\t1"` collapses the empty field, so every field after it shifts left: that fork's head sha was reported as its "areas" and its commit count as its head sha. Internal records now use U+001F (`\037`), which is not IFS whitespace, so empty fields survive. The files under `notes/` still use tabs — only the shell's own reads needed the change.

**3. A 404 is not an empty string.** With `--jq`, gh writes the API's error body to **stdout**, so a failed compare was captured as if it were data (`{"message":"Not Found"...}` parsed as `ahead_by`), and `[ -z "$res" ]` never fired. The check is now "did we get the separator, and does the record have the fields", plus one retry, and the API's own reason is kept for the report — which is how `Abdul-mo-dev/computer` is recorded as *"No common ancestor between open-webui:main and Abdul-mo-dev:main"* (a rewritten history) instead of a shrug.

Two smaller lies: compare repeats a subject when the same commit is carried twice, so subjects are de-duplicated (and the "substantive" count is a count of *distinct* subjects); and a filename that is a 40-char sha-length blob path is dropped from the "areas" column.

## Findings, 2026-09-19 (base `f9d1d8c`)

77 forks scanned, 18 with commits we lack. The interesting ones, with the "do we already have it?" check done by grep against this tree:

| fork | ahead / substantive | what they have | do we have it? |
| --- | --- | --- | --- |
| `heidi-dang/computer` | 260 / 215 | live activity streams, steering provenance + worker secrets redaction, scoped direct-coding API, resumable terminal events, lease lifecycle for long jobs | no — the largest excursion of any fork; needs a proper read, not a skim |
| `1-kabir/computer` | 75 / 48 | subagent visibility bar + cancel API, tab rename via context menu, provider-side stream truncation detection with a "Continue" affordance, per-workspace memory char limits, WhatsApp bridge hardening (typing indicator, v23 API, webhook signature) | partly: `cptr/utils/async_subagents.py` already has cancel-all, but no UI bar; no truncation detection; `cptr/utils/bridge.py` exists |
| `janhetzler/opencomputer` | 103 / 98 | HuggingFace Space packaging, port routing, own `cptr` wheel plan (German journal + docs) | no — packaging ideas only, not code we would merge |
| `ql-isaac/computer` | 23 / 22 | `monospaceFont` propagated to editor/mermaid/chat/ShortcutBar, OMP (oh-my-pi) agent integration, `update.sh` rebase + conflict-wait loop | no — the font-propagation batch is directly relevant to the e-ink work |
| `themrsung/secure-cptr` | 5 / 5 | mandatory TOTP 2FA, HTTPS, gated terminal, capability roles, audit-log redaction, no upload bodies in the audit log | no — security posture, probably too opinionated to merge wholesale |
| `aaronbolton/computer` | 8 / 4 | Readability controls in Appearance, **border-contrast ceiling raised from 16% to 100%**, `dev2.sh` | the contrast ceiling is the interesting one for `bw`/`bw-dark` |
| `cclecle/computer` | 2 / 2 | freeze system prompt per conversation + drift report, nudge-and-retry degenerate reasoning-only turns | no |
| `satouriko/computer` | 2 / 2 | persistent file explorer on wide screens, more C++ extensions | no |
| `Limicrafter/computer` | 2 / 2 | token usage tracking: inline live counter + per-workspace usage panel | no |
| `paulorcf/computer` | 2 / 1 | stream opencode 1.17 responses live | n/a unless opencode is used |
| `0x5t4l1n/computer` | 2 / 1 | content-type validation on upload + safe serving | related to `themrsung`'s hardening |
| `Abdul-mo-dev/computer` | unscanned | — | no common ancestor with upstream: history rewritten, nothing to compare |

## Maintenance

A monthly automation ("Sibling fork feature scan", first Monday 10:00) runs the scan, triages the *new* candidates, checks each against this tree, appends the judgement to `notes/fork-scan-triage.md`, and reports. It does not merge anything — the scan is read-only and the triage is a recommendation.

The scan rots in a different direction from the probes: a fork that deletes or force-pushes leaves an `ERR` row, and GitHub caps `compare` at 250 commits and 300 files, so a very large fork (heidi-dang) is summarised from a truncated list. When a fork matters, look at it directly:

```bash
gh api repos/open-webui/computer/compare/open-webui:main...OWNER:main --jq '.commits[].commit.message'
git fetch https://github.com/OWNER/computer main:refs/remotes/scan/OWNER   # then git log/diff
```

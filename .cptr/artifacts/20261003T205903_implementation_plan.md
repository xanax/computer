# Chat findings → a tracked knowledge base, read first in every new chat

## Answer to "is there anything already like this?"

Yes — three overlapping things exist today. None of them is what you want, and the
useful one is in the wrong place.

| What | Where | State |
| --- | --- | --- |
| **agent-kb skill** — already has the exact format (`index.md` + `areas/<slug>.md` + `changelog.md`, fact tags, ≤120-line areas, no transcripts) | `~/computer/.cptr/skills/agent-kb/SKILL.md` | **Gitignored.** `.gitignore:29` ignores `.cptr`, and cptr re-asserts it on every workspace open (`cptr/utils/workspace.py:26 ensure_cptr_gitignored`, config `workspace.auto_gitignore_dot_cptr`, default **true**). Advisory skill, so the model may never load it. One copy per workspace = the thing you don't want. |
| **`.agent-kb/` data** | `~/computer/.agent-kb/` | **Already tracked and correct** — index + 4 areas + changelog, committed, and `git check-ignore` says it is *not* ignored. This is the format to standardise on. |
| **Managed memory** (`cptr/utils/memory.py`) | `~/.cptr/memory/users/<id>/USER.md` (global) + `<ws>/.cptr/memory/users/<id>/WORKSPACE.md` | Auto-written by a background reviewer every 10 user turns. Deliberately **inside the gitignored `.cptr`**, 2000/3000-char budgets, flat bullet list — designed to be forgettable, not a KB to read first. |
| **`~/knowledge-base`** | `git@github.com:byt8/knowledge-base.git` | Tracked, README index table + one folder per subject. Hand-written — **no chat ever feeds it** — holding a single note so far (VR / SteamVR 208). |

Also already flagged: `.agent-kb/index.md:5` says the 43 `notes/NOTES-*.md` files
"have **not** been folded in here yet" — same problem, in-repo.

So: keep the format, move it out of `.cptr`, make it a first-class prompt block, and
feed it automatically. Confirmed choices: **per-repo `.agent-kb/`, committed**;
**built into cptr's prompt (code change)**; **prompt + background reviewer**.

---

## Phase 1 — cptr reads the KB first, in every workspace

New `cptr/utils/knowledge_base.py`:

- `KB_DIR = ".agent-kb"`; `detect(workspace)` → present iff `.agent-kb/index.md` exists.
- `read_index(workspace, max_chars=6000)` — the only file injected by default; over
  budget, inject a pointer to the path instead (never half a table).
- `build_prompt_block(workspace)` — returns `""` when there is no KB *and* no reason
  to create one.
- `KB_README` template (methodology, see Phase 1b) + `scaffold(workspace)` used only
  by the reviewer/user action, never silently on open.

`cptr/utils/prompt_templates.py`:

- Add `KNOWLEDGE_BASE` to `_build_template_variables()` and `{{KNOWLEDGE_BASE}}` to
  `DEFAULT_SYSTEM_PROMPT`.
- Auto-inject in `load_system_prompt()` exactly like `{{WORKSPACE_PROMPT}}` /
  `{{MEMORY}}` are today (`prompt_templates.py:403-425`), so a custom
  `.cptr/system.md` still receives it. Placement: after `{{WORKSPACE_PROMPT}}`,
  **before** `{{MEMORY}}`.
- The block is short and imperative:

  ```text
  [KNOWLEDGE BASE]
  <workspace>/.agent-kb/ is this project's memory. Read .agent-kb/index.md before
  you search anything, then open at most three areas/*.md whose Summary or Keywords
  match the task. Trust a bullet tagged verified/ran instead of re-deriving it. If
  what you just read contradicts a bullet, fix that bullet this turn — code wins.
  Before you finish, upsert the area you touched, update only your own index row,
  and prepend one changelog line. Format: .agent-kb/README.md. Never gitignore
  .agent-kb, never put transcripts, secrets or pasted code in it.
  No .agent-kb/ yet? Create one on your first real finding, from the template in
  .agent-kb/README.md, and commit it with your change.
  ```

### 1b. The methodology moves into the repo it describes

- New **tracked** `.agent-kb/README.md` per project = today's skill body, including
  the empty-state template so a workspace with no KB can bootstrap itself.
- The per-workspace skill copy in `~/computer/.cptr/skills/agent-kb/` goes away; keep
  one **global** fallback at `~/.cptr/skills/agent-kb/` (already a discovery path,
  `cptr/utils/skills.py:45`) for workspaces that have no KB and no prompt block.
- Net effect: no per-workspace skill to apply, and the instructions version with the
  code instead of hiding in a gitignored directory.

## Phase 2 — capture, two ways

**In-turn:** Phase 1's block. Cheap, no plumbing, and the model already has the
files in hand.

**Background reviewer:** `review_knowledge_base_after_turn()` in
`knowledge_base.py`, a near-copy of `review_memory_after_turn` /
`review_skills_after_turn`:

- Gated on `knowledge_base.*` settings, every `review_interval_turns` (default 10)
  user turns, skipped in plan mode and for subagents, skipped when the turn learned
  nothing.
- Prompt returns only
  `{"actions":[{"action":"upsert_area|update_index|append_changelog|none", "slug", "summary", "keywords", "facts", "built", "dead_ends", "changelog_line"}]}`.
- **Hard rails, because this writes into your repos:**
  - every path resolves under `<workspace>/.agent-kb/` (reuse the `_safe_relative_path`
    idea from `memory.py:220`); slug must match `^[a-z0-9][a-z0-9-]*$`;
  - ≤3 actions per review, ≤120-line area files, newest-40 changelog lines;
  - never delete a file, never rewrite `index.md` wholesale — one row by slug;
  - atomic tmp + `os.replace` (`skills._write_text_atomic` / `memory._atomic_write_text`);
  - refuse and back off if the file changed under us (memory's `_write_if_unchanged`
    hash pattern) — no clobbering an edit you made by hand;
  - commits stay the agent's job (`AGENT:` message), the reviewer only writes files.
- Wiring: call it from `cptr/utils/chat_task.py` beside the two existing reviews
  (`:2986-3022`).
- Add `"knowledge_base_background_review": "knowledge_base.background_review.model"`
  to `cptr/utils/utility_models.py:5`.
- Settings surface mirroring Memory: `knowledge_base.*` config namespace
  (`enabled`, `tool_enabled`, `background_review_enabled`, `review_interval_turns`,
  `model`), `/api/knowledge-base` router (index, files, "review this chat now"),
  `cptr/frontend/src/lib/apis/knowledgeBase.ts`, and `Settings/KnowledgeBase.svelte`
  modelled on `Settings/Memory.svelte`. A manual "capture this chat" button gives you
  the on-demand path without a new tool.

## Phase 3 — make "not gitignored" enforced, not incidental

Today it works only because nobody added the line. Add `ensure_agent_kb_tracked()`
beside `ensure_cptr_gitignored()`: if the workspace is a git repo and its
`.gitignore` (or `.git/info/exclude`) would ignore `.agent-kb`, append `!.agent-kb/`
and log it. Test both directions: `.cptr` stays ignored, `.agent-kb` is never
ignored.

## Phase 4 — fold what already exists

- `~/computer`: 43 `notes/NOTES-*.md` → `areas/<slug>.md`, newest-40-per-area, index
  rows added one at a time; leave the `notes/` files in place and let the index point
  at them where an area would only duplicate.
- Other workspaces with material but no KB: `~/dwp-remote-cmd` (`.cptr/skills/remote-cmd`
  + its `references/`) is the obvious first seed.
- `~/knowledge-base` stays as the *machine-wide* repo (hardware, host quirks); add one
  README row per project KB so the two indexes point at each other. Do **not** merge
  them — code findings belong in the repo line they describe.

## Phase 5 — verification

- `tests/test_knowledge_base.py`: block present only when `.agent-kb/index.md`
  exists; injected into a custom `.cptr/system.md`; reviewer rejects `../` slugs and
  any path outside `.agent-kb/`; index updated row-wise; over-budget index degrades to
  a pointer.
- Live: fresh chat in a KB workspace → assert the block is in the system prompt; run
  one real turn and confirm one changelog line and no other file changed; then
  `git check-ignore -v .agent-kb/index.md` must be silent.
- Server-side change ⇒ **restart needed; I will not restart it for you.**

## Risks

- One extra utility-model call per review interval (same cost class as the memory
  reviewer that already runs). Defaults to on; switchable off.
- A reviewer with write access to your repos is the real risk — bounded by path rails,
  action/size caps, atomic writes and the changed-since hash guard.
- Prompt growth: capped at 6 KB of index; nothing else is injected.
- Two stores will still exist (`.agent-kb` = read-first knowledge, `.cptr/memory` =
  forgettable preferences). The prompt block says which to use when, and Phase 1b
  keeps the memory store as-is rather than merging it.

## Sequence

1. Phase 1 + 1b (prompt block, README, drop the per-workspace skill) — the part that
   changes behaviour immediately.
2. Phase 3 (the gitignore guarantee) — tiny, protects everything after it.
3. Phase 2 (reviewer + settings) — the automation.
4. Phase 4 (fold the 43 notes) — incremental, can be a recurring job.

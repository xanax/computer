---
area: skills-scope
title: Skill scope (workspace vs global)
aliases: [skills, global skill, all-workspace skill, skill discovery, agent-kb]
updated: 2026-10-05
---

# Skill scope (workspace vs global)

## Map
- `cptr/utils/skills.py` — discovery, loading, catalog, managed create/update/delete.
- `cptr/utils/prompt_templates.py` — injects the catalog as the `SKILLS` block.
- `~/.cptr/skills/<name>/SKILL.md` — the global (all-workspace) skill root.
- `.cptr/skills/<name>/SKILL.md` — the workspace-scoped skill root (gitignored).

## Facts
- [verified 2026-10-03 cptr/utils/skills.py:37-48] Two scopes. Workspace dirs: `.cptr/skills`, `.agents/skills`, `.claude/skills`, `.codex/skills`. Global dirs: `~/.cptr/skills`, `~/.agents/skills`.
- [verified 2026-10-03 cptr/utils/skills.py:416-465] `discover_skills(workspace)` scans workspace first, then global, and dedupes by **name — workspace wins**. A workspace copy therefore *shadows* the global one rather than merging.
- [verified 2026-10-03 cptr/utils/prompt_templates.py:310] `build_catalog_xml(discover_skills(workspace))` is the `SKILLS` block of the system prompt. A global skill needs **no per-workspace prompt edit** to appear in `<available_skills>`.
- [verified 2026-10-03 cptr/utils/skills.py:321-333] `_is_managed_skill` treats anything under `~/.cptr/skills` as managed, so `manage_skill` and the background skill reviewer can create/update/delete it. A *symlinked* skill dir resolves out of the managed root and silently becomes read-only.
- [verified 2026-10-03 cptr/utils/prompt_templates.py:120-147] `_load_instruction_files` reads well-known instruction files from the **workspace root only** — there is no global instruction-file channel. The cross-workspace push channels are the skills catalog and managed memory.
- [verified 2026-10-05 cptr/utils/prompt_templates.py:450-484] **Trap:** `load_system_prompt` auto-appends only `{{MEMORY}}`, `{{WORKSPACE_PROMPT}}`, `{{WORKSPACE_SERVICES}}`, `{{WORKSPACE_NOTES}}`, and (in `_render_system_template`) `{{CPTR_CONTEXT}}`. `{{SKILLS}}`, `{{INSTRUCTIONS}}`, `{{FILE_TREE}}` and `{{WORKSPACE_NAME}}` exist **only** in `DEFAULT_SYSTEM_PROMPT`, so setting the global system prompt (`chat.models['*'].params.system_prompt`, Admin → Models) *without* re-listing those slots silently drops the skills catalog and instruction files from every chat in every workspace. The Admin panel lists the variables but does not warn.
- [ran 2026-10-05 sqlite3 read-only on ~/.cptr/app.db] On this instance `chat.models['*'].params` is `{}` and no model carries its own `system_prompt`, so `load_system_prompt` resolves to `DEFAULT_SYSTEM_PROMPT` today. The only `*prompt*` config keys are `audio.voice_mode_system_prompt`, `browser.quality.profiles` (unrelated).
- [verified 2026-10-03 .gitignore] `.cptr` contents are gitignored, so a global skill is **untracked**. **Corrected 2026-10-03:** a *workspace* skill (`.cptr/skills/`) is now re-included by the curated allowlist in `.gitignore`, so it **is** tracked and diffed — see `areas/dot-cptr-in-git.md`. `scripts/cptr-backup.sh` globs `~/**/.cptr` into the nightly tarball, covering both either way.
- [ran 2026-10-03 `discover_skills()` on 3 workspaces] After moving `agent-kb` to `~/.cptr/skills`, workspaces with no `.cptr/skills` at all (tesla, greyhound-odds, AIjly) resolve it as `source=global managed=True` and load it by name.
- [ran 2026-10-03 `build_catalog_xml()` on 3 workspaces] The catalog is 794 chars and includes the `agent-kb` entry for every workspace tested.

## Built
- `agent-kb` is now a **global** skill (`~/.cptr/skills/agent-kb/`), so all 42 workspaces discover it through the catalog with no per-workspace setup. The `.agent-kb/` area files it writes stay **per-project**.
- `manage_skill(scope="global")` is the supported way to author one; the workspace scope is the default.

## Decisions
- Split method from facts: the *skill* (how to keep a KB) is global; the *`.agent-kb/` contents* (what this repo traps on) stay in the repo. Rejected: keeping a copy in `.cptr/skills/` — it would shadow the global copy here and drift out of sync.
- Dropped the absolute path from this workspace's prompt (`always use this skill /home/brendan/computer/.cptr/skills/agent-kb/SKILL.md`). It became a dead path the moment the skill moved. Rejected: leaving it — it instructs the model to read a file that no longer exists. The prompt now names the skill, not a path.

## Dead ends
- `~/.agents/skills` (the cross-agent convention) is scanned but does not exist on this machine. Only `~/.cptr/skills` is live.
- `scope` is scoped for **creation**, not for update: `update_managed_skill` locates the skill by discovery and edits it wherever it lives. Passing the wrong scope to a create is not a safety net for an edit.

## Do not redo
- "Can a skill apply to all workspaces?" Yes — it always could; `GLOBAL_SKILL_DIRS` plus the catalog already did it. Read `cptr/utils/skills.py:37` before building any new sharing mechanism.

## Open
- Workspaces with no prompt of their own get `agent-kb` only via the catalog's on-demand trigger ("use on every build…"), not an "always" push. The other push channel, managed memory, is at 1959/2000 chars, so an "always read the KB" line there needs a trim elsewhere first.
- No **per-user global profile prompt** exists (asked 2026-10-05). The candidates are the instance-wide admin `chat.models['*']` prompt, per-workspace `workspaces.data["prompt"]`, user memory `USER.md` (1963/2000 chars, fact-list not persona), and `~/.cptr/skills`. A real one wants a `user_states.data` / prefs field read in `load_system_prompt` and prepended ahead of the workspace prompt, with the same B-019 server-owned guard as the workspace prompt.

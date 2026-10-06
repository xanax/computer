# Global profile — one prompt that opens every chat

## The ask

"yes build global" — after the skills-scope audit (2026-10-05) listed the four
existing channels and noted there was no **per-user** global profile. The
workspace prompt only reaches its own workspace, and an admin `chat.models['*']`
system prompt is instance-wide (every user, every workspace) and, if set, silently
drops `{{SKILLS}}`/`{{INSTRUCTIONS}}` unless the admin re-lists them. What was
missing is the middle: *this user's* standing instructions, in every chat, on
every project, plus the home page.

## Shape

- Stored in `user_states.data["profile"]` — the same JSON blob as the other
  preferences. No migration, and it rides along with the per-user state row that
  already exists.
- `MAX_PROFILE_CHARS = 8000` (`cptr/models/users.py`). It is prepended to every
  chat's system prompt, so its size is paid on every request; the cap keeps one
  paste from doubling every prompt. Trimmed on the way in; blank means "clear it"
  and the key is removed rather than stored empty.
- Read back by `GET /api/state/profile`, written by `PUT /api/state/profile`
  (`ProfileBody`). Both return `max_chars` so the Settings box can show a counter
  without hardcoding the limit.
- Rendered as a `[PROFILE]` block by `format_user_profile()`, prepended in
  `load_system_prompt`.

## Ordering: why the profile leads even the workspace prompt

Both blocks "prepend themselves to the front" of the template, so the order
between them is decided by the order of the prepends, not by the template. The
profile is prepended **last**, which puts it first in the prompt:

```
[PROFILE]
<the user's standing instructions>

[WORKSPACE PROMPT]
<this workspace>

You are Computer (cptr) …
```

The justification is scope: the profile is the only instruction that is true in
*every* chat, including a home chat with no workspace at all. Everything narrower
comes after it. Two consequences worth knowing:

- A home chat (`workspace == ""`) has no `[WORKSPACE PROMPT]` and no
  `[WORKSPACE NOTES]` block — the profile is the only standing instruction it gets.
- All four `load_system_prompt` call sites (`chat_task.py:1807/2126/2433`,
  `routers/chat.py:1001`) pass a `user_id`, so subagent/automation/compaction
  prompts get the profile too. That is intended: it is "every chat this user
  starts", not "every chat they type into".

## The B-019 shape, one layer up

`PUT /api/state/preferences` is an autosave: `stores.ts` debounces ~300 ms and
PUTs the **whole** preferences object it loaded minutes ago. If the profile rode in
that blob, a stale echo would revert a profile the user had just typed — exactly
the workspace-prompt bug (B-019), with a worse blast radius, since the profile is
read by every chat rather than one workspace.

Fix at the endpoint, not at the caller: `put_preferences` **drops** `profile` from
the body unconditionally, then merges the rest. Two tests pin it — a stale echo
carrying an old profile (the new text survives, the rest of the blob still saves)
and an autosave omitting the key entirely (preserved, because it merges over the
stored dict). The prefs *read* still carries the key; the write can no longer use
it. Same decision as `put_workspace`, and the same residual: two concurrent
read-modify-write requests can still interleave, which this codebase's state
endpoints all share.

Rejected: putting the profile in the prefs blob and having the client delete the
key on load. That fixes one client, not the race — and a tab on the old build
would keep clobbering.

## Where the user edits it

Settings → General, a card directly under the Computer/about block (the first
setting on the page), because it is the most global setting there is. Read view
with a hint; `Edit`/`Add` swaps in a textarea with a counter and a Cancel, and
`Saved` appears briefly on success. The hint line doubles as the error line, so a
failed save says so in place without a toast (and keeps the box open with the
user's text).

Five `general.profile*` keys, all ten locales, by
`scripts/add-profile-locales.py` (idempotent, `--check` mode, validates that the
patched file is still JSON and that only the intended keys changed).

## Evidence

- `tests/test_global_profile.py` — 16 tests: storage, cap, blank-clears, null-clears, the
  `[PROFILE]` block leading the workspace prompt *and* the home prompt, no
  duplication when a template places `{{PROFILE}}` itself, literal insertion (`{{…}}`
  in the profile is not expanded), the round trip through the router, the cap at
  the endpoint, the stale-prefs echo, the omitting autosave, and a non-object prefs
  body not raising.
- Full suite 437 passed; `npm run build` and `svelte-check` clean for these files
  (only the repo-wide `'$t' is of type 'unknown'` noise).
- Injection was written test-first against the ordering rule: the prepend order in
  `load_system_prompt` is the whole mechanism, so a reorder of those three blocks
  is a silent behaviour change with no other test to catch it.

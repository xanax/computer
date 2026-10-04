---
area: stream-control-chars
title: Providers leak NUL bytes into tool arguments (and sometimes as the tool NAME); cptr never stripped them
aliases: [embedded null byte, nul byte, control chars, minimax watermark, x00, crashing chat, empty function name, non-empty string, u0000 escape, 400 on tool_calls]
updated: 2026-10-04
---

# Providers leak NUL bytes into tool arguments; cptr never stripped them

## Map
- `cptr/utils/chat_task.py` — `_CONTROL_CHARS_RE` + `_strip_control_chars()` /
  `_had_control_chars()` (~line 455, next to `_plain_message_text`); the stream
  loop that every provider adapter converges on (`async for event in stream`,
  ~line 2440); `_output_items_to_messages()` (~line 894), which replays the
  persisted `m.output` as prompt context.
- `cptr/utils/ai.py` — the adapters. They pass deltas through verbatim:
  `current_block["input_json"] += delta["partial_json"]` (:499, Anthropic) and
  `yield {"type": "text_delta", "content": event["delta"]}` (:1046, Responses).
- `scripts/repair-nul-control-chars.py` — repair of what is already on disk; dry-run
  by default, `--write` to apply. Must `json.loads` before judging — see below.
- `scripts/nul-census.py` — read-only tally of control chars per codepoint and
  per JSON field. Run this before ever writing the repair.
- `scripts/find-empty-tool-names.py` — scans every chat by replaying it through
  the real `_output_items_to_messages()` and reports calls that would serialise
  with an empty `function.name`.
- `scripts/inspect-ctrl-chars.py` — hex/context window around each control char.
- `tests/test_stream_control_chars.py`, `tests/test_tool_call_name_sanitisation.py`,
  `tests/test_repair_nul_script.py` — regression tests.
- `BUGS.md` — B-021 (the NUL) and B-022 (the empty tool name it became).

## Facts
- [verified 2026-10-04, chat `0717880f-8203-4cca-93c0-c13b6a0d0214`] A provider
  route reached as `openrouter/stealth/space-bunny-alpha` (MiniMax behind
  OpenRouter) emitted **NUL bytes at token boundaries**. Persisted examples:
  `"cd /home/br\x00endan/computer && sqlite3 ..."` and `"cptr/\x00frontend/src/lib/stores"`.
- [verified] It is **not** cptr crashing. The task never died: no `Chat task
  error` in the log window, no stuck `done=0` rows, server stayed up. The agent
  simply looped on its own malformed tool calls.
- [verified] NULs survive JSON encoding, so they round-tripped model → DB →
  tools untouched. The **only** visible symptom downstream was the tool's own
  error: `Error: embedded null byte` (Python `ValueError` from the argument
  string) and `Error: lstat: embedded null character in path` (ripgrep). Those
  opaque messages are what made it read as "it keeps crashing".
- [verified] The watermark `]<]minimax[>[0` in that same transcript is the same
  upstream route leaking non-control junk; it is **not** covered by the
  control-char regex and is only cosmetic. Only this one chat/model carries it.
- [verified 2026-10-04, full-DB scan] Blast radius at the time of the fix was
  **1 message in 1 chat**, i.e. a single bad roll of the dice — not a systemic
  corruption. Other routes in the same period were clean.
- [verified] `git`-style byte-range corruption is *not* the cause; the NUL sits
  mid-token at a path boundary, which is a tokenizer/upstream artefact.
- [verified 2026-10-04, **corrects B-021's “1 message” figure**] A NUL in a
  persisted column is stored as the JSON **escape** `\u0000`, never as a raw
  byte, because `json.dumps` escapes it. So scanning the DB for a raw `\x00`
  finds **0 rows** and wrongly reports the corruption as already gone — while the
  decoded value is still corrupt. **Decode first, then count.**
- [verified] The real blast radius was **4 messages in 1 chat**, not 1: the missed
  text lived in three more shapes that the first repair did not reach — items
  with `type: "reasoning"` (not `"message"`), the `reasoning_details` key, and
  `function_call.arguments`, which is a **dict**, so a string-only check silently
  skips it.
- [verified] The same route emitted a bare NUL **as a tool name** (`\x00` for an
  intended `update_memory`). Before any stripping that serialised as the 6-char
  escape `\u0000`, which providers accept — cptr just failed that one call locally
  with `unknown tool`. Once the B-021 strip was applied it became `""`, and
  **every** provider rejects an empty function name at the **request** level:
  `400 tool_calls[0].function.name must be a non-empty string`. That kills the
  whole conversation, so one bad call bricked the chat permanently — the fix for
  B-021 *caused* the B-022 outage.
- [verified] An empty function name is **not** a per-call failure like B-021's
  arguments: it is a whole-request 400, so the chat cannot make progress at all.
- [verified] Control characters inside a `function_call_output` **body** are
  usually real data, not corruption: terminal output is full of ANSI colour
  escapes (U+001B, 45k of them DB-wide) and `read_url` once fetched an 80KB
  binary `eng.traineddata` (30k+ control bytes in one row). A naive repair that
  strips every control char destroys real content — it wanted to rewrite 320 rows.
  Only assistant text, `reasoning`, reasoning_details and call arguments/name are
  safe to clean.

## Built
- `_strip_control_chars()` / `_had_control_chars()` in `chat_task.py` — recursive
  over str/list/dict, drops `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]` but **keeps
  `\n`, `\r` and `\t`**, which legitimate tool output is full of. Applied:
  1. at the top of the `async for event in stream` loop, so text deltas,
     reasoning items and tool arguments are all covered by one guard;
  2. at the top of `_output_items_to_messages()`, because `m.output` is
     re-injected as prompt context on **every later turn** — cleaning only the
     live stream would let a pre-existing bad row poison the chat forever.
- `scripts/repair-nul-control-chars.py` — `--chat <id>` to scope, `--write` to
  apply. Now `json.loads`-es the `output` column first (raw-byte scanning cannot
  see the corruption), cleans `message`/`reasoning` content, `reasoning_details`,
  the call `name` and `arguments` (a **dict**), **skips tool-output bodies**
  wholesale, and drops a call whose name is unusable together with its orphaned
  output.
- `_drop_invalid_tool_calls()` in `chat_task.py` — called from
  `_output_items_to_messages()` right after the strip. It judges the name *after*
  scrubbing, so it is safe even if it runs before the control-char pass. The
  first draft only tested `not name.strip()`, which a raw `\x00` passes (it is
  truthy) — a regression test caught it.
- `_sanitise_tool_calls()` in `ai.py` — outbound belt-and-braces on the
  OpenAI-completions path; `_to_responses_input()` got the same check inline for
  the Responses path. Both are last-line guards against a request-level 400.
- `scripts/verify-repair.py` — asserts a repaired DB copy equals the original
  scrubbed, minus exactly the invalid call and its output, and that the binary
  payload row is byte-identical. Always run it on a **copy** before writing.

## Decisions
- Guard in `chat_task.py`, not in `ai.py`: the stream loop is the single point
  every adapter (Anthropic / OpenAI-completions / Responses / llama.cpp) passes
  through, so one guard covers all of them and new providers inherit it.
- Strip rather than reject the tool call. A rejected call makes the model retry
  an argument it will regenerate byte-identically (it came from the same
  sampler), so the loop repeats; stripping the NUL turns `br\x00endan` back into
  the path the model meant, which then works.
- Guard the replay path too even though it looks redundant — that is exactly
  the sticky half of the bug, and the DB was full of pre-guard rows.
- **Drop, never blank, a tool call whose name is unusable.** Stripping is right for
  arguments (it restores the path the model meant) but wrong for a name: there is
  no shorter name to recover, and `""` is an invalid *request*, not a degraded one.
- Fix the DB with the same rule as the runtime, or the repaired row re-poisons the
  chat on the next turn.

## Dead ends
- Do **not** count NULs with SQL `LIKE '%00%'` or `instr(col, char(0))`. `char(0)`
  in SQLite builds the *string* `'0'`… it matches the literal digits `00`, and
  `LIKE` happily matched ~1000 innocent rows (paths, timestamps, byte counts).
  I reported "1015 corrupted messages across 377 chats" from that query before
  scanning in Python; the true figure was **1**. Scan in Python (`'\x00' in s`)
  or use `substr(col, i, 1) = x'00'`.
- **And the Python one-liner `'\x00' in row` is equally wrong**, for the opposite
  reason: the column stores the *escape* `\u0000`, so it reports 0 on a corrupt
  database. Load with `json.loads` and test the decoded string. Both wrong answers
  were plausible and I believed each in turn.
- Do **not** strip control characters from `function_call_output` bodies. See
  Facts — ANSI colour (45k U+001B DB-wide) and an 80KB binary `eng.traineddata`
  blob are real content there.
- Do **not** treat a `reasoning` item as a `message`. Item types in `output` are
  `message`, `reasoning`, `function_call`, `function_call_output`; text can live in
  `content` *or* `reasoning_details`, and `arguments` is a dict, not a string.
- `grep -rn "x00\|isprintable" cptr/utils/` is a dead end for "where should the
  guard go": the only hit is an unrelated filename cleaner in `tools.py:252`.
- The `Chat task error` tracebacks at 09:35 in `cptr-start.log` are OpenRouter
  **connect timeouts** (30s TCP to :443), unrelated to this corruption.

## Do not redo
- "Is cptr crashing?" is the wrong first question for a chat that stalls. Check
  in this order: (1) `chat_messages.done=0` rows left behind — none here;
  (2) `grep 'Chat task error for message <id>' cptr-start.log` — none in the
  window; (3) read the **stored** `content` + `output`, not the rendered view.
  A stalled run with healthy `done` flags and clean logs is corrupt *input*.
- A 400 naming `tool_calls[N]` is a **request-shape** bug, not a per-call one: it
  rejects the whole conversation, so it presents as "chat is bricked" with no
  usable error. Locate the offending item by replaying the chat through the real
  builder (`scripts/find-empty-tool-names.py`) rather than grepping the column —
  the stored value and the value actually sent are different.
- `notes/` grep before re-deriving the "user says you keep crashing" class of
  report; this area now has `areas/stream-control-chars.md`.

## Open
- The MiniMax watermark `]<]minimax[>[0` is still persisted (cosmetic, 1 chat).
  If it recurs across routes it deserves its own targeted strip.
- The server has **not** been restarted since the `_drop_invalid_tool_calls` /
  `_sanitise_tool_calls` changes, so the running process is still on the B-021
  code. The chat is unstuck in the DB, but the guard needs a restart to load.
- Other control-ish junk arrived the same turn: a stray `"/invoke"` key in a
  `run_command` arguments dict. That is a malformed tool-call **shape** from the
  same route, which `execute_tool` rejects with an unhelpful
  `unexpected keyword argument`. Not fixed — a shape validator would be a
  separate, larger change.
- The strip is per-event, not per-message: if a NUL ever straddles two deltas it
  still gets caught (it is filtered when it arrives), but there is no
  whole-message sanitisation on the persistence path.

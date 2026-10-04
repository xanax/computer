---
area: stream-control-chars
title: Providers leak NUL bytes into tool arguments; cptr never stripped them
aliases: [embedded null byte, nul byte, control chars, minimax watermark, x00, crashing chat]
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
- `scripts/repair-nul-control-chars.py` — one-off repair of what is already on
  disk; dry-run by default, `--write` to apply.
- `tests/test_stream_control_chars.py` — regression tests.
- `BUGS.md` — this is a **probe-only** row, not a live P-* bug.

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
  apply. Recurses into `content` + `output`, so it also repairs nested
  `function_call.arguments`.

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

## Dead ends
- Do **not** count NULs with SQL `LIKE '%00%'` or `instr(col, char(0))`. `char(0)`
  in SQLite builds the *string* `'0'`… it matches the literal digits `00`, and
  `LIKE` happily matched ~1000 innocent rows (paths, timestamps, byte counts).
  I reported "1015 corrupted messages across 377 chats" from that query before
  scanning in Python; the true figure was **1**. Scan in Python (`'\x00' in s`)
  or use `substr(col, i, 1) = x'00'`.
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
- `notes/` grep before re-deriving the "user says you keep crashing" class of
  report; this area now has `areas/stream-control-chars.md`.

## Open
- The MiniMax watermark `]<]minimax[>[0` is still persisted (cosmetic, 1 chat).
  If it recurs across routes it deserves its own targeted strip.
- Other control-ish junk arrived the same turn: a stray `"/invoke"` key in a
  `run_command` arguments dict. That is a malformed tool-call **shape** from the
  same route, which `execute_tool` rejects with an unhelpful
  `unexpected keyword argument`. Not fixed — a shape validator would be a
  separate, larger change.
- The strip is per-event, not per-message: if a NUL ever straddles two deltas it
  still gets caught (it is filtered when it arrives), but there is no
  whole-message sanitisation on the persistence path.

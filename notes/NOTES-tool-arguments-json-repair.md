# Malformed tool-call arguments killed the whole turn (B-026)

Live failure: chat `9ffb4266-fe0f-4ce7-8806-c8fc94eadf13`, workspace
`~/qr-code-transfer`, the agent mid-way through building a GA test harness for
QR-code density/speed parameters.

```
json.decoder.JSONDecodeError: Expecting value: line 1 column 1782
```

The `async for event in stream(...)` loop in `run_chat_task` is the async
generator's only consumer, so an exception inside the generator propagates out
of the `async for` and terminates `run_chat_task`. The turn was saved as an
error and everything the model had batched into that message was lost.

## Ruling out the usual suspects

Before assuming a fix, three things were checked, and the first two are worth
repeating because they are the fast ways to tell this family of bugs apart:

- **Control chars / NUL in the stored output** (`NOTES-stream-control-chars.md`,
  `scripts/nul-census.py`): the stored assistant message parsed as valid JSON
  and had no control characters. Not this bug.
- **Provider truncation**: the payload was whole (its tail was intact), so it
  was not cut mid-stream. Not this bug.
- **What the model actually sent**: one unescaped `"` inside a large string
  value (a plan artifact), at roughly column 1782. Everything after that point
  is still the model's intent — only the quoting was wrong.

### Where the evidence lives (this cost time to find)

- **The failing blob was never persisted.** The exception killed the turn
  before its save, so neither the DB row nor the chat file contains the bad
  arguments — searching them for it is a dead end. The chat's
  `.cptr/chats/9ffb4266-*.json` holds only the *earlier, successful* turn.
- **The traceback in `cptr-start.log` is the real evidence**, and loguru prints
  the arguments preview inline. Find it with:
  `awk 'NR>=200860 && NR<=201000' cptr-start.log | grep -nE "JSONDecodeError|ai\.py|Expecting value"`
  It names the exact line (`ai.py:810`, openai-completions), the tool
  (`create_file`), and the value (`implementation_plan`).
- **Stored `output` is not `role`-shaped.** Items are flat
  `{"type": "function_call", "arguments": {...}}` with `arguments` already a
  **dict**, not OpenAI's `role`/`tool_calls` shape — so a replay that looks for
  `m["tool_calls"]` finds nothing and reports "0 broken calls" when the tool
  was in fact the thing that broke. Count `type == "function_call"` instead.
- **The DB is `~/.cptr/app.db`** (tables `chats`, `chat_messages`,
  `created_at`); `~/.cptr/cptr.db` exists but is empty.
- **The log also shows the damage:** the `db save (error)` line right after the
  traceback reports `19 items`, against `63` in the working turn — that is the
  batched calls being lost.

## The fix

`cptr/utils/ai.py`:

- `_parse_tool_arguments(raw)` — **never raises**. Fast path is a plain
  `json.loads`. On failure it repairs; on a hopeless blob it returns
  `{"__invalid_tool_arguments__": True, "__raw__": ...}`.
- `_read_relaxed_string(text, i)` — reads a string that may contain unescaped
  `"`. A quote is treated as the *end* of the string only when the next
  non-space character is `,` `}` or `]`; otherwise it is a literal quote and
  the scan continues. Also tolerates raw newlines (the model writing a
  multi-line body) and salvages a truncated tail mid-string.
- `_repair_tool_arguments_json(text)` — iterative member-by-member salvage.
  Keeps every complete member, drops only the incomplete one.

`cptr/utils/chat_task.py::run_chat_task` — a call carrying the sentinel is
recorded as a **completed** `function_call` plus a `function_call_output` whose
text explains the arguments were not valid JSON, then `continue`s. The model
sees the error and re-emits; the turn lives. Pending-call bookkeeping is
untouched because the call is completed, not abandoned.

## Two traps in this repair

**1. `raw_decode` succeeds on a prefix.** With `{"a": "x "y" z", "b": 2}` the
first `raw_decode` happily returns `"x "` — it cannot tell the stray quote from
the real end — and the next parse then fails on the word `y`. So *a successful
parse is not proof the value was consumed*. The fix checks that the next
non-space token after the parsed value is `,` `}` or `]`; if not, it re-reads the
value relaxed. Without this check the quote-storm test still "passed" while
throwing away 120 000 of 120 002 characters.

**2. Do not make the repair recursive.** A first attempt recursed into
`_repair_tool_arguments_json` per value, which turns a pathologically nested
payload into a `RecursionError` — inside the stream loop, which is the worst
place for one. The shipped version iterates over members, so nesting depth is
bounded by the payload, not the stack. `tests/test_tool_arguments_repair.py::
test_deeply_nested_input_does_not_recurse` pins this.

## Deliberately not `json-repair`

The obvious move is to depend on `json-repair`, which is battle-tested. It was
skipped because the shapes that actually arrive here are few and all
recoverable in ~100 lines, and a new runtime dependency in the request path is
a worse trade than 100 lines that are covered by tests and readable in one
sitting. Revisit if a third malformed shape shows up in practice.

## What was left alone

`cptr/utils/ai.py:414` also `json.loads`es, but on **tool arguments replayed
out of our own chat history** — written by `json.dumps` in
`_convert_messages_to_anthropic`, therefore always valid. Parsing it with the
repairing parser would risk pushing the `__invalid_tool_arguments__` sentinel
into an outbound request, which is strictly worse. Left as-is.

## Verifying a fix like this

Reverting the three adapter call sites to bare `json.loads` and re-running the
tests reproduces the original `JSONDecodeError` in the two end-to-end tests —
that is what makes them a real regression guard rather than a description of
current behaviour.

```
.venv/bin/python -m pytest tests/test_tool_arguments_repair.py -q   # 31 passed
.venv/bin/python -m pytest tests/ -q                                 # 263 passed
```
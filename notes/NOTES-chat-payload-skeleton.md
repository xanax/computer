# A chat load shipped megabytes nobody was looking at

## Symptom

Opening the largest chat (`4c61866d…`) fetched **6.60 MB** and took ~1.2 s — for a
transcript whose collapsed rows draw a few hundred KB. Traced with
`notes/_scratch/chat-payload-size.py` over the whole box:

```
== totals over 772 messages ==
  tool_output         32.47 MB
  reasoning           16.11 MB
  tool_call            9.23 MB
  other_output         1.48 MB
  content              1.18 MB
  usage/meta/summary   0.35 MB
  TOTAL               60.81 MB
```

86% of the stored bytes are reasoning text and tool output, and **none of it is
drawn** until a row is expanded — the client even truncates tool output to
10 000 chars when it does draw it. Two secondary contributors were measured and
clipped too: tool-call `arguments` (9.2 MB across the box, almost all of it one
argument carrying a whole file) and reasoning summaries.

## Fix

`GET /api/chats/{id}` now trims what only a collapsed row hides
(`_skeleton_output`, `cptr/routers/chat.py`):

| output item | collapsed load | on expand |
| --- | --- | --- |
| `function_call_output` longer than 200 chars | `{call_id, chars, stripped: true}` | full text |
| `reasoning` with text | `{id, status, chars, stripped: true}` | full text |
| `function_call` arguments | strings clipped to 200 chars | full arguments |
| `message`, `image`, `artifact`, `file`, `download` | verbatim | — |

Kept verbatim on purpose: every `ask_user` call **and its output** (that row
renders itself expanded, so trimming it would blank the question it answers),
and any tool output of 200 chars or less.

A message whose output was trimmed carries `output_stripped: true`, which the
frontend turns into one request per expanded message:

```
GET /api/chats/{chat_id}/messages/{message_id}/output
```

The new endpoint is auth-checked against the chat's owner and refuses a message
from another chat. A message that is **still streaming** is never trimmed — its
live text is on screen as it arrives.

Client side: `$lib/utils/messageOutput.ts` holds the trimmed ids and the fetched
streams; `registerLoadedMessages(data.messages)` records them on every chat load
(which also drops the previous chat's cache), and expanding a group, a reasoning
row or a tool call calls `ensureMessageOutput` — a no-op for messages that were
never trimmed, and one shared request for concurrent calls. `AssistantMessage`
renders and saves from `fullOutput` (`$hydratedOutputs.get(id) ?? output`) so the
trimmed copy is never mistaken for the real one; `startEdit` awaits hydration
before opening the editor, since editing writes the stream back. While a fetch is
in flight the row shows a "Loading…" line instead of an empty body, and a failed
fetch leaves the placeholder in place — a later expand retries.

Row expansion is the user's choice, so the fetch happens only if they ask for
detail. The exception is the `expandToolDetails` preference, which keeps rows
open by default: `loadChat` then passes `full=1` and takes the whole payload in
one request rather than one request per message.

## Verification

`notes/_scratch/verify-chat-payload.py` drives the real ASGI app (no restart) and
reports, then checks the trim against the full load:

```
GET /api/chats/4c61866d
  lean   0.66 MB     482 ms      (616 ms on a second run)
  full   6.60 MB     880 ms      (1029 ms)
  saved 89.9%

  ok    same message count  (18)
  ok    same message ids
  ok    lean load flags reduced messages  (5 msgs)
  ok    collapsed-row detail preserved
  (info) 40 tool outputs kept verbatim
  ok    per-message endpoint == full load
  (info) fetching all 5 reduced messages: 6.55 MB in 849 ms
  ok    unreduced message has no flag
  ok    endpoint works for unreduced message
  ok    endpoint requires the chat's own stream
ALL CHECKS PASSED
```

`per-message endpoint == full load` compares the assembled result item by item
against `?full=1`, so nothing is lost by the trim. Sizes are stable across runs;
wall times wander by ~15% with the box's mood. The last info line is the
worst case — opening *every* reduced row in that chat costs what the old load
cost, but only the rows actually opened are paid for, and a typical chat has one.

`npm run build` passes; the built chunks carry both new call shapes
(`?full=1` and `/messages/{id}/output`).

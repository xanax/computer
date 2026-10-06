---
area: provider-retry-policy
title: Retrying outbound provider calls (everything except a real 4xx)
aliases: [retry, 429, backoff, retry-after, transient failure, empty stream]
updated: 2026-10-07
---

# Retrying outbound provider calls

## Map
- `cptr/utils/retry.py` — the whole policy: `RETRY_ATTEMPTS` (4 = first try + 3 retries), `RETRYABLE_4XX` (408/409/425/429), `is_retryable_status`, `is_retryable_exception(exc, extra=...)`, `retry_delay`, `describe_exception`, and `retry_async(fn, label=...)`.
- `cptr/env.py` — `CPTR_PROVIDER_RETRY_ATTEMPTS` (4), `CPTR_PROVIDER_RETRY_MAX_DELAY` (30 s).
- `cptr/utils/ai.py` — three streaming transports each run their own `for attempt in range(RETRY_ATTEMPTS)` loop (Anthropic ~l.642, OpenAI completions ~l.897, OpenAI responses ~l.1250); non-streaming `chat_completion` (~l.398) uses `retry_async`.
- `cptr/utils/images.py` (`images/generations`, `images/edits`) and `cptr/routers/audio.py` (`audio/transcriptions`, `audio/speech`) use `retry_async`.
- `tests/test_retry_policy.py` — 40 tests: the rule, the wait, the runner, and the call sites.

## Facts
- [verified 2026-10-07 cptr/utils/ai.py] Streaming cannot use `retry_async` for the whole call. All three adapters take an `emitted` flag and refuse to retry once it is set, because replaying the request would duplicate text the client has already rendered. The retry only fires for a failure *before the first event*.
- [ran 2026-10-07 in-process against the real OpenRouter key] The retry rescues the exact case that started this: `ai.chat_completion(model="inclusionai/ling-3.1-flash")` 429'd on all three attempts and then **returned 200 on the fourth**, total 10.2 s (`[retry] ... failed (HTTP 429); retrying in 1.1s/2.2s/4.6s`). Before the change that call failed the turn outright. The control model answered in 0.3 s on the first try.
- [verified 2026-10-07] Non-streaming `chat_completion` had **no** retry at all before this — every title/summary/utility call went out once and a transient 5xx silently dropped it.
- [verified 2026-10-07 tests/test_retry_policy.py] `Retry-After` is obeyed whether it is seconds or an HTTP-date (`parsedate_to_datetime`), and clamped to `RETRY_MAX_DELAY_SECONDS` (30 s) so a `Retry-After: 9999` cannot hang a turn.
- [verified 2026-10-07] Without `Retry-After`, the delay is `1.0 * 2**attempt` plus up to 25 % jitter — 1–1.25 s, 2–2.5 s, 4–5 s, capped at 30 s. The jitter matters because several chats rate-limited by the same drained pool would otherwise wake together and drain it again.
- [verified 2026-10-07 tests/test_retry_policy.py] The last failure is re-raised **unchanged**, so callers still see the provider's real exception (and its stored body) after the retries — the retry does not launder a 429 into a generic error.
- Transport failures retried (`RETRYABLE_TRANSPORT_ERRORS`): `httpx.TransportError` (connect/read/write/pool timeout, connection error, remote protocol) plus `httpx.DecodingError`. A `ValueError` or other programming error is deliberately **not** retryable — that would dress up a bug as a transient failure.
- `EmptyCompletionError` (a 200 stream with no content) is retryable via the `extra=` parameter, not by type-matching in the policy module.

## Built
- `cptr/utils/retry.py` (new), pulled out of `ai.py`. The old `_STREAM_RETRY_ERRORS` / `_STREAM_RETRY_ATTEMPTS` / `_is_retryable_stream_error` / `_stream_retry_delay` are gone; the old streaming policy was 429-only with a 0.5/1.0 s linear wait, which is shorter than a drained upstream pool stays drained.
- Retry on all of: `ai.stream_anthropic`, `ai.stream_openai_completions`, `ai.stream_openai_responses`, `ai.chat_completion`, `images.generate_images`, `images.edit_images`, `audio._transcribe_chunk`, `audio.speech`.
- `tests/test_retry_policy.py`, including a structural guard that counts `retry_async(` / `is_retryable_exception(` per file — the original bug was a *missing* wrapper, which a behavioural test on one call site cannot see.

## Decisions
- Retry the widest set that is still honest: everything except a genuine 4xx (400/401/403/404/405/422/451). The user's instruction was "retry all calls on non-4xx failures", and the policy reads the status rather than a list of blessed 5xx, so an unlisted 5xx from a new provider is retried by default.
- Attempts stay small (4) and the cap short (30 s) on purpose. The turn-level deadline still applies — `generate_text` wraps `chat_completion` in a 20 s `asyncio.wait_for`, and `CancelledError` is a `BaseException`, so it passes through the retry loop untouched.
- Not covered: web-search backends (Exa/Tavily/Brave/Perplexity/SearXNG) and the Firecrawl/browser tools. Those are tool calls, not model calls — a failure surfaces as a tool error the model can react to, so a silent multi-second retry inside the tool is the wrong trade.

## Dead ends
- Not worth chasing per-provider retry tuning: the 429 in question is `limit_source: upstream_provider_shared_pool`, i.e. a *shared free* pool, so no header, routing preference (`order` / `allow_fallbacks`) or key change can avoid it — only waiting can. See `areas/provider-error-surfacing.md`.

## Open
- The oldest still-open item in this family: `chat_task.py` keeps only `error.message` from a failed provider body, so the chat shows "Provider returned error" while `code` / `provider_name` / `limit_source` / `remedy_hint` are dropped. Retrying was the user-visible half; this is the diagnosability half.

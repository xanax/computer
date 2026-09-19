# Compaction reloaded from the oldest checkpoint on a branch

Ledger: **B-010**. Probe: `scripts/fork-probes.tsv` → `B-010` (kind `bug`, pattern `existing_summary = m.chat_summary`,
still `PRESENT-UPSTREAM` at `f9d1d8c`). Fix commit: `bb7faa6`.

## Symptom

Chats that had been compacted more than once behaved as if compaction had never
helped: context grew back to the same size after a few turns, the same region of
history was re-summarized over and over, and each new summary covered ground an
earlier one already covered.

## Why: two halves of one invariant

Compaction is checkpointed on a message row — `chat_messages.chat_summary`
(migration `0003_add_context_compaction.py`) — summarising everything *before*
that message. Two places have to agree about where the checkpoint sits:

- **Placement** — `_summary_checkpoint_message_id(keep_zone, fallback)` stamps the
  summary on the first **user** message inside the keep zone, so the replay
  starts on a user turn (never mid-tool-call).
- **Reload** — `_load_message_history(chat_id, message_id)` walks root→leaf and
  slices the chain at the checkpoint it finds.

Placement was right. Reload was not: it stopped at the **first** `chat_summary`
on the branch — the oldest, loosest checkpoint — not the newest.

```python
# before — oldest checkpoint wins
for i, m in enumerate(chain):
    if m.chat_summary:
        chain = chain[i:]
        existing_summary = m.chat_summary
        break

# after — newest checkpoint wins
checkpoint_index = -1
for i, m in enumerate(chain):
    if m.chat_summary:
        checkpoint_index = i
if checkpoint_index >= 0:
    existing_summary = chain[checkpoint_index].chat_summary
    chain = chain[checkpoint_index:]
```

Summaries stack on a branch, and each one is built *from* the previous summary
plus the turns since — so the newest is strictly the tightest bound, and it is
also the one whose placement rule the next compaction will continue from. Taking
the oldest meant re-sending everything after it (already summarized) and, on the
next compaction, summarizing that duplicated tail *again*, which is why the
symptom looked like compaction not working rather than like a slicing bug.

## Verification

Unit tests ([`tests/test_context_compaction.py`](../tests/test_context_compaction.py),
5 tests, no DB — `ChatMessage.get_all_by_chat` is patched with a fake table):

- no checkpoint → whole branch replays
- newest checkpoint wins (fails before the fix: kept `u2,a2` it should not have)
- the checkpoint message itself is replayed, not swallowed
- a checkpoint on a *sibling* branch is ignored (regenerating from before a
  checkpoint must not resurrect its summary)
- `_summary_checkpoint_message_id` still picks the user turn that opens the keep
  zone, so placement and reload agree

Real data, before vs after: `notes/_scratch/compaction-replay-check.py` replays
every chat's active branch through the real loader against a **copy** of the
live DB (`CPTR_DATA_DIR=/tmp/cptr-dbcheck`, `sqlite3.backup()` so the WAL is
folded in; never point it at the live data dir).

| | chats | with a checkpoint | stacked (`>1` checkpoint) | messages replayed |
| --- | --- | --- | --- | --- |
| before | 158 | 46 | 23 | **17,236** |
| after | 158 | 46 | 23 | **8,119** |

All 23 stacked chats shrink (e.g. `1a35d2a9` 326 → 62, `6430a34f` 1937 → 50);
single-checkpoint chats are unchanged, as they must be. The summary actually
used also improved in places, because the newest is the fuller one — `d86131c1`
went from a 1,468-char stale summary to a 3,244-char current one.

`replayed` counts messages as the **LLM** sees them: one DB row holding N tool
rounds expands to N+ messages, which is why a 6-row branch can read as 101.
That expansion is the unit the context budget is spent in, so it is the honest
number — but it means `replayed` is not comparable to `chain=`.

### The same check against the real function

That harness *reimplements* the slicing rule, so it can drift from the code it
is supposed to be testing — it is a model of the fix, not the fix. The
authoritative check is `notes/_scratch/compaction-realfn-check.py`, which loads
the pre-fix module (`git show d66bd9d:cptr/utils/chat_task.py`) and the working
tree side by side under two module names (relative imports still resolve, since
both are named `cptr.utils.*`) and calls `_load_message_history` on each:

| | chats | resumed history | summary differs |
| --- | --- | --- | --- |
| pre-fix | 23 | **11,484** messages | — |
| working tree | 23 | **2,367** messages | 23/23 |

79% less history replayed, e.g. `1a35d2a9` 326 → 62 rows-of-history and
`277e477e` 491 → 161, with the summary in use in each case being the newer one.
Every chat in the set differs, which is the expected shape: a chat is only in
the set if the newest checkpoint is not the first, i.e. exactly the cases where
the two rules disagree by construction.

## Note on the tests' shape

The regression test asserts on the *replayed set* (`["u3", "a3"]`), not on an
internal index. Any refactor that keeps the invariant — the replay starts at the
newest checkpoint and on a user turn — passes; the pre-fix slicing cannot.

# Feature Lanes — proposal

Goal: when the agent is about to modify a file that has **uncommitted work** in it, don't
just clobber it and don't let two features fight over one working tree. Snapshot first,
optionally isolate each feature in its own checkout, and merge later.

Status: proposal only, nothing implemented. All git mechanics below were verified by hand on
this machine (git 2.53.0) in `/tmp/lanetest`.

---

## 1. What actually goes wrong today

Two distinct failure modes, often conflated:

| # | Failure | Cause |
|---|---------|-------|
| F1 | Uncommitted work is lost | `write_file`/`edit_file` overwrite a file whose only copy of your in-progress edits lives in the working tree |
| F2 | Two features conflict mid-development | One working tree, one index. Feature A's half-finished edits are in the same files Feature B is editing; nothing can run/test cleanly, and `edit_file`'s exact-`target` match breaks the moment the other side moves the text |

F1 needs a **snapshot**. F2 needs **isolation**. They're different features and should ship
separately.

### Why not literally duplicate the file

`foo.py` → `foo.py.beta` gives F1 protection and zero F2 protection: imports don't resolve,
nothing runs, no test/typecheck, and merging is per-file manual work. Duplication needs to
happen at the **tree** level, not the file level. The one place a physical file copy is the
right answer is the non-git fallback (§6).

---

## 2. Verified primitives

All commands run from a repo root; nothing below touches the user's working tree or index.

**a) Non-destructive "seed commit" of the full dirty state (tracked + staged + untracked).**
This is the load-bearing trick. A temporary index means the real index and worktree are
never modified:

```sh
export GIT_INDEX_FILE=$(mktemp -u /tmp/laneidx.XXXX)
git read-tree HEAD
git add -A .                      # adds modifications, deletions AND untracked
TREE=$(git write-tree)
unset GIT_INDEX_FILE
SEED=$(git commit-tree "$TREE" -p HEAD -m "cptr lane seed")
```

Verified: seed tree contained `staged.txt tracked.txt untracked.txt`; `git status --porcelain`
on the base was still `A staged.txt / M tracked.txt / ?? untracked.txt` afterwards.

**b) `git stash create -u` does NOT capture untracked files.** Verified — the `-u` is accepted
but the resulting commit has 2 parents and its tree omits the untracked file (untracked would
normally live in the third parent). So do **not** build lanes on `stash create`; use (a).

**c) Lane worktree from the seed is clean and complete:**

```sh
git worktree add -b lane/beta /path/to/lane-beta "$SEED"
```

Verified: lane contained all three files, `git status` in the lane was **clean** (the dirty
state becomes the lane's first commit), and the base worktree/index were untouched. That
inverse property is nice: inside a lane, "uncommitted at seed time" work is already a commit,
so the agent can freely diff/undo against it.

**d) Conflict preview without touching any worktree:** `git merge-tree --write-tree <a> <b>`
(exit 0 = clean, non-zero + conflict listing = conflicted). Present in 2.53; the whole
"what will this merge do?" UI can be built on it.

**e) Repo-less fallback:** `git merge-file -p ours base theirs` works outside a repository
(verified, exit 1 with `<<<<<<<` markers on conflict). This is the escape hatch for workspaces
that aren't git repos.

---

## 3. Existing choke points in cptr (why this is cheaper than it looks)

* `__context__["workspace"]` is the single source of truth for every filesystem tool —
  `_resolve_path()` (`cptr/utils/tools.py:1530`) and 10+ call sites resolve against it, and
  `run_command` uses it as the cwd base. **Retarget the workspace → retarget the whole agent.**
* `start_task(..., workspace=...)` (`cptr/utils/chat_task.py:392`) already threads workspace
  through a chat run, and subagent chats persist it (`meta["workspace"]`,
  `tools.py:2711`), and `async_subagents` records carry `workspace` too. So a lane does not
  need new plumbing — a lane *is* a workspace path. Per-subagent lanes come almost free.
* `cptr/utils/git.py` already has `worktrees()`, `create_worktree()`, `_worktree_path_for_branch()`,
  plus `status()` which already parses unmerged entries into `"conflict"`.
* `POST /api/git/worktrees` (`cptr/routers/git.py:703`) and `frontend/src/lib/apis/git.ts:162`
  already exist, and `GitBar.svelte` already lists worktrees.
* `.cptr` is auto-gitignored on workspace open (`workspace.ensure_cptr_gitignored`), so
  bookkeeping can live in `.cptr/` without polluting the repo.

Conclusion: ~70% of the git plumbing exists. New work is the seed, the guard, and the merge UX.

---

## 4. Design

### 4.1 Checkpoint (F1) — default on, no user action

Before any write that would destroy unsnapshotted content:

1. `git status --porcelain=v2` for the target path (single path, cheap).
2. If the path is **dirty or untracked** and no checkpoint exists for the current
   `(chat_id, path, content-hash)`: store the pre-edit content as a content-addressed blob
   (`git hash-object -w` in a repo; `.cptr/checkpoints/<sha256>` outside one) and record a row
   `(chat_id, path, blob_sha, bytes, created_at, reason)`.
3. Then perform the write.

Result: "duplicate the file before you change it" is satisfied literally and cheaply — the
duplicate is a blob plus a DB row, addressed by content, deduped, and restorable via a new
`restore_checkpoint` tool. This is what makes an accidental clobber survivable.

### 4.2 Read-set guard (F2's safety net) — default on

This is the highest value-per-line piece and doesn't need lanes at all.

* When a read tool returns a file, record `(path, blob_sha, mtime, size)` in the chat's
  tool context.
* Before `write_file`/`edit_file`/`multi_edit_file`, re-hash the file. If it changed since the
  agent last read it → **refuse the write** (after checkpointing), and return
  `"path changed since you read it (by <lane/editor/other agent>); re-read before editing"`.
* For `edit_file` this also kills a real current bug class: the exact-`target` match silently
  applies to text the agent never saw.

This is compare-and-swap for files. It converts "two agents quietly corrupt each other's work"
into a retryable, explained error — which is exactly what makes concurrent feature work viable.

### 4.3 Lane (F2's actual isolation) — opt-in, or auto on second concurrent feature

A **lane** = `(name, branch, worktree path, seed commit, owner chat_id)`.

* `lane_open(name)`: seed per §2a from the *current* workspace state, `git worktree add -b
  lane/<name> <path> <seed>`, register lane, and hand the caller the lane root.
* Routing: a lane-aware run sets `__context__["workspace"] = lane_root` (and the chat's
  `meta["workspace"]`, so reload/resume stays in the lane). Everything else — tools, commands,
  subagents — follows automatically because they all read that one key.
* `lane_list()` / `lane_status()`: surface branch, dirty file count, ahead/behind vs the base
  branch, and whether it has been merged.
* `lane_close(name)`: refuse if unmerged/dirty unless `force`.

Auto-lane heuristic (opt-in by config flag, off by default): if the same `path` receives a
write request from a second concurrent chat/agent while the first is mid-feature on a dirty
tree, propose "open a lane for this feature?" rather than silently serialising both.

### 4.4 Merge

Three tiers, cheapest first:

1. **Preview** (no side effects): `git merge-tree --write-tree lane/x <base>` → clean or list of
   conflicted paths. Rendered in GitBar before anything moves.
2. **Apply**: merge in a dedicated integration worktree (never in the user's live tree). If
   clean → fast-forward/merge commit on the base branch, then the user pulls it into their
   tree normally. If conflicted → hand both sides to the agent with the conflict markers, or
   let the user resolve in the diff UI (the `status()` parser already reports `"conflict"`.
3. **Non-repo / no-merge-base fallback**: `git merge-file -p` per file from the checkpoint
   blobs, showing markers as ink-on-paper text. Degraded but never blocked.

Cross-lane conflict classes worth naming in the UI: same-file overlapping hunks (real
conflict), same-file non-overlapping (auto-merges), lockfiles / generated artifacts
(`package-lock.json`, build output — auto-regenerate, never auto-merge), gitignored heavy dirs
(never copied, see §5).

### 4.5 Storage layout

```
~/.cptr/lanes/<workspace-hash>/<lane-slug>/     # worktrees, on ext4, NOT in the repo
~/.cptr/checkpoints/<workspace-hash>/<blob>     # non-repo snapshot store
<workspace>/.cptr/lanes.json                    # lane registry (gitignored, portable)
```

---

## 5. The WSL/9p reality check (this is the main risk)

Per the measured cost model in this workspace: `/mnt/c` goes through 9p (`stat` ~1–5 ms vs
~0.005 ms on ext4), and the driver is **number of directories visited**. Two consequences:

1. **Lane worktrees must not live on `/mnt/c`.** Put them under `~/.cptr/lanes/...` (ext4).
   `git worktree add` only materialises tracked files, so a live `/mnt/c` repo still works,
   but every file the lane's tools touch is now fast.
2. **Gitignored heavy dirs are absent in a lane** (`node_modules`, `.venv`, `vendor`,
   `target`, build output). A lane without them can't build or test. Fix: after creating a
   lane, symlink them from the base workspace (read-only sharing) or run the lane's install
   step once. This must be a first-class part of `lane_open`, not an afterthought, otherwise
   lanes will look broken.

Also: a lane is a checkout, so `worktree add` on a large repo costs a real checkout. Measure
before promising interactivity, and consider making lane creation an explicitly long-running
operation with progress rather than a blocking request.

---

## 6. Non-git workspaces

If `is_repo()` is false, there are no branches and no merge base. Degrade to:

* checkpoints (blobs in `.cptr/checkpoints/`) as the F1 snapshot,
* physical shadow trees `.cptr/lanes/<lane>/<relpath>` for isolation,
* `git merge-file -p base ours theirs` for the 3-way merge (works repo-less, verified).

Same API surface, weaker guarantees — worth stating plainly in the UI ("isolated copy, not a
branch").

---

## 7. Suggested phasing

| Phase | Scope | Why this order |
|-------|-------|----------------|
| 0 | `cptr/utils/lanes.py` seed primitive (§2a) + checkpoint store + read-set guard | Immediately kills F1 and the silent-clobber class; no UI, no lanes, ~200–300 lines |
| 1 | `lane_open/list/status/close` + workspace retargeting + `POST/GET /api/lanes/*` | Real isolation; subagent-per-lane falls out for free |
| 2 | Merge preview (`merge-tree`) + merge apply in integration worktree + GitBar pane | Makes "2 features at once" actually finishable |
| 3 | Auto-lane heuristic on concurrent dirty writes, heavy-dir symlinking, lane-aware diff UI | Convenience on top of a working core |

Tests worth writing: seed captures untracked/modified/staged and leaves base untouched
(assert `status` before == after); lane worktree is clean; guard rejects a stale write and
accepts after re-read; `merge-tree` preview flags a known conflict; repo-less checkpoint +
`merge-file` round-trip.

---

## 7b. Containers vs worktrees (measured 2026-09-18, this machine)

Question raised: would Docker containers be better for this? Measured rather than guessed.
Host: Docker Desktop 4.90.0, Engine 29.7.2, overlayfs, WSL2 kernel 6.18.33.2.
Benchmark: walk 40 dirs x 10 files (400 entries), lstat + full read of each.

| Configuration | Elapsed |
|---|---|
| native ext4 (`/home/brendan/...`) | **0.013 s** |
| native `/mnt/c` (9p) | 4.068 s |
| container → bind mount of ext4 | **0.013 s** |
| container → bind mount of `/mnt/c` | 4.306 s |
| container → its own overlayfs | 0.040 s |

Per-lane / per-command overhead:

| Operation | Cost |
|---|---|
| `docker run --rm <img> true` (fresh container) | 0.91 – 1.61 s |
| `docker exec <persistent> true` | 0.30 – 0.41 s |
| `git worktree add` (this repo, 10 MB `.git`) | **0.39 s one-time, then 0** |

Conclusions:

1. **Containers are not the variable that matters here.** On ext4 a container bind mount is
   indistinguishable from native (0.013 s both). The 313× penalty is entirely *which
   filesystem* the files live on, not whether a container is involved. Moving lanes to ext4
   buys the same win with or without Docker.
2. **Containers solve a different axis.** Worktrees isolate *source state* (two features
   editing the same file). Containers isolate *runtime, dependency and process state*. A
   container does not help with "two features editing the same file" at all — you still need
   two checkouts, because the conflict is in the source, not the environment.
3. **Container adds real per-command latency into the agent loop.** `cptr` currently executes
   everything on the host (PTY `os.fork` in `utils/terminal.py`, `asyncio.create_subprocess_exec`
   in `utils/git.py`, direct file I/O). There is no container exec transport — `/.dockerenv`
   is only used to detect whether *cptr itself* is containerized. Routing every agent tool
   call through `docker run` costs ~1.5 s each; a persistent container + `docker exec` still
   costs ~0.4 s each, multiplied by every command in a task. A worktree lane costs 0.39 s
   once and zero thereafter, and reuses the existing host execution path unchanged.
4. **Where containers genuinely win:** conflicting *dependencies* per feature, isolated dev
   servers / ports / databases per lane, and sandboxing untrusted agent commands. The first
   two are the same "heavy dirs" gap noted in §5; the third is a security goal, not a
   parallelism goal.
5. **Best of both if containers are wanted:** lane = worktree on ext4, optionally with a
   container per lane that *bind-mounts that lane's worktree*. Since ext4 bind mounts are
   free (0.013 s), this combination is viable; bind-mounting `/mnt/c` into a container is not
   (4.3 s).

Caveat on method: synthetic syscall-bound workload (400 small files, warm cache), matching
the cost model already recorded for this workspace. It does not measure large-file throughput,
container CPU/memory overhead, or cold-cache behaviour.

## 8. Decisions needed

1. **Auto or opt-in?** I'd default checkpoints + guard on (invisible, cheap), lanes opt-in via
   an explicit tool/UI toggle, auto-lane only behind a config flag.
2. **Where do lanes live** — external (`~/.cptr`, recommended, esp. on `/mnt/c`) vs beside the
   repo (`../<repo>-<lane>`, current `_worktree_path_for_branch` behaviour)?
3. **Heavy dirs**: symlink from base (fast, shared) vs per-lane install (correct, slow)?
4. **Merge target**: commit to the base branch automatically, or always leave it for the user
   to pull into their own tree?
5. Is "one lane per chat" good enough, or do you want multiple lanes nested inside one chat?

---

## 9. Non-goals

* Not a substitute for committing. Lanes make concurrent work survivable, not tidy.
* Not a lock manager / no daemon-level file locking; the guard is optimistic (CAS), which is
  the right trade-off for agent-scale edit rates.
* Not auto-resolving semantic conflicts (two differently-named functions, API drift). Textual
  merges only; semantic conflicts get surfaced to the agent/human.

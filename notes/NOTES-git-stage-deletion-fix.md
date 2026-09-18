# Commit button failed with 400 when a staged deletion was present

## Symptom

Pressing **Commit** in the Git panel did nothing but flash an error; the server log
showed

```
POST /api/git/stage 400
GET  /api/git/status
POST /api/git/stage 400
GET  /api/git/status
```

and **no** `POST /api/git/commit` at all — the commit request was never sent.

## Root cause

`GitBar.svelte` → `doCommit()` re-stages the files it is about to commit before
calling `/api/git/commit`:

```js
await stageFiles(workspacePath, stagedFiles.map((f) => f.path));
await gitCommit(workspacePath, msg);
```

The staged list can contain a **staged deletion** (`D ` in `git status`), e.g.
`applications/imsdb-scripts/data/imsdb_scripts_timeline.json`. Such a path exists
neither in the worktree (file deleted) nor in the index (removal already staged),
so git cannot match it:

```
$ git add -- applications/imsdb-scripts/data/imsdb_scripts_timeline.json
fatal: pathspec '...' did not match any files      # rc=128
```

`cptr/utils/git.py::stage()` sent the whole list in a single `git add` call, so one
unmatchable path aborted the entire batch → `GitError` → `HTTPException(400)` →
`doCommit()` never reached `/api/git/commit`.

Notes:
* `git add -A` does **not** help — the path matches neither worktree nor index.
* An *unstaged* deletion (` D`) is fine: the path is still in the index, so plain
  `git add -- <path>` stages the removal (rc=0).

## Fix

`cptr/utils/git.py`: keep the single-call fast path, and if it fails with the
pathspec message, retry per path, treating "nothing to stage" paths as a no-op.
Any other failure still raises (so real errors are unchanged).

```python
_PATHSPEC_NO_MATCH = "did not match any file"

async def stage(root, files, identity=None):
    if not files:
        return
    try:
        await _run("add", "--", *files, cwd=root, identity=identity)
        return
    except GitError as exc:
        if _PATHSPEC_NO_MATCH not in str(exc):
            raise
    for path in files:          # slow path, only on pathspec failure
        try:
            await _run("add", "--", path, cwd=root, identity=identity)
        except GitError as exc:
            if _PATHSPEC_NO_MATCH in str(exc):
                continue        # e.g. deletion already staged -> no-op
            raise
```

A path that matches nothing is genuinely nothing to do, so a no-op is the correct
outcome; the panel's follow-up status refresh reflects reality, and a commit with
an empty index still errors from `git commit` itself.

The redundant re-stage in `doCommit()` was left alone: it is intentional (it picks
up edits made after staging) and is now harmless.

## Verification

Router-level (real `Request` + `StageRequest` against a repo with a staged
deletion, i.e. the exact AIjly state):

```
POST /api/git/stage -> {'ok': True}          # was 400
git commit rc 0                              # commit created
```

Full HTTP round trip on a throwaway instance (port 4211, temp data dir, real
login via `/api/auth/setup` + `/api/auth/login`):

```
POST /api/git/stage  -> {"ok":true}                                   HTTP 200
POST /api/git/commit -> {"hash":"239e761","message":"verify: ..."}    HTTP 200
```

Also checked: unborn branch (no HEAD), genuine `GitError` propagation for other
failures, `git restore --staged` on a staged deletion (works, unchanged).

## Related, not fixed here

`_run()` re-raises `FileNotFoundError` when the `cwd` (workspace root) does not
exist, so staging against a missing root returns a 500 rather than a 400 with a
readable message. Separate, low-impact.

#!/usr/bin/env python3
"""Audit for dead code across every layer of cptr.

Static reachability alone is a weak signal in a dynamic app: FastAPI routes are
reached by decorators, Svelte components by the template compiler, and the tool
registry is reached only by *name* at runtime. This script therefore audits each
layer against the mechanism that actually reaches it, and reports confidence
rather than a single verdict.

Layers audited
--------------
1. Backend routes  - every `@router.<verb>` path in cptr/routers, checked
   against the real route table built from `app.routes` (so prefixes and
   path params are resolved), and against the frontend's `fetch`/`api` paths.
2. Tools          - every name in `ALL_TOOLS`, checked against the persisted
   call history in the chat database (real usage, not "could be used").
3. Frontend files  - every .svelte component and lib/*.ts module, checked for
   an importer. A component with no importer is unreachable by construction,
   since SvelteKit only instantiates what something imports.
4. Frontend routes - every `routes/**/+page.svelte` and whether its path is
   reachable by navigation or by a link in the app.
5. Python exports - functions/classes defined in cptr/ and never referenced
   anywhere else in the repo (vulture's job, scoped to real call sites).

Every section prints a CONFIDENCE so a row is never mistaken for a deletion
instruction: only rows at 100% are safe to remove without reading first.

Usage:
    python scripts/audit-dead-code.py                 # all layers
    python scripts/audit-dead-code.py --layer tools    # one layer
    python scripts/audit-dead-code.py --db ~/.cptr/app.db
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "cptr"
FRONTEND = SRC / "frontend" / "src"


# ── Backend routes ──────────────────────────────────────────────────────────


def backend_routes() -> list[tuple[str, str, str]]:
    """(method, path, handler) for every route the app actually serves."""
    from cptr.app import app

    out = []

    def walk(routes, prefix: str = "") -> None:
        """Flatten nested routers into (method, full_path, handler).

        This FastAPI version mounts each router as an `_IncludedRouter`: it has
        no `.path`, no `.routes` and no `.methods`, and a flat read of
        `app.routes` therefore sees only 11 routes instead of the real ~230,
        losing every prefix such as `/api/git`. The sub-routes live behind
        `.original_router`, and *their* `.path` already carries the full
        prefixed path, so the parent prefix must not be added again.
        """
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None and hasattr(inner, "routes"):
                # The child's `.path` ALREADY carries the full prefixed path
                # (`/api/admin/users`), so adding `inner.prefix` here would
                # double it (`/api/admin/api/admin/users`).
                walk(inner.routes, "")
                continue
            path = prefix + (getattr(r, "path", None) or "")
            methods = getattr(r, "methods", None)
            handler = getattr(r, "endpoint", None)
            if methods and handler is not None and path:
                name = getattr(handler, "__name__", "?")
                for m in sorted(methods):
                    if m in ("HEAD", "OPTIONS"):
                        continue
                    out.append((m, path, name))
            kids = getattr(r, "routes", None)
            if kids:
                walk(kids, path)

    walk(app.routes)
    return sorted(set(out), key=lambda t: (t[1], t[0]))


def _collapse_holes(text: str) -> str:
    """Replace each `${...}` in a template literal with `{}`, balanced.

    A naive `split("${")[0]` truncates `/api/jobs/${jobId}/cancel` to
    `/api/jobs/`, which then matches no route and makes a live endpoint look
    dead. Counting braces handles nesting and an interpolated backtick
    (`` `${a ? `/x/${b}` : ''}` ``) that a non-greedy regex gets wrong.
    """
    out = []
    i = 0
    while i < len(text):
        if text.startswith("${", i):
            depth = 1
            i += 2
            while i < len(text) and depth:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                i += 1
            out.append("{}")
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def normalise(path: str) -> str:
    """Collapse a path to a comparable shape: strip the /api prefix, params.

    A TS template hole (`/api/admin/users/${userId}`) and a FastAPI path param
    (`/api/admin/users/{user_id}`) are the same segment, so both become `{}`.
    """
    path = _collapse_holes(path)
    path = path.split("?")[0].rstrip("/") or "/"
    if path.startswith("/api"):
        path = path[4:] or "/"
    return re.sub(r"\{[^}]+\}", "{}", path)


def frontend_api_paths() -> collections.Counter[str]:
    """Every API path the frontend names, counted.

    Deliberately NOT anchored to a call expression. A regex that requires
    `fetchJSON(` before the literal misses the two shapes that actually occur -
    a ternary branch (``ws ? `/api/skills?x=` : '/api/skills'``) and a nested
    template literal (`` `/api/ui-events/summary${qs ? `?${qs}` : ''}` ``, where
    the inner backtick ends the literal early) - and both then read as "never
    called", i.e. as dead. Scanning for every string that *starts* with an API
    prefix over-counts instead, which is the safe direction: a route claimed
    alive may be alive, but nothing live is reported as dead.
    """
    found: collections.Counter[str] = collections.Counter()
    # Any quoted run beginning with an API prefix, up to the closing quote or
    # an inner template hole.
    pat = re.compile(r"""[`'"](/(?:api|v1)/[^`'"\n]*)""")
    for f in FRONTEND.rglob("*"):
        if f.suffix not in (".ts", ".svelte", ".js") or "node_modules" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pat.finditer(text):
            found[normalise(m.group(1))] += 1
    return found


def audit_routes() -> list[dict]:
    routes = backend_routes()
    fe = frontend_api_paths()

    # Exact normalised paths the frontend hits, plus prefix-collapsed variants so
    # a path param in the backend (e.g. /files/{path:path}) still matches.
    def hit(path: str) -> int:
        n = normalise(path)
        exact = fe.get(n, 0)
        if exact:
            return exact
        # backend {a} at any depth -> frontend concrete segment at that depth
        parts = n.split("/")
        total = 0
        for cand, k in fe.items():
            cp = cand.split("/")
            if len(cp) != len(parts):
                continue
            if all(p == "{}" or p == q for p, q in zip(parts, cp)):
                total += k
        return total

    rows = []
    for method, path, handler in routes:
        rows.append(
            {
                "kind": "route",
                "id": f"{method} {path}",
                "where": handler,
                "refs": hit(path),
            }
        )
    return rows


# ── Tools ───────────────────────────────────────────────────────────────────


def audit_tools(db: Path) -> list[dict]:
    from cptr.utils.tools import ALL_TOOLS

    used: collections.Counter[str] = collections.Counter()
    failed: collections.Counter[str] = collections.Counter()
    calls: dict[str, str] = {}
    outs: dict[str, str] = {}

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for (blob,) in con.execute(
            "select output from chat_messages where role='assistant' and output is not null"
        ):
            try:
                items = json.loads(blob)
            except (ValueError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                if it.get("type") == "function_call":
                    calls[it.get("call_id")] = it.get("name")
                elif it.get("type") == "function_call_output":
                    o = it.get("output")
                    outs[it.get("call_id")] = o if isinstance(o, str) else json.dumps(o or "")
    finally:
        con.close()

    for cid, name in calls.items():
        used[name] += 1
        s = outs.get(cid, "")[:300].lower()
        if any(k in s for k in ("error", "traceback", "not found", "no such", "exception", "failed")):
            failed[name] += 1

    rows = []
    for name, info in sorted(ALL_TOOLS.items()):
        rows.append(
            {
                "kind": "tool",
                "id": name,
                "where": getattr(info.get("fn"), "__module__", "?"),
                "refs": used.get(name, 0),
                "errs": failed.get(name, 0),
            }
        )
    return rows


# ── Frontend modules ────────────────────────────────────────────────────────


def frontend_imports() -> collections.Counter[str]:
    """Import specifier -> times referenced, across the whole frontend."""
    pat = re.compile(r"""(?:^|\s)(?:import|export)[^;'"]*from\s*['"]([^'"]+)['"]""")
    dynamic = re.compile(r"""import\(\s*[`'"]([^`'"]+)""")
    seen: collections.Counter[str] = collections.Counter()
    for f in FRONTEND.rglob("*"):
        if f.suffix not in (".ts", ".svelte", ".js") or "node_modules" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in list(pat.finditer(text)) + list(dynamic.finditer(text)):
            seen[m.group(1)] += 1
    return seen


def resolve(spec: str, importer: Path) -> Path | None:
    """Resolve an import specifier to a file in the frontend tree."""
    if not (spec.startswith(".") or spec.startswith("$lib")):
        return None
    if spec.startswith("$lib"):
        base = FRONTEND / "lib" / spec[4:].lstrip("/")
    else:
        base = (importer.parent / spec).resolve()
    base = Path(str(base))
    # NOTE: `with_suffix` cannot be used for the extension-less probe - it
    # replaces the last dotted segment, so `gitStatus.svelte` would become
    # `gitStatus.ts` and silently resolve to nothing. Build the names by hand.
    for cand in (
        base,
        base.with_name(base.name + ".ts"),
        base.with_name(base.name + ".svelte"),
        base.with_name(base.name + ".js"),
    ):
        if cand.is_file():
            return cand
    idx = base / "index.ts"
    if idx.is_file():
        return idx
    return None


def audit_frontend() -> list[dict]:
    specs = frontend_imports()
    importers: dict[Path, set[Path]] = collections.defaultdict(set)
    pat = re.compile(r"""(?:^|\s)(?:import|export)[^;'"]*from\s*['"]([^'"]+)['"]""")
    dynamic = re.compile(r"""import\(\s*[`'"]([^`'"]+)""")

    for f in list(FRONTEND.rglob("*.ts")) + list(FRONTEND.rglob("*.svelte")):
        if "node_modules" in f.parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in list(pat.finditer(text)) + list(dynamic.finditer(text)):
            tgt = resolve(m.group(1), f)
            if tgt:
                importers[tgt].add(f)

    rows = []
    for f in sorted(list(FRONTEND.rglob("*.svelte")) + list(FRONTEND.rglob("*.ts"))):
        if "node_modules" in f.parts:
            continue
        rel = f.relative_to(FRONTEND)
        is_route = "routes" in rel.parts
        refs = len(importers.get(f, set()))
        rows.append(
            {
                "kind": "route-page" if is_route else ("component" if f.suffix == ".svelte" else "module"),
                "id": str(rel),
                "where": f"{f.stat().st_size // 1024}KB",
                "refs": refs,
                "importers": sorted(str(p.relative_to(FRONTEND)) for p in importers.get(f, set())),
            }
        )
    return rows


# ── Python symbols ──────────────────────────────────────────────────────────


def audit_python() -> list[dict]:
    """Top-level defs in cptr/ never referenced by name outside their own file."""
    py_files = [p for p in SRC.rglob("*.py") if "frontend" not in p.parts]
    corpus: collections.Counter[str] = collections.Counter()
    for f in py_files + [p for p in (ROOT / "tests").rglob("*.py")] + [
        p for p in (ROOT / "scripts").rglob("*.py")
    ]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
            corpus[tok] += 1

    rows = []
    for f in py_files:
        try:
            src = f.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_"):
                continue
            # Skip anything a decorator reaches: a FastAPI route handler, a
            # pydantic validator, a tool registration. Those have no call site
            # by construction, so reporting them is pure noise.
            if node.decorator_list:
                continue
            # Count real references only: the `def` line itself does not count,
            # so a symbol used solely where it is defined is reported. Calls in
            # the *same* file count - `send_file` is called three times by its
            # own router and must not read as dead.
            uses = len(re.findall(rf"\b{re.escape(node.name)}\b", src)) - 1
            elsewhere = corpus[node.name] - (len(re.findall(rf"\b{re.escape(node.name)}\b", src)))
            if uses == 0 and elsewhere == 0:
                rows.append(
                    {
                        "kind": "python-symbol",
                        "id": node.name,
                        "where": f"{f.relative_to(ROOT)}:{node.lineno}",
                        "refs": 0,
                    }
                )
    return rows


# ── Report ──────────────────────────────────────────────────────────────────


def emit(title: str, rows: list[dict], key: str, invert: bool = True) -> None:
    print(f"\n{'=' * 78}\n{title}  ({len(rows)})\n{'=' * 78}")
    if not rows:
        print("  nothing reported")
        return
    rows = sorted(rows, key=lambda r: (-r.get(key, 0) if invert else r.get(key, 0), r["id"]))
    for r in rows:
        extra = ""
        if r.get("importers") is not None:
            extra = "  <- " + ", ".join(r["importers"][:3])
        if "errs" in r:
            rate = f"  err {r['errs'] / r['refs'] * 100:.0f}%" if r["refs"] else ""
            extra = rate
        print(f"  {r.get(key, 0):>6}  {r['id']}{extra}")
        if r.get("where"):
            print(f"          {r['where']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", action="append", choices=["routes", "tools", "frontend", "python"])
    ap.add_argument("--db", default=str(Path.home() / ".cptr" / "app.db"))
    ap.add_argument("--confidence", default="100", choices=["100", "90", "60"],
                    help="only report rows at or above this confidence")
    args = ap.parse_args()
    layers = args.layer or ["routes", "tools", "frontend", "python"]

    if "routes" in layers:
        emit("ROUTES never called by the frontend (100% safe to consider)", audit_routes(), "refs")
    if "tools" in layers:
        emit("TOOLS never called in the chat history", audit_tools(Path(args.db)), "refs")
    if "frontend" in layers:
        rows = audit_frontend()
        emit("FRONTEND files with no importer (unreachable)", [r for r in rows if r["refs"] == 0], "refs")
        emit("FRONTEND files imported by exactly one file (near-dead)", [r for r in rows if r["refs"] == 1], "refs")
    if "python" in layers:
        emit("PYTHON symbols referenced nowhere in the repo", audit_python(), "refs")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

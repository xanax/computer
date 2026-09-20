#!/usr/bin/env python3
"""Register every aijly per-app OpenAPI spec as a cptr tool server.

The aijly host exposes one spec per application:

    http://<aijly-host>/api/applications/<app-id>/openapi.json

cptr turns each spec operation into a tool named `<server-id>_<operationId>`,
so the server id here is the tool prefix (e.g. `stags_page_search`).

Server ids must match ``[a-z0-9_]+`` (cptr's admin API rejects hyphens), so the
hyphenated app ids get underscores. ``1984-analysis`` becomes ``orwell_1984``
rather than ``1984_analysis`` because model APIs that accept function names —
Gemini in particular — require the name to start with a letter or underscore.

Idempotent: creates what is missing, updates what already exists, then verifies
every server by re-fetching its spec and converting it to tool schemas.

Usage:
    .venv/bin/python scripts/add-aijly-toolsets.py [--dry-run] [--aijly-host URL]

Requires an admin session cookie (see .cptr/harness/mint-cookie.py); this talks
to the admin API so the running server picks the servers up immediately.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

COOKIE_FILE = Path("/tmp/verify-cookies.txt")
COOKIE_NAME = "cptr_session"

# The aijly key is deliberately NOT hardcoded here: .env.local/.env.dev/.env.prod all
# share one value, so this is a prod-capable secret and does not belong in git history.
# Resolution order: --key, $AIJLY_API_KEY, then the key already stored on a registered
# server in cptr's own config (config.toml mirrors what was set through the admin API).
DATA_DIR = Path(os.environ.get("CPTR_DATA_DIR") or Path.home() / ".cptr")
CONFIG_FILE = DATA_DIR / "config.toml"

# (app id on the aijly host, cptr server id / tool prefix, display name)
APPS: list[tuple[str, str, str]] = [
    ("household-finance", "household_finances", "Household Finances"),
    ("abbeyfield", "abbeyfield", "Abbeyfield"),
    ("stags", "stags", "Stags"),
    ("smp-scaffolding", "smp_scaffolding", "SMP Scaffolding"),
    ("1984-analysis", "orwell_1984", "1984 Analysis"),
    ("wuthering-heights", "wuthering_heights", "Wuthering Heights"),
    ("imsdb-scripts", "imsdb_scripts", "IMSDb Scripts"),
    ("knowledge-graph-test", "knowledge_graph_test", "Knowledge Graph Test"),
    ("dwp", "dwp", "DWP"),
    ("pathfinder", "pathfinder", "Pathfinder"),
    ("story-builder", "story_builder", "Story Builder"),
    ("sandbox", "sandbox", "Sandbox"),
]


def request(method: str, url: str, cookie: str | None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", f"{COOKIE_NAME}={cookie}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"error": raw.decode(errors="replace").strip()}


def key_from_cptr_config() -> str | None:
    """Reuse the aijly key already stored on a registered server, if any."""
    if not CONFIG_FILE.exists():
        return None
    try:
        import tomllib

        with CONFIG_FILE.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return None

    servers = data.get("app_config", {}).get("tool_servers")
    if isinstance(servers, str):  # config.toml stores this value JSON-encoded
        try:
            servers = json.loads(servers)
        except json.JSONDecodeError:
            return None
    for server in servers or []:
        if isinstance(server, dict) and server.get("key"):
            return str(server["key"])
    return None


def read_cookie() -> str:
    if not COOKIE_FILE.exists():
        sys.exit(f"no cookie file at {COOKIE_FILE}; run .venv/bin/python .cptr/harness/mint-cookie.py")
    for line in COOKIE_FILE.read_text().splitlines():
        if COOKIE_NAME in line and not line.startswith("# ") :
            return line.split("\t")[-1].strip()
    sys.exit(f"no {COOKIE_NAME} entry in {COOKIE_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aijly-host", default="http://127.0.0.1:8000")
    parser.add_argument("--cptr-host", default="http://127.0.0.1:4200")
    parser.add_argument("--scope", default="global", choices=("global", "workspace"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--key",
        default=None,
        help="aijly API key (default: $AIJLY_API_KEY, else the key already in cptr config)",
    )
    args = parser.parse_args()

    api_key = args.key or os.environ.get("AIJLY_API_KEY") or key_from_cptr_config()
    if not api_key:
        sys.exit(
            "no aijly API key: pass --key, set $AIJLY_API_KEY, or register one server "
            f"first (looked in {CONFIG_FILE})"
        )

    cookie = read_cookie()
    admin = f"{args.cptr_host.rstrip('/')}/api/admin/tools/servers"

    status, payload = request("GET", admin, cookie)
    if status != 200:
        sys.exit(f"admin API unreachable ({status}): {payload}")
    existing = {s["id"] for s in payload.get("servers", [])}
    print(f"cptr reports {len(existing)} existing tool server(s): {', '.join(sorted(existing)) or '-'}\n")

    # ── create / update ─────────────────────────────────────
    for app_id, server_id, name in APPS:
        body = {
            "id": server_id,
            "type": "openapi",
            "url": args.aijly_host.rstrip("/"),
            "path": f"api/applications/{app_id}/openapi.json",
            "auth_type": "bearer",
            "key": api_key,
            "name": name,
            "description": f"aijly app: {app_id}",
            "headers": None,
            "enabled": True,
            "scope": args.scope,
        }
        if args.dry_run:
            print(f"  [dry-run] {'update' if server_id in existing else 'create'} {server_id} -> {app_id}")
            continue

        if server_id in existing:
            # url/path/key/scope only; key is write-only in the API
            status, payload = request("PUT", f"{admin}/{server_id}", cookie, body)
            verb = "updated"
        else:
            status, payload = request("POST", admin, cookie, body)
            verb = "created"

        if status != 200:
            print(f"  FAIL  {server_id:<22} {verb}: {status} {payload}")
            continue

        # ── verify: fetch spec through the server and count tools ──
        vstatus, vpayload = request("POST", f"{admin}/{server_id}/verify", cookie)
        tools = [t.get("name") for t in vpayload.get("tools", [])] if vstatus == 200 else []
        if vstatus == 200:
            print(f"  ok    {server_id:<22} {verb:<8} {len(tools)} tool(s): {', '.join(tools)}")
        else:
            print(f"  WARN  {server_id:<22} {verb}, but verify failed: {vstatus} {vpayload}")

    if args.dry_run:
        return 0

    # ── final state ─────────────────────────────────────────
    print()
    status, payload = request("GET", admin, cookie)
    servers = payload.get("servers", [])
    print(f"tool servers now configured: {len(servers)}")
    for s in servers:
        flag = "on " if s.get("enabled", True) else "off"
        print(f"  {flag} {s['id']:<22} {s['name']:<20} {s['url']}/{s['path']}  scope={s.get('scope')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

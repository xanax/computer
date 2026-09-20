import sys; sys.path.insert(0, "/home/brendan/computer")
import asyncio, json
from cptr.utils import tools as T

NAMES = [
    "household_finances_household_sql", "stags_maps_distance", "pathfinder_pathfinder_search",
    "sandbox_query_hardware", "abbeyfield_page_search", "story_builder_build_epub",
    "orwell_1984_graphiti_query", "abbeyfield_contact_form", "smp_scaffolding_page_search",
    "dwp_graphiti_query", "wuthering_heights_graphiti_query", "imsdb_scripts_graphiti_query",
    "knowledge_graph_test_graphiti_query", "stags_stags_property_search",
    "abbeyfield_abbeyfield_property_search", "sandbox_web_search",
]


def _text(out: str) -> str:
    """Flatten an external-tool result (JSON envelope or raw) to searchable text."""
    s = str(out)
    try:
        env = json.loads(s)
    except Exception:
        return s.lower()
    result = env.get("result") if isinstance(env, dict) else env
    if isinstance(result, (dict, list)):
        return json.dumps(result).lower()
    return str(result).lower()


async def main():
    cache = await T._load_tool_servers()
    print("=== SCHEMA CHECK ===")
    for n in NAMES:
        spec = cache["tools"][n]["spec"]
        p = spec.get("parameters", {})
        print(f"{n}\n  required={p.get('required')}\n  props={list(p.get('properties', {}))}")
        print(f"  desc={(spec.get('description') or '')[:120]!r}")

    print("\n=== EXECUTION TEST (direct external-tool path, bypassing workspace gating) ===")
    results = {}
    for name, args in [
        ("sandbox_query_hardware", {}),
        ("pathfinder_pathfinder_search", {"query": "test"}),
        ("wuthering_heights_graphiti_query", {"query": "Heathcliff", "search": "nodes", "num_results": 3}),
        ("orwell_1984_graphiti_query", {"query": "Winston Smith", "search": "nodes", "num_results": 3}),
    ]:
        try:
            out = await T._execute_external_tool(name, args)
        except Exception as e:
            out = f"EXCEPTION {type(e).__name__}: {e}"
        results[name] = str(out)
        print(f"\n{name}({args}) ->\n{str(out)[:400]}")

    print("\n=== ROUTING CHECK (each app must hit its OWN graph) ===")
    wh = _text(results.get("wuthering_heights_graphiti_query", ""))
    orwell = _text(results.get("orwell_1984_graphiti_query", ""))

    # Markers that exist in one book's graph and not the other.
    wh_markers = ["heathcliff", "wuthering", "earnshaw"]
    orwell_markers = ["winston", "oceania", "big brother"]

    problems = []
    if not any(m in wh for m in wh_markers):
        problems.append("wuthering_heights_graphiti_query: no Wuthering Heights markers in result")
    if any(m in wh for m in orwell_markers):
        problems.append("wuthering_heights_graphiti_query: Orwell markers leaked in (wrong graph!)")
    if not any(m in orwell for m in orwell_markers):
        problems.append("orwell_1984_graphiti_query: no Orwell markers in result")
    if any(m in orwell for m in wh_markers):
        problems.append("orwell_1984_graphiti_query: Wuthering Heights markers leaked in (wrong graph!)")

    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
    else:
        print("  PASS  wuthering_heights -> Wuthering Heights, orwell_1984 -> 1984")

    print("\n=== WORKSPACE GATING (scope=workspace -> no attachment needed unless opted in) ===")
    attached = await T._workspace_attached_servers("", "")
    print("attached for empty workspace:", attached)
    for s in cache["servers"]:
        print(f"  {s['id']:<22} allowed={T.server_allowed_in_workspace(s, attached)} scope={s.get('scope')}")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

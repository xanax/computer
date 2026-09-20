import sys; sys.path.insert(0, "/home/brendan/computer")
import asyncio, sys
from cptr.utils import tools as T

NAMES = [
    "household_finances_household_sql", "stags_maps_distance", "pathfinder_pathfinder_search",
    "sandbox_query_hardware", "abbeyfield_page_search", "story_builder_build_epub",
    "orwell_1984_graphiti_query", "abbeyfield_contact_form", "smp_scaffolding_page_search",
    "dwp_graphiti_query", "wuthering_heights_graphiti_query", "imsdb_scripts_graphiti_query",
    "knowledge_graph_test_graphiti_query", "stags_stags_property_search",
    "abbeyfield_abbeyfield_property_search", "sandbox_web_search",
]


async def main():
    cache = await T._load_tool_servers()
    print("=== SCHEMA CHECK ===")
    for n in NAMES:
        spec = cache["tools"][n]["spec"]
        p = spec.get("parameters", {})
        print(f"{n}\n  required={p.get('required')}\n  props={list(p.get('properties', {}))}")
        print(f"  desc={(spec.get('description') or '')[:120]!r}")

    print("\n=== EXECUTION TEST (real calls through cptr's external-tool path) ===")
    for name, args in [
        ("sandbox_query_hardware", {}),
        ("pathfinder_pathfinder_search", {"query": "test"}),
    ]:
        try:
            out = await T.execute_tool(name, args, {"workspace": "", "user_id": ""})
        except Exception as e:
            out = f"EXCEPTION {type(e).__name__}: {e}"
        print(f"\n{name}({args}) ->\n{str(out)[:400]}")

    print("\n=== WORKSPACE GATING (scope=global -> no attachment needed) ===")
    attached = await T._workspace_attached_servers("", "")
    print("attached for empty workspace:", attached)
    for s in cache["servers"]:
        print(f"  {s['id']:<22} allowed={T.server_allowed_in_workspace(s, attached)} scope={s.get('scope')}")


asyncio.run(main())

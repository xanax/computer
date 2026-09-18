"""Run an arbitrary JS snippet against the running app with a given theme.

    python3 downloads/_shots/probe_js.py <theme> <path> <<'JS'
    ...snippet...
    JS
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import APP, set_theme
from cdp import Browser


async def main(theme: str, path: str, snippet: str):
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    print("PUT ->", set_theme(theme, jwt), "| theme:", theme)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}{path}", settle=10)
        print((await b.js(snippet))[:4000])


if __name__ == "__main__":
    theme = sys.argv[1]
    path = sys.argv[2] if len(sys.argv) > 2 else "/"
    asyncio.run(main(theme, path, sys.stdin.read()))

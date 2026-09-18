"""Audit a surface that is not reachable by URL alone.

    python3 downloads/_shots/audit_chat.py <theme> <tag> <js-click-selector-js>

Opens `/`, optionally runs a snippet of JS to navigate inside the app (click a
chat, open a file, ...), then reuses `audit.py`'s colour audit and writes a
screenshot to `downloads/_shots/<tag>.png`.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import APP, AUDIT, set_theme
from cdp import Browser


async def main(theme: str, tag: str, prelude: str):
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    print("PUT ->", set_theme(theme, jwt), "| theme:", theme)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=10)
        if prelude:
            print("prelude ->", await b.js(prelude))
            await b.sleep(6)
        await b.shot(f"downloads/_shots/{tag}.png")
        print(
            "surface ->",
            await b.js(
                """JSON.stringify({
                    text: document.body.innerText.length,
                    prose: document.querySelectorAll('[class*=prose]').length,
                    pre: document.querySelectorAll('pre').length,
                    code: document.querySelectorAll('code').length,
                    svg: document.querySelectorAll('svg').length,
                    cm: document.querySelectorAll('.cm-editor').length,
                    head: document.body.innerText.trim().slice(0, 220).replace(/\\s+/g, ' ')
                })"""
            ),
        )
        print((await b.js(AUDIT))[:3500])


if __name__ == "__main__":
    theme, tag = sys.argv[1], sys.argv[2]
    prelude = sys.argv[3] if len(sys.argv) > 3 else ""
    asyncio.run(main(theme, tag, prelude))

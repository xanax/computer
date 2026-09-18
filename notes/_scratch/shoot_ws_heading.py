"""Screenshot the sidebar (full window + sidebar-only clip) for a theme.

Usage: .venv/bin/python notes/_scratch/shoot_ws_heading.py <theme>
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/home/brendan/computer/downloads/_shots")
from audit import set_theme  # noqa: E402
from cdp import Browser  # noqa: E402

APP = "http://127.0.0.1:4200"
JWT = Path("/tmp/jwt.txt").read_text().strip()
OUT = Path("/home/brendan/computer/downloads/_shots")


async def main(theme: str, tag: str, path: str):
    print("PUT ->", set_theme(theme, JWT)[:60], "| theme:", theme)
    async with Browser(1200, 860) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(f"{APP}{path}", settle=12)
        await b.shot(str(OUT / f"{tag}.png"))
        res = await b.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {"x": 0, "y": 0, "width": 420, "height": 860, "scale": 1},
            },
        )
        import base64

        (OUT / f"{tag}-sidebar.png").write_bytes(base64.b64decode(res["result"]["data"]))
        print("wrote", OUT / f"{tag}.png", "and", OUT / f"{tag}-sidebar.png")


if __name__ == "__main__":
    th = sys.argv[1]
    tg = sys.argv[2] if len(sys.argv) > 2 else f"ws-heading-{th}"
    pth = sys.argv[3] if len(sys.argv) > 3 else "/"
    asyncio.run(main(th, tg, pth))

"""Render-level proof: screenshot single pixels at each workspace heading and
check the plate is pure ink and the page around it is pure paper.

Usage: .venv/bin/python notes/_scratch/probe_ws_render_px.py <theme>
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/brendan/computer")
sys.path.insert(0, "/home/brendan/computer/downloads/_shots")
sys.path.insert(0, "/home/brendan/computer/notes/_scratch")
from audit import set_theme  # noqa: E402
from cdp import Browser  # noqa: E402
from pngpx import png1x1  # noqa: E402

APP = "http://127.0.0.1:4200"
JWT = Path("/tmp/jwt.txt").read_text().strip()

RECTS = """
JSON.stringify([...document.querySelectorAll('.ws-heading')].map(h => {
  const r = h.getBoundingClientRect();
  return {t: h.textContent.trim().slice(0, 22), x: r.x, y: r.y, w: r.width, h: r.height};
}))
"""


async def main(theme: str):
    print("PUT ->", set_theme(theme, JWT)[:60], "| theme:", theme)
    ink = (0, 0, 0) if theme == "bw" else (255, 255, 255)
    paper = (255, 255, 255) if theme == "bw" else (0, 0, 0)

    async with Browser(1200, 860) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(APP, settle=12)

        async def px(x, y):
            res = await b.send(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "clip": {"x": x, "y": y, "width": 1, "height": 1, "scale": 1},
                    "captureBeyondViewport": False,
                },
            )
            return png1x1(base64.b64decode(res["result"]["data"]))[:3]

        rows = json.loads(await b.js(RECTS))
        bad = []
        for idx in range(len(rows)):
            r = rows[idx]
            if r["y"] + r["h"] > 845:  # off-screen: scroll it into view first
                await b.js(
                    f"document.querySelectorAll('.ws-heading')[{idx}]"
                    ".scrollIntoView({block:'center'})"
                )
                await asyncio.sleep(0.5)
                rows = json.loads(await b.js(RECTS))
                r = rows[idx]
            in_plate = await px(int(r["x"]) + 3, int(r["y"] + r["h"] / 2))
            above = await px(int(r["x"]) + 3, int(r["y"]) - 3)
            name = await px(int(r["x"]) + int(r["w"] * 0.5), int(r["y"] + r["h"] * 0.62))
            ok = in_plate == ink and above == paper
            print(
                f"  {'OK ' if ok else 'BAD'} {r['t']:24} plate_px={in_plate} "
                f"above_px={above} textband_px={name} expect_ink={ink} expect_paper={paper}"
            )
            if not ok:
                bad.append(r["t"])
        print(f"summary: {len(rows) - len(bad)}/{len(rows)} rows ink-on-paper, bad={bad}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

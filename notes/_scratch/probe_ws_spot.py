"""One-off: what is that blue-ish pixel inside the android-webview-template
heading band? Reports elementFromPoint + a small pixel grid.

Usage: .venv/bin/python notes/_scratch/probe_ws_spot.py
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/brendan/computer/downloads/_shots")
sys.path.insert(0, "/home/brendan/computer/notes/_scratch")
from audit import set_theme  # noqa: E402
from cdp import Browser  # noqa: E402
from pngpx import png1x1  # noqa: E402

APP = "http://127.0.0.1:4200"
JWT = Path("/tmp/jwt.txt").read_text().strip()
NAME = "android-webview-template"


async def main():
    set_theme("bw", JWT)
    async with Browser(1200, 860) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(APP, settle=12)
        info = json.loads(
            await b.js(
                f"""
                (() => {{
                  const all = [...document.querySelectorAll('.ws-heading')];
                  const i = all.findIndex(h => h.textContent.includes({NAME!r}));
                  all[i].scrollIntoView({{block:'center'}});
                  return JSON.stringify({{i, total: all.length}});
                }})()
                """
            )
        )
        print("found:", info)
        await asyncio.sleep(0.6)
        probe = json.loads(
            await b.js(
                f"""
                (() => {{
                  const all = [...document.querySelectorAll('.ws-heading')];
                  const i = all.findIndex(h => h.textContent.includes({NAME!r}));
                  const r = all[i].getBoundingClientRect();
                  const x = Math.round(r.x + r.width * 0.5), y = Math.round(r.y + r.height * 0.62);
                  const at = document.elementFromPoint(x, y);
                  return JSON.stringify({{
                    rect: [r.x, r.y, r.width, r.height], x, y,
                    chain: (() => {{ const out = []; let e = at;
                      while (e && out.length < 6) {{
                        out.push(e.tagName + '.' + (e.className || '').toString().slice(0, 70)
                          + '|' + getComputedStyle(e).color + '|' + getComputedStyle(e).backgroundColor);
                        e = e.parentElement; }}
                      return out; }})(),
                    text: all[i].textContent.trim().slice(0, 40)
                  }});
                }})()
                """
            )
        )
        print(json.dumps(probe, indent=1)[:2200])

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

        x0, y0 = probe["x"], probe["y"]
        for dy in (-2, 0, 2):
            row = []
            for dx in (-6, -3, 0, 3, 6):
                row.append((dx, dy, await px(x0 + dx, y0 + dy)))
            print(row)


if __name__ == "__main__":
    asyncio.run(main())

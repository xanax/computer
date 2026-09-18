"""Robust monochrome audit: normalises ANY css colour (hex, oklab, color-mix...) via a
canvas, then reports every visible element whose paint is not exactly pure black or
pure white (i.e. grey or translucent)."""

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"

AUDIT = r"""
(() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const norm = (css) => {
    ctx.clearRect(0,0,1,1);
    try { ctx.fillStyle = css; } catch (e) { return null; }
    ctx.fillRect(0,0,1,1);
    const d = ctx.getImageData(0,0,1,1).data;
    return { r: d[0], g: d[1], b: d[2], a: +(d[3]/255).toFixed(3) };
  };
  const PROPS = ['backgroundColor','borderTopColor','borderBottomColor','color','outlineColor','textDecorationColor','fill','stroke'];
  const out = new Map();
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight ||
        r.right < 0 || r.left > innerWidth) continue;
    const cs = getComputedStyle(el);
    for (const prop of PROPS) {
      // A border colour is only visible if that side actually has a width.
      const side = prop === 'borderTopColor' ? 'borderTopWidth'
                 : prop === 'borderBottomColor' ? 'borderBottomWidth' : null;
      if (side && parseFloat(cs[side]) === 0) continue;
      const raw = cs[prop];
      if (!raw || raw === 'none') continue;
      const c = norm(raw);
      if (!c || c.a === 0) continue;
      const pure = (c.r===0&&c.g===0&&c.b===0) || (c.r===255&&c.g===255&&c.b===255);
      if (pure && c.a === 1) continue;
      if (!out.has(raw)) out.set(raw, {count:0, props:new Set(), alpha:c.a, rgb:[c.r,c.g,c.b], sample:[]});
      const e = out.get(raw); e.count++; e.props.add(prop);
      if (e.sample.length < 2) e.sample.push(el.tagName+'.'+String(el.className).slice(0,55)+' @'+JSON.stringify([Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]));
    }
  }
  const rows = [...out.entries()].sort((a,b)=>b[1].count-a[1].count)
    .map(([k,v])=>({color:k, alpha:v.alpha, rgb:v.rgb, count:v.count, props:[...v.props], sample:v.sample}));
  return JSON.stringify({distinct: rows.length, flaggedElements: rows.reduce((a,b)=>a+b.count,0), rows: rows.slice(0,22)});
})()
"""


def set_theme(theme: str, jwt: str):
    req = urllib.request.Request(
        f"{APP}/api/state/preferences",
        data=json.dumps({"theme": theme, "appearance": {"theme": theme}}).encode(),
        method="PUT",
        headers={"Content-Type": "application/json", "Cookie": f"cptr_session={jwt}"},
    )
    with urllib.request.urlopen(req) as r:
        return r.read().decode()


async def main(theme: str, tag: str, settings: bool, path: str = "/"):
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    print("PUT ->", set_theme(theme, jwt), "| theme:", theme)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}{path}", settle=10)
        if settings:
            await b.js(
                "window.dispatchEvent(new CustomEvent('cptr:open-settings',{detail:{tab:'appearance'}}));true"
            )
            await b.sleep(5)
        await b.shot(f"downloads/_shots/{tag}.png")
        print((await b.js(AUDIT))[:3500])


if __name__ == "__main__":
    theme = sys.argv[1]
    tag = sys.argv[2]
    settings = len(sys.argv) > 3 and sys.argv[3] == "settings"
    path = sys.argv[4] if len(sys.argv) > 4 else "/"
    asyncio.run(main(theme, tag, settings, path))

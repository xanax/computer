"""Set a theme preference, capture screenshots, and report the rendered colour palette."""

from __future__ import annotations

import asyncio
import collections
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"
OUT = Path(__file__).parent


def set_pref(theme: str, jwt: str):
    body = json.dumps({"theme": theme, "appearance": {"theme": theme}}).encode()
    req = urllib.request.Request(
        f"{APP}/api/state/preferences",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json", "Cookie": f"cptr_session={jwt}"},
    )
    with urllib.request.urlopen(req) as r:
        return r.read().decode()


PALETTE_JS = r"""
(() => {
  const counts = new Map();
  const add = (c) => counts.set(c, (counts.get(c) || 0) + 1);
  const parse = (s) => {
    if (!s || s === 'rgba(0, 0, 0, 0)' || s === 'transparent') return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    const a = p.length > 3 ? p[3] : 1;
    if (a === 0) return null;
    return [p[0], p[1], p[2], a];
  };
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const cs = getComputedStyle(el);
    for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'borderBottomColor']) {
      const px = parse(cs[prop]);
      if (px) add('rgb(' + px[0] + ',' + px[1] + ',' + px[2] + ')@' + px[3]);
    }
  }
  const out = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  return JSON.stringify({
    root: {
      appBg: getComputedStyle(document.documentElement).getPropertyValue('--app-bg').trim(),
      appFg: getComputedStyle(document.documentElement).getPropertyValue('--app-fg').trim(),
      classes: document.documentElement.className,
      colorScheme: document.documentElement.style.colorScheme,
    },
    distinct: out.length,
    top: out.slice(0, 40),
  });
})()
"""

# In bw modes every rendered colour must be pure black or pure white.
GREY_JS = r"""
(() => {
  const bad = [];
  const parse = (s) => {
    if (!s || s === 'rgba(0, 0, 0, 0)' || s === 'transparent') return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const pure = (c) => (c.r === 0 && c.g === 0 && c.b === 0) || (c.r === 255 && c.g === 255 && c.b === 255);
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const cs = getComputedStyle(el);
    for (const prop of ['color', 'backgroundColor', 'borderTopColor']) {
      const c = parse(cs[prop]);
      if (!c) continue;
      if (c.a < 1) {
        bad.push({ sel: el.tagName + '.' + String(el.className).slice(0, 60), prop, val: cs[prop], why: 'alpha' });
      } else if (!pure(c)) {
        bad.push({ sel: el.tagName + '.' + String(el.className).slice(0, 60), prop, val: cs[prop], why: 'grey' });
      }
    }
  }
  const uniq = new Map();
  for (const b of bad) uniq.set(b.why + b.val + b.prop, b);
  return JSON.stringify({ total: bad.length, samples: [...uniq.values()].slice(0, 25) });
})()
"""


async def main(theme: str, tag: str, width: int = 1400, height: int = 900, pages: str = "both"):
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    print("PUT preferences ->", set_pref(theme, jwt))

    async with Browser(width=width, height=height) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=6)

        await b.shot(str(OUT / f"{tag}-main.png"))
        print("palette(main) ", (await b.js(PALETTE_JS))[:900])
        print("greys(main)   ", (await b.js(GREY_JS))[:2000])

        if pages in ("both", "settings"):
            await b.js(
                "window.dispatchEvent(new CustomEvent('cptr:open-settings', {detail:{tab:'appearance'}})); true"
            )
            await b.sleep(2.5)
            await b.shot(str(OUT / f"{tag}-appearance.png"))
            print("greys(sett)   ", (await b.js(GREY_JS))[:2000])


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else "bw"
    tag = sys.argv[2] if len(sys.argv) > 2 else theme
    pages = sys.argv[3] if len(sys.argv) > 3 else "both"
    asyncio.run(main(theme, tag, pages=pages))

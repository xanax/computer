"""Report theme vars + plate/name colours for one theme.

Usage: .venv/bin/python notes/_scratch/probe_ws_vars.py <theme>
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/brendan/computer/downloads/_shots")
from audit import set_theme  # noqa: E402
from cdp import Browser  # noqa: E402

APP = "http://127.0.0.1:4200"
JWT = Path("/tmp/jwt.txt").read_text().strip()

JS = r"""
(() => {
  const root = document.documentElement;
  const cs = getComputedStyle(root);
  const h = document.querySelector('.ws-heading');
  const name = h.querySelector('span.min-w-0');
  const a = h.querySelector('a');
  const g = (el) => { const c = getComputedStyle(el); return { color: c.color, bg: c.backgroundColor, border: c.borderColor }; };
  return JSON.stringify({
    cls: root.className,
    vars: { '--app-bg': cs.getPropertyValue('--app-bg').trim(), '--app-fg': cs.getPropertyValue('--app-fg').trim(),
            '--app-fg-muted': cs.getPropertyValue('--app-fg-muted').trim() },
    aside: g(document.querySelector('aside.sidebar')),
    heading: g(h),
    anchor: g(a),
    name: g(name),
    nameClasses: name.className,
    iconWrap: h.querySelector('svg') ? g(h.querySelector('svg')) : null
  });
})()
"""


async def main(theme: str):
    set_theme(theme, JWT)
    async with Browser(1200, 860) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(APP, settle=10)
        print(theme, "->", json.dumps(json.loads(await b.js(JS)), indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

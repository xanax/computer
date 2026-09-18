"""Inject a real unread badge into a workspace heading (and a control copy outside
the plate) and read back the computed colours — verifies the paper-chip rules and
their specificity against the mono neutralisation block.

Usage: .venv/bin/python notes/_scratch/probe_ws_badge.py <theme>
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
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const norm = (css) => {
    ctx.clearRect(0,0,1,1);
    try { ctx.fillStyle = css; } catch (e) { return 'n/a'; }
    ctx.fillRect(0,0,1,1);
    const d = ctx.getImageData(0,0,1,1).data;
    return `rgb(${d[0]},${d[1]},${d[2]})@${(d[3]/255).toFixed(2)}`;
  };
  const CLS = "ws-unread inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300";
  const scope = [...document.querySelector('.ws-heading').classList].find(c => c.startsWith('svelte-'));
  const mk = () => { const s = document.createElement('span'); s.className = CLS; s.textContent = '3'; if (scope) s.classList.add(scope); return s; };
  const head = document.querySelector('.ws-heading');
  const inPlate = mk();
  head.querySelector('a').appendChild(inPlate);
  const outside = mk();
  document.body.appendChild(outside);
  const read = (el) => { const cs = getComputedStyle(el); return { bg: norm(cs.backgroundColor), color: norm(cs.color) }; };
  return JSON.stringify({
    theme: document.documentElement.className,
    scope,
    plate: read(inPlate),
    bodyControl: read(outside),
    plateBg: norm(getComputedStyle(head).backgroundColor),
    plateText: norm(getComputedStyle(head.querySelector('span.min-w-0')).color)
  });
})()
"""


async def main(theme: str):
    print("PUT ->", set_theme(theme, JWT)[:80], "| theme:", theme)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(APP, settle=10)
        print(json.dumps(json.loads(await b.js(JS)), indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

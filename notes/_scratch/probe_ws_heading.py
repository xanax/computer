"""Probe the sidebar workspace headings: plate colours per theme + mono grey audit.

Usage: .venv/bin/python notes/_scratch/probe_ws_heading.py <theme> [tag] [settings]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/brendan/computer/downloads/_shots")
from audit import AUDIT, set_theme  # noqa: E402
from cdp import Browser  # noqa: E402

APP = "http://127.0.0.1:4200"
JWT = Path("/tmp/jwt.txt").read_text().strip()

PROBE = r"""
(() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const norm = (css) => {
    if (!css) return null;
    ctx.clearRect(0,0,1,1);
    try { ctx.fillStyle = css; } catch (e) { return null; }
    ctx.fillRect(0,0,1,1);
    const d = ctx.getImageData(0,0,1,1).data;
    return [d[0], d[1], d[2], +(d[3]/255).toFixed(3)];
  };
  const s = (css) => JSON.stringify(css);
  const hs = [...document.querySelectorAll('.ws-heading')];
  return s({
    mono: document.documentElement.className,
    count: hs.length,
    rows: hs.map(h => {
      const cs = getComputedStyle(h);
      const name = h.querySelector('span.min-w-0');
      const badge = h.querySelector('.ws-unread');
      const chev = h.querySelector('.ws-icon-chevron');
      const act = h.querySelector('.ws-heading-action');
      const svg = h.querySelector('svg');
      const r = h.getBoundingClientRect();
      return {
        text: name ? name.textContent.trim() : null,
        current: h.classList.contains('ws-heading-current'),
        rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
        plateBg: norm(cs.backgroundColor),
        plateColor: norm(cs.color),
        border: norm(cs.borderTopColor),
        borderWidth: cs.borderTopWidth,
        nameColor: name ? norm(getComputedStyle(name).color) : null,
        badgeBg: badge ? norm(getComputedStyle(badge).backgroundColor) : null,
        badgeColor: badge ? norm(getComputedStyle(badge).color) : null,
        chevron: chev ? norm(getComputedStyle(chev).color) : null,
        action: act ? norm(getComputedStyle(act).color) : null,
        iconStroke: svg ? norm(getComputedStyle(svg).stroke) : null
      };
    })
  });
})()
"""


async def main(theme: str, tag: str, settings: bool, path: str = "/"):
    print("PUT ->", set_theme(theme, JWT)[:160], "| theme:", theme, "| path:", path)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", JWT)
        await b.goto(f"{APP}{path}", settle=12)
        if settings:
            await b.js(
                "window.dispatchEvent(new CustomEvent('cptr:open-settings',{detail:{tab:'appearance'}}));true"
            )
            await b.sleep(4)
        rows = await b.js(PROBE)
        data = json.loads(rows)
        print("html class:", data["mono"], "| headings found:", data["count"])
        for r in data["rows"]:
            print(
                f"  {r['text'][:26]:28} cur={str(r['current']):5} "
                f"plate={r['plateBg']} name={r['nameColor']} border={r['border']} "
                f"badge={r['badgeBg']}/{r['badgeColor']} chev={r['chevron']} "
                f"act={r['action']} icon={r['iconStroke']} rect={r['rect']}"
            )
        if theme.startswith("bw"):
            print("AUDIT:", (await b.js(AUDIT))[:2600])
        await b.shot(f"/home/brendan/computer/downloads/_shots/{tag}.png")


if __name__ == "__main__":
    th = sys.argv[1]
    tg = sys.argv[2] if len(sys.argv) > 2 else f"ws-heading-{th}"
    st = len(sys.argv) > 3 and sys.argv[3] == "settings"
    pth = sys.argv[4] if len(sys.argv) > 4 else "/"
    asyncio.run(main(th, tg, st, pth))

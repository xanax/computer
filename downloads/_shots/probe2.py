import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"

PROBE = r"""
(() => {
  const info = [];
  const pts = [[100,450],[700,450],[1200,450],[700,120],[700,850]];
  for (const [x,y] of pts) {
    const el = document.elementFromPoint(x,y);
    info.push({pt:[x,y], hit: el ? el.tagName+'.'+String(el.className).slice(0,70) : null,
               bg: el ? getComputedStyle(el).backgroundColor : null,
               color: el ? getComputedStyle(el).color : null});
  }
  // every visible element with a non-opaque or grey paint
  const bad = [];
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null;
      const p = m[1].split(',').map(parseFloat); return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1}; };
    for (const prop of ['backgroundColor','borderTopColor','color']) {
      const c = parse(cs[prop]);
      if (!c) continue;
      const pure = (c.r===0&&c.g===0&&c.b===0)||(c.r===255&&c.g===255&&c.b===255);
      if (c.a < 1 || !pure) {
        bad.push({sel: el.tagName+'.'+String(el.className).slice(0,70), prop, val: cs[prop],
                  box:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]});
      }
    }
  }
  return JSON.stringify({points: info, badCount: bad.length, bad: bad.slice(0,20)});
})()
"""


async def main():
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=12)
        await b.shot("downloads/_shots/late-main.png")
        print("MAIN:", (await b.js(PROBE))[:2500])
        await b.js("window.dispatchEvent(new CustomEvent('cptr:open-settings',{detail:{tab:'appearance'}}));true")
        await b.sleep(6)
        await b.shot("downloads/_shots/late-appearance.png")
        print("\nSETTINGS:", (await b.js(PROBE))[:2500])


asyncio.run(main())

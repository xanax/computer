import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"

Q = r"""
(() => {
  const pts = [[548,350],[548,300],[548,410],[520,320],[580,360]];
  const out = pts.map(([x,y]) => {
    const el = document.elementFromPoint(x,y);
    if (!el) return {x,y,hit:null};
    let chain = [], n = el;
    while (n && chain.length < 5) { chain.push(n.tagName+'.'+String(n.className).slice(0,50)); n = n.parentElement; }
    const cs = getComputedStyle(el);
    return {x, y, hit: chain[0], chain, bg: cs.backgroundColor, color: cs.color,
            src: el.getAttribute && (el.getAttribute('src')||'').slice(0,90),
            text: (el.textContent||'').trim().slice(0,60),
            rect: (()=>{const r=el.getBoundingClientRect(); return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];})()};
  });
  return JSON.stringify(out, null, 1);
})()
"""


async def main():
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=10)
        print(await b.js(Q))


asyncio.run(main())

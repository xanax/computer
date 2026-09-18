import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"

JS = r"""
(() => {
  const html = document.documentElement;
  const cs = getComputedStyle(html);
  const prose = document.querySelector('.prose');
  const pcs = prose ? getComputedStyle(prose) : null;
  const strong = document.querySelector('.prose strong');
  const probe = (el, names) => Object.fromEntries(names.map(n => [n, getComputedStyle(el).getPropertyValue(n).trim()]));
  return JSON.stringify({
    classes: html.className,
    colorScheme: cs.colorScheme,
    root: probe(html, ['--app-bg','--app-fg','--app-fg-muted','--app-border','--app-hover','--color-gray-700','--color-gray-900','--color-gray-50','--tw-prose-body','--tw-prose-bold','--tw-prose-headings','--tw-prose-links']),
    prose: prose ? { color: pcs.color, cls: prose.className, vars: probe(prose, ['--tw-prose-body','--tw-prose-bold']) } : null,
    strong: strong ? { color: getComputedStyle(strong).color, cls: strong.className } : null,
    slider: (() => { const s = document.querySelector('.slider'); if (!s) return null; const c = getComputedStyle(s); return { cls: s.className, bg: c.backgroundColor, bgImage: c.backgroundImage.slice(0,120), opacity: c.opacity }; })(),
  }, null, 1);
})()
"""

async def main():
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=8)
        print(await b.js(JS))

asyncio.run(main())

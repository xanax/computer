"""Find which CSS rules give an element its border colour (mono debugging).

    python3 downloads/_shots/probe_border.py <theme> "<css selector or text match>"
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import APP, set_theme
from cdp import Browser

PROBE = r"""
(() => {
  const needle = __NEEDLE__;
  const el = [...document.querySelectorAll('*')].find(
    e => String(e.className).includes(needle)
  );
  if (!el) return 'element not found';
  const cs = getComputedStyle(el);
  const out = {
    cls: String(el.className).slice(0, 200),
    borderTopColor: cs.borderTopColor,
    borderTopWidth: cs.borderTopWidth,
    bg: cs.backgroundColor,
    html: el.outerHTML.slice(0, 120),
    rules: []
  };
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const r of rules) {
      if (!r.selectorText || !r.style) continue;
      const css = r.cssText;
      if (!/border(-[a-z]+)?-color|border-color|border:|border-bottom/.test(css)) continue;
      let matches = false;
      try { matches = el.matches(r.selectorText); } catch (e) { matches = false; }
      if (!matches) continue;
      out.rules.push(r.selectorText + ' {' +
        (r.style.getPropertyValue('border-color') ? ' border-color:' + r.style.getPropertyValue('border-color') : '') +
        (r.style.getPropertyValue('border-bottom-color') ? ' border-bottom-color:' + r.style.getPropertyValue('border-bottom-color') : '') +
        (r.style.getPropertyValue('border-top-color') ? ' border-top-color:' + r.style.getPropertyValue('border-top-color') : '') +
        (r.style.getPropertyValue('border-bottom-width') ? ' border-bottom-width:' + r.style.getPropertyValue('border-bottom-width') : '') +
        ' }');
    }
  }
  out.rules = out.rules.slice(-14);
  return JSON.stringify(out, null, 1);
})()
"""


async def main(theme: str, needle: str, prelude: str):
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    print("PUT ->", set_theme(theme, jwt), "| theme:", theme)
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=10)
        if prelude:
            await b.js(prelude)
            await b.sleep(6)
        print((await b.js(PROBE.replace("__NEEDLE__", repr(needle))))[:4000])


if __name__ == "__main__":
    theme = sys.argv[1]
    needle = sys.argv[2]
    prelude = sys.argv[3] if len(sys.argv) > 3 else ""
    asyncio.run(main(theme, needle, prelude))

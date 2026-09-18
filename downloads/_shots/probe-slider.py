import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"
JS = r"""
(() => {
  const s = document.querySelector('.slider');
  if (!s) return 'no .slider';
  const chain = [];
  for (let el = s; el && chain.length < 5; el = el.parentElement) chain.push(el.tagName + '.' + String(el.className).slice(0, 60));
  const rules = [];
  for (const sheet of document.styleSheets) {
    let list; try { list = sheet.cssRules; } catch (e) { continue; }
    for (const r of list) {
      if (r.selectorText && r.selectorText.includes('slider')) rules.push((sheet.href ? sheet.href.split('/').pop() : 'inline') + ' :: ' + r.selectorText + ' { ' + r.style.cssText.slice(0, 90) + ' }');
    }
  }
  return JSON.stringify({ chain, rules: rules.slice(0, 12), cls: document.documentElement.className }, null, 1);
})()
"""
async def main():
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=8)
        print(await b.js(JS))
asyncio.run(main())

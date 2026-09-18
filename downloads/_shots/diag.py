import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import Browser

APP = "http://127.0.0.1:4200"


async def main():
    jwt = Path("/tmp/jwt.txt").read_text().strip()
    async with Browser(1400, 900) as b:
        await b.set_cookie("cptr_session", jwt)
        await b.goto(f"{APP}/", settle=4)
        for i in range(6):
            info = await b.js(
                """JSON.stringify({
                    url: location.href,
                    title: document.title,
                    ready: document.readyState,
                    elements: document.querySelectorAll('*').length,
                    htmlLen: document.body.innerHTML.length,
                    text: document.body.innerText.slice(0, 200),
                    htmlClass: document.documentElement.className,
                    bodyBg: getComputedStyle(document.body).backgroundColor,
                    dpr: window.devicePixelRatio,
                    vw: innerWidth, vh: innerHeight
                })"""
            )
            print(i, info)
            await b.shot(f"downloads/_shots/diag-{i}.png")
            await b.sleep(3)


asyncio.run(main())

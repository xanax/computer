"""Minimal Chrome DevTools Protocol driver for headless screenshots of the local app."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import urllib.request

import websockets

CHROME = "/home/brendan/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
LIBS = "/tmp/chromelibs/root/usr/lib/x86_64-linux-gnu"
PORT = 9333
PROFILE = "/tmp/chromeprofile"


class Browser:
    def __init__(self, width: int = 1400, height: int = 900):
        self.width = width
        self.height = height
        self.proc: subprocess.Popen | None = None
        self.ws = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.events: list[dict] = []

    async def __aenter__(self):
        env = dict(os.environ, LD_LIBRARY_PATH=LIBS)
        subprocess.run(["rm", "-rf", PROFILE], check=False)
        self.proc = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--disable-extensions",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--remote-debugging-port={PORT}",
                f"--user-data-dir={PROFILE}",
                f"--window-size={self.width},{self.height}",
                "about:blank",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ws_url = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1) as r:
                    targets = json.load(r)
                pages = [t for t in targets if t["type"] == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)
        if not ws_url:
            raise RuntimeError("chrome did not start")
        self.ws = await websockets.connect(ws_url, max_size=100 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        await self.send("Page.enable")
        await self.send("Network.enable")
        await self.send("Runtime.enable")
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False},
        )
        return self

    async def __aexit__(self, *exc):
        try:
            if self.ws:
                await self.ws.close()
        finally:
            if self._reader:
                self._reader.cancel()
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.kill()

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._pending:
                    self._pending.pop(msg["id"]).set_result(msg)
                else:
                    self.events.append(msg)
        except Exception:
            pass

    async def send(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=60)

    async def set_cookie(self, name: str, value: str, url: str = "http://127.0.0.1:4200/"):
        await self.send("Network.setCookie", {"name": name, "value": value, "url": url})

    async def goto(self, url: str, settle: float = 3.0):
        self.events.clear()
        await self.send("Page.navigate", {"url": url})
        await self.sleep(settle)

    async def sleep(self, secs: float):
        await asyncio.sleep(secs)

    async def js(self, expression: str):
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if res.get("result", {}).get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["result"]["exceptionDetails"])[:600])
        return res["result"]["result"].get("value")

    async def shot(self, path: str, full: bool = False):
        res = await self.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": full})
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["result"]["data"]))
        return path

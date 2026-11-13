"""The robot's own Chrome: a real installed Chrome with a separate profile (logins stay there), driven over CDP.
Started on first use and left running, so the windows can be watched and a human can sign in."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]


def find_chrome(cfg: dict) -> str:
    for c in [cfg.get("chrome")] + CANDIDATES + [shutil.which("chrome"), shutil.which("google-chrome")]:
        if c and Path(c).exists():
            return c
    raise RuntimeError("Chrome not found: set \"chrome\" in config.json")


def cdp_up(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return True
    except OSError:
        return False


def launch_args(cfg: dict) -> list[str]:
    args = [find_chrome(cfg), f"--user-data-dir={cfg['profile']}", f"--remote-debugging-port={cfg['cdp_port']}",
            "--no-first-run", "--no-default-browser-check", "--window-size=1280,900"]
    if cfg.get("proxy"):
        args.append(f"--proxy-server={cfg['proxy']}")
    if cfg.get("headless"):
        args.append("--headless=new")
    return args + ["about:blank"]


class Session:
    """One connection to the robot Chrome, shared by all asks of a process. Tabs: one per (site, thread)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._pw = None
        self.browser: Browser | None = None
        self.tabs: dict[str, Page] = {}
        self._lock = asyncio.Lock()
        self.proc: subprocess.Popen | None = None   # set when this session started Chrome itself

    async def context(self) -> BrowserContext:
        async with self._lock:
            if self.browser is None or not self.browser.is_connected():
                port = self.cfg["cdp_port"]
                if not cdp_up(port):
                    Path(self.cfg["profile"]).mkdir(parents=True, exist_ok=True)
                    self.proc = subprocess.Popen(launch_args(self.cfg), stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL)
                    for _ in range(60):
                        await asyncio.sleep(0.5)
                        if cdp_up(port):
                            break
                    else:
                        raise RuntimeError(f"Chrome did not open the debugging port {port}")
                self._pw = self._pw or await async_playwright().start()
                self.browser = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                self.tabs = {}
            return self.browser.contexts[0]

    async def tab(self, key: str, url: str | None = None) -> Page:
        """The tab for this key (reused while open); else an open tab already showing `url` (left by an earlier
        run); else a new one. The oldest robot tabs close past max_tabs."""
        ctx = await self.context()
        page = self.tabs.get(key)
        if page is None or page.is_closed():
            norm = lambda u: (u or "").split("?")[0].rstrip("/")   # noqa: E731
            taken = set(map(id, self.tabs.values()))
            page = next((p for p in ctx.pages if url and norm(p.url) == norm(url) and id(p) not in taken), None)
            page = page or await ctx.new_page()
            self.tabs[key] = page
            while len(self.tabs) > self.cfg["max_tabs"]:
                old = next(iter(self.tabs))
                p = self.tabs.pop(old)
                if not p.is_closed():
                    await p.close()
        return page

    async def close(self, kill: bool = False) -> None:
        """Disconnect; the Chrome window stays open with its logins unless kill=True (and we started it)."""
        if self._pw:
            await self._pw.stop()
        self._pw = self.browser = None
        if kill and self.proc:
            if os.name == "nt":   # Chrome's child processes keep the profile locked unless the whole tree goes
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

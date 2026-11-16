"""Drive one web chat like an API: open (new chat or a saved conversation), pick the strongest model and the highest
reasoning level, attach files, send, wait until the answer is finished, read it back as markdown."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path

from playwright.async_api import Locator, Page

from . import threads as T
from . import usage as U
from .browser import Session
from .config import PKG

JS_MD = (PKG / "js" / "to_markdown.js").read_text(encoding="utf-8")
JS_HOOK = (PKG / "js" / "hook_copy.js").read_text(encoding="utf-8")
JS_MARK = (PKG / "js" / "mark_copy.js").read_text(encoding="utf-8")
LIMIT_WORDS = re.compile(r"(rate limit|too many requests|usage limit|limit reached|try again later|лимит|слишком много"
                         r"|превышен|上限|次数已用完)", re.I)
CAPTCHA_CSS = ("iframe[src*='captcha'], iframe[src*='challenges.cloudflare.com'], iframe[src*='hcaptcha'], "
               "iframe[src*='recaptcha'], [id*='captcha' i], [class*='captcha' i]")
# what covers the middle of the window (a modal), not text inside chat messages, decides
JS_CENTER = """(pat) => { const re = new RegExp(pat, 'i');
  let e = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
  for (let i = 0; e && i < 8; i++, e = e.parentElement) {
    const st = getComputedStyle(e);
    if ((st.position === 'fixed' || +st.zIndex > 10 || e.getAttribute('role') === 'dialog') && re.test(e.innerText || ''))
      return true;
  }
  return false; }"""
CAPTCHA_TEXT = re.compile(r"(security verification|verify you are human|i'?m not a robot|captcha|drag the slider"
                          r"|подтвердите, что вы не робот|проверка безопасности|安全验证|拖动滑块)", re.I)
LOGIN_WORDS = re.compile(r"(sign in|log in|login|sign up|войти|вход|регистр|登录|注册|year were you born|году вы родились"
                         r"|confirm your age|подтвердите свой возраст)", re.I)


class WebChatError(RuntimeError):
    """kind: login (a human must sign in or answer a dialog in the window), limit, timeout, selector, input."""

    def __init__(self, kind: str, message: str, shot: str | None = None):
        super().__init__(f"{kind}: {message}" + (f" (screenshot: {shot})" if shot else ""))
        self.kind, self.shot = kind, shot


async def visible(loc: Locator) -> bool:
    try:
        return await loc.first.is_visible()
    except Exception:  # noqa: BLE001 - detached/invalid while the page re-renders
        return False


async def usable(loc: Locator) -> bool:
    """Visible, enabled and not greyed out (menus show locked models dimmed rather than disabled)."""
    if not await visible(loc):
        return False
    el = loc.first
    try:
        # class tokens like "is-disabled"/"item--locked" count; Tailwind variants ("aria-disabled:cursor-…") do not
        return await el.evaluate("""e => !e.disabled && e.getAttribute('aria-disabled') !== 'true'
            && e.getAttribute('data-disabled') === null
            && ![...e.classList].some(c => !c.includes(':') && /(^|[-_])(disabled|locked)$/.test(c))
            && +getComputedStyle(e).opacity >= 0.6
            && (!e.parentElement || +getComputedStyle(e.parentElement).opacity >= 0.6)""")
    except Exception:  # noqa: BLE001
        return False


class Chat:
    def __init__(self, session: Session, site: str):
        cfg = session.cfg
        if site not in cfg["site_defs"]:
            raise WebChatError("input", f"unknown site {site!r}; known: {', '.join(cfg['site_defs'])}")
        self.session, self.cfg, self.key = session, cfg, site
        self.s = cfg["site_defs"][site]
        self.page: Page | None = None
        self.captcha_waited = 0.0   # seconds spent waiting for a person to solve a captcha (kept off the timers)

    # ---------- navigation and checks ----------
    async def shot(self, tag: str) -> str | None:
        try:
            d = Path(self.cfg["shots"])
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{self.key}-{tag}-{time.strftime('%m%d-%H%M%S')}.png"
            await self.page.bring_to_front()
            await self.page.screenshot(path=str(path), timeout=15000)
            return str(path)
        except Exception:  # noqa: BLE001
            return None

    async def captcha_up(self) -> bool:
        return bool(await visible(self.page.locator(CAPTCHA_CSS)) or
                    await self.page.evaluate(JS_CENTER, CAPTCHA_TEXT.pattern))

    async def wall(self) -> str | None:
        """Text of the first login wall / blocking dialog that is showing, else None.
        Closable nags listed in the site's `dismiss` (e.g. a "sign in for more" popup with an X) are closed first;
        that only presses the popup's own close button, it never signs in, accepts terms or answers anything."""
        for d in self.s.get("dismiss", []):
            loc = self.page.locator(d)
            try:
                if await visible(loc):
                    await loc.first.click(timeout=3000)
                    await asyncio.sleep(0.5)
            except Exception:  # noqa: BLE001 - popup went away by itself
                pass
        for w in self.s.get("login_wall", []):
            if w.startswith("url:"):
                if w[4:] in self.page.url:
                    return f"page went to {self.page.url}"
            elif await visible(self.page.locator(w)):
                return f"page shows {w!r}"
        if await self.captcha_up():
            # a person may solve it in the robot window: bring the tab forward and wait for them, never touch it
            wait = float(self.cfg.get("captcha_wait", 0))
            if wait > 0:
                await self.page.bring_to_front()
                t0 = time.time()
                while time.time() - t0 < wait and await self.captcha_up():
                    await asyncio.sleep(2)
                self.captcha_waited += time.time() - t0
            if await self.captcha_up():
                raise WebChatError("captcha", f"{self.s['name']}: the site shows a human check (captcha). A person must "
                                              "solve it in the robot window; the robot never does", await self.shot("captcha"))
        dialogs = self.page.locator("[role=dialog]:visible, [role=alertdialog]:visible")
        try:
            for i in range(min(await dialogs.count(), 3)):
                text = re.sub(r"\s+", " ", await dialogs.nth(i).inner_text(timeout=2000)).strip()
                if LOGIN_WORDS.search(text):
                    return f"dialog: {text[:200]}"
        except Exception:  # noqa: BLE001 - dialog vanished while reading
            pass
        return None

    async def open(self, tab_key: str, url: str | None) -> bool:
        """Open a saved conversation (url) or a fresh chat. Returns True when a new chat was opened."""
        self.page = await self.session.tab(tab_key, url)
        target = url or self.s["home"]
        same = url and self.page.url.split("?")[0].rstrip("/") == url.split("?")[0].rstrip("/")
        if not same:
            await self.page.goto(target, wait_until="domcontentloaded", timeout=60000)
        inp = self.page.locator(self.s["input"])
        for _ in range(60):
            if await visible(inp):
                break
            if why := await self.wall():
                raise WebChatError("login", f"{self.s['name']}: {why}. Sign in / answer it in the robot window, "
                                            "then ask again", await self.shot("login"))
            await asyncio.sleep(0.5)
        else:
            raise WebChatError("selector", f"{self.s['name']}: no input box {self.s['input']!r}", await self.shot("noinput"))
        if url and self.s.get("chat_url") and self.s["chat_url"] not in self.page.url:
            return True   # the saved conversation is gone: the site fell back to a new chat
        return url is None

    async def _first(self, sels: list[str], check, secs: float):
        """First locator among sels passing check, polling up to secs (pages and menus render late)."""
        t0 = time.time()
        while True:
            for sel in sels:
                loc = self.page.locator(sel)
                if await check(loc):
                    return loc
            if time.time() - t0 >= secs:
                return None
            await asyncio.sleep(0.4)

    async def run_steps(self, steps: list[dict]) -> None:
        for st in steps:
            secs = 1.2 if st.get("optional") else 10.0
            try:
                if "click" in st:
                    loc = await self._first([st["click"]], visible, secs)
                    if loc:
                        await loc.first.click()
                    elif not st.get("optional"):
                        raise WebChatError("selector", f"step {st}: not found")
                elif "pick" in st:
                    loc = await self._first(st["pick"], usable, secs)
                    if loc:
                        await loc.first.click()
                    elif not st.get("optional"):
                        raise WebChatError("selector", f"step {st}: no usable option")
                elif "key" in st:
                    if "on" in st:
                        loc = await self._first([st["on"]], visible, secs)
                        if loc:
                            await loc.first.focus()
                            await self.page.keyboard.press(st["key"])
                        elif not st.get("optional"):
                            raise WebChatError("selector", f"step {st}: not found")
                    else:
                        await self.page.keyboard.press(st["key"])
                elif "wait" in st:
                    await asyncio.sleep(st["wait"])
            except WebChatError:
                raise
            except Exception as e:  # noqa: BLE001 - a flaky menu must not kill the ask; the label tells what we got
                if not st.get("optional"):
                    raise WebChatError("selector", f"step {st}: {str(e)[:200]}") from None
            await asyncio.sleep(0.6)

    async def prepare(self, model: str | None) -> str | None:
        """Strongest model and max reasoning (the site's model_steps), or the named model. Returns the model label."""
        steps = [dict(st) for st in self.s.get("model_steps", [])]
        if model and self.s.get("model_option"):
            first_pick = next((st for st in steps if "pick" in st), None)
            if first_pick:
                first_pick["pick"] = [self.s["model_option"].replace("{name}", model)]
                first_pick.pop("optional", None)
        await self.run_steps(steps)
        return await self.label()

    async def label(self) -> str | None:
        """The model/mode label the page shows now (None when the site has no such label)."""
        if self.s.get("model_label"):
            loc = self.page.locator(self.s["model_label"])
            if await visible(loc):
                return re.sub(r"\s+", " ", (await loc.first.inner_text()).strip())[:60] or None
        return None

    # ---------- one turn ----------
    async def attach(self, files: list[str]) -> None:
        loc = self.page.locator(self.s.get("file_input") or "input[type=file]")
        if not await loc.count():
            raise WebChatError("input", f"{self.s['name']}: no file upload here (signed out?)", await self.shot("nofile"))
        await loc.first.set_input_files(files)
        await asyncio.sleep(1.5 + 1.0 * len(files))
        if why := await self.wall():
            raise WebChatError("login", f"{self.s['name']}: upload asks to sign in ({why})", await self.shot("login"))

    async def type_text(self, text: str) -> None:
        box = self.page.locator(self.s["input"]).first
        await box.click()
        if await box.evaluate("e => e.tagName === 'TEXTAREA' || e.tagName === 'INPUT'"):
            await box.fill(text)
        else:
            await self.page.keyboard.insert_text(text)

    async def submit(self) -> None:
        if self.s["send"] == "enter":
            await self.page.keyboard.press("Enter")
            return
        btn = self.page.locator(self.s["send"]).first
        for _ in range(240):   # uploads may keep the send button disabled for a while
            if why := await self.wall():
                raise WebChatError("login", f"{self.s['name']}: {why}", await self.shot("login"))
            if await btn.is_enabled():
                try:
                    await btn.click(timeout=10000)
                    return
                except Exception as e:  # noqa: BLE001 - something covers the button
                    raise WebChatError("selector", f"{self.s['name']}: cannot press send: {str(e).splitlines()[0]}",
                                       await self.shot("send")) from None
            await asyncio.sleep(0.5)
        raise WebChatError("timeout", f"{self.s['name']}: send button stayed disabled", await self.shot("send"))

    async def answer_count(self) -> int:
        return await self.page.locator(self.s["message"]).count()

    async def answer_md(self) -> str:
        return await self.page.evaluate(JS_MD, {"message": self.s["message"], "answer": self.s.get("answer"),
                                                "thinking": self.s.get("thinking")})

    async def wait_answer(self, before: int, timeout: float) -> str:
        """Done = a new assistant message exists, the stop button is gone and the text has not changed for `stable` s."""
        stop = self.page.locator(self.s["stop"]) if self.s.get("stop") else None
        t0, last, since, started = time.time(), None, time.time(), False
        while time.time() - t0 - self.captcha_waited < timeout:
            await asyncio.sleep(1.0)
            if why := await self.wall():
                raise WebChatError("login", f"{self.s['name']}: {why}. Sign in / answer it in the robot window, "
                                            "then ask again", await self.shot("login"))
            generating = bool(stop) and await visible(stop)
            if not started:
                started = generating or await self.answer_count() > before
                if not started:
                    if time.time() - t0 - self.captcha_waited > 90:
                        raise WebChatError("timeout", f"{self.s['name']}: no answer started in 90 s",
                                           await self.shot("nostart"))
                    continue
            text = await self.answer_md() if await self.answer_count() > before else ""
            if text != last:
                last, since = text, time.time()
            elif text and not generating and time.time() - since >= self.cfg["stable"]:
                return text
        raise WebChatError("timeout", f"{self.s['name']}: answer not finished in {timeout:.0f} s", await self.shot("timeout"))

    async def copied_md(self) -> str | None:
        """The answer through the site's own copy button (exact markdown), when it has one."""
        if not self.s.get("copy"):
            return None
        try:
            await self.page.evaluate(JS_HOOK)
            found = await self.page.evaluate(JS_MARK, {"message": self.s["message"], "answer": self.s.get("answer"),
                                                       "copy": self.s["copy"]})
            if not found:
                return None
            btn = self.page.locator("[data-wc-copy='1']").first
            await btn.scroll_into_view_if_needed()
            await btn.click()
            for _ in range(10):
                await asyncio.sleep(0.2)
                got = await self.page.evaluate("() => window.__wcCopied")
                if got:
                    return got
        except Exception:  # noqa: BLE001 - fall back to the DOM conversion
            return None
        return None


def _long_to_file(message: str, files: list[str]) -> tuple[str, list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="webchat-")) / "message.md"
    tmp.write_text(message, encoding="utf-8")
    return ("The full task is in the attached file message.md. Read it completely and answer it.", files + [str(tmp)])


async def ask(session: Session, site: str, message: str, files: list[str] | None = None, thread: str | None = None,
              new_chat: bool = False, model: str | None = None, timeout: float | None = None) -> dict:
    """One message to a web chat. thread: a name to keep talking in the same conversation (its URL is saved);
    new_chat: start over (also for a thread: its old conversation is replaced). Returns {answer, site, model, url,
    thread, new_chat, seconds}. Every ask, answered or not, goes to the usage log."""
    t0 = time.time()
    try:
        res = await _ask(session, site, message, files, thread, new_chat, model, timeout)
    except Exception as e:
        U.log(session.cfg, site=site, thread=thread, chars_in=len(message), files=len(files or []), chars_out=0,
              seconds=round(time.time() - t0, 1), kind=getattr(e, "kind", "error"))
        raise
    U.log(session.cfg, site=site, thread=thread, chars_in=len(message), files=len(files or []),
          chars_out=len(res["answer"]), seconds=res["seconds"], kind="ok")
    return res


# a site that is out of quota, wants a human or broke its layout: worth trying the next one
FALLBACK_KINDS = ("login", "captcha", "limit", "timeout", "selector")


async def ask_first(session: Session, sites: list[str], message: str, files: list[str] | None = None,
                    thread: str | None = None, new_chat: bool = False, model: str | None = None,
                    timeout: float | None = None) -> dict:
    """ask() on the first site that answers, in the given order. A thread that already lives on one site stays there
    (its context is in that chat). The result says which sites were skipped and why: {..., skipped: [{site, kind}]}."""
    t = T.load(session.cfg, thread) if thread and not new_chat else None
    if t:
        sites = [t["site"]]
    skipped = []
    for i, s in enumerate(sites):
        try:
            res = await ask(session, s, message, files, thread, new_chat, model, timeout)
            return res | {"skipped": skipped}
        except WebChatError as e:
            if e.kind not in FALLBACK_KINDS or i == len(sites) - 1:
                raise
            skipped.append({"site": s, "kind": e.kind, "error": str(e)[:200]})
    raise WebChatError("input", "no sites given")


def clip(cfg: dict, res: dict, max_chars: int | None) -> dict:
    """Keep an agent's context small: an answer longer than max_chars is saved whole to answers/ and only its head is
    returned, with answer_file (read the rest from there when needed) and answer_chars."""
    a = res.get("answer")
    if not a or not max_chars or len(a) <= max_chars:
        return res
    d = Path(cfg["answers"])
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%m%d-%H%M%S')}-{res.get('site', 'x')}-{os.getpid()}.md"
    p.write_text(a, encoding="utf-8")
    cut = a.rfind("\n", 0, max_chars)
    head = a[:cut if cut > max_chars // 2 else max_chars]
    return res | {"answer": head + f"\n\n[… cut at {len(head)} of {len(a)} characters; full answer: {p}]",
                  "answer_file": str(p), "answer_chars": len(a)}


async def _ask(session: Session, site: str, message: str, files: list[str] | None = None, thread: str | None = None,
               new_chat: bool = False, model: str | None = None, timeout: float | None = None) -> dict:
    cfg = session.cfg
    files = [str(Path(f).resolve()) for f in files or []]
    for f in files:
        if not Path(f).exists():
            raise WebChatError("input", f"no such file: {f}")
    if len(message) > cfg["long_message"] and cfg["site_defs"][site].get("file_input"):
        message, files = _long_to_file(message, files)
    t = T.load(cfg, thread) if thread else None
    if t and t["site"] != site:
        raise WebChatError("input", f"thread {thread!r} belongs to {t['site']}; use another thread name")
    url = None if new_chat or not t else t.get("url")
    chat = Chat(session, site)
    t0 = time.time()
    fresh = await chat.open(f"{site}:{thread or '_'}", url)
    label = await chat.prepare(model) if fresh or model else await chat.label()
    if files:
        await chat.attach(files)
    before = await chat.answer_count()
    await chat.type_text(message)
    await chat.submit()
    text = await chat.wait_answer(before, timeout or cfg["timeout"])
    exact = await chat.copied_md()
    answer = exact if exact and len(exact) >= 0.5 * len(text) else text
    if LIMIT_WORDS.search(answer[:300]) and len(answer) < 400:
        raise WebChatError("limit", f"{chat.s['name']}: {answer[:300]}")
    res = {"answer": answer, "site": site, "model": label, "url": chat.page.url, "thread": thread,
           "new_chat": fresh, "seconds": round(time.time() - t0, 1)}
    if thread:
        T.record(cfg, thread, site, chat.page.url, message, files, answer, label, reset=fresh)
    return res


async def council(session: Session, message: str, sites: list[str], files: list[str] | None = None,
                  thread: str | None = None, new_chat: bool = False) -> dict:
    """The same message to several sites at once, each in its own tab (and thread `<thread>-<site>`)."""
    async def one(s: str) -> dict:
        try:
            return await ask(session, s, message, files, f"{thread}-{s}" if thread else None, new_chat)
        except Exception as e:  # noqa: BLE001 - one site down must not cost the others' answers
            return {"answer": None, "site": s, "error": str(e)[:600], "kind": getattr(e, "kind", "error")}
    return dict(zip(sites, await asyncio.gather(*(one(s) for s in sites))))


async def status(session: Session, site: str) -> dict:
    """Open the site's home page and report whether it is usable (input visible, no login wall) + a screenshot."""
    chat = Chat(session, site)
    try:
        await chat.open(f"{site}:_status", None)
        ok, why = True, None
    except WebChatError as e:
        ok, why = False, str(e)
    return {"site": site, "ready": ok, "problem": why, "verified": chat.s.get("verified"),
            "screenshot": await chat.shot("status"), "url": chat.page.url if chat.page else None}


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1)

"""End-to-end against tests/fake_chat.html in a headless Chrome with a throwaway profile."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from webchat import config, engine, threads
from webchat.browser import Session, find_chrome

FAKE = (Path(__file__).parent / "fake_chat.html").resolve().as_uri()
SITE = {
    "name": "Fake", "home": FAKE, "chat_url": "#/c/", "input": "#in", "send": "#send", "stop": "#stop",
    "message": ".assistant", "answer": ".md", "thinking": ".think", "copy": "button[aria-label='Copy']",
    "file_input": "#file", "login_wall": [],
    "model_steps": [
        {"click": "#model"},
        {"pick": ["#menu button:has-text('Huge')", "#menu button:has-text('Big')", "#menu button:has-text('Small')"]},
        {"click": "#model"},
        {"key": "End", "on": "[role=slider]", "optional": True},
        {"click": "#model"},
    ],
    "model_label": "#model", "model_option": "#menu button:has-text('{name}')", "verified": "test",
}

try:
    find_chrome({})
    HAVE_CHROME = True
except RuntimeError:
    HAVE_CHROME = False
pytestmark = pytest.mark.skipif(not HAVE_CHROME, reason="needs an installed Chrome")


@pytest.fixture
async def session(tmp_path):
    """A fresh headless Chrome per test (own profile outside the pytest dir: Chrome holds file locks briefly)."""
    profile = tempfile.mkdtemp(prefix="webchat-test-")
    cfg = config.load() | {"profile": profile, "cdp_port": 9355, "proxy": None, "headless": True,
                           "threads": str(tmp_path / "threads"), "shots": str(tmp_path / "shots"), "stable": 1.0,
                           "timeout": 60}
    cfg["site_defs"] = {"fake": SITE}
    s = Session(cfg)
    yield s
    await s.close(kill=True)
    shutil.rmtree(profile, ignore_errors=True)


async def test_picks_strongest_usable_model_and_max_effort_and_reads_markdown(session):
    r = await engine.ask(session, "fake", "привет", thread="t1")
    assert r["model"] == "Big"                                  # Huge is greyed out, Big is the best usable
    assert "Answer 1 from **Big** effort 3" in r["answer"]      # slider pushed to the end
    assert "| q | len |" in r["answer"] and "```python" in r["answer"]   # whole-message copy, not the code's copy
    assert "thinking about" not in r["answer"]
    assert r["new_chat"] is True and "#/c/" in r["url"]


async def test_thread_continues_in_the_same_chat_and_new_chat_starts_over(session):
    r1 = await engine.ask(session, "fake", "один", thread="t2")
    r2 = await engine.ask(session, "fake", "два", thread="t2")
    assert r2["url"] == r1["url"] and r2["new_chat"] is False and "Answer 2" in r2["answer"]
    r3 = await engine.ask(session, "fake", "три", thread="t2", new_chat=True)
    assert r3["url"] != r1["url"] and "Answer 1" in r3["answer"]
    saved = threads.load(session.cfg, "t2")
    assert saved["url"] == r3["url"] and r1["url"] in saved["chats"] and len(saved["turns"]) == 1


async def test_files_are_uploaded(session, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    r = await engine.ask(session, "fake", "что в файле", files=[str(f)])
    assert "files: data.csv" in r["answer"]


async def test_named_model_is_used(session):
    r = await engine.ask(session, "fake", "x", model="Small")
    assert r["model"] == "Small" and "from **Small**" in r["answer"]


async def test_login_dialog_and_captcha_stop_with_a_screenshot(session):
    with pytest.raises(engine.WebChatError) as e:
        await engine.ask(session, "fake", "LOGIN", timeout=20)
    assert e.value.kind == "login" and e.value.shot and Path(e.value.shot).exists()
    with pytest.raises(engine.WebChatError) as e:
        await engine.ask(session, "fake", "CAPTCHA", timeout=20)
    assert e.value.kind == "captcha"


async def test_council_returns_each_site_even_when_one_fails(session):
    res = await engine.council(session, "вопрос", ["fake", "nope"])
    assert "Answer 1" in res["fake"]["answer"] and res["nope"]["answer"] is None and "unknown site" in res["nope"]["error"]


def test_long_message_goes_as_a_file():
    msg, files = engine._long_to_file("x" * 10, [])
    assert "message.md" in msg and Path(files[0]).read_text(encoding="utf-8") == "x" * 10


def test_sites_json_is_complete():
    sites = json.loads((config.PKG / "sites.json").read_text(encoding="utf-8"))
    sites.pop("_doc")
    for key, s in sites.items():
        for field in ("name", "home", "input", "send", "message", "model_steps"):
            assert field in s, f"{key} lacks {field}"


async def test_dom_markdown_skips_reasoning_blocks_and_code_headers(session):
    """Some sites render the reasoning with the answer's own markdown class, and put a language/Copy header next to
    each <pre>: neither may leak into the answer."""
    page = await session.tab("md")
    await page.set_content(
        "<div class='assistant'><div class='think'><div class='md'><p>secret reasoning</p></div></div>"
        "<div class='md'><p>Result:</p><div class='code'><div class='hdr'>go<div role='button'>Copy</div></div>"
        "<pre>fmt.Println(1)</pre></div><p>Output</p><pre><code class='language-sh'>go run .</code></pre></div></div>")
    md = await page.evaluate(engine.JS_MD, {"message": ".assistant", "answer": ".md", "thinking": ".think"})
    assert "secret" not in md and "Copy" not in md
    assert "```go\nfmt.Println(1)\n```" in md
    assert "Output" in md and "```sh\ngo run .\n```" in md
    nag = engine.Chat(session, "fake")
    nag.page = page
    nag.s = dict(SITE, dismiss=["#x"])
    await page.set_content("<div role='dialog'>Sign in to get more <button id='x' onclick='this.parentNode.remove()'>x"
                           "</button></div>")
    assert await nag.wall() is None

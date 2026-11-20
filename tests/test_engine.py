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
                           "timeout": 60, "captcha_wait": 0, "usage": str(tmp_path / "usage.jsonl"), "answers": str(tmp_path / "answers")}
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


async def test_next_site_answers_when_the_first_needs_a_human_and_usage_is_logged(session):
    session.cfg["site_defs"]["walled"] = dict(SITE, name="Walled", login_wall=["url:fake_chat"])
    r = await engine.ask_first(session, ["walled", "fake"], "hello", thread="fb")
    assert r["site"] == "fake" and r["skipped"][0]["site"] == "walled" and r["skipped"][0]["kind"] == "login"
    assert threads.load(session.cfg, "fb")["site"] == "fake"
    s = __import__("webchat.usage", fromlist=["x"]).summary(session.cfg)
    assert s["walled"]["errors"] == {"login": 1} and s["fake"]["ok"] == 1 and s["fake"]["chars_in"] == 5


def test_long_answers_are_clipped_to_a_file(tmp_path):
    cfg = {"answers": str(tmp_path / "a")}
    long = "\n".join(f"line {i}" for i in range(1000))
    r = engine.clip(cfg, {"answer": long, "site": "glm"}, 500)
    assert len(r["answer"]) < 800 and r["answer_chars"] == len(long)
    assert Path(r["answer_file"]).read_text(encoding="utf-8") == long
    assert engine.clip(cfg, {"answer": "short"}, 500) == {"answer": "short"}


async def test_upload_behind_a_menu_opens_it_and_images_go_to_the_image_input(session, tmp_path):
    """Gemini (11.2026) has no file input until its "+" menu is open, and keeps documents and images apart."""
    page = await session.tab("menu-upload")
    await page.set_content(
        "<button id='plus' onclick=\"document.body.insertAdjacentHTML('beforeend',"
        "'<div id=menu><input type=file id=docs accept=.txt,.pdf multiple>"
        "<input type=file id=imgs accept=image/*,video/* multiple></div>')\">+</button>")
    chat = engine.Chat(session, "fake")
    chat.page = page
    chat.s = dict(SITE, file_input="input[type=file]", file_input_images="input[type=file][accept*='image']",
                  upload_open="#plus")
    img = tmp_path / "grid.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0jpg")
    await chat.attach([str(img)])
    assert await page.evaluate("document.getElementById('imgs').files.length") == 1
    assert await page.evaluate("document.getElementById('docs').files.length") == 0
    txt = tmp_path / "message.md"
    txt.write_text("x", encoding="utf-8")
    await page.evaluate("document.getElementById('menu').remove()")
    await chat.attach([str(txt)])
    assert await page.evaluate("document.getElementById('docs').files.length") == 1


async def test_a_captcha_a_person_solves_in_time_is_waited_out(session):
    """The robot never touches a captcha; it brings the tab forward and waits while a person solves it."""
    page = await session.tab("captcha-wait")
    await page.set_content("<div id='cap' role='dialog' style='position:fixed;inset:0;z-index:99;background:#fff'>"
                           "Please complete security verification</div>"
                           "<script>setTimeout(() => document.getElementById('cap').remove(), 3000)</script>")
    chat = engine.Chat(session, "fake")
    chat.page = page
    session.cfg["captcha_wait"] = 15
    assert await chat.wall() is None
    assert 2 <= chat.captcha_waited < 15
    session.cfg["captcha_wait"] = 0
    await page.set_content("<div role='dialog' style='position:fixed;inset:0;z-index:99;background:#fff'>"
                           "Please complete security verification</div>")
    with pytest.raises(engine.WebChatError) as e:
        await chat.wall()
    assert e.value.kind == "captcha"

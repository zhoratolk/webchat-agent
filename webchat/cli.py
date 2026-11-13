"""Command line.

    webchat ask glm "Разбери таблицу" --file data.csv --thread review
    webchat ask glm "Продолжим" --thread review
    webchat ask kimi "Новый вопрос" --thread review-kimi --new
    webchat council "Вопрос" --sites glm,qwen,kimi
    webchat status glm | open glm | sites | threads | history review
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import config, engine, threads, usage
from .browser import Session


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="webchat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("site")
    a.add_argument("message", help="text, or @path to read it from a file")
    a.add_argument("--file", action="append", default=[])
    a.add_argument("--thread")
    a.add_argument("--new", action="store_true", help="start a new chat")
    a.add_argument("--model")
    a.add_argument("--timeout", type=int)
    c = sub.add_parser("council")
    c.add_argument("message")
    c.add_argument("--sites", default="glm,qwen,kimi")
    c.add_argument("--file", action="append", default=[])
    c.add_argument("--thread")
    c.add_argument("--new", action="store_true")
    for name in ("status", "open"):
        sub.add_parser(name).add_argument("site")
    sub.add_parser("sites")
    u = sub.add_parser("usage", help="asks per site, errors, sizes, quota hits")
    u.add_argument("--days", type=float, default=1)
    sub.add_parser("threads")
    h = sub.add_parser("history")
    h.add_argument("thread")
    h.add_argument("--last", type=int, default=6)
    args = ap.parse_args()
    cfg = config.load()
    text = lambda s: Path(s[1:]).read_text(encoding="utf-8") if s.startswith("@") else s   # noqa: E731

    async def run() -> None:
        session = Session(cfg)
        try:
            if args.cmd == "ask":
                r = await engine.ask_first(session, args.site.split(","), text(args.message), args.file, args.thread,
                                           args.new, args.model, args.timeout)
                for k in r.get("skipped", []):
                    print(f"[skipped {k['site']}: {k['kind']}]")
                print(f"[{r['site']} · {r['model'] or '?'} · {r['seconds']} s · {r['url']}]")
                print(r["answer"])
            elif args.cmd == "council":
                res = await engine.council(session, text(args.message), args.sites.split(","), args.file,
                                           args.thread, args.new)
                for s, r in res.items():
                    print(f"\n===== {s} =====")
                    print(r.get("answer") or f"ERROR: {r.get('error')}")
            elif args.cmd == "status":
                print(engine.dumps(await engine.status(session, args.site)))
            elif args.cmd == "open":
                chat = engine.Chat(session, args.site)
                chat.page = await session.tab(f"{args.site}:_login")
                await chat.page.goto(chat.s["home"])
                await chat.page.bring_to_front()
                print(f"Opened {chat.s['home']} in the robot window: sign in there yourself.")
        finally:
            await session.close()

    if args.cmd == "sites":
        for k, v in cfg["site_defs"].items():
            print(f"{k:9} {v.get('home', ''):32} verified={v.get('verified')}")
    elif args.cmd == "usage":
        print(engine.dumps(usage.summary(cfg, args.days)))
    elif args.cmd == "threads":
        for t in threads.listing(cfg):
            print(f"{t['updated']}  {t['site']:8} {t['turns']:3d} turns  {t['name']}  {t['url']}")
    elif args.cmd == "history":
        for x in threads.history(cfg, args.thread, args.last):
            print(f"--- {x['at']}\n> {x['message']}\n\n{x['answer']}\n")
    else:
        try:
            asyncio.run(run())
        except engine.WebChatError as e:
            print(f"ERROR {e}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()

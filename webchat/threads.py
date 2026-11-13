"""Named conversations: which site, the conversation URL to come back to, and a local log of the turns."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path


def safe_name(name: str) -> str:
    n = re.sub(r"[^\w.-]+", "-", name.strip(), flags=re.U).strip("-.")
    if not n:
        raise ValueError("empty thread name")
    return n[:80]


def path(cfg: dict, name: str) -> Path:
    return Path(cfg["threads"]) / f"{safe_name(name)}.json"


def load(cfg: dict, name: str) -> dict | None:
    p = path(cfg, name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def record(cfg: dict, name: str, site: str, url: str, message: str, files: list[str], answer: str,
           model: str | None, reset: bool = False) -> None:
    t = load(cfg, name)
    if t is None or reset:
        t = {"name": name, "site": site, "url": url, "chats": (t or {}).get("chats", []) + [url], "turns": []}
    t["url"] = url
    if url not in t["chats"]:
        t["chats"].append(url)
    t["turns"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "message": message,
                       "files": files, "answer": answer})
    p = path(cfg, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")


def listing(cfg: dict) -> list[dict]:
    d = Path(cfg["threads"])
    out = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []:
        t = json.loads(p.read_text(encoding="utf-8"))
        out.append({"name": t["name"], "site": t["site"], "turns": len(t["turns"]), "url": t["url"],
                    "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
    return out


def history(cfg: dict, name: str, last: int = 6, clip: int = 2000) -> list[dict]:
    t = load(cfg, name)
    if not t:
        return []
    return [{"at": x["at"], "message": x["message"][:clip], "answer": x["answer"][:clip]} for x in t["turns"][-last:]]

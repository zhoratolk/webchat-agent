"""What the robot spends: one JSON line per ask in usage.jsonl, and a per-site summary of it.

Sites do not publish their free limits, so the log is how you find them: count the asks a site took before its
first 'limit' / 'captcha' error of the day. chars_out also tells what the answers cost the calling agent: whatever
the tool returns lands in the agent's context (rough estimate: ~3 characters per token for Russian, ~4 for English).
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def log(cfg: dict, **rec) -> None:
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S")} | rec
    p = Path(cfg["usage"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def records(cfg: dict, days: float = 1) -> list[dict]:
    p = Path(cfg["usage"])
    if not p.exists():
        return []
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("at", "") >= since:
            out.append(r)
    return out


def summary(cfg: dict, days: float = 1) -> dict:
    """Per site over the last `days`: asks, ok, errors by kind, characters sent/received, average seconds and the
    asks it took before the first limit/captcha (None = never hit one)."""
    out: dict[str, dict] = {}
    for r in records(cfg, days):
        s = out.setdefault(r["site"], {"asks": 0, "ok": 0, "errors": {}, "chars_in": 0, "chars_out": 0,
                                       "seconds": 0.0, "asks_before_first_limit": None})
        s["asks"] += 1
        s["chars_in"] += r.get("chars_in", 0)
        s["chars_out"] += r.get("chars_out", 0)
        s["seconds"] += r.get("seconds", 0)
        kind = r.get("kind", "ok")
        if kind == "ok":
            s["ok"] += 1
        else:
            s["errors"][kind] = s["errors"].get(kind, 0) + 1
            if kind in ("limit", "captcha") and s["asks_before_first_limit"] is None:
                s["asks_before_first_limit"] = s["asks"] - 1
    for s in out.values():
        s["avg_seconds"] = round(s.pop("seconds") / s["asks"], 1)
        s["approx_tokens_out"] = s["chars_out"] // 3
    return out

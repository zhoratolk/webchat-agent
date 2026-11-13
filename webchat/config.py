"""Settings: defaults < config.json in the project root (or the file in $WEBCHAT_CONFIG). Sites come from
webchat/sites.json, and a `sites` block in config.json overrides single fields of a site (fix a selector locally)."""
from __future__ import annotations

import json
import os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
DEFAULTS = {
    "chrome": None,                       # path to chrome.exe / google-chrome; None = look in the usual places
    "profile": str(Path.home() / ".webchat-agent" / "chrome-profile"),
    "cdp_port": 9341,
    "proxy": None,                        # e.g. "socks5://127.0.0.1:10808"
    "threads": str(ROOT / "threads"),
    "shots": str(ROOT / "shots"),         # screenshots saved when a site needs a human
    "timeout": 900,                       # seconds to wait for one answer (thinking models are slow)
    "stable": 3.0,                        # the answer must stop changing for this long after the stop button is gone
    "long_message": 30000,                # longer messages go as an attached .md file where the site takes files
    "max_tabs": 10,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    path = Path(os.environ.get("WEBCHAT_CONFIG") or ROOT / "config.json")
    if path.exists():
        cfg |= json.loads(path.read_text(encoding="utf-8"))
    sites = json.loads((PKG / "sites.json").read_text(encoding="utf-8"))
    sites.pop("_doc", None)
    for key, patch in (cfg.pop("sites", None) or {}).items():
        sites[key] = sites.get(key, {}) | patch
    cfg["site_defs"] = sites
    return cfg

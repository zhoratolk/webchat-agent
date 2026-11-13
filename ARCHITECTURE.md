# Architecture

```
agent (Claude Code / local model / any OpenAI client)
   │  MCP: webchat/mcp_server.py      CLI: webchat/cli.py      HTTP: webchat/openai_api.py
   ▼
webchat/engine.py   ask_first() (site list, skips sites out of quota) → ask() / council() / status(); clip()
   │  Chat: open (new chat or saved URL) → prepare (model_steps) → attach → type → submit
   │        → wait_answer (stop gone + text stable) → copied_md (site copy button) | to_markdown.js
   │  walls: login dialogs, age gates, captcha → WebChatError(kind, screenshot); never solved by the robot
   ▼
webchat/browser.py  Session: the robot's own Chrome (separate profile, CDP port), one tab per (site, thread)
webchat/sites.json  per-site selectors and steps — the only place that knows a site's layout
webchat/threads.py  threads/<name>.json: site, current chat URL, earlier chat URLs, turn log
webchat/usage.py    usage.jsonl: one line per ask (site, sizes, seconds, ok/error kind) and the per-site summary
webchat/js/         in-page helpers: to_markdown.js, hook_copy.js (clipboard capture), mark_copy.js
tests/              fake_chat.html + headless Chrome end-to-end tests
```

Config: defaults in `webchat/config.py` < `config.json` (gitignored) < its `sites` block overriding fields of
`sites.json`. Runtime files (threads/, shots/, answers/, usage.jsonl) are gitignored.

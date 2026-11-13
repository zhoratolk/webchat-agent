"""OpenAI-compatible HTTP endpoint over the web chats, for tools that speak /v1/chat/completions.

    webchat-api --port 8765            # then: base_url=http://127.0.0.1:8765/v1, model="glm" (or "web:glm")

Stateless clients send the whole history each time: it is folded into one prompt in a NEW chat. To continue a real
web conversation instead, pass a thread name (body field "thread", or the OpenAI "user" field): only the last user
message is sent into that thread's chat. Image/file parts are not supported here; use the MCP server or CLI.
Listens on 127.0.0.1 only; nothing here is authenticated, do not expose it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, engine
from .browser import Session


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


def fold(messages: list[dict]) -> str:
    """One prompt from a chat history (system first, then the turns), for a fresh web chat."""
    if len(messages) == 1 and messages[0]["role"] == "user":
        return text_of(messages[0]["content"])
    parts = []
    for m in messages:
        role = {"system": "Instructions", "user": "User", "assistant": "Assistant", "tool": "Tool result"}.get(m["role"], m["role"])
        parts.append(f"### {role}\n{text_of(m.get('content'))}")
    parts.append("### Assistant\n(answer the last User message, following the Instructions)")
    return "\n\n".join(parts)


def completion(model: str, answer: str) -> dict:
    return {"id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def serve(port: int) -> None:
    cfg = config.load()
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    session = Session(cfg)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict | str, ctype="application/json") -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/").endswith("/models"):
                self._send(200, {"object": "list", "data": [{"id": k, "object": "model", "owned_by": "webchat"}
                                                            for k in cfg["site_defs"]]})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self):  # noqa: N802
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._send(404, {"error": {"message": "not found"}})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                site = body["model"].removeprefix("web:")
                thread = body.get("thread") or body.get("user")
                msgs = body["messages"]
                prompt = text_of(msgs[-1]["content"]) if thread else fold(msgs)
                fut = asyncio.run_coroutine_threadsafe(   # model "deepseek,qwen": first site that answers
                    engine.ask_first(session, site.split(","), prompt, thread=thread, new_chat=not thread), loop)
                r = fut.result()
            except engine.WebChatError as e:
                return self._send(503 if e.kind in ("login", "captcha", "limit") else 500,
                                  {"error": {"message": str(e), "type": e.kind}})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": {"message": str(e)[:500], "type": "bad_request"}})
            out = completion(body["model"], r["answer"])
            if body.get("stream"):   # one chunk is enough for clients that insist on streaming
                chunk = dict(out, object="chat.completion.chunk",
                             choices=[{"index": 0, "delta": {"role": "assistant", "content": r["answer"]},
                                       "finish_reason": "stop"}])
                return self._send(200, f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n",
                                  "text/event-stream")
            self._send(200, out)

        def log_message(self, fmt, *args):  # quieter
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"webchat OpenAI-compatible API on http://127.0.0.1:{port}/v1 (models: {', '.join(cfg['site_defs'])})")
    srv.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    serve(ap.parse_args().port)


if __name__ == "__main__":
    main()

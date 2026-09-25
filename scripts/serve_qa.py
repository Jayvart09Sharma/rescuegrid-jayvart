#!/usr/bin/env python3
"""Tiny HTTP endpoint for the frontend (stdlib only, no extra deps).
  .venv/bin/python scripts/serve_qa.py --port 8095
  POST /qa            {"question": "...", "mode": "auto|llm|fallback"}  -> QAResponse JSON (docs/QA_CONTRACT.md)
  GET  /health        -> {"status":"ok", ...}
  GET  /questions     -> the pre-baked questions guaranteed to work"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuegrid.graph import GraphStore  # noqa: E402
from rescuegrid.qa import QAEngine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8095)
args = ap.parse_args()

graph = GraphStore()
engine = QAEngine(graph)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict | str):
        data = (body if isinstance(body, str) else json.dumps(body, default=str)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "llm": engine.llm.name if engine.llm else None, "as_of": engine.now().isoformat()})
        elif self.path == "/questions":
            self._send(200, {"questions": engine.fallback.examples()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/qa":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") if n else "{}")
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "body must be UTF-8 JSON: {\"question\": \"...\", \"mode\": \"auto|llm|fallback\"}"})
        if not isinstance(body, dict):
            return self._send(400, {"error": "body must be a JSON object {question, mode}"})
        q, mode = body.get("question"), body.get("mode", "auto")
        if not isinstance(q, str) or not q.strip():
            return self._send(400, {"error": "question must be a non-empty string"})
        if mode not in ("auto", "llm", "fallback"):
            return self._send(400, {"error": "mode must be one of auto, llm, fallback"})
        try:
            self._send(200, engine.answer(q, mode=mode).model_dump_json())
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % a))


print(f"RescueGrid Q&A on http://{args.host}:{args.port}  (LLM: {engine.llm.name if engine.llm else 'none'})")
ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()

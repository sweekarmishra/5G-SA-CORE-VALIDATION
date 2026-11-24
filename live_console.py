#!/usr/bin/env python3
"""Live interactive console for the two validation projects.

Starts the project's simulated network and a small web page on http://127.0.0.1:PORT
where you type inputs, press Send, and watch the request, response and network state.

    python3 live_console.py          # opens http://127.0.0.1:8001

Python 3 standard library only. Listens on 127.0.0.1 (this computer) only.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECTS = {
    "5g": {"dir": ".", "sub": "mock_core", "sim": "mock_5gc", "port": 8001,
           "title": "5G SA Core Validation", "faults": ["pfcp_fail"]},
}


class State:
    """Holds the running simulator and the project's test library."""

    def __init__(self, key):
        self.key = key
        cfg = self.cfg = PROJECTS[key]
        pdir = os.path.join(ROOT, cfg["dir"])
        for sub in ("", cfg["sub"], "tools"):
            sys.path.insert(0, os.path.join(pdir, sub))
        import importlib
        self.sim = importlib.import_module(cfg["sim"])
        self.run_suite = importlib.import_module("run_suite")
        self.tests = importlib.import_module("test_cases").TESTS
        os.makedirs(os.path.join(ROOT, "live_logs"), exist_ok=True)
        self.logpath = os.path.join(ROOT, "live_logs", key + ".log")
        self.fault = None
        self.server, self.base = self.sim.start_server(0, self.logpath, None)

    def _holder(self):
        return "core" if self.key == "5g" else "net"

    def core(self):
        return getattr(self.server, self._holder())

    def reset(self, fault):
        cls = self.sim.Core if self.key == "5g" else self.sim.Net
        setattr(self.server, self._holder(), cls(self.logpath, fault))
        self.fault = fault

    def set_fault(self, fault):
        self.core().fault = fault
        self.fault = fault

    def forward(self, method, path, body):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        try:
            obj = json.loads(raw) if raw else None
        except ValueError:
            obj = raw.decode(errors="replace")
        return {"status": code, "response": obj, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def run_test(self, tid):
        t = next((x for x in self.tests if x["id"] == tid), None)
        if not t:
            return {"error": "unknown test id"}
        client = self.run_suite.Client(self.base)
        t0 = time.perf_counter()
        status, msg, metrics = "PASS", "", None
        try:
            metrics = t["fn"](client)
        except AssertionError as e:
            status, msg = "FAIL", str(e)
        except Exception as e:
            status, msg = "ERROR", f"{type(e).__name__}: {e}"
        trace = client.trace if len(client.trace) <= 14 else client.trace[:7] + client.trace[-7:]
        return {"id": t["id"], "title": t["title"], "ref": t["ref"], "status": status, "message": msg,
                "metrics": metrics, "calls": len(client.trace), "trace": trace,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def log_tail(self, n=60):
        try:
            with open(self.logpath) as f:
                return f.read().splitlines()[-n:]
        except OSError:
            return []


ST = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(ROOT, "live_console.html"), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/info":
            self._json({"project": ST.key, "title": ST.cfg["title"], "faults": ST.cfg["faults"], "fault": ST.fault,
                        "ports": {k: v["port"] for k, v in PROJECTS.items()},
                        "tests": [{"id": t["id"], "title": t["title"], "ref": t["ref"]} for t in ST.tests]})
        elif self.path == "/api/log":
            self._json({"lines": ST.log_tail()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/call":
            self._json(ST.forward(body.get("method", "GET"), body.get("path", "/"), body.get("body")))
        elif self.path == "/api/fault":
            ST.set_fault(body.get("fault") or None)
            self._json({"fault": ST.fault})
        elif self.path == "/api/reset":
            ST.reset(body.get("fault") if body.get("keepFault") else None)
            self._json({"fault": ST.fault})
        elif self.path == "/api/run-test":
            self._json(ST.run_test(body.get("id")))
        else:
            self._json({"error": "not found"}, 404)


def main():
    global ST
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default="5g", choices=list(PROJECTS))
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    ST = State(a.project)
    port = a.port or ST.cfg["port"]
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        sys.exit(f"Port {port} is already in use. Close the other console window, or use --port 9000")
    url = f"http://127.0.0.1:{port}"
    print(f"\n{ST.cfg['title']}\nLive console running at {url}\nPress Ctrl+C in this window to stop.\n")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

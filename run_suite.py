#!/usr/bin/env python3
"""Run the 5G SA core validation suite and produce an HTML + JSON report.

Local demo (starts the built-in mock core, nothing to install):
    python3 run_suite.py
    python3 run_suite.py --fault pfcp_fail        # shows how failures are reported

Against another endpoint that speaks the same simplified API:
    python3 run_suite.py --target http://HOST:PORT [--core-log path/to/core.log]
"""
import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "mock_core"))
sys.path.insert(0, os.path.join(HERE, "tools"))

import log_tracker  # noqa: E402
import mock_5gc  # noqa: E402
from test_cases import TESTS  # noqa: E402


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.trace = []

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        ms = (time.perf_counter() - t0) * 1000
        try:
            obj = json.loads(raw) if raw else None
        except ValueError:
            obj = raw.decode(errors="replace")
        self.trace.append({"method": method, "path": path, "request": body,
                           "status": code, "response": obj, "ms": round(ms, 1)})
        return code, obj


def run_all(base):
    results = []
    for t in TESTS:
        client = Client(base)
        t0 = time.perf_counter()
        status, msg = "PASS", ""
        try:
            t["fn"](client)
        except AssertionError as e:
            status, msg = "FAIL", str(e)
        except Exception as e:  # connection problems, bugs in a test
            status, msg = "ERROR", f"{type(e).__name__}: {e}"
        results.append({"id": t["id"], "title": t["title"], "ref": t["ref"], "status": status,
                        "message": msg, "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "trace": client.trace})
    return results


def render_html(results, target, fault, log_summary):
    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "ERROR")}
    total = len(results)
    rate = 100.0 * counts["PASS"] / total if total else 0.0
    rows = []
    for r in results:
        trace = "\n".join(
            f"{x['method']} {x['path']}  ->  {x['status']}  ({x['ms']} ms)\n"
            f"  request : {json.dumps(x['request'])}\n  response: {json.dumps(x['response'])}"
            for x in r["trace"])
        detail = (f"<p class='msg'>{html.escape(r['message'])}</p>" if r["message"] else "") + \
                 f"<pre>{html.escape(trace)}</pre>"
        rows.append(
            f"<tr class='{r['status']}'><td>{r['id']}</td><td>{html.escape(r['title'])}</td>"
            f"<td>{html.escape(r['ref'])}</td><td><b>{r['status']}</b></td><td>{r['ms']}</td></tr>"
            f"<tr class='d'><td colspan='5'><details><summary>Message trace</summary>{detail}</details></td></tr>")
    log_html = ""
    if log_summary:
        log_html = ("<h2>Core log analysis (Log Tracker)</h2><pre>" + html.escape(log_summary) + "</pre>")
    banner = f"<p class='fault'>Fault injected: {html.escape(fault)}</p>" if fault else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>5G SA Core Validation Report</title>
<style>
:root{{--bg:#fff;--fg:#1c2330;--mut:#5b6678;--line:#dde2ea;--ok:#1a7f4b;--bad:#b3261e;--warn:#9a6700;--card:#f5f7fa}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12161d;--fg:#e6eaf0;--mut:#9aa6b8;--line:#2a3240;--ok:#4cc38a;--bad:#ff7b72;--warn:#e3b341;--card:#1a2029}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:980px;margin:auto}} h1{{margin:0 0 4px;font-size:24px}} h2{{margin-top:32px;font-size:18px}}
.sub{{color:var(--mut);margin:0 0 20px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;min-width:110px}}
.kpi b{{display:block;font-size:26px}} .kpi span{{color:var(--mut);font-size:13px}}
table{{width:100%;border-collapse:collapse}} th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:13px}}
tr.PASS td b{{color:var(--ok)}} tr.FAIL td b,tr.ERROR td b{{color:var(--bad)}}
tr.d td{{border-bottom:1px solid var(--line);padding-top:0}} summary{{cursor:pointer;color:var(--mut);font-size:13px}}
pre{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px;overflow-x:auto;font-size:12px}}
.msg{{color:var(--bad);margin:6px 0}} .fault{{color:var(--warn);font-weight:600}}
.table-wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>5G SA Core Validation Report</h1>
<p class="sub">Target: {html.escape(target)} &middot; {time.strftime('%Y-%m-%d %H:%M:%S')} &middot; Mock-core run unless a real target was given</p>
{banner}
<div class="kpis">
<div class="kpi"><b>{total}</b><span>test cases</span></div>
<div class="kpi"><b style="color:var(--ok)">{counts['PASS']}</b><span>passed</span></div>
<div class="kpi"><b style="color:var(--bad)">{counts['FAIL'] + counts['ERROR']}</b><span>failed / error</span></div>
<div class="kpi"><b>{rate:.0f}%</b><span>pass rate</span></div></div>
<h2>Results</h2><div class="table-wrap"><table>
<tr><th>ID</th><th>Test case</th><th>3GPP reference</th><th>Result</th><th>ms</th></tr>
{''.join(rows)}</table></div>{log_html}
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="base URL of the core; omit to start the built-in mock")
    ap.add_argument("--fault", choices=["pfcp_fail"], help="inject a fault (mock core only)")
    ap.add_argument("--core-log", help="core log file to analyse (auto for the mock)")
    ap.add_argument("--out", default=os.path.join(HERE, "reports"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    core_log = a.core_log
    if a.target:
        base = a.target
    else:
        core_log = core_log or os.path.join(a.out, "core_test_logs.txt")
        _srv, base = mock_5gc.start_server(0, core_log, a.fault)
    print(f"Target: {base}")

    results = run_all(base)

    log_summary = ""
    if core_log and os.path.exists(core_log):
        res = log_tracker.parse(core_log)
        log_tracker.write_report(res, os.path.join(a.out, "summary_report.txt"))
        with open(os.path.join(a.out, "summary_report.txt")) as f:
            log_summary = f.read()

    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    with open(os.path.join(a.out, "report.html"), "w") as f:
        f.write(render_html(results, base, a.fault, log_summary))

    for r in results:
        print(f"{r['status']:<5} {r['id']:<11} {r['title']}" + (f"\n        -> {r['message']}" if r["message"] else ""))
    bad = sum(r["status"] != "PASS" for r in results)
    print(f"\n{len(results) - bad}/{len(results)} passed. Report: {os.path.join(a.out, 'report.html')}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

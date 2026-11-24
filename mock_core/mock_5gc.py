#!/usr/bin/env python3
"""Mock 5G SA core: NRF + AMF + SMF + UPF (N4/PFCP state) behind one HTTP port.

This is a *behavioural* model for exercising a validation framework without a
real core. Paths follow the 3GPP SBI naming (TS 29.510 / 29.518 / 29.502), but
payloads are simplified and transport is HTTP/1.1 (a real SBI uses HTTP/2).

It writes a log file in the same line format the Log Tracker understands.

Run standalone:  python3 mock_5gc.py --port 8080 --log core_test_logs.txt
Fault injection: --fault pfcp_fail   (N4 session establishment fails)
"""
import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ALLOWED_PLMN = {("001", "01")}
ALLOWED_DNN = {"internet", "ims"}
ALLOWED_SST = {1, 2}
BARRED_SUPIS = {"imsi-001019999999999"}
SUPI_RE = re.compile(r"^imsi-(\d{15})$")


class Core:
    def __init__(self, logpath=None, fault=None):
        self.lock = threading.Lock()
        self.logpath = logpath
        self.fault = fault
        self.nfs = {}        # nfInstanceId -> profile
        self.ues = {}        # supi -> context
        self.sessions = {}   # smContextRef -> session
        self.n4 = {}         # seid -> pfcp session
        self.next_ip = 2
        self.next_seid = 1000
        self.next_ref = 1
        if logpath:
            open(logpath, "w").close()

    # ---- logging (format understood by tools/log_tracker.py) ----
    def log(self, level, node, event, supi=None, detail=""):
        if not self.logpath:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        ident = ""
        if supi and SUPI_RE.match(supi):
            d = SUPI_RE.match(supi).group(1)
            ident = f" IMSI={d} MCC={d[:3]} MNC={d[3:5]}"
        with open(self.logpath, "a") as f:
            f.write(f"{ts} [{level}] {node} {event}{ident} {detail}".rstrip() + "\n")

    # ---- request dispatch ----
    def handle(self, method, path, query, body):
        with self.lock:
            m = re.match(r"^/nnrf-nfm/v1/nf-instances/([\w-]+)$", path)
            if m and method == "PUT":
                return self.nf_register(m.group(1), body)
            if path == "/nnrf-disc/v1/nf-instances" and method == "GET":
                return self.nf_discover(query)
            if path == "/namf-comm/v1/ue-contexts" and method == "POST":
                return self.register_ue(body)
            m = re.match(r"^/namf-comm/v1/ue-contexts/([\w-]+)$", path)
            if m and method == "DELETE":
                return self.deregister_ue(m.group(1))
            if path == "/nsmf-pdusession/v1/sm-contexts" and method == "POST":
                return self.create_session(body)
            m = re.match(r"^/nsmf-pdusession/v1/sm-contexts/([\w-]+)$", path)
            if m and method == "DELETE":
                return self.release_session(m.group(1))
            if path == "/n4/sessions" and method == "GET":
                return 200, {"sessions": list(self.n4.values())}
            return 404, {"cause": "RESOURCE_NOT_FOUND"}

    # ---- NRF ----
    def nf_register(self, nf_id, body):
        if not isinstance(body, dict) or body.get("nfType") not in ("AMF", "SMF", "UPF", "AUSF", "UDM", "PCF"):
            self.log("ERROR", "NRF", "NF_REGISTER_FAILURE", detail="invalid nfType")
            return 400, {"cause": "INVALID_NF_TYPE"}
        new = nf_id not in self.nfs
        self.nfs[nf_id] = {"nfInstanceId": nf_id, "nfStatus": "REGISTERED", **body}
        self.log("INFO", "NRF", "NF_REGISTER", detail=f"nf={body['nfType']} id={nf_id}")
        return (201 if new else 200), self.nfs[nf_id]

    def nf_discover(self, query):
        target = (query.get("target-nf-type") or [None])[0]
        if not target:
            return 400, {"cause": "MANDATORY_QUERY_PARAM_MISSING", "param": "target-nf-type"}
        found = [p for p in self.nfs.values() if p["nfType"] == target]
        return 200, {"nfInstances": found}

    def has_nf(self, nf_type):
        return any(p["nfType"] == nf_type for p in self.nfs.values())

    # ---- AMF ----
    def register_ue(self, body):
        supi = (body or {}).get("supi", "")
        plmn = (body or {}).get("plmnId", {})
        if not SUPI_RE.match(supi):
            self.log("ERROR", "AMF", "REGISTRATION_REJECT", detail="invalid SUPI format")
            return 400, {"cause": "INVALID_SUPI"}
        if (plmn.get("mcc"), plmn.get("mnc")) not in ALLOWED_PLMN:
            self.log("ERROR", "AMF", "REGISTRATION_REJECT", supi, "5GMM cause #11 PLMN not allowed")
            return 403, {"cause": "PLMN_NOT_ALLOWED"}
        if supi in BARRED_SUPIS:
            self.log("ERROR", "AMF", "AUTH_FAILURE", supi, "authentication rejected by AUSF")
            return 403, {"cause": "AUTHENTICATION_FAILURE"}
        again = supi in self.ues
        if not again:
            self.ues[supi] = {"supi": supi, "state": "REGISTERED",
                              "guti": f"5g-guti-001-01-{len(self.ues) + 1:08x}"}
        self.log("INFO", "AMF", "REGISTRATION_SUCCESS", supi,
                 "re-registration" if again else "initial registration")
        return (200 if again else 201), self.ues[supi]

    def deregister_ue(self, supi):
        if supi not in self.ues:
            return 404, {"cause": "CONTEXT_NOT_FOUND"}
        for ref in [r for r, s in self.sessions.items() if s["supi"] == supi]:
            self._drop_session(ref)
        del self.ues[supi]
        self.log("INFO", "AMF", "DEREGISTRATION_SUCCESS", supi)
        return 204, None

    # ---- SMF / UPF ----
    def create_session(self, body):
        body = body or {}
        supi = body.get("supi", "")
        sid = body.get("pduSessionId")
        dnn = body.get("dnn")
        sst = (body.get("sNssai") or {}).get("sst")

        def fail(code, cause):
            self.log("ERROR", "SMF", "PDU_SESSION_FAILURE", supi, cause)
            return code, {"cause": cause}

        if supi not in self.ues:
            return fail(403, "UE_NOT_REGISTERED")
        if not isinstance(sid, int) or not 1 <= sid <= 15:
            return fail(400, "INVALID_PDU_SESSION_ID")
        if dnn not in ALLOWED_DNN:
            return fail(403, "DNN_NOT_SUPPORTED")
        if sst not in ALLOWED_SST:
            return fail(403, "SNSSAI_NOT_ALLOWED")
        if any(s["supi"] == supi and s["pduSessionId"] == sid for s in self.sessions.values()):
            return fail(409, "PDU_SESSION_ALREADY_EXISTS")
        if not self.has_nf("UPF"):
            return fail(503, "NO_UPF_AVAILABLE")
        if self.fault == "pfcp_fail":
            self.log("ERROR", "UPF", "PFCP_ERROR", supi, "PFCP Session Establishment Response cause=SYSTEM_FAILURE")
            return fail(500, "N4_SESSION_ESTABLISHMENT_FAILURE")
        ref = f"smctx-{self.next_ref}"
        self.next_ref += 1
        seid = self.next_seid
        self.next_seid += 1
        ip = f"10.45.0.{self.next_ip}"
        self.next_ip = self.next_ip % 253 + 2
        self.sessions[ref] = {"smContextRef": ref, "supi": supi, "pduSessionId": sid,
                              "dnn": dnn, "sst": sst, "ueIp": ip, "pfcpSeid": seid}
        self.n4[seid] = {"seid": seid, "supi": supi, "pduSessionId": sid, "ueIp": ip, "state": "ESTABLISHED"}
        self.log("INFO", "SMF", "PDU_SESSION_SUCCESS", supi, f"id={sid} dnn={dnn} ip={ip} seid={seid}")
        return 201, self.sessions[ref]

    def _drop_session(self, ref):
        s = self.sessions.pop(ref)
        self.n4.pop(s["pfcpSeid"], None)

    def release_session(self, ref):
        if ref not in self.sessions:
            return 404, {"cause": "CONTEXT_NOT_FOUND"}
        supi = self.sessions[ref]["supi"]
        self._drop_session(ref)
        self.log("INFO", "SMF", "PDU_SESSION_RELEASE_SUCCESS", supi)
        return 204, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep console quiet
        pass

    def _go(self, method):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            return self._send(400, {"cause": "MALFORMED_JSON"})
        code, obj = self.server.core.handle(method, u.path, parse_qs(u.query), body)
        self._send(code, obj)

    def _send(self, code, obj):
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = lambda self: self._go("GET")
    do_POST = lambda self: self._go("POST")
    do_PUT = lambda self: self._go("PUT")
    do_DELETE = lambda self: self._go("DELETE")


def start_server(port=0, logpath=None, fault=None):
    """Start in a background thread; returns (server, base_url). port=0 picks a free port."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.core = Core(logpath, fault)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--log", default="core_test_logs.txt")
    ap.add_argument("--fault", choices=["pfcp_fail"])
    a = ap.parse_args()
    srv, url = start_server(a.port, a.log, a.fault)
    print(f"Mock 5GC listening on {url} (log: {a.log}, fault: {a.fault})")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass

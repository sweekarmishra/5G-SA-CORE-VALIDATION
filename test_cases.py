"""Test case library for the 5G SA core validation suite.

Each test: an ID, a title, a 3GPP reference, and a function that drives the core
through a Client and raises AssertionError on mismatch. Tests use unique SUPIs so
they do not depend on each other, except that the NRF tests run first to register
the network functions.
"""
import itertools

TESTS = []
_supi_counter = itertools.count(1)
PLMN = {"mcc": "001", "mnc": "01"}
SLICE = {"sst": 1}


def tc(tc_id, title, ref):
    def deco(fn):
        TESTS.append({"id": tc_id, "title": title, "ref": ref, "fn": fn})
        return fn
    return deco


def new_supi():
    return f"imsi-00101{next(_supi_counter):010d}"


def eq(actual, expected, what):
    if actual != expected:
        raise AssertionError(f"{what}: expected {expected!r}, got {actual!r}")


def contains(container, item, what):
    if item not in container:
        raise AssertionError(f"{what}: {item!r} not found in {container!r}")


def register(c, supi=None):
    supi = supi or new_supi()
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts", {"supi": supi, "plmnId": PLMN})
    eq(code, 201, "setup registration status")
    return supi


def pdu(c, supi, sid=1, dnn="internet", s_nssai=SLICE):
    return c.call("POST", "/nsmf-pdusession/v1/sm-contexts",
                  {"supi": supi, "pduSessionId": sid, "dnn": dnn, "sNssai": s_nssai})


def n4_seids(c):
    code, obj = c.call("GET", "/n4/sessions")
    eq(code, 200, "N4 inspection status")
    return {s["seid"] for s in obj["sessions"]}


# ---------------- NRF (TS 29.510) ----------------
for _id, _type in (("01", "AMF"), ("02", "SMF"), ("03", "UPF")):
    def _make(nf_id=_id, nf_type=_type):
        def fn(c):
            code, obj = c.call("PUT", f"/nnrf-nfm/v1/nf-instances/nf-{nf_type.lower()}-1",
                               {"nfType": nf_type, "ipv4Addresses": ["10.0.0." + nf_id]})
            eq(code, 201, f"{nf_type} register status")
            eq(obj["nfStatus"], "REGISTERED", f"{nf_type} nfStatus")
        return fn
    tc(f"TC-NRF-{_id}", f"{_type} registers with NRF", "TS 29.510 5.2.2.2")(_make())


@tc("TC-NRF-04", "NF re-registration updates the profile", "TS 29.510 5.2.2.2")
def _(c):
    code, _o = c.call("PUT", "/nnrf-nfm/v1/nf-instances/nf-smf-1",
                      {"nfType": "SMF", "ipv4Addresses": ["10.0.0.22"]})
    eq(code, 200, "update status")


@tc("TC-NRF-05", "SMF discovery returns the registered SMF", "TS 29.510 5.3.2.2")
def _(c):
    code, obj = c.call("GET", "/nnrf-disc/v1/nf-instances?target-nf-type=SMF&requester-nf-type=AMF")
    eq(code, 200, "discovery status")
    eq([p["nfInstanceId"] for p in obj["nfInstances"]], ["nf-smf-1"], "discovered SMF ids")


@tc("TC-NRF-06", "Discovery without target-nf-type is rejected", "TS 29.510 5.3.2.2")
def _(c):
    code, obj = c.call("GET", "/nnrf-disc/v1/nf-instances")
    eq(code, 400, "status")
    eq(obj["cause"], "MANDATORY_QUERY_PARAM_MISSING", "cause")


@tc("TC-NRF-07", "Discovery of an absent NF type returns an empty list", "TS 29.510 5.3.2.2")
def _(c):
    code, obj = c.call("GET", "/nnrf-disc/v1/nf-instances?target-nf-type=PCF")
    eq(code, 200, "status")
    eq(obj["nfInstances"], [], "instances")


# ---------------- Registration (TS 23.502 4.2.2.2.2) ----------------
REF_REG = "TS 23.502 4.2.2.2.2"


@tc("TC-REG-01", "Initial registration succeeds", REF_REG)
def _(c):
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts", {"supi": new_supi(), "plmnId": PLMN})
    eq(code, 201, "status")
    eq(obj["state"], "REGISTERED", "state")
    contains(obj["guti"], "5g-guti", "5G-GUTI")


@tc("TC-REG-02", "Re-registration of a registered UE is accepted", REF_REG)
def _(c):
    supi = register(c)
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts", {"supi": supi, "plmnId": PLMN})
    eq(code, 200, "status")


@tc("TC-REG-03", "Malformed SUPI is rejected", REF_REG)
def _(c):
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts", {"supi": "imsi-123", "plmnId": PLMN})
    eq(code, 400, "status")
    eq(obj["cause"], "INVALID_SUPI", "cause")


@tc("TC-REG-04", "PLMN not allowed is rejected", REF_REG)
def _(c):
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts",
                       {"supi": new_supi(), "plmnId": {"mcc": "404", "mnc": "99"}})
    eq(code, 403, "status")
    eq(obj["cause"], "PLMN_NOT_ALLOWED", "cause")


@tc("TC-REG-05", "Barred subscriber fails authentication", REF_REG)
def _(c):
    code, obj = c.call("POST", "/namf-comm/v1/ue-contexts",
                       {"supi": "imsi-001019999999999", "plmnId": PLMN})
    eq(code, 403, "status")
    eq(obj["cause"], "AUTHENTICATION_FAILURE", "cause")


# ---------------- PDU session (TS 23.502 4.3.2.2.1) ----------------
REF_PDU = "TS 23.502 4.3.2.2.1"


@tc("TC-PDU-01", "PDU session establishment creates an N4 session", REF_PDU)
def _(c):
    supi = register(c)
    code, obj = pdu(c, supi)
    eq(code, 201, "status")
    contains(obj["ueIp"], "10.45.0.", "UE IP pool")
    contains(n4_seids(c), obj["pfcpSeid"], "N4 session list (PFCP SEID)")


@tc("TC-PDU-02", "Second PDU session (IMS DNN) on the same UE", REF_PDU)
def _(c):
    supi = register(c)
    eq(pdu(c, supi, 1)[0], 201, "first session status")
    code, obj = pdu(c, supi, 2, "ims")
    eq(code, 201, "second session status")
    eq(obj["dnn"], "ims", "dnn")


@tc("TC-PDU-03", "PDU session without registration is rejected", REF_PDU)
def _(c):
    code, obj = pdu(c, new_supi())
    eq(code, 403, "status")
    eq(obj["cause"], "UE_NOT_REGISTERED", "cause")


@tc("TC-PDU-04", "Unsupported DNN is rejected", REF_PDU)
def _(c):
    code, obj = pdu(c, register(c), dnn="private.corp")
    eq(code, 403, "status")
    eq(obj["cause"], "DNN_NOT_SUPPORTED", "cause")


@tc("TC-PDU-05", "Unsupported network slice (S-NSSAI) is rejected", REF_PDU)
def _(c):
    code, obj = pdu(c, register(c), s_nssai={"sst": 9})
    eq(code, 403, "status")
    eq(obj["cause"], "SNSSAI_NOT_ALLOWED", "cause")


@tc("TC-PDU-06", "Duplicate PDU session ID is rejected", REF_PDU)
def _(c):
    supi = register(c)
    eq(pdu(c, supi, 5)[0], 201, "first status")
    code, obj = pdu(c, supi, 5)
    eq(code, 409, "duplicate status")


@tc("TC-PDU-07", "Out-of-range PDU session ID is rejected", REF_PDU)
def _(c):
    code, obj = pdu(c, register(c), sid=16)
    eq(code, 400, "status")
    eq(obj["cause"], "INVALID_PDU_SESSION_ID", "cause")


@tc("TC-PDU-08", "PDU session release removes the N4 session", "TS 23.502 4.3.4.2")
def _(c):
    supi = register(c)
    code, obj = pdu(c, supi)
    eq(code, 201, "setup status")
    seid = obj["pfcpSeid"]
    eq(c.call("DELETE", f"/nsmf-pdusession/v1/sm-contexts/{obj['smContextRef']}")[0], 204, "release status")
    if seid in n4_seids(c):
        raise AssertionError(f"N4 session {seid} still present after release")


# ---------------- Deregistration (TS 23.502 4.2.2.3) ----------------
@tc("TC-DEREG-01", "Deregistration releases all PDU sessions and N4 state", "TS 23.502 4.2.2.3.2")
def _(c):
    supi = register(c)
    seids = set()
    for sid, dnn in ((1, "internet"), (2, "ims")):
        code, obj = pdu(c, supi, sid, dnn)
        eq(code, 201, f"session {sid} status")
        seids.add(obj["pfcpSeid"])
    eq(c.call("DELETE", f"/namf-comm/v1/ue-contexts/{supi}")[0], 204, "deregistration status")
    left = seids & n4_seids(c)
    if left:
        raise AssertionError(f"orphaned N4 sessions after deregistration: {sorted(left)}")


@tc("TC-DEREG-02", "Deregistering an unknown UE returns 404", "TS 23.502 4.2.2.3.2")
def _(c):
    eq(c.call("DELETE", f"/namf-comm/v1/ue-contexts/{new_supi()}")[0], 404, "status")

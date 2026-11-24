"""Log Tracking System - parse core test logs and summarise failures.

Usage:
    python3 log_tracker.py core_test_logs.txt summary_report.txt
"""
import re
import sys
from collections import Counter

# Reads the file line by line (never loads it all into memory).
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} [\d:]+) \[(?P<level>\w+)\] (?P<node>\w+) (?P<event>\w+)"
)
IMSI_RE = re.compile(r"IMSI=(\d{15})")
MCC_RE = re.compile(r"MCC=(\d{3})")
MNC_RE = re.compile(r"MNC=(\d{2,3})")
FAIL_KEYWORDS = ("REJECT", "FAILURE", "ERROR")


def validate_imsi(imsi: str) -> bool:
    """An IMSI must be exactly 15 digits."""
    return len(imsi) == 15 and imsi.isdigit()


def parse(path):
    failures = []
    by_node = Counter()
    by_event = Counter()
    by_imsi = Counter()
    plmns = set()
    total = 0
    bad_imsi = 0

    with open(path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            total += 1
            m = LINE_RE.match(line)
            if not m:
                continue
            imsi_m = IMSI_RE.search(line)
            mcc_m = MCC_RE.search(line)
            mnc_m = MNC_RE.search(line)
            if mcc_m and mnc_m:
                plmns.add((mcc_m.group(1), mnc_m.group(1)))
            if imsi_m and not validate_imsi(imsi_m.group(1)):
                bad_imsi += 1

            is_fail = m["level"] == "ERROR" or any(k in m["event"] for k in FAIL_KEYWORDS)
            if is_fail:
                imsi = imsi_m.group(1) if imsi_m else "UNKNOWN"
                failures.append((m["ts"], m["node"], m["event"], imsi, line))
                by_node[m["node"]] += 1
                by_event[m["event"]] += 1
                by_imsi[imsi] += 1

    return {
        "total": total,
        "failures": failures,
        "by_node": by_node,
        "by_event": by_event,
        "by_imsi": by_imsi,
        "plmns": plmns,
        "bad_imsi": bad_imsi,
    }


def write_report(res, out_path):
    with open(out_path, "w") as f:
        f.write("CORE TEST LOG SUMMARY\n" + "=" * 40 + "\n")
        f.write(f"Total log lines : {res['total']}\n")
        f.write(f"Failure lines   : {len(res['failures'])}\n")
        f.write(f"Invalid IMSIs   : {res['bad_imsi']}\n")
        f.write("PLMNs seen      : " + ", ".join(f"MCC {a} / MNC {b}" for a, b in sorted(res["plmns"])) + "\n\n")
        f.write("Failures by node:\n")
        for k, v in res["by_node"].most_common():
            f.write(f"  {k:<6} {v}\n")
        f.write("\nFailures by event:\n")
        for k, v in res["by_event"].most_common():
            f.write(f"  {k:<22} {v}\n")
        f.write("\nTop IMSIs by failure count:\n")
        for k, v in res["by_imsi"].most_common(5):
            f.write(f"  {k}  {v}\n")
        f.write("\nFailure detail:\n")
        for ts, node, event, imsi, line in res["failures"]:
            f.write(f"  {line}\n")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "core_test_logs.txt"
    dst = sys.argv[2] if len(sys.argv) > 2 else "summary_report.txt"
    try:
        result = parse(src)
    except FileNotFoundError:
        sys.exit(f"File not found: {src}")
    write_report(result, dst)
    print(f"Parsed {result['total']} lines, {len(result['failures'])} failures -> {dst}")

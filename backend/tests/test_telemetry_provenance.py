"""Guards against telemetry fabrication regressing (ADR-010).

Asserting "this value is not hardcoded" is impossible in general. Three angles
get close:

  1. STATIC   - no bare multipliers or magic constants standing in for readings.
  2. SCHEMA   - every numeric field carries `measured`; unmeasured ones are null.
  3. RESPONSE - change the world, assert the number moves. A constant cannot.

(3) is the one that actually catches fabrication.

Run:  python tests/test_telemetry_provenance.py
"""
import os
import re
import shutil
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import database as db

fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        fails.append(name)


# --- 1. STATIC: the fabricating patterns must not come back ------------------
src = open(os.path.join(BACKEND, "database.py"), encoding="utf-8").read()

banned = {
    "used_bytes * 0.62 share": r"used_bytes\s*\*\s*0\.6",
    "used_bytes * 0.32 share": r"used_bytes\s*\*\s*0\.3",
    "hardcoded 2.0 GB RAM":    r'"used_gb"\s*:\s*2\.0',
    "hardcoded 50% OCPU":      r'"percent"\s*:\s*50\.0',
    "hardcoded egress est.":   r"estimated_used_gb",
    '"Safe (" status string':  r'"Safe \(',
}
for label, pattern in banned.items():
    check(f"static: no {label}", re.search(pattern, src) is None)

suspicious = re.findall(r"(?:used|total|free|size)_bytes\s*\*\s*0\.\d+", src)
check("static: no float multiplier applied to a measured byte count",
      not suspicious, f"{suspicious}")


# --- 2. SCHEMA: every numeric field declares its provenance ------------------
db.init_pool()
db.init_db()
summary = db.get_telemetry_summary()

check("schema: provenance block present", "provenance" in summary)
check("schema: oracle_quota key removed", "oracle_quota" not in summary)

for item in summary["free_tier"]["items"]:
    has_flag = "measured" in item
    check(f"schema: free_tier[{item['key']}] declares measured", has_flag)
    if has_flag and not item["measured"]:
        check(f"schema: unmeasured {item['key']} has null percent",
              item.get("percent") is None)
        check(f"schema: unmeasured {item['key']} explains why",
              bool(item.get("unavailable_reason")))

for f in summary["folder_analytics"]["folders"]:
    check(f"schema: folder '{f['name']}' declares measured=True", f.get("measured") is True)
    check(f"schema: folder '{f['name']}' names its source", bool(f.get("source")))

check("schema: folder analytics states its scope",
      "container" in summary["folder_analytics"]["scope"].lower())
check("schema: unaccounted remainder is exposed, not distributed",
      "unaccounted_bytes" in summary["folder_analytics"])

meminfo_total = db._read_meminfo().get("MemTotal")
check("schema: reported memory total equals /proc/meminfo",
      summary["system"]["memory"]["total_bytes"] == meminfo_total,
      f"({summary['system']['memory']['total_display']})")
check("schema: memory is NOT the old hardcoded 2.0 GB",
      summary["system"]["memory"]["total_bytes"] != int(2.0 * 1024 ** 3))

# Two panels disagreeing about current memory looks exactly like fabrication.
mem_panel = summary["system"]["memory"]["used_percent"]
mem_quota = next(i for i in summary["free_tier"]["items"] if i["key"] == "memory")["percent"]
check("schema: memory panel and quota panel report the SAME reading",
      mem_panel == mem_quota, f"({mem_panel} vs {mem_quota})")


# --- 3. RESPONSE: change the world, the number must move --------------------
probe_dir = os.path.join(BACKEND, "output")
os.makedirs(probe_dir, exist_ok=True)
probe = os.path.join(probe_dir, "_provenance_probe.bin")


def output_dir_bytes():
    for f in db.get_folder_storage_sizes()["folders"]:
        if f["path"] == probe_dir:
            return f["size_bytes"]
    return None


before = output_dir_bytes()
with open(probe, "wb") as fh:
    fh.write(b"\0" * (5 * 1024 * 1024))          # add exactly 5 MB
after = output_dir_bytes()
os.remove(probe)
restored = output_dir_bytes()

check("response: output dir was measurable before the probe", before is not None, f"({before} B)")
check("response: writing 5 MB moved the measurement",
      before is not None and after is not None and after - before >= 5 * 1024 * 1024,
      f"(+{(after - before) if (before is not None and after is not None) else '?'} B)")
check("response: deleting the probe moved it back",
      restored is not None and abs(restored - before) < 64 * 1024)

real_total, real_used, real_free = shutil.disk_usage("/")
fresh = db.get_folder_storage_sizes()
check("response: disk total matches shutil.disk_usage",
      fresh["disk_total_bytes"] == real_total)
check("response: disk used within 1% of shutil.disk_usage",
      abs(fresh["disk_used_bytes"] - real_used) < real_used * 0.01)

check("response: _du_bytes returns None for an unreadable path",
      db._du_bytes("/definitely/not/a/real/path") is None)

db.close_pool()
print()
print("ALL PASSED" if not fails else f"{len(fails)} FAILURE(S): {fails}")
sys.exit(1 if fails else 0)

# Standalone reach measurement for the A1 per-field concurrency audit: scans
# the kernel tree for lockless-access markers on the accessed fields and
# reports the tier-(b) demotion rate. Robust-by-construction (fixed-marker
# grep + Python ->field extraction) and self-proves non-vacuous (asserts
# 'flags' is found). realize.py's field_audit() uses the same logic inline;
# this script is the offline census over fn_fields.json.
import json
import os
import re
import subprocess
from collections import Counter

LS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

fn_fields = {k: set(v) for k, v in json.load(open(os.path.join(LS, "dream/realize/fn_fields.json"))).items()}
all_fields = set().union(*fn_fields.values())
print(f"candidates: {len(fn_fields)}, distinct accessed fields: {len(all_fields)}")

dirs = [os.path.join(KSRC, d) for d in
        ("kernel", "mm", "block", "fs", "net", "drivers", "lib", "sound",
         "crypto", "security", "include", "arch/arm64", "ipc")]

# ROBUST: grep the three markers as fixed strings (no field alternation, no
# \w-in-bracket ambiguity), then extract ->field in Python and intersect.
racy_all = Counter()          # every ->field seen in a marker, tree-wide
for marker in ("READ_ONCE(", "WRITE_ONCE(", "data_race("):
    r = subprocess.run(["grep", "-rhoF", "--include=*.c", "--include=*.h", marker, *dirs],
                       capture_output=True, text=True)
    # -oF gives just the marker; we need the surrounding arg, so re-grep with -E for the line
for marker in ("READ_ONCE", "WRITE_ONCE", "data_race"):
    r = subprocess.run(
        ["grep", "-rhoE", "--include=*.c", "--include=*.h",
         marker + r"\([^;]*->[A-Za-z_][A-Za-z0-9_]*", *dirs],
        capture_output=True, text=True)
    assert r.returncode in (0, 1), f"grep error: {r.stderr[:200]}"
    for ln in r.stdout.splitlines():
        for m in re.finditer(r"->([A-Za-z_][A-Za-z0-9_]*)", ln):
            racy_all[m.group(1)] += 1

# non-vacuous proof: known racy fields must show up
for probe in ("flags", "state", "head", "count", "refcount"):
    print(f"  NONVACUOUS probe {probe!r}: seen {racy_all.get(probe,0)}x tree-wide")
assert racy_all.get("flags", 0) > 0, "VACUOUS AUDIT — flags not found, aborting"

racy = {f for f in all_fields if racy_all.get(f, 0) > 0}
print(f"accessed fields that appear in a lockless marker anywhere: {len(racy)}/{len(all_fields)}")
demoted = {k for k, fs in fn_fields.items() if fs & racy}
n = len(fn_fields)
print(f"DEMOTED by name-level conservative audit: {len(demoted)}/{n} ({100*len(demoted)/n:.0f}%)")
print(f"AUDIT-PASS (tier-b eligible): {n-len(demoted)} ({100*(n-len(demoted))/n:.0f}%)")
print("top demoting fields:", Counter(f for k in demoted for f in fn_fields[k] & racy).most_common(15))
json.dump({"n_singlenode": n, "demoted": sorted(demoted),
           "racy_accessed_fields": sorted(racy)},
          open(os.path.join(LS, "dream/realize/audit_preview.json"), "w"), indent=0)
print("wrote audit_preview.json")

#!/usr/bin/env python3
"""M1 proof: the static `protects` map matches the runtime race observation.

One subject (`ringbuf.c`), read two ways:

  static  — extract.py builds {protects, unprotected_accesses} from the source.
  runtime — compile with -fsanitize=thread, run the concurrent harness, and see
            which struct fields TSan reports races on.

The milestone (design.md §4) holds iff:
  * NO field the static map calls protected shows up in a TSan race, and
  * every field TSan does race on was flagged unprotected by the static map.

TSan is the userspace stand-in for KCSAN/lockdep; run this where clang has TSan
(Linux or macOS). Exits non-zero if the two readings disagree.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract import extract  # noqa: E402

SUBJECT = os.path.join(HERE, "ringbuf.c")

# obj->field / obj.field on a source line, to map a cited line -> struct field.
_FIELD_ON_LINE = re.compile(r"\b[A-Za-z_]\w*\s*(?:->|\.)\s*([A-Za-z_]\w*)")


def run_tsan() -> str:
    """Compile the subject under TSan, run it, return combined stderr (reports)."""
    binpath = os.path.join(HERE, "ringbuf_tsan")
    # -O0: no inlining, so TSan attributes a racy access to the exact source line
    # of the field write (not the collapsed call site), which we map back to a field.
    subprocess.run(
        ["clang", "-fsanitize=thread", "-O0", "-g", SUBJECT, "-o", binpath],
        check=True,
    )
    # halt_on_error=0 so every distinct race is reported, not just the first.
    env = {**os.environ, "TSAN_OPTIONS": "halt_on_error=0 exitcode=0"}
    proc = subprocess.run([binpath], capture_output=True, text=True, env=env)
    return proc.stdout + proc.stderr


def raced_fields(report: str, src_lines: list[str]) -> set[str]:
    """Struct fields TSan observed a race on. TSan cites the racy access as
    `ringbuf.c:NN`; we read line NN of the subject and pull the field off it —
    robust to -O inlining renaming the enclosing frame."""
    fields: set[str] = set()
    for lineno in {int(n) for n in re.findall(r"ringbuf\.c:(\d+)", report)}:
        if 1 <= lineno <= len(src_lines):
            m = _FIELD_ON_LINE.search(src_lines[lineno - 1])
            if m:
                fields.add(m.group(1))
    return fields


def main() -> int:
    src = open(SUBJECT).read()
    ir = extract(src)
    protected = {
        f for d in ir["protects"].values() for fs in d.values() for f in fs
    }
    flagged_unprotected = {u["field"] for u in ir["unprotected_accesses"]}

    print("STATIC reading (extract.py):")
    print(f"  protected fields   : {sorted(protected)}")
    print(f"  flagged unprotected: {sorted(flagged_unprotected)}")

    report = run_tsan()
    raced = raced_fields(report, src.splitlines())
    n_reports = report.lower().count("data race")
    print("\nRUNTIME reading (ThreadSanitizer):")
    print(f"  TSan race reports  : {n_reports}")
    print(f"  fields raced on    : {sorted(raced)}")

    # The two agreement checks.
    false_safe = raced & protected  # map said safe, TSan raced -> map is WRONG
    unflagged = raced - flagged_unprotected  # raced but map never warned

    ok = True
    print("\nCROSS-CHECK:")
    if false_safe:
        print(f"  ✗ map called PROTECTED but TSan raced: {sorted(false_safe)}")
        ok = False
    else:
        print("  ✓ no field the map called protected raced at runtime")
    if unflagged:
        print(f"  ✗ TSan raced on fields the map never flagged: {sorted(unflagged)}")
        ok = False
    else:
        print("  ✓ every raced field was flagged unprotected by the static map")
    if not raced:
        print("  ✗ TSan observed no race — harness did not exercise the bug")
        ok = False

    print("\nM1 PROOF:", "PASS — static map matches runtime" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

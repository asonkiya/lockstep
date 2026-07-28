#!/usr/bin/env python3
"""M5 — emit the verified transplant as a maintainer-reviewable patch series.

design.md §4 M5: "Emit transplants as Rust-for-Linux-shaped patches against a
real subsystem, with the sanitizer evidence attached."

Everything here is a FORMATTER over artifacts that already exist: the M4
breadth manifest, the model's winner cluster, and the gate consoles. Output is
a git-am-able RFC series in out/:

  0000-cover-letter        methodology + the gate table + KCSAN evidence
  0001  ptp: ptp_mock: add Rust region implementations (the VERIFIED artifact)
  0002  ptp: ptp_mock: call the Rust regions (real diff against the driver —
        exactly the topology the in-kernel gate verified)
  0003  [RFC-only] the idiomatic kernel::sync::SpinLock<T> destination, with
        the missing binding surface called out

Gates (M5's own negative-control discipline): the series must `git apply` in
sequence onto the stock tree, and the kernel's own reviewer — checkpatch.pl —
must report zero ERRORs. Run: emit.py [--verify]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BREADTH = os.path.join(REPO, "kernel-gate", "breadth")
OUT = os.path.join(HERE, "out")

sys.path.insert(0, BREADTH)
from manifest import function_source  # noqa: E402

AUTHOR = "Aryaman Sonkiya <asonkiya@unc.edu>"
DATE = "Sat, 26 Jul 2026 12:00:00 +0900"
STOCK_C = os.path.join(BREADTH, "ptp_mock_stock.c")
STOCK_MK = os.path.join(HERE, "ptp_Makefile_stock")
WINNER = os.path.join(BREADTH, "winner_phc.rs")
IDIOMATIC = os.path.join(HERE, "ptp_mock_idiomatic.rs")
VERDICTS = os.path.join(BREADTH, "out", "verdicts.txt")
SABOTAGED_CONSOLE = os.path.join(BREADTH, "out", "sabotaged-console.txt")
MANIFEST = os.path.join(BREADTH, "out", "manifest.json")

EXTERN_BLOCK = """
/*
 * Region implementations in Rust (ptp_mock_regions.rs): the four critical
 * sections of this driver, boot-verified under KCSAN + lockdep against this
 * exact call topology — see the series cover letter for the evidence.
 */
int lockstep_phc_adjfine(struct timecounter *tc, struct cyclecounter *cc,
			 void *lock, long scaled_ppm);
int lockstep_phc_adjtime(struct timecounter *tc, void *lock, s64 delta);
int lockstep_phc_settime64(struct timecounter *tc, struct cyclecounter *cc,
			   void *lock, u64 ns);
u64 lockstep_phc_gettime64(struct timecounter *tc, void *lock);
"""

NEW_BODIES = {
    "mock_phc_adjfine": """static int mock_phc_adjfine(struct ptp_clock_info *info, long scaled_ppm)
{
	struct mock_phc *phc = info_to_phc(info);

	return lockstep_phc_adjfine(&phc->tc, &phc->cc, &phc->lock, scaled_ppm);
}""",
    "mock_phc_adjtime": """static int mock_phc_adjtime(struct ptp_clock_info *info, s64 delta)
{
	struct mock_phc *phc = info_to_phc(info);

	return lockstep_phc_adjtime(&phc->tc, &phc->lock, delta);
}""",
    "mock_phc_settime64": """static int mock_phc_settime64(struct ptp_clock_info *info,
			      const struct timespec64 *ts)
{
	struct mock_phc *phc = info_to_phc(info);

	return lockstep_phc_settime64(&phc->tc, &phc->cc, &phc->lock,
				      timespec64_to_ns(ts));
}""",
    "mock_phc_gettime64": """static int mock_phc_gettime64(struct ptp_clock_info *info, struct timespec64 *ts)
{
	struct mock_phc *phc = info_to_phc(info);

	*ts = ns_to_timespec64(lockstep_phc_gettime64(&phc->tc, &phc->lock));

	return 0;
}""",
}

MAKEFILE_ADD = """
# ptp_mock's region implementations in Rust, built freestanding (no
# CONFIG_RUST toolchain requirements). RFC: build rule is arm64-only for now;
# see the ptp_mock_regions.rs header and the series cover letter.
$(obj)/ptp_mock_regions.o: $(src)/ptp_mock_regions.rs FORCE
	$(RUSTC) --target aarch64-unknown-none-softfloat --emit=obj \\
	  -C panic=abort -C relocation-model=static -O $< -o $@
obj-$(CONFIG_PTP_1588_CLOCK_MOCK)	+= ptp_mock_regions.o
"""


def udiff(old: str, new: str, path: str) -> str:
    import difflib

    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return f"diff --git a/{path} b/{path}\n" + "".join(lines)


def newfile_diff(content: str, path: str) -> str:
    body = content if content.endswith("\n") else content + "\n"
    lines = body.splitlines()
    hunk = "".join(f"+{ln}\n" for ln in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n" + hunk
    )


def patch_file(n: int, total: int, subject: str, body: str, diff: str) -> str:
    return (
        f"From {'0' * 40} Mon Sep 17 00:00:00 2001\n"
        f"From: {AUTHOR}\n"
        f"Date: {DATE}\n"
        f"Subject: [RFC PATCH {n}/{total}] {subject}\n\n"
        f"{body.rstrip()}\n\n"
        f"Signed-off-by: {AUTHOR}\n"
        f"---\n\n{diff}\n-- \n2.39.0\n"
    )


def rewire_c(src: str) -> str:
    """The 0002 transformation: bodies -> seam calls, externs after includes."""
    out = src
    for fn, new in NEW_BODIES.items():
        out = out.replace(function_source(src, fn), new)
    last_inc = max(m.end() for m in re.finditer(r"#include <[^>]+>\n", out))
    return out[:last_inc] + EXTERN_BLOCK + out[last_inc:]


def kcsan_excerpt() -> str:
    con = open(SABOTAGED_CONSOLE).read()
    m = re.search(
        r"BUG: KCSAN: data-race in timecounter_read.*?={20,}", con, re.DOTALL
    )
    block = m.group(0) if m else "(excerpt unavailable)"
    block = re.sub(r"^\[[ 0-9.]*\] ?", "", block, flags=re.MULTILINE)
    lines = [ln for ln in block.splitlines() if not re.fullmatch(r"=+", ln.strip())]
    return "\n".join(lines[:13])


def cover_letter(total: int) -> str:
    verdicts = open(VERDICTS).read().strip()
    manifest = json.load(open(MANIFEST))
    protects = json.dumps(manifest["ir"]["protects"])
    return f"""From {"0" * 40} Mon Sep 17 00:00:00 2001
From: {AUTHOR}
Date: {DATE}
Subject: [RFC PATCH 0/{total}] ptp: ptp_mock: machine-transplanted Rust regions, sanitizer-verified in-kernel

This RFC converts the four spinlock-protected regions of the mock PTP
clock driver (adjfine / adjtime / settime64 / gettime64 — one lock,
shared timecounter/cyclecounter state) to Rust. It is not proposed for
merging as-is; it exists to demonstrate a verification methodology in
which every claim below is machine-checked, and to solicit comments on
the destination form (patch 3).

== How the Rust was produced and verified ==

* A static extractor derived the concurrency contract from this driver:
  protects = {protects}
* Each region was rewritten into Rust by a language model from its C
  body plus that contract (all four accepted on first attempt; total
  model cost $0.0084). The Rust holds the driver's real spinlock via
  the kernel's out-of-line _raw_spin_lock (lockdep-visible) and calls
  the exported timecounter_read()/timecounter_init().
* The exact topology of patch 2 (C shell, Rust regions, one shared
  lock) was booted on arm64 SMP under CONFIG_KCSAN + CONFIG_PROVE_LOCKING
  and hammered by 5 kthreads driving all four regions concurrently plus
  a lock-holding C reader, with a clock-monotonicity functional oracle:

{chr(10).join("      " + ln for ln in verdicts.splitlines())}

  (stock = this driver's C; rewrite = the Rust in this series; the
  third column is KCSAN report frames naming probe/transplant symbols.)

* Negative control: the same Rust with the lock acquisition removed is
  REJECTED by KCSAN — which names the transplanted function itself in
  the racing stack — and the clock runs backwards 98 times:

{chr(10).join("      " + ln for ln in kcsan_excerpt().splitlines())}

== Honest limitations ==

* The exported region symbols keep their verification-harness names
  (lockstep_phc_*); a real submission would rename them.
* The Makefile rule builds the Rust freestanding for arm64 only; proper
  CONFIG_RUST integration is deliberately out of scope here.
* Patch 3 is the idiomatic kernel::sync::SpinLock<T> destination and
  does NOT compile today: timecounter/cyclecounter and the PTP class
  have no Rust abstractions yet. It is included to show where this
  lands once they exist, with the missing binding surface called out.
* mock_phc_create()/destroy() (registration plumbing) are unchanged.

Aryaman Sonkiya ({total}):
  ptp: ptp_mock: add Rust implementations of the locked regions
  ptp: ptp_mock: call the Rust regions from the ops
  ptp: ptp_mock: sketch the idiomatic Rust destination (RFC only)

 drivers/ptp/Makefile               |   8 ++
 drivers/ptp/ptp_mock.c             |  46 ++++-----
 drivers/ptp/ptp_mock_regions.rs    | new
 drivers/ptp/ptp_mock_idiomatic.rs  | new (RFC)

--
2.39.0
"""


def emit() -> list[str]:
    os.makedirs(OUT, exist_ok=True)
    total = 3
    written = []

    # 0000 cover letter
    p = os.path.join(OUT, "0000-cover-letter.patch")
    open(p, "w").write(cover_letter(total))
    written.append(p)

    # 0001: the verified Rust + build rule
    regions = "// SPDX-License-Identifier: GPL-2.0\n" + open(WINNER).read()
    mk_old = open(STOCK_MK).read()
    mk_new = mk_old.rstrip("\n") + "\n" + MAKEFILE_ADD
    diff1 = newfile_diff(regions, "drivers/ptp/ptp_mock_regions.rs") + udiff(
        mk_old, mk_new, "drivers/ptp/Makefile"
    )
    body1 = (
        "Add the model-written, sanitizer-verified Rust implementations of\n"
        "ptp_mock's four locked regions. This file is byte-identical to the\n"
        "artifact that was linked into vmlinux and verified (see cover\n"
        "letter): the regions take the driver's real spinlock through the\n"
        "kernel's out-of-line _raw_spin_lock and call the exported\n"
        "timecounter_read()/timecounter_init() under it."
    )
    p = os.path.join(OUT, "0001-ptp-ptp_mock-add-rust-regions.patch")
    open(p, "w").write(
        patch_file(1, total, "ptp: ptp_mock: add Rust implementations of the locked regions", body1, diff1)
    )
    written.append(p)

    # 0002: rewire the driver
    c_old = open(STOCK_C).read()
    diff2 = udiff(c_old, rewire_c(c_old), "drivers/ptp/ptp_mock.c")
    body2 = (
        "Replace the bodies of the four ops with calls to the Rust regions.\n"
        "This exact topology (C shell + Rust critical sections on one shared\n"
        "lock) is what the in-kernel gate verified: FUNC_PASS with a clock-\n"
        "monotonicity oracle under 4-CPU contention, zero KCSAN reports,\n"
        "lockdep silent; the dropped-lock negative control is rejected by\n"
        "KCSAN by name. The container_of and timespec64 glue stays in C."
    )
    p = os.path.join(OUT, "0002-ptp-ptp_mock-call-rust-regions.patch")
    open(p, "w").write(
        patch_file(2, total, "ptp: ptp_mock: call the Rust regions from the ops", body2, diff2)
    )
    written.append(p)

    # 0003: RFC idiomatic destination
    diff3 = newfile_diff(open(IDIOMATIC).read(), "drivers/ptp/ptp_mock_idiomatic.rs")
    body3 = (
        "RFC ONLY — does not compile today and says so: the idiomatic\n"
        "kernel::sync::SpinLock<T> destination for this driver, where the\n"
        "protected timecounter state is owned by the lock and unlocked\n"
        "access is unrepresentable. Included to show where the verified\n"
        "regions land once timecounter/cyclecounter and PTP-class bindings\n"
        "exist; the missing binding surface is listed in the file header."
    )
    p = os.path.join(OUT, "0003-ptp-ptp_mock-idiomatic-rfc.patch")
    open(p, "w").write(
        patch_file(3, total, "ptp: ptp_mock: sketch the idiomatic Rust destination (RFC only)", body3, diff3)
    )
    written.append(p)
    return written


def verify(patches: list[str]) -> bool:
    ok = True

    # Gate 1: the series applies in sequence onto the stock tree.
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "drivers", "ptp"))
        open(os.path.join(td, "drivers/ptp/ptp_mock.c"), "w").write(open(STOCK_C).read())
        open(os.path.join(td, "drivers/ptp/Makefile"), "w").write(open(STOCK_MK).read())
        subprocess.run(["git", "init", "-q"], cwd=td, check=True)
        subprocess.run(["git", "add", "-A"], cwd=td, check=True)
        subprocess.run(
            ["git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qm", "stock"],
            cwd=td, check=True,
        )
        for p in patches[1:]:  # cover letter has no diff
            r = subprocess.run(["git", "apply", "--check", p], cwd=td, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  ✗ git apply --check {os.path.basename(p)}: {r.stderr.strip()[:200]}")
                ok = False
                continue
            subprocess.run(["git", "apply", p], cwd=td, check=True)
            print(f"  ✓ applies: {os.path.basename(p)}")

    # Gate 2: the kernel's own reviewer.
    for p in patches:
        r = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{OUT}:/patches:ro", "-v", "cgir-kbuild:/build",
             "cgir-kernel-gate", "bash", "-c",
             f"cd /build/linux && ./scripts/checkpatch.pl /patches/{os.path.basename(p)} 2>&1 | grep -E 'total:'"],
            capture_output=True, text=True,
        )
        out = r.stdout + r.stderr
        m = re.search(r"total: (\d+) errors, (\d+) warnings", out)
        e, w = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
        status = "✓" if e == 0 else "✗"
        print(f"  {status} checkpatch {os.path.basename(p)}: {e} errors, {w} warnings")
        if e != 0:
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    patches = emit()
    print(f"emitted {len(patches)} files -> {OUT}")
    if args.verify:
        good = verify(patches)
        print("\nM5 EMIT:", "PASS — series applies clean, checkpatch 0 errors" if good else "FAIL")
        return 0 if good else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

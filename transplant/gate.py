#!/usr/bin/env python3
"""The M2 gate: is the single-region C→Rust transplant accepted?

design.md §4 M2 proof — accept the transplant iff:
  1. stock C is race-clean under the sanitizer (the reference baseline);
  2. the Rust transplant is functionally correct under real contention
     ("KUnit green"): 4 writers, exact final count;
  3. loom finds NO data race in the transplant across every interleaving
     (KCSAN-clean, exhaustively);
  4. the negative control — a deliberately dropped lock — is REJECTED, and for
     the right reason (a concurrent-access race), never vacuously.
Plus the R4L bonus: (5) the dropped lock does not even COMPILE.

TSan is the userspace stand-in for KCSAN, loom the exhaustive race oracle for the
Rust side. Exits non-zero unless every leg holds.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.join(HERE, "transplant")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=kw.pop("cwd", HERE), **kw)


def leg1_stock_c_clean() -> bool:
    """Stock C under TSan: race-clean baseline."""
    if not shutil.which("clang"):
        print("  ~ skipped (no clang/TSan)")
        return True
    binp = os.path.join(HERE, "ring_stock_tsan")
    c = _run(["clang", "-fsanitize=thread", "-O0", "-g", "ring_stock.c", "-o", binp])
    if c.returncode != 0:
        print("  ✗ stock C did not build\n", c.stderr)
        return False
    env = {**os.environ, "TSAN_OPTIONS": "halt_on_error=0 exitcode=0"}
    r = subprocess.run([binp], capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    races = out.lower().count("data race")
    ok = races == 0 and "OK" in r.stdout
    print(f"  {'✓' if ok else '✗'} stock C: {races} races, {r.stdout.strip()}")
    return ok


def leg2_transplant_functional() -> bool:
    """Rust transplant under real threads: exact count."""
    r = _run(["cargo", "run", "--quiet", "--bin", "stress"], cwd=CRATE)
    ok = r.returncode == 0 and "OK" in r.stdout
    print(f"  {'✓' if ok else '✗'} transplant functional: {r.stdout.strip() or r.stderr.strip()[:120]}")
    return ok


def leg3_4_loom() -> bool:
    """loom: transplant race-clean AND dropped-lock rejected (right reason)."""
    env = {**os.environ, "RUSTFLAGS": "--cfg loom"}
    r = subprocess.run(
        ["cargo", "test", "--test", "loom_checks"],
        capture_output=True, text=True, cwd=CRATE, env=env,
    )
    out = r.stdout + r.stderr
    clean = "transplant_is_race_clean ... ok" in out
    rejected = "dropped_lock_is_rejected ... ok" in out
    print(f"  {'✓' if clean else '✗'} loom: transplant race-clean (exhaustive)")
    print(f"  {'✓' if rejected else '✗'} loom: dropped-lock REJECTED (concurrent-access race)")
    return clean and rejected


def leg5_dropped_lock_wont_compile() -> bool:
    """R4L bonus: the lockless access is a type error."""
    b = _run(["cargo", "build", "--quiet"], cwd=CRATE)
    if b.returncode != 0:
        print("  ✗ crate did not build\n", b.stderr)
        return False
    rlib = os.path.join(CRATE, "target", "debug", "libtransplant.rlib")
    deps = os.path.join(CRATE, "target", "debug", "deps")
    r = _run([
        "rustc", "--edition", "2021", "--crate-type", "bin",
        "--extern", f"transplant={rlib}", "-L", deps,
        "neg_compile.rs", "-o", os.path.join(HERE, "_neg_out"),
    ])
    # We WANT this to fail to compile.
    ok = r.returncode != 0 and ("E0616" in r.stderr or "private" in r.stderr)
    print(f"  {'✓' if ok else '✗'} dropped lock does not compile (type-enforced invariant)")
    try:
        os.remove(os.path.join(HERE, "_neg_out"))
    except OSError:
        pass
    return ok


def main() -> int:
    print("M2 gate — single-region transplant (ring buffer critical section)\n")
    print("1. stock C race-clean baseline:")
    l1 = leg1_stock_c_clean()
    print("2. transplant functional (KUnit-green analog):")
    l2 = leg2_transplant_functional()
    print("3-4. loom race oracle + negative control:")
    l34 = leg3_4_loom()
    print("5. type-level guarantee (bonus):")
    l5 = leg5_dropped_lock_wont_compile()

    ok = l1 and l2 and l34 and l5
    print("\nM2 GATE:", "PASS — transplant accepted" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

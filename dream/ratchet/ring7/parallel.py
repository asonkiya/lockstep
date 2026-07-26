#!/usr/bin/env python3
"""Ring 7 — parallel QEMU workers: the last wall-clock lever from the research.

Rings 5-6 verified fleets one boot at a time. The research's cost model says the
bottleneck is verification wall-clock and the fix is BATCHES x PARALLEL WORKERS:
independent build+boot pipelines on independent kernel trees, concurrently. Ring 7
exercises that — two pristine volumes, the fleet split into two batches, both
built and booted at the same time. If wall-clock ~ max(batch) rather than
sum(batches), the lever works and scales linearly with worker count.

Reuses the Ring 5 fleet candidates (already synthesized) + WORKLIST metadata.
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RATCHET = os.path.dirname(HERE)
RING5 = os.path.join(RATCHET, "ring5")
KSRC = "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"
IMG = "cgir-kernel-gate"
OUT = os.path.join(RATCHET, "out")
sys.path.insert(0, RING5)
import fleet  # noqa: E402  (WORKLIST + probe pieces)

# split the 6-function fleet across two workers
BATCHES = {
    "A": ["lcm", "int_pow", "int_sqrt"],
    "B": ["gcd", "__sw_hweight32", "lcm_not_zero"],
}
VOLS = {"A": "cgir-kbuild", "B": "cgir-kbuild-w2"}


def sh(vol, script, mounts=None, capture=True):
    cmd = ["docker", "run", "--rm", "-v", f"{vol}:/build"]
    for m in mounts or []:
        cmd += ["-v", m]
    cmd += [IMG, "bash", "-eo", "pipefail", "-uc", script]
    return subprocess.run(cmd, capture_output=True, text=True)


def restore_pristine(vol):
    """Reset a volume's tree to pristine C (undo any weave) so differential
    gating has real C references."""
    files = ("drivers/ptp/ptp_mock.c drivers/ptp/Makefile lib/math/int_sqrt.c "
             "lib/math/int_pow.c lib/hweight.c lib/Makefile lib/math/Makefile "
             "lib/math/lcm.c crypto/Makefile")
    sh(VOLS["A"] if vol is None else vol,
       f"cd /build/linux && for f in {files}; do cp /src/$f $f 2>/dev/null || true; done && "
       "rm -f drivers/ptp/*_regions.o_shipped lib/math/*_rust.o_shipped lib/*_rust.o_shipped && "
       "rm -rf crypto/lockstep_gate && ./scripts/config -d PTP_1588_CLOCK_MOCK >/dev/null 2>&1 && "
       "make -s olddefconfig >/dev/null 2>&1 || true",
       mounts=[f"{KSRC}:/src:ro"])


def ensure_second_volume():
    have = subprocess.run(["docker", "volume", "inspect", "cgir-kbuild-w2"], capture_output=True)
    if have.returncode == 0:
        print("  worker volume cgir-kbuild-w2 exists")
        return
    print("  cloning cgir-kbuild -> cgir-kbuild-w2 (one-time, ~3.7GB)...")
    subprocess.run(["docker", "volume", "create", "cgir-kbuild-w2"], capture_output=True)
    t0 = time.time()
    subprocess.run(["docker", "run", "--rm", "-v", "cgir-kbuild:/from", "-v", "cgir-kbuild-w2:/to",
                    IMG, "bash", "-c", "cp -a /from/. /to/"], capture_output=True)
    print(f"  clone done in {time.time() - t0:.0f}s")


def gen_probe(batch):
    entries = [w for w in fleet.WORKLIST if w["sym"] in BATCHES[batch]]
    externs = "\n".join(e["extern"] for e in entries)
    blocks = []
    for e in entries:
        blocks.append(
            f'\t{{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;\n'
            f'\t  {e["loop"]}\n'
            f'\t  pr_emerg("PW_PROBE: {e["sym"]} bad=%lu verdict=%s\\n", bad, bad==0?"DIFF_PASS":"DIFF_FAIL"); }}'
        )
    src = ("// SPDX-License-Identifier: GPL-2.0\n#include <linux/init.h>\n#include <linux/kernel.h>\n"
           "#include <linux/types.h>\n\n" + externs + "\n\nstatic int __init pw_init(void)\n{\n"
           + "\n".join(blocks) + '\n\tpr_emerg("PW_PROBE: done\\n");\n\treturn 0;\n}\nlate_initcall(pw_init);\n')
    p = os.path.join(OUT, f"pw_probe_{batch}.c")
    open(p, "w").write(src)
    return p


def worker(batch):
    """One parallel worker: install its batch into its own volume, build, boot,
    return (verdicts, elapsed)."""
    vol = VOLS[batch]
    entries = [w for w in fleet.WORKLIST if w["sym"] in BATCHES[batch]]
    probe = gen_probe(batch)
    t0 = time.time()
    # install candidates + probe
    setup = ("cd /build/linux && mkdir -p crypto/lockstep_gate && "
             "grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile && "
             "cd crypto/lockstep_gate && rm -f *.c *.o *.o_shipped && cp /p/$(basename " + probe + ") pw_probe.c && ")
    objs = ["pw_probe.o"]
    for i, e in enumerate(entries):
        loc = "" if i == 0 else f" && aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' {e['obj']}_c.o_shipped"
        setup += (f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                  f"-C relocation-model=static -O /g/{e['obj']}.rs -o {e['obj']}_c.o_shipped{loc} && ")
        objs.append(f"{e['obj']}_c.o")
    setup += f"printf 'obj-y := {' '.join(objs)}\\n' > Kbuild"
    r = sh(vol, setup, mounts=[f"{RING5}:/g:ro", f"{OUT}:/p:ro"])
    if r.returncode != 0:
        return batch, "INSTALL_FAIL", time.time() - t0, (r.stdout + r.stderr)[-300:]
    b = sh(vol, "cd /build/linux && rm -f arch/arm64/boot/Image && make -s -j$(nproc) Image 2>&1 | tail -2 && test -f arch/arm64/boot/Image")
    if b.returncode != 0:
        return batch, "BUILD_FAIL", time.time() - t0, (b.stdout + b.stderr)[-300:]
    q = sh(vol, "cd /build/linux && timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 "
                "-nographic -net none -kernel arch/arm64/boot/Image "
                "-append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true")
    con = q.stdout + q.stderr
    open(os.path.join(OUT, f"pw_{batch}-console.txt"), "w").write(con)
    verdicts = [ln.split("] ", 1)[-1] for ln in con.splitlines() if "PW_PROBE:" in ln and "verdict=" in ln]
    return batch, verdicts, time.time() - t0, ""


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    print("Ring 7 — parallel workers")
    print("preparing two pristine worker volumes...")
    restore_pristine("cgir-kbuild")
    ensure_second_volume()
    restore_pristine("cgir-kbuild-w2")

    print(f"\nlaunching 2 workers CONCURRENTLY: A={BATCHES['A']} | B={BATCHES['B']}")
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(worker, ["A", "B"]))
    wall = time.time() - t0

    total_worker_time = 0.0
    allpass = True
    for batch, verdicts, elapsed, err in results:
        total_worker_time += elapsed
        print(f"\n[worker {batch}] {elapsed:.0f}s")
        if isinstance(verdicts, list):
            for v in verdicts:
                print("   " + v)
            allpass = allpass and all("DIFF_PASS" in v for v in verdicts) and len(verdicts) == len(BATCHES[batch])
        else:
            print(f"   {verdicts}: {err}")
            allpass = False

    print(f"\n=== PARALLELISM ===")
    print(f"  wall-clock (concurrent) : {wall:.0f}s")
    print(f"  sum of worker times     : {total_worker_time:.0f}s")
    print(f"  speedup                 : {total_worker_time / wall:.2f}x on 2 workers")
    print("RING 7:", "PASS" if allpass else "FAIL")
    return 0 if allpass else 1


if __name__ == "__main__":
    raise SystemExit(main())

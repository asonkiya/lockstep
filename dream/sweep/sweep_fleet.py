#!/usr/bin/env python3
"""The massive solve-rate fleet: parallel-synthesize every harvested Tier-A leaf,
verify them ALL in one boot against the kernel's own symbols, measure the real
solve rate + which fail and why. Honest: state-dependent "scalar" functions
(round_jiffies, is_prime, ...) should be REJECTED — that is the gate working.

Runs on a PRISTINE volume (differential needs the C originals, not woven shells).
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RATCHET = os.path.join(os.path.dirname(HERE), "ratchet")
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"
IMG, VOL, GATE = "cgir-kernel-gate", "cgir-kbuild", "crypto/lockstep_gate"
sys.path.insert(0, os.path.join(REPO, "m3"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
_ALL = json.load(open(os.path.join(HERE, "worklist.json")))
# Excluded before the boot, by honest reason (see sweep-console / build):
EXCLUDE = {
    # kernel reference symbol not linked in this config -> nothing to diff against
    "div64_s64": "ref not linked in config", "div64_u64": "ref not linked in config",
    "is_prime_number": "ref not linked in config", "next_prime_number": "ref not linked in config",
    "slow_is_prime_number": "ref not linked in config", "jiffies_to_msecs": "ref not linked in config",
    "jiffies_to_usecs": "ref not linked in config", "zstd_compress_bound": "ref not linked in config",
    # candidate needs a Rust intrinsic that isn't available freestanding
    "clock_t_to_jiffies": "candidate needs __udivti3 (u128 div) freestanding",
    # side-effectful: the reference actually sleeps -> not differentially testable,
    # and would hang the probe loop
    "msleep_interruptible": "side effect (sleeps) — not a pure differential target",
}
WORK = [w for w in _ALL if w["sym"] not in EXCLUDE]
RUST = {"i32": "i32", "u32": "u32", "i64": "i64", "u64": "u64", "i8": "i8", "u8": "u8",
        "i16": "i16", "u16": "u16", "bool": "bool", "usize": "usize"}
CT = {"i32": "int", "u32": "unsigned int", "i64": "long long", "u64": "unsigned long long",
      "i8": "signed char", "u8": "unsigned char", "i16": "short", "u16": "unsigned short",
      "bool": "int", "usize": "unsigned long"}
CSCALAR = {  # ctype -> rust
    "int": "i32", "unsigned int": "u32", "unsigned": "u32", "long": "i64",
    "unsigned long": "u64", "long long": "i64", "unsigned long long": "u64",
    "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64", "s8": "i8", "s16": "i16",
    "s32": "i32", "s64": "i64", "bool": "bool", "size_t": "usize",
}
PRELUDE = ("#![no_std]\n#![no_main]\n#[panic_handler]\nfn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n")


def rustsig(w):
    args = ", ".join(f"a{i}: {CSCALAR[t]}" for i, (t, _) in enumerate(w["args"]))
    return f'#[no_mangle]\npub extern "C" fn cgir_{w["sym"]}({args}) -> {CSCALAR[w["ret"]]}'


def _compiles(sym):
    path = os.path.join(HERE, "cand", f"{sym}.rs")
    if not os.path.exists(path):
        return False
    rc = subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.dirname(path)}:/w", IMG, "bash", "-c",
                         f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                         f"-C relocation-model=static -O {sym}.rs -o /tmp/{sym}.o 2>/dev/null && "
                         f"nm /tmp/{sym}.o 2>/dev/null | grep -c cgir_{sym}"],
                        capture_output=True, text=True)
    return rc.stdout.strip().split("\n")[-1] == "1"


def synth_one(w):
    if _compiles(w["sym"]):        # reuse a prior compiled candidate
        return w["sym"], True, 0.0
    import anthropic
    client = anthropic.Anthropic(api_key=_api_key())
    prompt = (
        "Reimplement this Linux kernel function as freestanding no_std Rust "
        "(target aarch64-unknown-none-softfloat), SELF-CONTAINED: inline any helper, "
        "assume nothing about global kernel state (if it reads a global/per-cpu/clock, "
        "you cannot — reproduce only the pure arithmetic). No panics, no externs, no "
        "allocation, wrapping arithmetic to match C.\n\n"
        f"```c\n{w['body']}\n```\n\n"
        f"Emit ONLY the Rust (no fences). Prelude (no_std/panic_handler) already present; "
        f"do NOT repeat it. First line `// leaf: cgir_{w['sym']}`, then exactly:\n{rustsig(w)}"
    )
    fb = None
    for _ in range(2):
        msgs = [{"role": "user", "content": prompt}]
        if fb:
            msgs += [{"role": "assistant", "content": "(prev)"}, {"role": "user", "content": f"FAILED:\n{fb}"}]
        r = client.messages.create(model=MODEL, max_tokens=900, messages=msgs)
        cost = (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        if f"cgir_{w['sym']}" not in code:
            fb = "wrong export"; continue
        path = os.path.join(HERE, "cand", f"{w['sym']}.rs")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(PRELUDE + "\n" + code + "\n")
        rc = subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.dirname(path)}:/w", IMG, "bash", "-c",
                             f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                             f"-C relocation-model=static -O {w['sym']}.rs -o /tmp/{w['sym']}.o 2>&1 | head -3 && "
                             f"nm /tmp/{w['sym']}.o 2>/dev/null | grep -c cgir_{w['sym']}"],
                            capture_output=True, text=True)
        if rc.stdout.strip().split("\n")[-1] == "1":
            return w["sym"], True, cost
        fb = "rustc: " + rc.stdout[-200:]
    return w["sym"], False, cost


def gen_probe(compiled):
    externs, blocks = [], []
    for w in compiled:
        # use the ORIGINAL C types (match any kernel header prototype exactly;
        # ABI-compatible with the Rust u64/i64/... the candidate exports)
        rt = w["ret"]
        cargs = ", ".join(t for t, _ in w["args"])
        externs.append(f"{rt} {w['sym']}({cargs});")
        externs.append(f"{rt} cgir_{w['sym']}({cargs});")
        # bounded input loops; multi-arg starts at 1 to avoid div-by-zero/oops
        na = len(w["args"])
        if na == 1:
            loop = f"for(long i0=0;i0<=2000;i0++){{c++;if(cgir_{w['sym']}(i0)!={w['sym']}(i0)){{bad++;if(fb<0)fb=i0;}}}}"
        elif na == 2:
            loop = (f"for(long i0=1;i0<=48;i0++)for(long i1=1;i1<=48;i1++){{c++;"
                    f"if(cgir_{w['sym']}(i0,i1)!={w['sym']}(i0,i1)){{bad++;if(fb<0)fb=i0*100+i1;}}}}")
        else:
            loop = (f"for(long i0=1;i0<=14;i0++)for(long i1=1;i1<=14;i1++)for(long i2=1;i2<=14;i2++){{c++;"
                    f"if(cgir_{w['sym']}(i0,i1,i2)!={w['sym']}(i0,i1,i2)){{bad++;if(fb<0)fb=1;}}}}")
        blocks.append(f'\t{{ unsigned long c=0,bad=0; long fb=-1;\n\t  {loop}\n'
                      f'\t  pr_emerg("SWEEP: {w["sym"]} n=%lu bad=%lu firstbad=%ld verdict=%s\\n",'
                      f' c,bad,fb, bad?"DIFF_FAIL":"DIFF_PASS"); }}')
    src = ("// SPDX-License-Identifier: GPL-2.0\n#include <linux/init.h>\n#include <linux/kernel.h>\n"
           "#include <linux/types.h>\n\n" + "\n".join(externs) +
           "\n\nstatic int __init sweep_init(void)\n{\n" + "\n".join(blocks) +
           '\n\tpr_emerg("SWEEP: done\\n");\n\treturn 0;\n}\nlate_initcall(sweep_init);\n')
    open(os.path.join(HERE, "sweep_probe.c"), "w").write(src)


def restore_pristine():
    files = ("drivers/ptp/ptp_mock.c drivers/ptp/Makefile lib/math/int_sqrt.c lib/math/int_pow.c "
             "lib/hweight.c lib/Makefile lib/math/Makefile lib/math/lcm.c crypto/Makefile")
    subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{KSRC}:/src:ro", IMG, "bash", "-c",
                    f"cd /build/linux && for f in {files}; do cp /src/$f $f 2>/dev/null||true; done && "
                    "rm -f drivers/ptp/*_regions.o_shipped lib/math/*_rust.o_shipped lib/*_rust.o_shipped && "
                    "rm -rf crypto/lockstep_gate && ./scripts/config -d PTP_1588_CLOCK_MOCK >/dev/null 2>&1 && "
                    "make -s olddefconfig >/dev/null 2>&1 || true"], capture_output=True)


def main():
    t0 = time.time()
    print(f"[sweep] parallel-synthesizing {len(WORK)} harvested leaves...")
    total, compiled = 0.0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        res = {w["sym"]: w for w in WORK}
        for sym, ok, cost in ex.map(synth_one, WORK):
            total += cost
            if ok:
                compiled.append(res[sym])
    print(f"[sweep] synth: {len(compiled)}/{len(WORK)} compiled, ${total:.4f}")
    json.dump([w["sym"] for w in compiled], open(os.path.join(HERE, "compiled.json"), "w"))

    print("[sweep] restoring pristine volume...")
    restore_pristine()
    gen_probe(compiled)
    objs = " ".join(f"{w['sym']}_c.o" for w in compiled)
    setup = (f"cd /build/linux && mkdir -p {GATE} && grep -q 'obj-y += lockstep_gate/' crypto/Makefile || "
             f"echo 'obj-y += lockstep_gate/' >> crypto/Makefile; cd {GATE} && rm -f *.c *.o *.o_shipped && "
             "cp /p/sweep_probe.c .; ")
    for i, w in enumerate(compiled):
        loc = "" if i == 0 else f" && aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' {w['sym']}_c.o_shipped"
        setup += (f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort -C relocation-model=static "
                  f"-O /cand/{w['sym']}.rs -o {w['sym']}_c.o_shipped{loc}; ")
    setup += f"printf 'obj-y := sweep_probe.o {objs}\\n' > Kbuild"
    print("[sweep] installing + building one kernel with all candidates...")
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{HERE}:/p:ro",
                        "-v", f"{os.path.join(HERE, 'cand')}:/cand:ro", IMG, "bash", "-euc", setup],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("[sweep] install failed:", (r.stdout + r.stderr)[-500:]); return 1
    b = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-eo", "pipefail", "-uc",
                        "cd /build/linux && rm -f arch/arm64/boot/Image && make -s -j$(nproc) Image 2>&1 | tail -3 && test -f arch/arm64/boot/Image"],
                       capture_output=True, text=True)
    open(os.path.join(HERE, "sweep-build.txt"), "w").write(b.stdout + b.stderr)
    if b.returncode != 0:
        print("[sweep] BUILD FAILED:", (b.stdout + b.stderr)[-600:]); return 1
    print("[sweep] booting + verifying the whole fleet in one boot...")
    q = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                        "cd /build/linux && timeout 400 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 "
                        "-nographic -net none -kernel arch/arm64/boot/Image "
                        "-append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true"],
                       capture_output=True, text=True)
    con = q.stdout + q.stderr
    open(os.path.join(HERE, "sweep-console.txt"), "w").write(con)
    verd = {}
    for ln in con.splitlines():
        m = re.search(r"SWEEP: (\w+) n=(\d+) bad=(\d+) .* verdict=DIFF_(PASS|FAIL)", ln)
        if m:
            verd[m.group(1)] = (m.group(4), int(m.group(2)), int(m.group(3)))
    npass = sum(1 for v in verd.values() if v[0] == "PASS")
    reached = "SWEEP: done" in con
    result = {
        "harvested_total": len(_ALL), "excluded_prelink": EXCLUDE, "attempted": len(WORK),
        "harvested": len(WORK), "synth_compiled": len(compiled), "synth_cost_usd": round(total, 4),
        "verified_pass": npass, "verified_fail": len(verd) - npass, "probe_reached_end": reached,
        "per_fn": {k: {"verdict": v[0], "n": v[1], "bad": v[2]} for k, v in sorted(verd.items())},
        "elapsed_s": round(time.time() - t0),
    }
    json.dump(result, open(os.path.join(HERE, "sweep_result.json"), "w"), indent=1)
    print(f"\n=== SWEEP FLEET ===")
    print(f"harvested {len(WORK)} | compiled {len(compiled)} | verified-PASS {npass} | rejected {len(verd)-npass}")
    for k, v in sorted(verd.items()):
        print(f"  {'PASS' if v[0]=='PASS' else 'FAIL'}  {k}  (n={v[1]} bad={v[2]})")
    print(f"reached probe end: {reached} | synth ${total:.4f} | {result['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

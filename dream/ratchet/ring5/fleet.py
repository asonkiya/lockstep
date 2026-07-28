#!/usr/bin/env python3
"""Ring 5 — the FLEET LOOP: the pipeline as automation over a worklist.

Rings 0-4 set up each transplant by hand. A full-kernel rewrite is a loop: take a
worklist of real functions, synthesize them in PARALLEL (model calls are cheap
and independent), generate ONE batch probe, verify the whole fleet in ONE boot,
report per-function verdicts. This is the wall-clock lever the research called
decisive — parallel synth + batched boot — running as a driver, not by hand.

Fleet = 6 real pure exported lib functions (fresh: lcm, lcm_not_zero; plus
proven-shape int_pow, gcd, int_sqrt, __sw_hweight32 re-verified through the same
loop). The reference needs no duplication: each cgir_* candidate is compared
against the live exported kernel symbol.

  fleet.py synth   # parallel-synthesize all candidates + generate the probe
  fleet.py gate    # build + boot + per-function verdicts
  fleet.py         # synth then gate
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
VOL, IMG, GATE = "cgir-kbuild", "cgir-kernel-gate", "crypto/lockstep_gate"
OUT = os.path.join(os.path.dirname(HERE), "out")

PRELUDE = (
    "#![no_std]\n#![no_main]\n#[panic_handler]\n"
    "fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n"
)

# Each entry: the Rust seam+signature, the extern decls the probe needs, and a
# probe loop comparing cgir_* against the kernel symbol over an input range.
WORKLIST = [
    {
        "seam": "cgir_lcm", "sym": "lcm", "obj": "lcm",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_lcm(a: u64, b: u64) -> u64',
        "note": "lcm(a,b) = if a&&b { (a/gcd(a,b))*b } else { 0 }. Reimplement gcd "
                "INLINE (Euclid); do not call any extern. u64 wrapping like C.",
        "extern": "unsigned long lcm(unsigned long, unsigned long);\nunsigned long cgir_lcm(unsigned long, unsigned long);",
        "loop": "for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_lcm(a,b)!=lcm(a,b)){bad++;if(fb<0)fb=a*1000+b;}}",
    },
    {
        "seam": "cgir_lcm_not_zero", "sym": "lcm_not_zero", "obj": "lcm_nz",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_lcm_not_zero(a: u64, b: u64) -> u64',
        "note": "lcm_not_zero(a,b) = { l=lcm(a,b); if l {l} else {a?a:b... } }. Exact C: "
                "l=lcm(a,b); return l ? l : (a ? a : b). Reimplement lcm+gcd inline.",
        "extern": "unsigned long lcm_not_zero(unsigned long, unsigned long);\nunsigned long cgir_lcm_not_zero(unsigned long, unsigned long);",
        "loop": "for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_lcm_not_zero(a,b)!=lcm_not_zero(a,b)){bad++;if(fb<0)fb=a*1000+b;}}",
    },
    {
        "seam": "cgir_int_pow", "sym": "int_pow", "obj": "int_pow",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_int_pow(base: u64, exp: u32) -> u64',
        "note": "result=1; while exp {if exp&1 result*=base; exp>>=1; base*=base;} u64 wrapping.",
        "extern": "unsigned long long int_pow(unsigned long long, unsigned int);\nunsigned long long cgir_int_pow(unsigned long long, unsigned int);",
        "loop": "{u64 bb;unsigned int e;for(bb=0;bb<=90;bb++)for(e=0;e<=14;e++){c++;if(cgir_int_pow(bb,e)!=int_pow(bb,e)){bad++;if(fb<0)fb=bb*100+e;}}}",
    },
    {
        "seam": "cgir_gcd", "sym": "gcd", "obj": "gcd",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_gcd(a: u64, b: u64) -> u64',
        "note": "the mathematical gcd; match C edge cases: gcd(0,x)=x, gcd(x,0)=x, gcd(0,0)=0.",
        "extern": "unsigned long gcd(unsigned long, unsigned long);\nunsigned long cgir_gcd(unsigned long, unsigned long);",
        "loop": "for(a=0;a<=400;a++)for(b=0;b<=400;b++){c++;if(cgir_gcd(a,b)!=gcd(a,b)){bad++;if(fb<0)fb=a*1000+b;}}",
    },
    {
        "seam": "cgir_int_sqrt", "sym": "int_sqrt", "obj": "int_sqrt5",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_int_sqrt(x: u64) -> u64',
        "note": "the kernel's EXACT algorithm (do not substitute another): "
                "if x<=1 return x; y=0; m = 1u64<<(__fls(x)&!1); "
                "while m!=0 { b=y+m; y>>=1; if x>=b { x-=b; y+=m; } m>>=2; } return y. "
                "__fls(x) = 63 - x.leading_zeros(). Verified counterexample of a WRONG "
                "variant: int_sqrt(4) must be 2, not 1.",
        "extern": "unsigned long int_sqrt(unsigned long);\nunsigned long cgir_int_sqrt(unsigned long);",
        "loop": "{u64 x;for(x=0;x<=40000;x++){c++;if(cgir_int_sqrt(x)!=int_sqrt(x)){bad++;if(fb<0)fb=x;}}}",
    },
    {
        "seam": "cgir_sw_hweight32", "sym": "__sw_hweight32", "obj": "hw32",
        "sig": '#[no_mangle]\npub extern "C" fn cgir_sw_hweight32(w: u32) -> u32',
        "note": "popcount of a u32 = w.count_ones().",
        "extern": "unsigned int __sw_hweight32(unsigned int);\nunsigned int cgir_sw_hweight32(unsigned int);",
        "loop": "{u32 w;for(w=0;w<200000u;w++){c++;if(cgir_sw_hweight32(w)!=__sw_hweight32(w)){bad++;if(fb<0)fb=w;}}}",
    },
]


def synth_one(item):
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    prompt = (
        f"Reimplement this Linux kernel function as freestanding no_std Rust "
        f"(target aarch64-unknown-none-softfloat), self-contained (inline any "
        f"helpers), no panics, no externs, no allocation.\n\n"
        f"Semantics: {item['note']}\n\n"
        f"Emit ONLY the Rust (no fences, no prose). The prelude (no_std, "
        f"panic_handler) is already present; do NOT repeat it. First line "
        f"`// fleet: {item['seam']}`, then exactly:\n{item['sig']}"
    )
    feedback = None
    for _ in range(3):
        msgs = [{"role": "user", "content": prompt}]
        if feedback:
            msgs += [{"role": "assistant", "content": "(prev)"},
                     {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
        r = client.messages.create(model=MODEL, max_tokens=900, messages=msgs)
        cost = (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        if item["seam"] not in code:
            feedback = f"must export {item['seam']}"
            continue
        path = os.path.join(HERE, f"{item['obj']}.rs")
        open(path, "w").write(PRELUDE + "\n" + code + "\n")
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{HERE}:/w", IMG, "bash", "-c",
             f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj "
             f"-C panic=abort -C relocation-model=static -O {item['obj']}.rs -o /tmp/{item['obj']}.o "
             f"&& nm /tmp/{item['obj']}.o | grep -c {item['seam']} "
             f"&& echo __UNDEF__ && nm -u /tmp/{item['obj']}.o"],
            capture_output=True, text=True)
        head, sep, undef_out = rc.stdout.partition("__UNDEF__")
        # undefined symbols => the candidate delegates to the C it should replace
        undef = {ln.split()[-1] for ln in undef_out.splitlines() if ln.strip()}
        undef -= {"memcpy", "memset", "memmove", "memcmp"}
        if sep and head.strip().split("\n")[-1] == "1" and not undef:
            return item["seam"], True, cost
        if undef:
            feedback = f"no extern references allowed, found: {sorted(undef)}"
        else:
            feedback = f"rustc: {(rc.stdout + rc.stderr)[:250]}"
    return item["seam"], False, cost


def gen_probe():
    externs = "\n".join(it["extern"] for it in WORKLIST)
    blocks = []
    for it in WORKLIST:
        blocks.append(
            f'\t{{ unsigned long c=0,bad=0; long fb=-1; unsigned long a,b;(void)a;(void)b;\n'
            f'\t  {it["loop"]}\n'
            f'\t  pr_emerg("FLEET_PROBE: {it["sym"]} n=%lu bad=%lu firstbad=%ld verdict=%s\\n",'
            f' c, bad, fb, bad==0?"DIFF_PASS":"DIFF_FAIL"); }}'
        )
    body = "\n".join(blocks)
    src = (
        "// SPDX-License-Identifier: GPL-2.0\n"
        "#include <linux/init.h>\n#include <linux/kernel.h>\n#include <linux/types.h>\n\n"
        f"{externs}\n\n"
        "static int __init fleet_init(void)\n{\n"
        f"{body}\n"
        '\tpr_emerg("FLEET_PROBE: done\\n");\n\treturn 0;\n}\n'
        "late_initcall(fleet_init);\n"
    )
    open(os.path.join(HERE, "probe", "fleet_probe.c"), "w").write(src)


def cmd_synth():
    print(f"parallel-synthesizing {len(WORKLIST)} candidates...")
    total, ok = 0.0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for seam, good, cost in ex.map(synth_one, WORKLIST):
            total += cost
            ok += good
            print(f"  {'✓' if good else '✗'} {seam}  (${cost:.4f})")
    gen_probe()
    print(f"synth: {ok}/{len(WORKLIST)} compiled, ${total:.4f}; probe generated")
    return 0 if ok == len(WORKLIST) else 1


def cmd_gate():
    objs = " ".join(f"{it['obj']}_c.o" for it in WORKLIST)
    setup = "cd /build/linux; mkdir -p " + GATE + "; grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile; cd " + GATE + "; rm -f *.c *.o *.o_shipped; cp /p/fleet_probe.c .; "
    for i, it in enumerate(WORKLIST):
        loc = "" if i == 0 else f" && aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' {it['obj']}_c.o_shipped"
        setup += (f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                  f"-C relocation-model=static -O /g/{it['obj']}.rs -o {it['obj']}_c.o_shipped{loc}; ")
    setup += f"printf 'obj-y := fleet_probe.o {objs}\\n' > Kbuild"
    print("installing fleet (6 candidates + probe)...")
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{HERE}:/g:ro",
                        "-v", f"{os.path.join(HERE, 'probe')}:/p:ro", IMG, "bash", "-euc", setup],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("install failed:", (r.stdout + r.stderr)[-400:]); return 2
    print("building...")
    b = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-eo", "pipefail", "-uc",
                        "cd /build/linux; rm -f arch/arm64/boot/Image; make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image"],
                       capture_output=True, text=True)
    open(os.path.join(OUT, "ring5-build.txt"), "w").write(b.stdout + b.stderr)
    if b.returncode != 0:
        print("BUILD FAILED:", (b.stdout + b.stderr)[-500:]); return 2
    print("booting + fleet-verifying...")
    q = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                        "cd /build/linux; timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 "
                        "-nographic -net none -kernel arch/arm64/boot/Image "
                        "-append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true"],
                       capture_output=True, text=True)
    con = q.stdout + q.stderr
    open(os.path.join(OUT, "ring5-console.txt"), "w").write(con)
    npass = con.count("verdict=DIFF_PASS")
    nfail = con.count("verdict=DIFF_FAIL")
    for line in con.splitlines():
        if "FLEET_PROBE:" in line and "n=" in line:
            print("  " + line.split("] ", 1)[-1])
    print(f"\nFLEET: {npass}/{len(WORKLIST)} passed, {nfail} failed")
    print("RING 5 FLEET GATE:", "PASS" if npass == len(WORKLIST) and nfail == 0 else "FAIL")
    return 0 if npass == len(WORKLIST) and nfail == 0 else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("synth", "all"):
        if cmd_synth() != 0:
            return 1
    if cmd in ("gate", "all"):
        return cmd_gate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

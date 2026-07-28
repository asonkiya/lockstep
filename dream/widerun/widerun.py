#!/usr/bin/env python3
"""The wide run — the first real production-scale verification pass.

Harvest pure-scalar exported leaves tree-wide, synthesize them all in parallel,
and verify them against the kernel's own symbols in batched boots. Robust to the
config artifacts the sweep exposed: functions whose reference symbol isn't linked
in this config are auto-dropped on the first link failure and the batch rebuilt
(so one module-only symbol can't sink the whole run).

Reports the grand total of real kernel functions verified bit-identical, the
honest reject categories, and the false-pass count (must stay 0).
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
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
IMG, VOL, GATE = "cgir-kernel-gate", "cgir-kbuild", "crypto/lockstep_gate"
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
DIRS = ["lib", "kernel", "mm", "crypto", "block", "net/core", "fs", "drivers/gpio",
        "drivers/clk", "drivers/pci", "drivers/base", "sound/core"]
BATCH = 40  # candidates per boot
# names that are side-effectful / config-fragile / div-by-zero prone — skip up front
SKIP = re.compile(r"sleep|delay|_div|div_|div64|prime|alloc|free|lock|jiffies|_wait|msleep|schedule|panic|printk")

SCALAR = {"int": "i32", "unsigned int": "u32", "unsigned": "u32", "long": "i64",
          "unsigned long": "u64", "long long": "i64", "unsigned long long": "u64",
          "u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64", "s8": "i8", "s16": "i16",
          "s32": "i32", "s64": "i64", "bool": "bool", "size_t": "usize"}
EXPORT = re.compile(r"EXPORT_SYMBOL(?:_GPL)?\(([A-Za-z_]\w*)\)")
PRELUDE = "#![no_std]\n#![no_main]\n#[panic_handler]\nfn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n"


def norm(t):
    return re.sub(r"\s+", " ", t.replace("const", "").strip())


def parse(src, name):
    m = re.search(rf"\n([A-Za-z_][\w \t]*?)\b{re.escape(name)}\s*\(([^;{{)]*)\)\s*\n?\{{", src)
    if not m:
        return None
    ret = norm(m.group(1))
    if ret not in SCALAR:
        return None
    a = m.group(2).strip()
    if a in ("void", ""):
        return None
    args = []
    for p in a.split(","):
        am = re.match(r"(.+?)\b([A-Za-z_]\w*)$", p.strip())
        if not am or norm(am.group(1)) not in SCALAR:
            return None
        args.append(norm(am.group(1)))
    if not (1 <= len(args) <= 3):
        return None
    i = src.index("{", m.start())
    d, j = 0, i
    while j < len(src):
        d += (src[j] == "{") - (src[j] == "}")
        if d == 0:
            break
        j += 1
    body = src[m.start():j + 1]
    if body.count("\n") > 40 or re.search(r"\b(kmalloc|kfree|readl|writel|spin_lock|mutex_lock|printk|msleep|udelay)\b", body):
        return None
    return {"sym": name, "file": "", "ret": ret, "args": args, "body": body}


def harvest():
    seen, work = set(), []
    for d in DIRS:
        for dp, _, fns in os.walk(os.path.join(KSRC, d)):
            for fn in fns:
                if not fn.endswith(".c"):
                    continue
                p = os.path.join(dp, fn)
                src = open(p, errors="ignore").read()
                for em in EXPORT.finditer(src):
                    nm = em.group(1)
                    if nm in seen or SKIP.search(nm):
                        continue
                    w = parse(src, nm)
                    if w:
                        w["file"] = os.path.relpath(p, KSRC)
                        seen.add(nm)
                        work.append(w)
    work.sort(key=lambda w: w["sym"])
    return work


def rsig(w):
    a = ", ".join(f"a{i}: {SCALAR[t]}" for i, t in enumerate(w["args"]))
    return f'#[no_mangle]\npub extern "C" fn cgir_{w["sym"]}({a}) -> {SCALAR[w["ret"]]}'


def synth(w):
    cand = os.path.join(HERE, "cand", f"{w['sym']}.rs")
    os.makedirs(os.path.dirname(cand), exist_ok=True)
    if os.path.exists(cand) and _ok(w["sym"]):
        return w["sym"], True, 0.0
    import anthropic
    cl = anthropic.Anthropic(api_key=_api_key())
    prompt = ("Reimplement this Linux kernel function as freestanding no_std Rust "
              "(aarch64-unknown-none-softfloat), self-contained, no panics/externs/alloc, "
              "wrapping arithmetic, integer division only if the divisor is a nonzero "
              "constant (else you cannot). If it reads any global/per-cpu/clock state, "
              "reply with exactly `// UNSUPPORTED` and nothing else — do NOT approximate "
              "or freeze state; a frozen-state port can pass the boot-time differential "
              "while being wrong at runtime.\n\n"
              f"```c\n{w['body']}\n```\n\nEmit ONLY Rust (no fences); prelude present, don't "
              f"repeat. First line `// leaf: cgir_{w['sym']}`, then:\n{rsig(w)}")
    fb, cost = None, 0.0
    for _ in range(2):
        msgs = [{"role": "user", "content": prompt}]
        if fb:
            msgs += [{"role": "assistant", "content": "x"}, {"role": "user", "content": f"FAILED: {fb}"}]
        r = cl.messages.create(model=MODEL, max_tokens=900, messages=msgs)
        cost += (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        if f"cgir_{w['sym']}" not in code:
            fb = "wrong export"; continue
        open(cand, "w").write(PRELUDE + "\n" + code + "\n")
        if _ok(w["sym"]):
            return w["sym"], True, cost
        fb = "rustc failed"
    return w["sym"], False, cost


# Undefined symbols a freestanding candidate object may legitimately carry
# (compiler-emitted intrinsics). Anything else — above all the C original's
# name — means the candidate DELEGATES instead of reimplementing: linked into
# the kernel it would call the real C and the differential compares C with C.
_INTRINSICS = {"memcpy", "memset", "memmove", "memcmp"}


def _ok(sym):
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.join(HERE,'cand')}:/w", IMG, "bash", "-c",
                        f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                        f"-C relocation-model=static -O {sym}.rs -o /tmp/{sym}.o 2>/dev/null && nm /tmp/{sym}.o 2>/dev/null | grep -c cgir_{sym} "
                        f"&& echo __UNDEF__ && nm -u /tmp/{sym}.o 2>/dev/null"],
                       capture_output=True, text=True)
    head, sep, undef_out = r.stdout.partition("__UNDEF__")
    if not sep or head.strip().split("\n")[-1] != "1":
        return False
    undef = {ln.split()[-1] for ln in undef_out.splitlines() if ln.strip()}
    return not (undef - _INTRINSICS)


CANARY = "lockstep_canary"


def _canary_target(batch):
    """The batch fn the canary diffs against (a deliberately-wrong constant
    Rust vs the real C — expected DIFF_FAIL; a PASS or a missing line means the
    probe machinery itself is broken and the whole batch must not be credited).
    Skip bool returns (a constant could legitimately match everywhere)."""
    for w in batch:
        if w["ret"] != "bool":
            return w
    return None


def canary_rs(w):
    a = ", ".join(f"a{i}: {SCALAR[t]}" for i, t in enumerate(w["args"]))
    return (PRELUDE + f'#[no_mangle]\npub extern "C" fn cgir_{CANARY}({a}) -> {SCALAR[w["ret"]]} '
            f'{{ 0x5A5A5A5A as {SCALAR[w["ret"]]} }}\n')


def _loop(sym_c, sym_rust, na):
    call = lambda nm: f"{nm}(" + ",".join(f"i{k}" for k in range(na)) + ")"  # noqa: E731
    if na == 1:
        return f"for(long i0=0;i0<=1500;i0++){{c++;if({call(sym_rust)}!={call(sym_c)}){{bad++;}}}}"
    if na == 2:
        return f"for(long i0=1;i0<=40;i0++)for(long i1=1;i1<=40;i1++){{c++;if({call(sym_rust)}!={call(sym_c)}){{bad++;}}}}"
    return f"for(long i0=1;i0<=12;i0++)for(long i1=1;i1<=12;i1++)for(long i2=1;i2<=12;i2++){{c++;if({call(sym_rust)}!={call(sym_c)}){{bad++;}}}}"


def probe(batch):
    ext, blk = [], []
    for w in batch:
        ca = ", ".join(w["args"])
        ext += [f"{w['ret']} {w['sym']}({ca});", f"{w['ret']} cgir_{w['sym']}({ca});"]
        lp = _loop(w["sym"], f"cgir_{w['sym']}", len(w["args"]))
        # n>0 required: a loop that never ran must FAIL, not vacuously pass
        blk.append(f'\t{{unsigned long c=0,bad=0;{lp} pr_emerg("WIDE: {w["sym"]} n=%lu bad=%lu verdict=%s\\n",c,bad,(c&&!bad)?"PASS":"FAIL");}}')
    cw = _canary_target(batch)
    if cw is not None:
        ca = ", ".join(cw["args"])
        ext += [f"{cw['ret']} cgir_{CANARY}({ca});"]
        lp = _loop(cw["sym"], f"cgir_{CANARY}", len(cw["args"]))
        # in-loop negative control: wrong-by-construction candidate vs the real
        # C — the probe must REJECT it every batch, or nothing it says counts
        blk.append(f'\t{{unsigned long c=0,bad=0;{lp} pr_emerg("WIDE: __{CANARY} n=%lu bad=%lu verdict=%s\\n",c,bad,(c&&!bad)?"PASS":"FAIL");}}')
    return ("// SPDX-License-Identifier: GPL-2.0\n#include <linux/init.h>\n#include <linux/kernel.h>\n#include <linux/types.h>\n\n"
            + "\n".join(ext) + "\n\nstatic int __init w_init(void){\n" + "\n".join(blk) + '\n\tpr_emerg("WIDE: done\\n");return 0;}\nlate_initcall(w_init);\n')


def restore():
    # DESTRUCTIVE: un-weaves the cumulative ratchet state on the volume (rust
    # objects woven by rings 0/6, PTP mock config). Require explicit opt-in so
    # a wide experiment can't silently reset the ratchet.
    if os.environ.get("LOCKSTEP_ALLOW_RESTORE") != "1":
        raise SystemExit("restore() would un-weave the ratchet volume state "
                         "(woven rust objects, PTP mock). Set LOCKSTEP_ALLOW_RESTORE=1 "
                         "to proceed deliberately.")
    files = "drivers/ptp/ptp_mock.c drivers/ptp/Makefile lib/math/int_sqrt.c lib/math/int_pow.c lib/hweight.c lib/Makefile lib/math/Makefile lib/math/lcm.c crypto/Makefile"
    subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{KSRC}:/src:ro", IMG, "bash", "-c",
                    f"cd /build/linux && for f in {files}; do cp /src/$f $f 2>/dev/null||true; done && rm -f drivers/ptp/*_regions.o_shipped lib/math/*_rust.o_shipped lib/*_rust.o_shipped && rm -rf crypto/lockstep_gate && make -s olddefconfig >/dev/null 2>&1||true"], capture_output=True)


def build_boot(batch, tag):
    """Install+build a batch; auto-drop symbols the linker can't resolve, rebuild."""
    dropped = {}
    for attempt in range(4):
        cur = [w for w in batch if w["sym"] not in dropped]
        if not cur:
            return {}, dropped
        open(os.path.join(HERE, "wide_probe.c"), "w").write(probe(cur))
        cw = _canary_target(cur)
        if cw is not None:
            open(os.path.join(HERE, "cand", f"__{CANARY}.rs"), "w").write(canary_rs(cw))
        objs = " ".join(f"{w['sym']}_c.o" for w in cur) + (f" __{CANARY}_c.o" if cw else "")
        s = f"cd /build/linux && mkdir -p {GATE} && grep -q 'lockstep_gate/' crypto/Makefile||echo 'obj-y += lockstep_gate/'>>crypto/Makefile; cd {GATE} && rm -f *.c *.o *.o_shipped && cp /p/wide_probe.c .;"
        rs_list = [(w["sym"], f"/cand/{w['sym']}.rs") for w in cur]
        if cw is not None:
            rs_list.append((f"__{CANARY}", f"/cand/__{CANARY}.rs"))
        for i, (sym, rs) in enumerate(rs_list):
            loc = "" if i == 0 else f" && aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' {sym}_c.o_shipped"
            s += f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort -C relocation-model=static -O {rs} -o {sym}_c.o_shipped{loc};"
        s += f"printf 'obj-y := wide_probe.o {objs}\\n'>Kbuild"
        subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{HERE}:/p:ro", "-v", f"{os.path.join(HERE,'cand')}:/cand:ro", IMG, "bash", "-euc", s], capture_output=True)
        b = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                            "cd /build/linux && rm -f arch/arm64/boot/Image && make -j$(nproc) Image 2>&1 | grep -iE 'undefined reference|panic_const|__udivti|__umodti' ; test -f arch/arm64/boot/Image && echo BUILT"], capture_output=True, text=True)
        out = b.stdout + b.stderr
        if "BUILT" in out and "undefined reference" not in out:
            break
        # drop offending candidates: any cgir_<sym> object or kernel ref that failed
        bad = set()
        for w in cur:
            if re.search(rf"\b{w['sym']}\b", out) or re.search(rf"cgir_{w['sym']}", out):
                bad.add(w["sym"])
        if not bad:  # intrinsic panic refs with no clear owner -> drop divisors heuristically
            for w in cur:
                if re.search(r"/|%", w["body"]):
                    bad.add(w["sym"]); break
        for s2 in bad:
            dropped[s2] = "unlinkable in config / freestanding intrinsic"
        if not bad:
            return None, dropped  # give up this batch
    q = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                        "cd /build/linux && timeout 400 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none -kernel arch/arm64/boot/Image -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1||true"], capture_output=True, text=True)
    con = q.stdout + q.stderr
    open(os.path.join(HERE, f"wide-{tag}-console.txt"), "w").write(con)
    verd = {}
    for ln in con.splitlines():
        m = re.search(r"WIDE: (\w+) n=(\d+) bad=(\d+) verdict=(PASS|FAIL)", ln)
        if m:
            status = m.group(4) if int(m.group(2)) > 0 else "FAIL"  # never credit n=0
            verd[m.group(1)] = (status, int(m.group(3)))
    # canary judgment: the wrong-by-construction candidate must be REJECTED.
    # Missing line = probe never ran; PASS = probe compares nothing. Either way
    # no PASS in this batch can be credited.
    cw = _canary_target([w for w in batch if w["sym"] not in dropped])
    if cw is not None:
        cv = verd.pop(f"__{CANARY}", None)
        if cv is None or cv[0] == "PASS":
            print(f"  [{tag}] CANARY {'missing' if cv is None else 'PASSED'} — "
                  f"probe not trustworthy, batch NOT credited")
            return {s: ("NO_VERDICT_CANARY", -1) for s in verd}, dropped
    return verd, dropped


def main():
    import purity
    t0 = time.time()
    print("[wide] harvesting tree-wide...")
    harvested = harvest()
    # PURITY ROUTER: only provably-pure functions are eligible for the scalar
    # differential; stateful ones are quarantined for the trace oracle (Ring 3).
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in harvested if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    work = [w for w in harvested if w["sym"] in pn]
    quarantined = {w["sym"]: purity.classify(w["body"], pn, w["sym"])[1]
                   for w in harvested if w["sym"] not in pn}
    print(f"[wide] harvested {len(harvested)}; purity router -> {len(work)} PURE "
          f"(scalar-gate), {len(quarantined)} quarantined (trace oracle)")
    json.dump({"harvested": [w["sym"] for w in harvested], "pure": sorted(pn),
               "quarantined": quarantined}, open(os.path.join(HERE, "routing.json"), "w"), indent=1)
    print(f"[wide] parallel-synthesizing {len(work)}...")
    total, compiled = 0.0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        idx = {w["sym"]: w for w in work}
        for sym, ok, c in ex.map(synth, work):
            total += c
            if ok:
                compiled.append(idx[sym])
    print(f"[wide] synth: {len(compiled)}/{len(work)} compiled, ${total:.4f}")
    restore()
    allv, alldrop = {}, {}
    batches = [compiled[i:i + BATCH] for i in range(0, len(compiled), BATCH)]
    for bi, batch in enumerate(batches):
        print(f"[wide] batch {bi+1}/{len(batches)} ({len(batch)} candidates): build+boot...")
        verd, dropped = build_boot(batch, f"b{bi}")
        if verd is None:
            print(f"  batch {bi+1} build unrecoverable"); continue
        allv.update(verd); alldrop.update(dropped)
        npass = sum(1 for v in verd.values() if v[0] == "PASS")
        ncanary = sum(1 for v in verd.values() if v[0] == "NO_VERDICT_CANARY")
        note = f", {ncanary} voided (canary failed)" if ncanary else ""
        print(f"  batch {bi+1}: {npass}/{len(verd)} PASS, {len(dropped)} dropped{note}")
    npass = sum(1 for v in allv.values() if v[0] == "PASS")
    nvoid = sum(1 for v in allv.values() if v[0] == "NO_VERDICT_CANARY")
    result = {"harvested": len(harvested), "pure_routed": len(work), "quarantined": len(quarantined),
              "compiled": len(compiled), "boot_attempted": len(allv),
              "verified_pass": npass, "rejected": len(allv) - npass - nvoid,
              "voided_canary_failed": nvoid, "dropped_unlinkable": len(alldrop),
              "synth_cost_usd": round(total, 4), "elapsed_min": round((time.time() - t0) / 60, 1),
              "pass_syms": sorted(k for k, v in allv.items() if v[0] == "PASS"),
              "reject_syms": sorted(k for k, v in allv.items() if v[0] == "FAIL")}
    json.dump(result, open(os.path.join(HERE, "widerun_result.json"), "w"), indent=1)
    print(f"\n=== WIDE RUN ===\nharvested {len(work)} | compiled {len(compiled)} | booted {len(allv)} | "
          f"VERIFIED {npass} | rejected {len(allv)-npass} | dropped(unlinkable) {len(alldrop)} | "
          f"${total:.4f} | {result['elapsed_min']}min")
    print("verified:", ", ".join(result["pass_syms"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

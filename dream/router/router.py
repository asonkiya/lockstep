#!/usr/bin/env python3
"""The unified multi-tier router — the last integration piece.

Every verification mechanism exists as a separate gate (hostdiff, in-kernel
differential, mirror+diff, recorder, concgate). This is the one loop that takes
an arbitrary worklist and routes EACH function to the strongest oracle it can
soundly use, executes the automatable tiers, and reports one dashboard. The
routing rules encode everything the project learned:

  census D (entangled)          -> C_FOREVER      (the ~11% floor; skip)
  census C (locked)             -> TC_REGION      (concgate/M-ladder class; region machinery, not auto)
  census B (struct-reading)     -> T2_MIRROR      (mirror + differential, per struct family)
  A + pure + host-TU-compiles   -> T0_HOST        (ladder synth + hostdiff; boot-free, sound)
  A + pure|readonly + linkable  -> T1_KERNEL      (synth + ONE batched boot, in-kernel differential;
                                                   pure has no effects to miss; read-only is checked
                                                   at ONE state point and credited as
                                                   verified_T1_at_boot_state, not full equivalence)
  A + pure|readonly, unlinkable -> T1_UNLINKABLE  (config gap — needs a bigger config, not a model)
  A + effectful + MMIO markers  -> T3_TRACE       (recorder; needs a per-driver recording)
  A + effectful otherwise       -> T3_EFFECT      (per-fn effect trace; quarantined)

Soundness invariants (the whole point):
  * nothing lands in a WEAKER oracle than its class requires (the widerun's
    over-crediting bug is structurally impossible here);
  * the verifiability gate runs BEFORE any paid rung (the $0.25 lesson);
  * T0/T1 verdicts are labeled verified_T0 / verified_T1; everything else is
    routed+reported, never silently "passed".

Executes T0 (boot-free) and T1 (one boot for the whole batch) end to end.
T2/T3/TC are routed with their per-family requirements named — their machinery
is proven (rings 8/9, recorder, concgate) but needs per-family artifacts.

Usage: router.py [--skip-t1-boot] [--local-attempts N] [--out router_result.json]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/hostdiff", "dream/cluster", "dream/localmodel", "dream/ladder",
          "dream/widerun", "dream/sweep", "synthesis"):
    sys.path.insert(0, os.path.join(REPO, p))
import census  # noqa: E402
import hostdiff  # noqa: E402
import ladder  # noqa: E402
import localbench  # noqa: E402
import purity  # noqa: E402
import widerun  # noqa: E402
from widerun72 import host_tu_ok  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
IMG, VOL, GATE = "cgir-kernel-gate", "cgir-kbuild", "crypto/lockstep_gate"
MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread|iowrite|in[bwl]|out[bwl])\b")
CANARY = "lockstep_canary"
PRELUDE = ("#![no_std]\n#![no_main]\n#[panic_handler]\n"
           "fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }\n")


# ---------------- classification ----------------

def kernel_symbols() -> set[str]:
    """Symbol set of the volume's last-built kernel (System.map) — one docker
    call. Linkability gates T1: no C symbol in the image = nothing to diff."""
    # Globals only ($2 T/W): taking every row swept in `t`/`d` LOCALS, so a
    # static fn looked linkable, the probe externed it, the link failed, and the
    # WHOLE T1 batch came back verdict-less.
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG,
                        "bash", "-c",
                        "awk '$2 ~ /^[TW]$/ {print $3}' /build/linux/System.map 2>/dev/null"],
                       capture_output=True, text=True)
    syms = set(r.stdout.split())
    if not syms:
        print("  [warn] no System.map in volume — treating all as unlinkable (conservative)")
    return syms


def route_one(w: dict, pure_names: set, ksyms: set) -> tuple[str, str]:
    """(route, reason) for one harvested fn — cheapest checks first, soundness
    ordered. Never places a fn in a weaker oracle than its class requires."""
    body = w["body"]
    tier = census.classify(body)
    if tier == "D":
        return "C_FOREVER", "census D: entangled (container_of/per-cpu/RCU/list)"
    # MMIO wins over B/C: a register program's correctness IS its access trace,
    # regardless of whether it also reads a struct field or takes a lock — that's
    # the recorder's whole premise (Ring 3/4). Checked before B so driver ops
    # land on the recorder, not the mirror.
    if MMIO.search(purity.mask(body)):
        return "T3_TRACE", "MMIO register program — recorder oracle (record once, replay)"
    if tier == "C":
        return "TC_REGION", "census C: takes a lock — region machinery (concgate class)"
    if tier == "B":
        return "T2_MIRROR", "census B: reads struct fields — needs a mirror for its struct family"
    verdict, why = purity.classify(body, pure_names, w["sym"])
    readonly = verdict == "pure" or purity.recoverable_readonly(body, pure_names, w["sym"])
    if not readonly:
        # (MMIO already routed to T3_TRACE above — anything here is non-MMIO effectful)
        return "T3_EFFECT", f"effectful ({why}) — per-fn effect trace owed"
    if verdict == "pure" and host_tu_ok(w["file"], w["sym"]):
        return "T0_HOST", "pure + TU compiles on host — boot-free differential"
    if w["sym"] in ksyms:
        label = "pure" if verdict == "pure" else "read-only-otherwise-pure"
        return "T1_KERNEL", f"{label} + linkable — in-kernel differential, one batched boot"
    return "T1_UNLINKABLE", "pure/read-only but symbol not in this config's kernel"


# ---------------- T0 execution (boot-free ladder) ----------------

def run_t0(w: dict, local_attempts: int) -> dict:
    path, func = w["file"], w["sym"]
    src = open(os.path.join(KSRC, path)).read()
    # parse_sig SystemExits on unmappable types (pointer args in a driver
    # worklist) — that is an unroutable FUNCTION, not a dead run.
    try:
        ret, params = hostdiff.parse_sig(src, func)
    except (SystemExit, Exception) as e:  # noqa: B014
        return {"rung": None, "cost": 0.0, "error": f"unroutable signature: {e}"}
    _, sig_line = localbench.rust_sig(func, ret, params)
    csrc = localbench.context_of(src, func)
    out = {"rung": None, "cost": 0.0}
    rs, note = ladder.c2rust_rung(path, func, [])
    if rs is not None and ladder.verify(path, func, [], rs, "r0")["verdict"] == "MATCH":
        out["rung"] = "c2rust"
        return out
    ok, _log = ladder.local_rung(path, func, [], csrc, sig_line, attempts=local_attempts)
    if ok:
        out["rung"] = "local-14b"
        return out
    ok, _log, cost = ladder.haiku_rung(path, func, [], csrc, sig_line)
    out["cost"] = cost
    if ok:
        out["rung"] = "haiku"
    return out


# ---------------- T1 execution (synth + one batched boot) ----------------

def t1_synth(w: dict, local_attempts: int) -> tuple[str, str | None, float]:
    """Produce a freestanding-compilable candidate for the kernel differential.
    Ladder discipline: local first, Haiku for the tail; the compile precheck is
    the container rustc (the real target), behavior judged by the boot."""
    src = open(os.path.join(KSRC, w["file"])).read()
    try:
        ret, params = hostdiff.parse_sig(src, w["sym"])
    except (SystemExit, Exception):  # noqa: B014 — unmappable sig = skip, not crash
        return w["sym"], None, 0.0
    _, sig_line = localbench.rust_sig(w["sym"], ret, params)
    csrc = localbench.context_of(src, w["sym"])
    cost, fb = 0.0, None

    cand = os.path.join(HERE, "cand", f"{w['sym']}.rs")
    os.makedirs(os.path.dirname(cand), exist_ok=True)

    def compiles(code: str | None = None) -> bool:
        if code is not None:
            open(cand, "w").write(PRELUDE + code)
        # nm -u must be empty (bar compiler intrinsics): an undefined symbol —
        # above all the C original's own name — means the candidate DELEGATES
        # to the real implementation; linked in-kernel the differential would
        # then compare C with C and pass anything.
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{os.path.dirname(cand)}:/c", IMG,
                            "bash", "-c", f"rustc --target aarch64-unknown-none-softfloat --emit=obj "
                            f"-C panic=abort -O /c/{w['sym']}.rs -o /tmp/x.o && nm -u /tmp/x.o"],
                           capture_output=True, text=True)
        nonlocal fb
        if r.returncode != 0:
            fb = "rustc: " + r.stderr[-300:]
            return False
        undef = {ln.split()[-1] for ln in r.stdout.splitlines() if ln.strip()}
        undef -= {"memcpy", "memset", "memmove", "memcmp"}
        if undef:
            fb = f"candidate references external symbols (no externs allowed): {sorted(undef)}"
            return False
        return True

    # reuse a cached candidate if one exists and still compiles (cheap, deterministic reruns)
    if os.path.exists(cand) and compiles():
        return w["sym"], "cached", cost
    for _ in range(local_attempts):
        code, _t = localbench.synth("qwen2.5-coder:14b", localbench.build_prompt(csrc, sig_line, fb))
        if compiles(localbench.extract_code(code)):
            return w["sym"], "local-14b", cost
    for _ in range(2):
        text, c = ladder.haiku_call(localbench.build_prompt(csrc, sig_line, fb))
        cost += c
        if compiles(localbench.extract_code(text)):
            return w["sym"], "haiku", cost
    return w["sym"], None, cost


def t1_boot(t1_work: list[dict]) -> dict[str, tuple[str, int, int]]:
    """Install all T1 candidates + one probe, build ONE Image, boot ONCE, parse
    per-fn verdicts. This is the sweep/fleet machinery, parameterized."""
    externs, blocks = [], []
    seen_exports: dict[str, str] = {}
    for w in t1_work:
        cargs = ", ".join(t for t in w["args"])
        # the candidate exports cgir_<sym-with-leading-underscores-stripped>
        # (localbench.rust_sig convention) — the probe MUST use the same name
        exp = "cgir_" + w["sym"].lstrip("_")
        # `__foo` and `foo` both map to cgir_foo — two candidates in one batch
        # would collide at link and the probe would diff the wrong pair. Drop
        # the later one (it gets T1_no_verdict) rather than mislink.
        if exp in seen_exports:
            print(f"  [t1] export collision: {w['sym']} vs {seen_exports[exp]} both -> {exp}; "
                  f"dropping {w['sym']} from this batch")
            continue
        seen_exports[exp] = w["sym"]
        externs += [f"{w['ret']} {w['sym']}({cargs});", f"{w['ret']} {exp}({cargs});"]
        na = len(w["args"])
        call = lambda pre, nm: f"{pre}{nm}(" + ",".join(f"i{k}" for k in range(na)) + ")"  # noqa: E731
        if na:
            rng = {1: "i0<=2000", 2: "i0<=48", 3: "i0<=14"}.get(na, "i0<=8")
            loops = "".join(f"for(long i{k}={1 if na > 1 else 0};{rng.replace('i0', f'i{k}')};i{k}++)"
                            for k in range(na))
        else:
            loops = ""  # 0-arg fn (reads globals) — one comparison
        # verdict requires n>0: a loop that never ran must FAIL, not pass
        blocks.append(
            f'\t{{ unsigned long c=0,bad=0; long fb=-1;\n'
            f'\t  {loops}{{c++;if({call("", exp)}!={call("", w["sym"])}){{bad++;if(fb<0)fb=1;}}}}\n'
            f'\t  pr_emerg("ROUTER: {w["sym"]} n=%lu bad=%lu verdict=%s\\n", c,bad, (c&&!bad)?"DIFF_PASS":"DIFF_FAIL"); }}')

    # in-loop negative control: a deliberately-wrong constant Rust vs a real C
    # symbol from this batch. It MUST be rejected — a PASS or a missing line
    # means the probe machinery is not actually comparing anything, so no PASS
    # in this batch can be trusted. (Skip bool-return targets: a constant could
    # legitimately match everywhere.)
    canary_w = next((w for w in t1_work if w["ret"] != "bool" and w["args"]), None)
    if canary_w is not None:
        na = len(canary_w["args"])
        cargs = ", ".join(canary_w["args"])
        call = lambda nm: f"{nm}(" + ",".join(f"i{k}" for k in range(na)) + ")"  # noqa: E731
        rng = {1: "i0<=2000", 2: "i0<=48", 3: "i0<=14"}.get(na, "i0<=8")
        loops = "".join(f"for(long i{k}=1;{rng.replace('i0', f'i{k}')};i{k}++)" for k in range(na))
        externs += [f"{canary_w['ret']} cgir_{CANARY}({cargs});"]
        blocks.append(
            f'\t{{ unsigned long c=0,bad=0;\n'
            f'\t  {loops}{{c++;if({call("cgir_" + CANARY)}!={call(canary_w["sym"])}){{bad++;}}}}\n'
            f'\t  pr_emerg("ROUTER: __{CANARY} n=%lu bad=%lu verdict=%s\\n", c,bad, (c&&!bad)?"DIFF_PASS":"DIFF_FAIL"); }}')
        rust_params = [(t, f"a{i}") for i, t in enumerate(canary_w["args"])]
        _, sig_line = localbench.rust_sig(CANARY, canary_w["ret"], rust_params)
        rust_ret = localbench.C2RUST_TY[canary_w["ret"]]
        open(os.path.join(HERE, "cand", f"__{CANARY}.rs"), "w").write(
            PRELUDE + f'{sig_line} {{ 0x5A5A5A5A as {rust_ret} }}\n')

    probe = ("// SPDX-License-Identifier: GPL-2.0\n#include <linux/init.h>\n#include <linux/kernel.h>\n"
             "#include <linux/types.h>\n\n" + "\n".join(externs) +
             "\n\nstatic int __init router_init(void)\n{\n" + "\n".join(blocks) +
             '\n\tpr_emerg("ROUTER: done\\n");\n\treturn 0;\n}\nlate_initcall(router_init);\n')
    open(os.path.join(HERE, "router_probe.c"), "w").write(probe)

    build_syms = [w["sym"] for w in t1_work if w["sym"] in seen_exports.values()]
    if canary_w is not None:
        build_syms.append(f"__{CANARY}")
    objs = " ".join(f"{s}_c.o" for s in build_syms)
    setup = (f"cd /build/linux && grep -q 'obj-y += lockstep_gate/' crypto/Makefile || "
             f"echo 'obj-y += lockstep_gate/' >> crypto/Makefile; mkdir -p {GATE} && cd {GATE} && "
             "rm -f *.c *.o *.o_shipped && cp /p/router_probe.c .; ")
    for i, s in enumerate(build_syms):
        loc = "" if i == 0 else f" && aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' {s}_c.o_shipped"
        setup += (f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
                  f"-C relocation-model=static -O /cand/{s}.rs -o {s}_c.o_shipped{loc}; ")
    setup += f"printf 'obj-y := router_probe.o {objs}\\n' > Kbuild"
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{HERE}:/p:ro",
                        "-v", f"{os.path.join(HERE, 'cand')}:/cand:ro", IMG, "bash", "-euc", setup],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  [t1] install failed:", (r.stdout + r.stderr)[-400:])
        return {}
    b = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-eo", "pipefail", "-uc",
                        "cd /build/linux && rm -f arch/arm64/boot/Image && "
                        "make -s -j$(nproc) Image 2>&1 | tail -3 && test -f arch/arm64/boot/Image"],
                       capture_output=True, text=True)
    if b.returncode != 0:
        print("  [t1] BUILD FAILED:", (b.stdout + b.stderr)[-400:])
        return {}
    q = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                        "cd /build/linux && timeout 400 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 "
                        "-nographic -net none -kernel arch/arm64/boot/Image "
                        "-append 'console=ttyAMA0 panic=-1' -no-reboot 2>&1 || true"],
                       capture_output=True, text=True)
    con = q.stdout + q.stderr
    open(os.path.join(HERE, "router-console.txt"), "w").write(con)
    verd = {}
    for ln in con.splitlines():
        m = re.search(r"ROUTER: (\w+) n=(\d+) bad=(\d+) verdict=DIFF_(PASS|FAIL)", ln)
        if m:
            n = int(m.group(2))
            # belt+braces with the probe's own n>0 check: never credit n=0
            status = m.group(4) if n > 0 else "FAIL"
            verd[m.group(1)] = (status, n, int(m.group(3)))
    # canary judgment: the wrong-by-construction candidate must be REJECTED.
    # A missing line or a PASS means the probe compares nothing this boot — void
    # every PASS so a broken harness can't be read as verification.
    if canary_w is not None:
        cv = verd.pop(f"__{CANARY}", None)
        if cv is None or cv[0] == "PASS":
            print(f"  [t1] CANARY {'missing' if cv is None else 'PASSED'} — "
                  f"probe not trustworthy, batch NOT credited")
            return {s: ("NO_VERDICT_CANARY", -1, -1) for s in verd}
    return verd


# ---------------- the loop ----------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-t1-boot", action="store_true")
    ap.add_argument("--local-attempts", type=int, default=1)
    ap.add_argument("--worklist", help="JSON worklist [{sym,file,ret,args,body}]; default = widerun.harvest()")
    ap.add_argument("--out", default=os.path.join(HERE, "router_result.json"))
    a = ap.parse_args()
    t_all, spend = time.time(), 0.0

    work = json.load(open(a.worklist)) if a.worklist else widerun.harvest()
    # fingerprint the worklist so t2/t3 executors can refuse a stale pairing
    # (the "wrong worklist" incident class)
    import hashlib
    worklist_sha = hashlib.sha256(
        "\n".join(sorted(w["sym"] for w in work)).encode()).hexdigest()[:16]
    pn = set()
    for _ in range(3):  # purity fixpoint
        pn = {w["sym"] for w in work if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    print(f"[router] worklist {len(work)} fns; resolving kernel symbol set...")
    ksyms = kernel_symbols()

    routes: dict[str, list[dict]] = {}
    rows = []
    for w in work:
        route, why = route_one(w, pn, ksyms)
        routes.setdefault(route, []).append(w)
        rows.append({"func": w["sym"], "file": w["file"], "route": route,
                     "reason": why, "status": "routed"})
    print("[router] routing:")
    for rt in sorted(routes):
        print(f"  {rt:14s} {len(routes[rt]):3d}  e.g. {', '.join(w['sym'] for w in routes[rt][:4])}")
    byrow = {r["func"]: r for r in rows}

    # bookkeeping must survive any mid-run crash — write what we have, always
    def _dump() -> None:
        v0 = sum(1 for r in rows if r["status"].startswith("verified_T0"))
        v1 = sum(1 for r in rows if r["status"] == "verified_T1")
        v1b = sum(1 for r in rows if r["status"] == "verified_T1_at_boot_state")
        json.dump({"rows": rows, "spend": round(spend, 4), "worklist_sha": worklist_sha,
                   "verified": v0 + v1, "verified_at_boot_state": v1b,
                   "wall_s": round(time.time() - t_all)},
                  open(a.out, "w"), indent=1)

    try:
        # ---- execute T0 (boot-free) ----
        t0 = routes.get("T0_HOST", [])
        print(f"\n[router] T0_HOST: executing ladder + hostdiff on {len(t0)} fns (boot-free)...")
        for w in t0:
            res = run_t0(w, a.local_attempts)
            spend += res["cost"]
            byrow[w["sym"]]["status"] = f"verified_T0({res['rung']})" if res["rung"] else "T0_unsolved"
            print(f"  {w['sym']:24s} -> {byrow[w['sym']]['status']}")

        # ---- execute T1 (one batched boot) ----
        t1 = routes.get("T1_KERNEL", [])
        if t1 and not a.skip_t1_boot:
            print(f"\n[router] T1_KERNEL: synthesizing {len(t1)} candidates (ladder discipline)...")
            good = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                for sym, rung, cost in ex.map(lambda w: t1_synth(w, a.local_attempts), t1):
                    spend += cost
                    if rung:
                        good.append(sym)
                        print(f"  {sym:24s} synth ok ({rung})")
                    else:
                        byrow[sym]["status"] = "T1_synth_fail"
                        print(f"  {sym:24s} synth FAILED")
            t1_go = [w for w in t1 if w["sym"] in good]
            if t1_go:
                print(f"[router] T1_KERNEL: ONE boot verifying {len(t1_go)} candidates in-kernel...")
                verd = t1_boot(t1_go)
                for w in t1_go:
                    v = verd.get(w["sym"])
                    if v is None:
                        byrow[w["sym"]]["status"] = "T1_no_verdict"
                    elif v[0] == "NO_VERDICT_CANARY":
                        byrow[w["sym"]]["status"] = "T1_no_verdict_canary_failed"
                    elif v[0] == "PASS":
                        # A read-only fn is checked at ONE state point (boot).
                        # That is not full behavior-equivalence — label it as
                        # what it is.
                        readonly = "read-only" in byrow[w["sym"]]["reason"]
                        byrow[w["sym"]]["status"] = ("verified_T1_at_boot_state" if readonly
                                                     else "verified_T1")
                    else:
                        byrow[w["sym"]]["status"] = f"T1_rejected(bad={v[2]})"
                    print(f"  {w['sym']:24s} -> {byrow[w['sym']]['status']}")
    finally:
        _dump()

    # ---- dashboard ----
    print("\n=== UNIFIED ROUTER DASHBOARD ===")
    counts: dict[str, int] = {}
    for r in rows:
        key = r["status"] if r["status"].startswith(("verified", "T")) else r["route"]
        counts[key] = counts.get(key, 0) + 1
    v0 = sum(1 for r in rows if r["status"].startswith("verified_T0"))
    v1 = sum(1 for r in rows if r["status"] == "verified_T1")
    v1b = sum(1 for r in rows if r["status"] == "verified_T1_at_boot_state")
    print(f"  worklist {len(rows)} | VERIFIED {v0 + v1} (T0 {v0}, T1 {v1}) "
          f"| boot-state-only {v1b} | spend ${spend:.4f} "
          f"| wall {round(time.time() - t_all)}s")
    for k in sorted(counts):
        print(f"    {k:26s} {counts[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

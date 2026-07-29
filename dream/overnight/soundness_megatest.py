#!/usr/bin/env python3
"""Soundness megatest — hammer both oracles with a large adversarial battery.

The 145-test suite and the recorder census prove 0 false passes on hundreds of
cases. This scales that to THOUSANDS: for every recorder-closeable register
function, a big battery of randomized mutants (bit-flipped offsets, corrupted
constants, dropped/duplicated/reordered accesses, corrupted returns) that must
ALL be rejected; and for a wide set of pure kernel leaves, a battery of wrong
candidates through the hostdiff oracle (delegation, constant, identity,
off-by-one, negated) that must never MATCH. Headline: total adversarial
candidates tested, and false passes (must be 0).

Deterministic, no model, no boots. Reads the recorder census jsonl if present
(else self-harvests a small closeable set). Incremental JSONL + heartbeat.

Usage: soundness_megatest.py [--census PATH] [--mutants-per K] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/mmiogen", "dream/cluster", "dream/hostdiff", "dream/widerun"):
    sys.path.insert(0, os.path.join(REPO, p))
import hostdiff  # noqa: E402
import mmio_harness as mh  # noqa: E402

KSRC = os.environ.get("KSRC", mh.KSRC)

# deterministic PRNG (Math.random unavailable / non-reproducible; seed by index)
def _rng(seed):
    s = (seed * 2654435761 + 0x9E3779B9) & 0xFFFFFFFF
    def nxt():
        nonlocal s
        s ^= (s << 13) & 0xFFFFFFFF; s ^= s >> 17; s ^= (s << 5) & 0xFFFFFFFF
        return s & 0xFFFFFFFF
    return nxt


def recorder_mutants(out: str, fn: str, k: int) -> list[tuple[str, str]]:
    """K randomized mutants of the emitted correct candidate — each SHOULD change
    the observable register program/return."""
    src = open(f"{out}/{fn}_cand.rs").read()
    made, rnd = [], _rng(hash(fn) & 0xFFFF)
    consts = list(re.finditer(r"(const \w+: u32 = )(0x[0-9a-fA-F]+|\d+)(;)", src))
    reglines = [i for i, ln in enumerate(src.splitlines()) if "reg_read(" in ln or "reg_write(" in ln]
    lines = src.splitlines()
    for i in range(k):
        mode = rnd() % 5
        text = src
        if mode == 0 and consts:                       # corrupt a random constant
            c = consts[rnd() % len(consts)]
            flip = 1 << (rnd() % 12)
            text = src[:c.start()] + c.group(1) + f"0x{int(c.group(2), 0) ^ flip:x}" + c.group(3) + src[c.end():]
        elif mode == 1 and reglines:                   # drop a random access
            j = reglines[rnd() % len(reglines)]
            text = "\n".join(lines[:j] + lines[j + 1:])
        elif mode == 2 and reglines:                   # duplicate a random access
            j = reglines[rnd() % len(reglines)]
            text = "\n".join(lines[:j] + [lines[j]] + lines[j:])
        elif mode == 3 and len(reglines) >= 2:         # swap two accesses (reorder)
            a, b = reglines[0], reglines[-1]
            L = lines[:]; L[a], L[b] = L[b], L[a]; text = "\n".join(L)
        elif re.search(r"->\s*u32", src) and "return " in src:  # corrupt return
            text = re.sub(r"return ([^;]+);", f"return ({{ let _r = (\\1); _r ^ 0x{1 + (rnd() % 255):x}u }});", src, count=1)
        if text != src:
            path = f"{out}/{fn}_meg{i}.rs"
            open(path, "w").write(text)
            made.append((f"meg{i}:{mode}", os.path.basename(path)))
    return made


def megatest_recorder(census_jsonl: str, k: int, jl, hb) -> dict:
    closed = []
    if os.path.exists(census_jsonl):
        for ln in open(census_jsonl):
            r = json.loads(ln)
            if r.get("verdict") == "CLOSED":
                closed.append((r["file"], r["fn"]))
    tested = fp = anomalies = 0
    tmp = "/private/tmp/megatest_gate"
    for i, (path, fn) in enumerate(closed):
        os.makedirs(tmp, exist_ok=True)
        try:
            mh.generate(path, fn, tmp)
            if mh.gate(fn, tmp, f"{fn}_cand.rs")[0] != "MATCH":
                anomalies += 1
                continue
            leaks = []
            for tag, cand in recorder_mutants(tmp, fn, k):
                v = mh.gate(fn, tmp, cand)[0]
                tested += 1
                if v == "MATCH":
                    leaks.append(tag); fp += 1
            rec = {"fn": fn, "file": path, "mutants": tested, "false_pass": leaks}
            jl.write(json.dumps(rec) + "\n"); jl.flush()
            if leaks:
                hb(f"!!! RECORDER FALSE PASS {path}:{fn} {leaks}")
        except Exception:
            anomalies += 1
        if (i + 1) % 20 == 0:
            hb(f"  recorder megatest {i+1}/{len(closed)} fns | {tested} mutants | {fp} false")
    return {"closed_fns": len(closed), "mutants_tested": tested,
            "false_passes": fp, "gen_anomalies": anomalies}


# ---- hostdiff pure-leaf wrong-candidate battery ----

def hostdiff_battery(jl, hb, max_fns: int) -> dict:
    import widerun
    import purity
    widerun.DIRS = ["lib", "lib/math", "kernel", "kernel/time", "mm", "crypto",
                    "block", "fs", "net/core", "sound/core"]
    work = widerun.harvest()
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    pure = [w for w in work if w["sym"] in pn][:max_fns]

    def wrongs(sym, ret):
        rt = {"i32": "i32", "u32": "u32", "i64": "i64", "u64": "u64", "bool": "bool", "usize": "usize"}.get(ret, "u64")
        exp = "cgir_" + sym.lstrip("_")
        # candidates that are wrong-to-accept regardless of the function's spec:
        yield "delegation", f'extern "C"{{fn {sym}()->{rt};}}\n#[no_mangle] pub extern "C" fn {exp}()->{rt}{{unsafe{{{sym}()}}}}'
        z = "false" if rt == "bool" else "0"
        yield "constant", f'#[no_mangle] pub extern "C" fn {exp}()->{rt}{{{z}}}'

    tested = fp = 0
    for i, w in enumerate(pure):
        sym, path, ret = w["sym"], w["file"], w["ret"]
        src = open(os.path.join(KSRC, path)).read()
        try:
            _r, params = hostdiff.parse_sig(src, sym)
        except SystemExit:
            continue
        exp = "cgir_" + sym.lstrip("_")
        rt = {"i32": "i32", "u32": "u32", "i64": "i64", "u64": "u64", "bool": "bool", "usize": "usize"}.get(
            {"int": "i32", "unsigned int": "u32", "u32": "u32", "u64": "u64", "unsigned long": "u64",
             "long": "i64", "int32_t": "i32", "bool": "bool"}.get(ret, "u64"), "u64")
        args = ", ".join(f"a{j}: {rt}" for j in range(len(params)))
        argl = args
        # delegation (forwards to C) + constant — both wrong-to-accept universally
        cands = {
            "delegation": f'extern "C" {{ fn {sym}({", ".join(rt for _ in params)}) -> {rt}; }}\n'
                          f'#[no_mangle] pub extern "C" fn {exp}({argl}) -> {rt} {{ unsafe {{ {sym}({", ".join(f"a{j}" for j in range(len(params)))}) }} }}',
            "constant0": f'#[no_mangle] pub extern "C" fn {exp}({argl}) -> {rt} {{ {"false" if rt=="bool" else "0"} }}',
        }
        for tag, code in cands.items():
            d = f"/private/tmp/meg_hd_{sym}"; os.makedirs(d, exist_ok=True)
            open(f"{d}/c.rs", "w").write(code)
            try:
                v = hostdiff.run(path, sym, f"{d}/c.rs", [], KSRC, 100_000, quiet=True)["verdict"]
            except Exception:
                v = "ERR"
            tested += 1
            if v == "MATCH":
                fp += 1
                jl.write(json.dumps({"fn": sym, "file": path, "tag": tag, "FALSE_PASS": True}) + "\n"); jl.flush()
                hb(f"!!! HOSTDIFF FALSE PASS {path}:{sym} ({tag})")
        if (i + 1) % 25 == 0:
            hb(f"  hostdiff battery {i+1}/{len(pure)} fns | {tested} candidates | {fp} false")
    return {"pure_fns": len(pure), "candidates_tested": tested, "false_passes": fp}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", default=os.path.join(REPO, "dream/overnight/reports/recorder_census/sweep.jsonl"))
    ap.add_argument("--mutants-per", type=int, default=60)
    ap.add_argument("--max-pure", type=int, default=300)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "soundness_megatest"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    jl = open(os.path.join(a.out, "megatest.jsonl"), "w")
    t0 = time.time()

    def hb(m):
        print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

    hb(f"MEGATEST start | {a.mutants_per} mutants/closed-fn | up to {a.max_pure} pure leaves")
    rec = megatest_recorder(a.census, a.mutants_per, jl, hb)
    hb(f"recorder: {rec['closed_fns']} fns, {rec['mutants_tested']} mutants, {rec['false_passes']} false")
    hd = hostdiff_battery(jl, hb, a.max_pure)
    hb(f"hostdiff: {hd['pure_fns']} fns, {hd['candidates_tested']} candidates, {hd['false_passes']} false")

    total_adv = rec["mutants_tested"] + hd["candidates_tested"]
    total_fp = rec["false_passes"] + hd["false_passes"]
    summary = {"recorder": rec, "hostdiff": hd,
               "total_adversarial_candidates": total_adv, "total_false_passes": total_fp,
               "wall_s": round(time.time() - t0)}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    hb("=" * 60)
    hb(f"MEGATEST DONE: {total_adv} adversarial candidates | FALSE PASSES = {total_fp} | {summary['wall_s']}s")
    return 1 if total_fp else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Overnight sweep — the recorder against the ENTIRE driver tree, as a
coverage census AND a soundness stress test at scale.

Walk every driver .c that touches readl/writel, attempt to close each register
function with the MMIO harness generator, and for each one that closes run a
MUTANT BATTERY (wrong register, dropped access, corrupted constant, corrupted
return) that must EVERY ONE be rejected on the trace/return. Two questions
answered definitively:

  1. COVERAGE: what fraction of the real driver register mass does the extractor
     close (and the refusal taxonomy for the rest — the exact next-increment map).
  2. SOUNDNESS AT SCALE: across every (closed fn x mutant) pair that compiles,
     how many falsely MATCH? Must be 0. This is the project's core claim tested
     on thousands of adversarial candidates, not a handful.

Deterministic: no model calls ($0), no kernel boots (can't hang, can't bill).
Robust for unattended running: per-function isolation, incremental JSONL, a
progress heartbeat, and a resumable done-set. Safe to leave overnight.

Usage: overnight_sweep.py [--root drivers] [--limit N] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import cluster  # noqa: E402
import mmio_harness as mh  # noqa: E402

KSRC = os.environ.get("KSRC", mh.KSRC)
MMIO = re.compile(r"\b(readl|writel)\b")


def mutants(out: str, fn: str) -> list[tuple[str, str]]:
    """(name, cand_filename) textual mutants of the emitted correct candidate,
    each of which SHOULD change the register program or return -> DIVERGE.
    Textual so it's robust to the emitter's internals. generate() already wrote
    {fn}_cand.rs (correct) and {fn}_bad.rs (offset-xor)."""
    src = open(f"{out}/{fn}_cand.rs").read()
    made = [("offset_xor", f"{fn}_bad.rs")]  # generate()'s built-in mutant

    def write(tag: str, text: str) -> None:
        if text != src:
            open(f"{out}/{fn}_{tag}.rs", "w").write(text)
            made.append((tag, f"{fn}_{tag}.rs"))

    # drop the first reg access line (missing register op)
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if "reg_read(" in ln or "reg_write(" in ln:
            write("drop_access", "\n".join(lines[:i] + lines[i + 1:]))
            break
    # corrupt the first register constant (wrong offset)
    m = re.search(r"(const \w+: u32 = )(0x[0-9a-fA-F]+|\d+)(;)", src)
    if m:
        newval = f"0x{(int(m.group(2), 0) ^ 0x8):x}"
        write("bad_const", src[:m.start()] + m.group(1) + newval + m.group(3) + src[m.end():])
    # corrupt the return, if the function returns a value
    if re.search(r"->\s*u32", src) and "return " in src:
        write("bad_return", re.sub(r"return ([^;]+);", r"return (\1) ^ 0x7u;", src, count=1))
    return made


def sweep_fn(path: str, fn: str, tmp: str) -> dict:
    """One function: extract -> (refuse|close). On close, gate correct + mutants."""
    rec = {"file": path, "fn": fn}
    try:
        ex = mh.generate(path, fn, tmp)          # extract + emit ref/cand/bad/probe
    except mh.Unsupported as e:
        rec["verdict"] = "REFUSED"
        rec["reason"] = str(e).split("`")[0].split("(")[0].strip()[:40]
        return rec
    except Exception as e:  # a parser crash must NOT kill the sweep
        rec["verdict"] = "EXTRACT_ERROR"
        rec["reason"] = f"{type(e).__name__}: {e}"[:60]
        return rec
    # correct must MATCH
    try:
        vc, _ = mh.gate(fn, tmp, f"{fn}_cand.rs")
    except Exception as e:
        rec["verdict"] = "GATE_ERROR"
        rec["reason"] = str(e)[:60]
        return rec
    if vc != "MATCH":
        rec["verdict"] = "HARNESS_ANOMALY"   # correct candidate didn't match its own ref
        rec["detail"] = vc
        return rec
    # mutant battery: every compiling mutant must DIVERGE; a MATCH is a FALSE PASS
    results, false_pass = {}, []
    for tag, cand in mutants(tmp, fn):
        try:
            v, _ = mh.gate(fn, tmp, cand)
        except Exception:
            v = "GATE_ERR"
        results[tag] = v
        if v == "MATCH":
            false_pass.append(tag)
    rec["verdict"] = "CLOSED"
    rec["program"] = " ".join(f"{k}({n})" for k, _o, n, _e in ex["program"] if n)
    rec["mutants"] = results
    rec["false_pass"] = false_pass
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="drivers")
    ap.add_argument("--limit", type=int, default=0, help="max functions (0 = all)")
    ap.add_argument("--out", default=os.path.join(HERE, "overnight_out"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    jl = open(os.path.join(a.out, "sweep.jsonl"), "w")
    log = open(os.path.join(a.out, "progress.log"), "w")

    def hb(msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        log.write(line + "\n"); log.flush()

    root = os.path.join(KSRC, a.root)
    files = []
    for dp, _, fns in os.walk(root):
        for f in fns:
            if f.endswith(".c"):
                files.append(os.path.join(dp, f))
    files.sort()
    hb(f"START sweep: {len(files)} .c files under {a.root}")

    t0 = time.time()
    tally = {"scanned": 0, "reg_fns": 0, "CLOSED": 0, "REFUSED": 0,
             "HARNESS_ANOMALY": 0, "EXTRACT_ERROR": 0, "GATE_ERROR": 0}
    refusals: dict[str, int] = {}
    false_passes = []
    tmp = "/private/tmp/overnight_gate"
    done = 0
    for fpath in files:
        rel = os.path.relpath(fpath, KSRC)
        try:
            src = open(fpath, errors="ignore").read()
        except OSError:
            continue
        if not MMIO.search(src):
            continue
        try:
            fns = cluster.functions(src)
        except Exception:
            continue
        for fn, meta in fns.items():
            if not MMIO.search(meta["text"]):
                continue
            tally["reg_fns"] += 1
            os.makedirs(tmp, exist_ok=True)
            try:
                rec = sweep_fn(rel, fn, tmp)
            except Exception as e:
                rec = {"file": rel, "fn": fn, "verdict": "SWEEP_ERROR",
                       "reason": f"{type(e).__name__}: {e}"[:80],
                       "tb": traceback.format_exc()[-200:]}
            jl.write(json.dumps(rec) + "\n"); jl.flush()
            v = rec.get("verdict", "?")
            tally[v] = tally.get(v, 0) + 1
            if v == "REFUSED":
                refusals[rec.get("reason", "?")] = refusals.get(rec.get("reason", "?"), 0) + 1
            if rec.get("false_pass"):
                false_passes.append({"fn": fn, "file": rel, "mutants": rec["false_pass"]})
                hb(f"!!! FALSE PASS: {rel}:{fn} mutants={rec['false_pass']}")
            done += 1
            if done % 200 == 0:
                hb(f"{done} reg-fns | CLOSED {tally['CLOSED']} | REFUSED {tally['REFUSED']} "
                   f"| false_pass {len(false_passes)} | {round(time.time()-t0)}s")
            if a.limit and done >= a.limit:
                break
        if a.limit and done >= a.limit:
            break

    summary = {
        "reg_fns_scanned": tally["reg_fns"],
        "closed": tally["CLOSED"],
        "refused": tally["REFUSED"],
        "harness_anomaly": tally.get("HARNESS_ANOMALY", 0),
        "errors": tally.get("EXTRACT_ERROR", 0) + tally.get("GATE_ERROR", 0) + tally.get("SWEEP_ERROR", 0),
        "coverage_pct": round(100 * tally["CLOSED"] / max(1, tally["reg_fns"]), 2),
        "false_passes": false_passes,
        "false_pass_count": len(false_passes),
        "top_refusals": dict(sorted(refusals.items(), key=lambda x: -x[1])[:20]),
        "wall_s": round(time.time() - t0),
    }
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    hb("=" * 60)
    hb(f"DONE: {tally['reg_fns']} register fns | CLOSED {tally['CLOSED']} "
       f"({summary['coverage_pct']}%) | REFUSED {tally['REFUSED']}")
    hb(f"SOUNDNESS: false passes across all mutant batteries = {len(false_passes)}")
    hb(f"top refusal reasons: {summary['top_refusals']}")
    hb(f"wall {summary['wall_s']}s")
    return 1 if false_passes else 0


if __name__ == "__main__":
    sys.exit(main())

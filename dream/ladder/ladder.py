#!/usr/bin/env python3
"""The synth ladder — c2rust → local 14B → Haiku, gate-arbitrated.

COSTDOWN §3 + localbench's taxonomy, wired into one loop. Every function walks
the rungs cheapest-first; the hostdiff oracle (sub-second, sound) arbitrates at
every step, so a rung's quality only decides how far the function climbs, never
whether a wrong transplant survives:

  rung 0  c2rust        deterministic transpile of the shimmed TU   $0
  rung 1  local model   qwen2.5-coder:14b via ollama, ≤2 attempts   $0
  rung 2  Haiku         API, ≤2 attempts, cost-ledgered             $ (tail only)

Escalate-after-2 is localbench's finding: small models loop on what they don't
understand; the next rung is cheaper than a third retry.

Outputs a per-function ledger: which rung solved it, attempts, seconds, dollars —
the empirical routing table the full-kernel cost model needs.

Usage: ladder.py [--skip-c2rust] [--skip-haiku] [--out ladder_results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("hostdiff", "cluster", "localmodel"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster  # noqa: E402
import hostdiff  # noqa: E402
import localbench  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
WORKLIST = localbench.WORKLIST  # same battery -> comparable numbers
LOCAL_MODEL = "qwen2.5-coder:14b"
HAIKU_MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 list price (per MTok); ladder runs interactive (no batch discount) —
# the batch/cache stack is a production lever, priced in the estimate instead.
HAIKU_IN, HAIKU_OUT = 1.00, 5.00


def verify(path, func, deps, cand_src, tag):
    cand = f"/tmp/ladder_{func}_{tag}.rs"
    open(cand, "w").write(cand_src)
    return hostdiff.run(path, func, cand, deps, KSRC, 500_000, quiet=True, probe_timeout=20)


# ---------------- rung 0: c2rust ----------------

C2RUST_TYPES = {  # C ffi type names -> core names (freestanding, no libc crate)
    "c_ulonglong": "u64", "c_longlong": "i64", "c_ulong": "u64", "c_long": "i64",
    "c_uint": "u32", "c_int": "i32", "c_ushort": "u16", "c_short": "i16",
    "c_uchar": "u8", "c_schar": "i8", "c_char": "i8",
}


def _map_ffi_types(rs: str) -> str:
    """`(::)?(libc|core::ffi|std::os::raw)::c_xxx` -> plain core int types."""
    return re.sub(
        r"(?:::)?(?:libc|core::ffi|std::ffi|std::os::raw)::(c_\w+)",
        lambda m: C2RUST_TYPES.get(m.group(1), m.group(0)),
        rs,
    )


def c2rust_rung(path: str, func: str, deps: list[str]) -> tuple[str | None, str]:
    """Transpile the SHIMMED TU (hostdiff's trick: it already compiles standalone
    on the host, which is exactly what c2rust needs). Keep the whole transpiled
    module — strip every #[no_mangle] so nothing collides with the C at link,
    then export ONLY the target under the ladder ABI name. extern "C" import
    blocks stay (they bind to the C dep TU at link). Returns (source|None, note)."""
    if not shutil.which("c2rust"):
        return None, "c2rust not installed"
    # canonical path — /tmp is a symlink on macOS and c2rust asserts on it
    w = f"/private/tmp/c2rust_{func}"
    subprocess.run(["rm", "-rf", w], check=True)
    os.makedirs(w)
    shutil.copy(os.path.join(HERE, "..", "hostdiff", "kshim.h"), w)
    # deps' exported prototypes so the TU compiles standalone (lcm -> gcd)
    protos = "\n".join(
        hostdiff.extern_protos(open(os.path.join(KSRC, d)).read()) for d in deps
    )
    open(f"{w}/tu.c", "w").write(hostdiff.shim_tu(path, KSRC, protos))
    json.dump(
        [{"directory": w, "file": f"{w}/tu.c",
          "arguments": ["cc", "-c", f"-I{w}", f"{w}/tu.c"]}],
        open(f"{w}/compile_commands.json", "w"),
    )
    r = subprocess.run(["c2rust", "transpile", f"{w}/compile_commands.json"],
                       capture_output=True, text=True, timeout=300)
    rs_path = f"{w}/tu.rs"
    if not os.path.exists(rs_path):
        return None, f"transpile failed: {(r.stderr or r.stdout).strip()[:200]}"
    rs = open(rs_path).read()

    if not re.search(rf"\bfn {re.escape(func)}\b", rs):
        return None, f"transpiled fn {func} not in output"
    rs = rs.replace("#[no_mangle]", "")           # nothing exported by default...
    rs = re.sub(rf"(pub (?:unsafe )?extern \"C\" fn {re.escape(func)}\b)",
                f'#[export_name = "cgir_{func.lstrip("_")}"]\n\\1', rs, count=1)
    rs = _map_ffi_types(rs)
    rs = re.sub(r"(?m)^use ::libc;\n", "", rs)
    rs = re.sub(r"(?m)^extern crate libc;\n", "", rs)
    header = ("#![allow(dead_code, mutable_transmutes, non_camel_case_types, "
              "non_snake_case, non_upper_case_globals, unused_assignments, "
              "unused_mut, unused_unsafe, path_statements)]\n")
    rs = re.sub(r"(?m)^#!\[.*\]\n", "", rs)       # replace its inner attrs with ours
    return header + rs, "ok"


# ---------------- rung 1: local model ----------------

def local_rung(path, func, deps, csrc, sig_line, attempts=2):
    fb, log = None, []
    for i in range(attempts):
        code, t = localbench.synth(LOCAL_MODEL, localbench.build_prompt(csrc, sig_line, fb))
        res = verify(path, func, deps, localbench.extract_code(code), f"local{i}")
        log.append(res["verdict"])
        if res["verdict"] == "MATCH":
            return True, log
        fb = localbench.feedback_of(res)
    return False, log


# ---------------- rung 2: Haiku ----------------

def haiku_call(prompt: str) -> tuple[str, float]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:  # same throwaway-key .env the fleet's synthesize.py uses
        envf = "/Users/aryaman/Documents/Programming/llm-semantic-compilers/.env"
        if os.path.exists(envf):
            m = re.search(r'ANTHROPIC_API_KEY="?([^"\s]+)', open(envf).read())
            if m:
                key = m.group(1)
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": HAIKU_MODEL, "max_tokens": 1500,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=120) as f:
        body = json.load(f)
    usage = body["usage"]
    cost = usage["input_tokens"] / 1e6 * HAIKU_IN + usage["output_tokens"] / 1e6 * HAIKU_OUT
    return body["content"][0]["text"], cost


def haiku_rung(path, func, deps, csrc, sig_line, attempts=2):
    fb, log, cost = None, [], 0.0
    for i in range(attempts):
        text, c = haiku_call(localbench.build_prompt(csrc, sig_line, fb))
        cost += c
        res = verify(path, func, deps, localbench.extract_code(text), f"haiku{i}")
        log.append(res["verdict"])
        if res["verdict"] == "MATCH":
            return True, log, cost
        fb = localbench.feedback_of(res)
    return False, log, cost


# ---------------- the loop ----------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-c2rust", action="store_true")
    ap.add_argument("--skip-haiku", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "ladder_results.json"))
    a = ap.parse_args()

    rows, spend = [], 0.0
    for path, func, deps in WORKLIST:
        src = open(os.path.join(KSRC, path)).read()
        csrc = localbench.context_of(src, func)
        ret, params = hostdiff.parse_sig(src, func)
        _, sig_line = localbench.rust_sig(func, ret, params)
        t0, row = time.time(), {"func": func, "rung": None, "cost": 0.0, "log": {}}

        # rung 0
        if not a.skip_c2rust:
            rs, note = c2rust_rung(path, func, deps)
            if rs is not None:
                res = verify(path, func, deps, rs, "c2rust")
                row["log"]["c2rust"] = res["verdict"]
                if res["verdict"] == "MATCH":
                    row["rung"] = "c2rust"
            else:
                row["log"]["c2rust"] = f"skip: {note}"

        # rung 1
        if row["rung"] is None:
            ok, log = local_rung(path, func, deps, csrc, sig_line)
            row["log"]["local"] = log
            if ok:
                row["rung"] = "local-14b"

        # rung 2
        if row["rung"] is None and not a.skip_haiku:
            ok, log, cost = haiku_rung(path, func, deps, csrc, sig_line)
            row["log"]["haiku"] = log
            row["cost"] = round(cost, 6)
            spend += cost
            if ok:
                row["rung"] = "haiku"

        row["secs"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"  {func:16s} -> {row['rung'] or 'UNSOLVED':10s} "
              f"${row['cost']:.4f} {row['secs']:6.1f}s  {row['log']}")

    n = len(rows)
    by = {r: sum(1 for x in rows if x["rung"] == r) for r in ("c2rust", "local-14b", "haiku", None)}
    print(f"\nLADDER: {n - by[None]}/{n} solved | "
          f"c2rust {by['c2rust']} | local {by['local-14b']} | haiku {by['haiku']} | "
          f"unsolved {by[None]} | total spend ${spend:.4f}")
    json.dump({"rows": rows, "spend": round(spend, 6)}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

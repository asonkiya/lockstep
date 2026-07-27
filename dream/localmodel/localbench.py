#!/usr/bin/env python3
"""localbench — measure what a FREE local model is worth in this pipeline.

COSTDOWN §3's claim: with sound gates, synthesizer quality is a wall-clock knob,
not a correctness knob — so a $0 local model (ollama) plus the $0 hostdiff
oracle should convert real kernel functions with zero external spend, just more
retries than Haiku (measured 74% first-pass). This benchmark measures exactly
that: first-pass rate, converge-by-retry-K rate, and wall-clock per function,
on the same real-kernel battery the boot gates verified.

The loop per function is the Ring 5 loop, with hostdiff replacing the boot:
  synth (local, temperature 0) -> hostdiff (~1 s) -> on reject, retry with the
  compiler error or the first counterexample -> converged | exhausted.

Usage: localbench.py [--model qwen2.5-coder:7b] [--retries 3] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "hostdiff"))
sys.path.insert(0, os.path.join(HERE, "..", "cluster"))
import cluster  # noqa: E402
import hostdiff  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
OLLAMA = "http://localhost:11434/api/generate"

# (file, func, deps-for-C-ref) — the boot-verified battery + one harder case
WORKLIST = [
    ("lib/math/gcd.c", "gcd", []),
    ("lib/math/int_sqrt.c", "int_sqrt", []),
    ("lib/math/int_pow.c", "int_pow", []),
    ("lib/hweight.c", "__sw_hweight32", []),
    ("lib/hweight.c", "__sw_hweight64", []),
    ("lib/math/lcm.c", "lcm", ["lib/math/gcd.c"]),
    ("lib/math/lcm.c", "lcm_not_zero", ["lib/math/gcd.c"]),
    # the hard one: table-driven fixed-point log — Haiku's fleet miss class
    ("lib/math/int_log.c", "intlog2", []),
]

C2RUST_TY = {
    "unsigned long": "u64", "unsigned long long": "u64", "long": "i64", "long long": "i64",
    "u64": "u64", "__u64": "u64", "s64": "i64", "unsigned int": "u32", "unsigned": "u32",
    "u32": "u32", "__u32": "u32", "int": "i32", "s32": "i32", "u16": "u16", "s16": "i16",
    "u8": "u8", "s8": "i8", "bool": "bool", "size_t": "usize", "ssize_t": "isize",
}


def rust_sig(func: str, ret: str, params: list[tuple[str, str]]) -> tuple[str, str]:
    sym = "cgir_" + func.lstrip("_")
    args = ", ".join(f"{n}: {C2RUST_TY[t]}" for t, n in params)
    return sym, f'#[no_mangle]\npub extern "C" fn {sym}({args}) -> {C2RUST_TY[ret]}'


def context_of(src: str, func: str) -> str:
    """Function text plus any file-scope static const tables it references —
    the model must embed them (the subw S-box pattern)."""
    body = cluster.functions(src)[func]["text"]
    ctx = []
    for m in re.finditer(r"static\s+const\s+[\w ]+\s+(\w+)\s*\[[^\]]*\]\s*=\s*\{.*?\};",
                         src, re.DOTALL):
        if re.search(rf"\b{m.group(1)}\b", body):
            ctx.append(m.group(0))
    return "\n\n".join(ctx + [body])


def build_prompt(csrc: str, sig_line: str, feedback: str | None) -> str:
    p = f"""Translate this Linux kernel C function to Rust. Rules:
- Output ONLY Rust code. No markdown fences, no explanation, no comments.
- The function MUST have exactly this signature:
{sig_line}
- Self-contained: implement any helper logic inline (private fns allowed). Do not call external functions.
- Kernel semantics: unsigned long is 64-bit. __ffs(x) is x.trailing_zeros(), __fls(x) is 63 - x.leading_zeros(). C unsigned arithmetic wraps: use wrapping_mul/wrapping_sub/wrapping_add where C could overflow.
- No std, no I/O, no globals, no unsafe needed.

C source:
{csrc}
"""
    if feedback:
        p += f"\nYour previous attempt FAILED:\n{feedback}\nOutput the corrected code only.\n"
    return p


def synth(model: str, prompt: str) -> tuple[str, float]:
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                         "options": {"temperature": 0, "num_predict": 1200}}).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as f:
        body = json.load(f)
    return body["response"], time.time() - t0


def extract_code(text: str) -> str:
    m = re.findall(r"```(?:rust)?\n(.*?)```", text, re.DOTALL)
    return (m[0] if m else text).strip()


def feedback_of(res: dict) -> str:
    if res["verdict"] == "RUSTC_FAIL":
        return "rustc error:\n" + res.get("detail", "")[:500]
    if res["verdict"] == "DIVERGE":
        m = re.search(r"first: (.+)$", res.get("out", ""), re.MULTILINE)
        return "wrong output. counterexample: " + (m.group(1) if m else res.get("out", ""))
    return res.get("detail", res["verdict"])[:400]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(HERE, "results.json"))
    a = ap.parse_args()

    rows, t_all = [], time.time()
    for path, func, deps in WORKLIST:
        src = open(os.path.join(KSRC, path)).read()
        csrc = context_of(src, func)
        ret, params = hostdiff.parse_sig(src, func)
        sym, sig_line = rust_sig(func, ret, params)

        attempts, verdict, fb, t_fn = 0, None, None, time.time()
        while attempts <= a.retries:
            attempts += 1
            code, t_gen = synth(a.model, build_prompt(csrc, sig_line, fb))
            cand = f"/tmp/localbench_{func}.rs"
            open(cand, "w").write(extract_code(code))
            res = hostdiff.run(path, func, cand, deps, KSRC, 500_000, quiet=True)
            verdict = res["verdict"]
            tag = "PASS" if verdict == "MATCH" else verdict
            print(f"  {func}: attempt {attempts} -> {tag} (gen {t_gen:.0f}s, verify {res.get('secs','-')}s)")
            if verdict == "MATCH":
                break
            fb = feedback_of(res)
        rows.append({"func": func, "verdict": verdict, "attempts": attempts,
                     "secs": round(time.time() - t_fn, 1)})

    solved = [r for r in rows if r["verdict"] == "MATCH"]
    first = [r for r in solved if r["attempts"] == 1]
    print(f"\nMODEL {a.model}")
    print(f"  solved       : {len(solved)}/{len(rows)}")
    print(f"  first-pass   : {len(first)}/{len(rows)}  (Haiku measured: 74%)")
    print(f"  wall-clock   : {round(time.time() - t_all, 1)}s total")
    print(f"  external cost: $0.00")
    json.dump({"model": a.model, "rows": rows}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

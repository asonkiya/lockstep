#!/usr/bin/env python3
"""Synthesize a freestanding Rust reimplementation of a PURE C leaf function
(scalar in/out, no struct args, no locks) — the Tier-A class. Model-driven, to
keep the ratchet's growth authentic. Cross-compiles the result.

Usage: synth_leaf.py <c_source> <symbol> <seam> <out.rs> [--sig "..."]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"

PRELUDE = """\
#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
"""


def function_source(src: str, name: str) -> str:
    for m in re.finditer(rf"(?<![\w.>])(?:static\s+)?[\w \t\*]*?\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", src):
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    return src[m.start():i + 1]
            i += 1
    raise KeyError(name)


def build_prompt(c_body: str, seam: str, sig: str) -> str:
    return f"""Reimplement this pure Linux kernel leaf function as freestanding Rust,
to be compiled `--target aarch64-unknown-none-softfloat` and linked into vmlinux.

THE C:
```c
{c_body}
```

Emit ONLY the Rust (no fences, no prose):
1. First line: `// leaf: {seam}`
2. Exactly this export:
{sig}
3. Reproduce the C semantics EXACTLY, including integer widths and wrapping.
   `__fls(x)` (find-last-set, 0-indexed highest set bit) = `63 - x.leading_zeros()`
   for u64 (x != 0); equivalently `x.ilog2()`. Match the C bit-for-bit.
4. Rules: no_std-safe, no panics (no unwrap/array-index that can panic; these are
   pure arithmetic/bitops), no allocation, no external calls. The prelude
   (no_std, panic_handler) is already present — do NOT repeat it."""


def sample(prompt: str, feedback: str | None) -> tuple[str, float]:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    msgs = [{"role": "user", "content": prompt}]
    if feedback:
        msgs += [{"role": "assistant", "content": "(prev)"},
                 {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
    r = client.messages.create(model=MODEL, max_tokens=900, messages=msgs)
    cost = (r.usage.input_tokens * 1.0 + r.usage.output_tokens * 5.0) / 1e6
    return r.content[0].text, cost


def crosscompile(rs: str, seam: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{os.path.dirname(rs)}:/w", "cgir-kernel-gate",
         "bash", "-c",
         f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
         f"-C relocation-model=static -O {os.path.basename(rs)} -o /tmp/l.o && nm /tmp/l.o | grep -c {seam}"],
        capture_output=True, text=True)
    return ("1" in r.stdout.strip().split("\n")[-1:], (r.stdout + r.stderr)[:400])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("c_source"); ap.add_argument("symbol")
    ap.add_argument("seam"); ap.add_argument("out")
    ap.add_argument("--sig", required=True)
    ap.add_argument("--k", type=int, default=3)
    a = ap.parse_args()

    body = function_source(open(a.c_source).read(), a.symbol)
    prompt = build_prompt(body, a.seam, a.sig)
    total, feedback = 0.0, None
    for attempt in range(1, a.k + 1):
        text, cost = sample(prompt, feedback); total += cost
        _, code = parse_candidate(text)
        if a.seam not in code:
            feedback = f"must export {a.seam}"; print(f"  attempt {attempt}: ✗ {feedback}"); continue
        full = PRELUDE + "\n" + code + "\n"
        a.out=os.path.abspath(a.out); open(a.out, "w").write(full)
        ok, msg = crosscompile(a.out, a.seam)
        print(f"  attempt {attempt}: {'✓' if ok else '✗'} {msg[:120]}")
        if ok:
            print(f"leaf -> {a.out} (${total:.4f})"); return 0
        feedback = f"rustc: {msg}"
    print("no candidate"); return 1


if __name__ == "__main__":
    raise SystemExit(main())

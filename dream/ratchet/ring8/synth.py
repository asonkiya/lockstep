#!/usr/bin/env python3
"""Ring 8 — transplant clk-divider's Tier-B table-walk helpers, compiled against
the ksdk crate's ClkDivTable mirror. Struct-pointer iteration + field reads: the
struct-context class the research called the biggest Tier-B unlocker."""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "m3"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
KSDK = open(os.path.join(HERE, "ksdk.rs")).read()

PROMPT = f"""Transplant these two Linux clk-divider table-walk helpers into freestanding
Rust, compiled AGAINST the ksdk crate below (its ClkDivTable #[repr(C)] mirror is
already in scope — use it; do NOT redefine it or the prelude).

THE C (clk_div_table has two u32 fields, val then div; the loop runs until it
hits the sentinel entry whose div==0):
```c
unsigned int get_table_div(const struct clk_div_table *table, unsigned int val) {{
    const struct clk_div_table *clkt;
    for (clkt = table; clkt->div; clkt++)
        if (clkt->val == val) return clkt->div;
    return 0;
}}
unsigned int get_table_val(const struct clk_div_table *table, unsigned int div) {{
    const struct clk_div_table *clkt;
    for (clkt = table; clkt->div; clkt++)
        if (clkt->div == div) return clkt->val;
    return 0;
}}
```

The ksdk prelude (already present — do NOT repeat it; `ClkDivTable` is in scope):
```rust
{KSDK}
```

Emit ONLY the Rust (no fences, no prose):
1. First line: `// tierB: clk-divider table walk`
2. Exactly these two exports:
     #[no_mangle] pub extern "C" fn cgir_get_table_div(table: *const ClkDivTable, val: u32) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_table_val(table: *const ClkDivTable, div: u32) -> u32
3. Walk the raw pointer array in `unsafe`: start at `table`, read `(*p).div` as the
   sentinel (stop when div==0), compare the matching field, return the other;
   advance with `p.add(1)`. Match the C exactly. No panics, no slices with
   unknown length."""


def main() -> int:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    feedback = None
    seams = ["cgir_get_table_div", "cgir_get_table_val"]
    for attempt in range(1, 4):
        msgs = [{"role": "user", "content": PROMPT}]
        if feedback:
            msgs += [{"role": "assistant", "content": "(prev)"},
                     {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
        r = client.messages.create(model=MODEL, max_tokens=1000, messages=msgs)
        cost = (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        missing = [s for s in seams if s not in code]
        if missing:
            feedback = f"missing exports: {missing}"
            print(f"attempt {attempt}: ✗ {feedback}")
            continue
        out = os.path.join(HERE, "clkdiv.rs")
        open(out, "w").write(KSDK + "\n" + code + "\n")
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{HERE}:/w", "cgir-kernel-gate", "bash", "-c",
             "cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
             "-C relocation-model=static -O clkdiv.rs -o /tmp/c.o && nm /tmp/c.o | grep -c cgir_get_table"],
            capture_output=True, text=True)
        ok = rc.stdout.strip().split("\n")[-1] == "2"
        print(f"attempt {attempt}: {'✓' if ok else '✗'} {(rc.stdout + rc.stderr).strip()[:200]}")
        if ok:
            print(f"tier-B transplant -> {out} (${cost:.4f})")
            return 0
        feedback = f"rustc: {(rc.stdout + rc.stderr)[:300]}"
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

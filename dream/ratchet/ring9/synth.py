#!/usr/bin/env python3
"""Ring 9 — sweep the clk-divider divider-math family (6 fns) as ONE Rust object
against the ksdk crate's ClkDivTable mirror. A real subsystem cluster, unlocked
by the Ring 8 depth substrate (mirror the struct once, the whole family follows)."""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RATCHET = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(RATCHET))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
# ksdk (mirror + clk_div_mask + panic handler) + the divider flag constants
KSDK = open(os.path.join(RATCHET, "ring8", "ksdk.rs")).read()
FLAGS = """
// clk-divider flags (include/linux/clk-provider.h)
const CLK_DIVIDER_ONE_BASED: u64 = 1 << 0;
const CLK_DIVIDER_POWER_OF_TWO: u64 = 1 << 1;
const CLK_DIVIDER_MAX_AT_ZERO: u64 = 1 << 6;
const CLK_DIVIDER_EVEN_INTEGERS: u64 = 1 << 8;
"""

PROMPT = f"""Transplant the Linux clk-divider divider-math family into freestanding Rust,
as ONE object compiled against the ksdk crate below (its ClkDivTable #[repr(C)]
mirror and clk_div_mask() are in scope — use them; do NOT redefine the prelude).
The six functions read struct clk_div_table arrays (sentinel: div==0) and branch
on divider flags.

THE C (clk_div_mask(w) = (1<<w)-1; __ffs(x) = trailing-zero count):
```c
u32 get_table_div(const ClkDivTable *t, u32 val)  // walk: return div where val matches, else 0
u32 get_table_val(const ClkDivTable *t, u32 div)  // walk: return val where div matches, else 0
u32 get_table_maxdiv(const ClkDivTable *t, u8 width) {{
  u32 maxdiv=0, mask=clk_div_mask(width);
  walk t: if (div>maxdiv && val<=mask) maxdiv=div; return maxdiv; }}
u32 get_maxdiv(const ClkDivTable *t, u8 width, u64 flags) {{
  if ONE_BASED return clk_div_mask(width);
  if POWER_OF_TWO return 1<<clk_div_mask(width);
  if EVEN_INTEGERS return 2*(clk_div_mask(width)+1);
  if t!=null return get_table_maxdiv(t,width);
  return clk_div_mask(width)+1; }}
u32 get_div(const ClkDivTable *t, u32 val, u64 flags, u8 width) {{
  if ONE_BASED return val; if POWER_OF_TWO return 1<<val;
  if MAX_AT_ZERO return val?val:clk_div_mask(width)+1;
  if EVEN_INTEGERS return 2*(val+1);
  if t!=null return get_table_div(t,val); return val+1; }}
u32 get_val(const ClkDivTable *t, u32 div, u64 flags, u8 width) {{
  if ONE_BASED return div; if POWER_OF_TWO return __ffs(div);   // div.trailing_zeros()
  if MAX_AT_ZERO return (div==clk_div_mask(width)+1)?0:div;
  if EVEN_INTEGERS return (div>>1)-1;
  if t!=null return get_table_val(t,div); return div-1; }}
```

Prelude already present (do NOT repeat); ClkDivTable, clk_div_mask, and these are in scope:
```rust
{KSDK}
{FLAGS}
```

Emit ONLY the Rust (no fences, no prose):
1. First line: `// subsystem: clk-divider divider-math family`
2. Export exactly these six (a null table is a null raw pointer):
     #[no_mangle] pub extern "C" fn cgir_get_table_div(t: *const ClkDivTable, val: u32) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_table_val(t: *const ClkDivTable, div: u32) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_table_maxdiv(t: *const ClkDivTable, width: u8) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_maxdiv(t: *const ClkDivTable, width: u8, flags: u64) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_div(t: *const ClkDivTable, val: u32, flags: u64, width: u8) -> u32
     #[no_mangle] pub extern "C" fn cgir_get_val(t: *const ClkDivTable, div: u32, flags: u64, width: u8) -> u32
3. Internal helpers may be plain Rust fns calling each other; match the C exactly
   including flag precedence and the wrapping/shift semantics. `1<<x` is u32.
   Guard the raw-pointer walks in `unsafe`; treat a null `t` as "no table". No panics."""


def main() -> int:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    seams = ["cgir_get_table_div", "cgir_get_table_val", "cgir_get_table_maxdiv",
             "cgir_get_maxdiv", "cgir_get_div", "cgir_get_val"]
    feedback = None
    for attempt in range(1, 4):
        msgs = [{"role": "user", "content": PROMPT}]
        if feedback:
            msgs += [{"role": "assistant", "content": "(prev)"},
                     {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
        r = client.messages.create(model=MODEL, max_tokens=1600, messages=msgs)
        cost = (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        missing = [s for s in seams if s not in code]
        if missing:
            feedback = f"missing exports: {missing}"
            print(f"attempt {attempt}: ✗ {feedback}")
            continue
        out = os.path.join(HERE, "clkfam.rs")
        open(out, "w").write(KSDK + FLAGS + "\n" + code + "\n")
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{HERE}:/w", "cgir-kernel-gate", "bash", "-c",
             "cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
             "-C relocation-model=static -O clkfam.rs -o /tmp/c.o && nm /tmp/c.o | grep -c cgir_get"],
            capture_output=True, text=True)
        n = rc.stdout.strip().split("\n")[-1]
        ok = n == "6"
        print(f"attempt {attempt}: {'✓' if ok else '✗'} exports={n} {(rc.stdout + rc.stderr).strip()[:150]}")
        if ok:
            print(f"subsystem cluster (6 fns) -> {out} (${cost:.4f})")
            return 0
        feedback = f"rustc: {(rc.stdout + rc.stderr)[:300]}"
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

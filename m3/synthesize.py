#!/usr/bin/env python3
"""M3 — model-synthesized transplant, same gate.

design.md §4 M3: "The model selects the abstraction and produces the region
rewrite from the IR + R4L catalog; same gate. Proof: a cheap-model transplant of
a real region passes the full battery; a wrong one is rejected."

Pipeline per candidate (k attempts, cheap model):
  IR (m1/extract.py on the stock C) + R4L catalog + scaffold API
    -> prompt -> model -> region.rs
    -> gate: [selection check] [cargo build] [functional stress] [loom exhaustive]
  then the NEGATIVE CONTROL on the accepted winner: sabotage the scaffold's lock
  acquisition (the dropped lock) and re-run loom — the same candidate must now be
  REJECTED, or the gate is vacuous.

Modes:
  --self-test   gate the committed reference region.rs (no network) — proves the
                harness can pass/fail before any model output is trusted.
  --live        real model synthesis (needs ANTHROPIC_API_KEY via the CGIR .env).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HARNESS = os.path.join(HERE, "harness")
REGION_RS = os.path.join(HARNESS, "src", "region.rs")
LIB_RS = os.path.join(HARNESS, "src", "lib.rs")
STOCK_C = os.path.join(REPO, "m2", "ring_stock.c")

sys.path.insert(0, os.path.join(REPO, "m1"))
from extract import extract  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.00, 5.00  # $/Mtok, haiku

# The R4L abstraction catalog — verbatim from design.md §3.2. The model SELECTS
# from this; it does not invent abstractions.
CATALOG: list[tuple[str, str]] = [
    ("spin_lock/unlock around fields", "SpinLock<Fields> + guard scope"),
    ("container_of + list_head", "impl ListItem intrusive list"),
    ("kmalloc/kfree pairing", "KBox<T> / KVec<T> ownership"),
    ("rcu_dereference / synchronize_rcu", "Rcu<T> protected pointer"),
    ("refcount + kref", "Arc<T> / ARef<T>"),
    ("__percpu", "PerCpu<T>"),
]

_SCAFFOLD_API = """\
pub struct SpinLock<T>;              // owns T; T is unreachable without the lock
impl<T> SpinLock<T> {
    pub fn new(value: T) -> Self;
    pub fn lock(&self) -> Guard<'_, T>;   // spins; guard releases on drop
}
pub struct Guard<'a, T>;
impl<'a, T> Guard<'a, T> {
    pub fn with<R>(&self, f: impl FnOnce(&T) -> R) -> R;         // read access
    pub fn with_mut<R>(&mut self, f: impl FnOnce(&mut T) -> R) -> R; // write access
}"""


def build_prompt(c_source: str, ir: dict) -> str:
    catalog = "\n".join(f"  {c}  ->  {r}" for c, r in CATALOG)
    ir_slim = {
        "structs": {s: v["locks"] for s, v in ir["structs"].items()},
        "regions": [
            {"function": r["function"], "lock_field": r["lock_field"]}
            for r in ir["regions"]
        ],
        "protects": ir["protects"],
        "unprotected_accesses": ir["unprotected_accesses"],
    }
    return f"""You are transplanting one concurrent C region into Rust-for-Linux style Rust.

THE STOCK C (a spinlock-protected ring buffer; the region is the critical section
in ring_push/ring_count):

```c
{c_source}
```

THE EXTRACTED CONCURRENCY IR (which lock protects which fields):

```json
{json.dumps(ir_slim, indent=1)}
```

THE RUST-FOR-LINUX ABSTRACTION CATALOG (C idiom -> target). Pick the ONE row that
encodes this region's invariant:

{catalog}

THE SCAFFOLD LIBRARY you compile against (already written — do NOT redefine
SpinLock or Guard; they are in scope via `use crate::SpinLock;`):

```rust
{_SCAFFOLD_API}
```

TASK — emit the contents of region.rs, and nothing else (no fences, no prose):
1. First line exactly: `// abstraction: <your catalog selection>`
2. `use crate::SpinLock;`
3. A struct holding the protected fields (per the IR's protects map), sized
   SIZE=64 for the buffer — the fields must live INSIDE the SpinLock so they are
   unreachable without the guard. Do not add other synchronization (no statics,
   no atomics of your own).
4. `pub struct Ring` wrapping `SpinLock<...>` with exactly this API:
     pub fn new() -> Ring
     pub fn push(&self, c: u8)      // the ring_push critical section
     pub fn count(&self) -> usize   // the ring_count critical section
   Match the C semantics: push writes buf[head % SIZE] = c, then head += 1,
   count += 1, all in ONE guard scope. count() reads count under the lock.
5. Also `impl Default for Ring` (clippy).

Rules: safe Rust only (the unsafe lives in the scaffold); the whole critical
section body in a single lock() guard scope; no I/O; no unwrap on lock (lock()
does not fail)."""


def parse_candidate(text: str) -> tuple[str, str]:
    """(abstraction selection, fence-stripped code)."""
    code = text.strip()
    m = re.match(r"```[a-zA-Z]*\n(.*?)```\s*$", code, re.DOTALL)
    if m:
        code = m.group(1).strip()
    sel = ""
    sm = re.search(r"//\s*abstraction:\s*(.+)", code)
    if sm:
        sel = sm.group(1).strip()
    return sel, code


def sabotage_scaffold(lib_src: str) -> str:
    """The negative control: delete the marked lock acquisition, so lock() hands
    out a guard without ever taking the lock — the 'dropped lock' transplant."""
    return re.sub(
        r"// SABOTAGE-BEGIN.*?// SABOTAGE-END\n",
        "// [sabotaged: acquisition removed by the negative control]\n",
        lib_src,
        flags=re.DOTALL,
    )


# ---- gate legs ----------------------------------------------------------


def _cargo(args: list[str], loom: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if loom:
        env["RUSTFLAGS"] = "--cfg loom"
    return subprocess.run(
        ["cargo", *args], capture_output=True, text=True, cwd=HARNESS, env=env
    )


def gate(tag: str) -> tuple[bool, str]:
    """Run the full accept battery on whatever is in region.rs."""
    b = _cargo(["build", "--quiet"])
    if b.returncode != 0:
        return False, f"rustc: {b.stderr.strip()[:400]}"
    f = _cargo(["test", "--quiet", "--test", "functional"])
    if f.returncode != 0 or "FAILED" in f.stdout + f.stderr:
        return False, f"functional: {(f.stdout + f.stderr).strip()[:400]}"
    l = _cargo(["test", "--test", "loom_clean"], loom=True)
    if "region_is_race_clean ... ok" not in l.stdout + l.stderr:
        return False, f"loom: {(l.stdout + l.stderr).strip()[:400]}"
    return True, f"{tag}: build+functional+loom all green"


def negative_control() -> tuple[bool, str]:
    """Sabotage the scaffold's acquisition; the accepted candidate must now FAIL
    loom with a concurrent-access race. Restores the scaffold afterwards."""
    original = open(LIB_RS).read()
    try:
        with open(LIB_RS, "w") as fh:
            fh.write(sabotage_scaffold(original))
        l = _cargo(["test", "--test", "loom_clean"], loom=True)
        out = l.stdout + l.stderr
        rejected = l.returncode != 0 and "region_is_race_clean ... ok" not in out
        raced = "Causality violation" in out or "Concurrent" in out
        if rejected and raced:
            return True, "dropped lock REJECTED (concurrent-access race)"
        if rejected:
            return False, f"rejected but not for a race: {out.strip()[:300]}"
        return False, "sabotaged scaffold PASSED loom — the gate is vacuous"
    finally:
        with open(LIB_RS, "w") as fh:
            fh.write(original)


# ---- live sampling -------------------------------------------------------


def _api_key() -> str:
    envfile = "/Users/aryaman/Documents/Programming/llm-semantic-compilers/.env"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    with open(envfile) as fh:
        for line in fh:
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("no ANTHROPIC_API_KEY")


def sample(prompt: str, feedback: str | None = None) -> tuple[str, float]:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    msgs = [{"role": "user", "content": prompt}]
    if feedback:
        msgs += [
            {"role": "assistant", "content": "(previous candidate)"},
            {
                "role": "user",
                "content": f"That candidate FAILED the gate:\n{feedback}\n"
                "Emit a corrected region.rs (same rules, no fences).",
            },
        ]
    r = client.messages.create(model=MODEL, max_tokens=2000, messages=msgs)
    cost = (r.usage.input_tokens * PRICE_IN + r.usage.output_tokens * PRICE_OUT) / 1e6
    return r.content[0].text, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="real model synthesis")
    ap.add_argument("--self-test", action="store_true", help="gate the reference impl")
    ap.add_argument("--k", type=int, default=3, help="max attempts")
    args = ap.parse_args()

    if args.self_test:
        print("SELF-TEST: gating the committed reference region.rs")
        ok, msg = gate("reference")
        print(("  ✓ " if ok else "  ✗ ") + msg)
        nk, nmsg = negative_control()
        print(("  ✓ " if nk else "  ✗ ") + nmsg)
        print("SELF-TEST:", "PASS" if ok and nk else "FAIL")
        return 0 if ok and nk else 1

    if not args.live:
        ap.error("pick --self-test or --live")

    c_source = open(STOCK_C).read()
    # the IR comes from the ANALYSIS half of the stock file (struct + region fns);
    # the pthread test harness below main() is not part of the region.
    ir = extract(c_source.split("#define WRITERS")[0])
    prompt = build_prompt(c_source.split("#define WRITERS")[0], ir)
    print(f"IR: protects={json.dumps(ir['protects'])}")

    original_region = open(REGION_RS).read()
    total_cost, feedback = 0.0, None
    try:
        for attempt in range(1, args.k + 1):
            text, cost = sample(prompt, feedback)
            total_cost += cost
            sel, code = parse_candidate(text)
            print(f"\nattempt {attempt}: selected abstraction: {sel!r} (${total_cost:.4f} so far)")
            if "SpinLock" not in sel:
                feedback = f"wrong abstraction selection {sel!r} — the IR shows spin_lock around fields"
                print(f"  ✗ selection: {feedback}")
                continue
            with open(REGION_RS, "w") as fh:
                fh.write(code + "\n")
            ok, msg = gate(f"attempt {attempt}")
            print(("  ✓ " if ok else "  ✗ ") + msg)
            if not ok:
                feedback = msg
                continue
            nk, nmsg = negative_control()
            print(("  ✓ " if nk else "  ✗ ") + nmsg)
            if not nk:
                print("\nM3: FAIL (gate vacuous)")
                return 1
            print(f"\nM3: PASS — model transplant accepted; negative control rejected."
                  f"  cost=${total_cost:.4f}, attempts={attempt}")
            # leave the winning candidate in region.rs, but save it aside too
            with open(os.path.join(HERE, "winner_region.rs"), "w") as fh:
                fh.write(code + "\n")
            return 0
        print(f"\nM3: FAIL — no candidate passed in {args.k} attempts (${total_cost:.4f})")
        return 1
    finally:
        # region.rs in git stays the reference impl; winner preserved separately
        with open(REGION_RS, "w") as fh:
            fh.write(original_region)


if __name__ == "__main__":
    raise SystemExit(main())

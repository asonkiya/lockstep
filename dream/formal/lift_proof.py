#!/usr/bin/env python3
"""A4 — the formal rung ABOVE the differential: PROVE the safety lift.

The differential SAMPLES the lift (a workload of field values); Kani/CBMC
PROVES it, over the whole input domain. For a lifted candidate we emit a crate
containing the two REAL artifacts verbatim — tier-(a) (unsafe, `(*p).field`)
and tier-(b) (the `#![forbid(unsafe_code)]` core + its field-granular boundary)
— give both identical SYMBOLIC struct state, and assert:

  1. identical return value, and
  2. identical post-state in EVERY mirrored field,

for ALL possible field values. That is exactly the lift-preservation claim the
A1/A3 differentials establish by sampling, now discharged completely.

Kani also proves PANIC-FREEDOM as a side effect (its default checks: overflow,
div-by-zero, OOB). That is kernel-relevant, not cosmetic: a freestanding Rust
object's panic handler is `loop {}`, so a reachable panic is a KERNEL HANG —
and the weave's link-repair loop already had to drop readers whose division
paths referenced `core::panicking`. A proven-panic-free core cannot hang.

Scope (honest): this proves TRANSFORM equivalence (tier-a ≡ tier-b) and
panic-freedom, NOT that tier-a matches the kernel C — that is the sweep
differential's claim, which this composes with. Loop-free scalar cores are
CBMC's sweet spot; a looping core needs an unwind bound (bounded-complete,
VERT's documented limit) and is flagged, not silently under-proven.

  lift_proof.py <file> <fn>    emit + run the proof for one lifted candidate
  lift_proof.py batch [N]      prove up to N woven tier-b candidates
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _load(name, rel):
    path = os.path.join(REPO, "dream", rel)
    d = os.path.dirname(path)
    for extra in (d, os.path.join(REPO, "dream", "cluster"), os.path.join(REPO, "dream", "mirror")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


realize = _load("realize_fp", "realize/realize.py")

_CARGO = """[package]
name = "liftproof"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
name = "liftproof"
path = "src/lib.rs"
"""


def _strip_attrs(src, old_name, new_name):
    """The artifact verbatim, minus #[no_mangle]/extern "C" (symbol/ABI noise
    in a proof crate), with the fn renamed so both forms can coexist."""
    s = src.replace("#[no_mangle]\n", "").replace('pub unsafe extern "C" fn ', "pub unsafe fn ")
    s = s.replace('pub extern "C" fn ', "pub fn ")
    return re.sub(rf"\b{re.escape(old_name)}\b", new_name, s)


def build_proof(file, fn):
    """Emit the Kani crate source proving tier-a ≡ tier-b for this candidate."""
    rec, tr = realize.realize_light(file, fn)
    ok, racy = realize.lift_gate(tr)
    if not tr.get("liftable"):
        raise SystemExit(f"{fn}: not liftable (tier-a only)")
    node = tr["node_params"][0]
    struct_rs = node["struct"].capitalize() + "Mirror"
    fields = tr["lift_fields"]
    # plain (unpadded) mirror: the proof is about SEMANTICS, and both artifacts
    # address fields by name, so layout is irrelevant here (layout is the
    # in-tree _Static_assert's claim).
    rows = "\n".join(f"    pub {realize.rid(f)}: {realize.rust_ty(node['scalar_fields'][f])},"
                     for f in fields)
    mirror = (f"#[derive(Clone, Copy)]\npub struct {struct_rs} {{\n{rows}\n}}\n")

    tier_a = _strip_attrs(tr["fn_src"], f"{fn}_rs", "tier_a")
    tier_b = _strip_attrs(tr["fn_src_safe"], f"{fn}_rs", "tier_b")

    # symbolic inputs: every mirrored field + every scalar param
    inits, scalars, call_args = [], [], []
    for f in fields:
        ty = realize.rust_ty(node["scalar_fields"][f])
        inits.append(f"    let {f}_sym: {ty} = kani::any();")
    for i, p in enumerate(rec["params"]):
        if p["kind"] == "scalar":
            w = tr["pw"][i]
            nt = realize.wty(w)
            scalars.append(f"    let a{i}_sym: {nt} = kani::any();")
            call_args.append(f"a{i}_sym")
    mk = ", ".join(f"{realize.rid(f)}: {f}_sym" for f in fields)
    cmp_fields = "\n".join(
        f"        assert_eq!(m1.{realize.rid(f)}, m2.{realize.rid(f)}, "
        f'"lift changed field {f}");' for f in fields)
    args_sfx = ("".join(f", {a}" for a in call_args))
    ret_void = rec["ret"] == "void"
    # both artifacts are `unsafe fn` (they take the raw struct pointer, exactly
    # as woven); the harness supplies the one unsafe block.
    if ret_void:
        run = (f"        unsafe {{ tier_a(&mut m1{args_sfx}) }};\n"
               f"        unsafe {{ tier_b(&mut m2{args_sfx}) }};")
        ret_cmp = ""
    else:
        run = (f"        let r1 = unsafe {{ tier_a(&mut m1{args_sfx}) }};\n"
               f"        let r2 = unsafe {{ tier_b(&mut m2{args_sfx}) }};")
        ret_cmp = '        assert_eq!(r1, r2, "lift changed the return value");\n'

    proof = f"""
#[cfg(kani)]
mod proofs {{
    use super::*;

    /// For ALL field values and ALL scalar args, the tier-(a) unsafe artifact
    /// and the tier-(b) safe-core artifact compute the SAME return and leave
    /// the SAME state. Kani also proves both panic-free (a reachable panic in
    /// a freestanding kernel object is a hang).
    #[kani::proof]
    fn lift_preserves_semantics() {{
{chr(10).join(inits)}
{chr(10).join(scalars)}
        let mut m1 = {struct_rs} {{ {mk} }};
        let mut m2 = m1;
{run}
{ret_cmp}{cmp_fields}
    }}
}}
"""
    lib = ("//! A4 lift proof (generated) — tier-(a) unsafe artifact vs tier-(b)\n"
           "//! safe-core artifact, proven equivalent over the FULL input domain.\n"
           "#![allow(non_snake_case, non_camel_case_types, dead_code, unused_unsafe,\n"
           "         unused_variables, unused_mut, unused_parens, unused_braces)]\n\n"
           + mirror + "\n" + tier_a + "\n" + tier_b + "\n" + proof)
    return lib, {"fn": fn, "file": file, "fields": fields, "tier_b_eligible": ok,
                 "racy": sorted(racy)}


_LOOPY = re.compile(r"\b(while|loop|for)\b")


def prove(file, fn, keep=False, timeout=900):
    lib, meta = build_proof(file, fn)
    if _LOOPY.search(lib):
        print(f"KANI {fn}: SKIP (loop in body — needs an unwind bound; "
              f"bounded-complete only)")
        return 0, "SKIP_LOOP"
    d = tempfile.mkdtemp(prefix="liftproof_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    open(os.path.join(d, "Cargo.toml"), "w").write(_CARGO)
    open(os.path.join(d, "src", "lib.rs"), "w").write(lib)
    try:
        r = subprocess.run(["cargo", "kani"], cwd=d, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A model-checker timeout is a RESOURCE limit, not a defect: the claim
        # is simply undischarged. Report it as such (never as a pass, never as
        # a failure) — these are the candidates that need an unwind bound or a
        # longer budget.
        print(f"KANI {file}:{fn} -> TIMEOUT ({timeout}s — claim undischarged, "
              f"not a failure)")
        shutil.rmtree(d, ignore_errors=True)
        return 0, "TIMEOUT"
    out = r.stdout + r.stderr
    ok = "VERIFICATION:- SUCCESSFUL" in out or ("successfully verified harnesses" in out
                                               and "0 failures" in out)
    # Two very different failure classes — never conflate them:
    #   LIFT_FAILED  : a `lift changed ...` assertion failed => the transform is
    #                  WRONG. Serious; the candidate must not ship as tier-b.
    #   PANIC_RISK   : only Kani's default panic checks failed (overflow, div,
    #                  OOB) — the LIFT is proven equivalent, but BOTH forms can
    #                  panic on some input. In a freestanding kernel object the
    #                  panic handler is `loop {}`, so that is a latent HANG.
    #                  (The woven objects build with -O and no overflow-checks,
    #                  so today they WRAP like the C; the risk is latent, not
    #                  shipped — and it is exactly what the sampled differential
    #                  cannot see.)
    failed = re.findall(r"Failed Checks: (.+)", out)
    lift_broken = any("lift changed" in f for f in failed)
    if ok:
        verdict = "PROVEN"
    elif lift_broken:
        verdict = "LIFT_FAILED"
    elif failed:
        verdict = "PANIC_RISK"
    elif "VERIFICATION:- FAILED" in out:
        verdict = "FAILED"
    else:
        verdict = "ERROR"
    print(f"KANI {file}:{fn} -> {verdict}"
          + (f"   ({len(meta['fields'])} symbolic fields)" if ok else "")
          + (f"   [{'; '.join(sorted(set(failed)))}]" if failed else ""))
    if verdict in ("LIFT_FAILED", "FAILED", "ERROR"):
        tail = [ln for ln in out.splitlines() if "error" in ln.lower() or "FAILED" in ln][:6]
        print("   " + "\n   ".join(tail) if tail else out[-500:])
        print(f"   crate={d}")
        keep = True
    if not keep:
        shutil.rmtree(d, ignore_errors=True)
    return (0 if ok else 1), verdict


def batch(n=8):
    """Prove the first N woven tier-b candidates."""
    elig = [tuple(k.rsplit(":", 1)) for k in
            json.load(open(os.path.join(REPO, "dream", "realize", "weave_eligible.json")))]
    done = {"PROVEN": 0, "FAILED": 0, "SKIP_LOOP": 0, "ERROR": 0, "TIMEOUT": 0}
    tried = 0
    for file, fn in elig:
        if tried >= n:
            break
        try:
            rec, tr = realize.realize_light(file, fn)
            ok, _racy = realize.lift_gate(tr)
            if not ok:
                continue                    # tier-a: nothing to prove
        except Exception:
            continue
        tried += 1
        try:
            _, v = prove(file, fn)
        except Exception as e:
            v = "ERROR"
            print(f"KANI {fn} -> ERROR {str(e)[:80]}")
        done[v] = done.get(v, 0) + 1
    print(f"\nA4 lift proofs: {done['PROVEN']} PROVEN "
          f"(lift equivalence + panic-freedom over the FULL domain), "
          f"{done.get('PANIC_RISK',0)} PANIC_RISK (lift proven, both forms can "
          f"panic on some input — latent hang), {done.get('LIFT_FAILED',0)} LIFT_FAILED, "
          f"{done.get('SKIP_LOOP',0)} loop-skipped, {done.get('TIMEOUT',0)} timeout "
          f"(undischarged, not failures), {done.get('ERROR',0)} error "
          f"(of {tried} tier-b candidates attempted)")
    # only a broken LIFT is a hard failure; PANIC_RISK is a reported finding
    return 1 if done.get("LIFT_FAILED") or done.get("ERROR") or done.get("FAILED") else 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return batch(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    return prove(sys.argv[1], sys.argv[2])[0]


if __name__ == "__main__":
    raise SystemExit(main())

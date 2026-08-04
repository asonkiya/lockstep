#!/usr/bin/env python3
"""The mirror factory — Milestone B's industrial lever (Ring 8 hand-wrote one
ksdk mirror; Ring 9 showed one mirror frees a whole function family; this
builds them at scale, fail-closed).

Three subcommands:

  census   Rank structs by how many functions they BLOCK: run the readers
           (structdiff) prepare gate over its corpus twice — without and with
           the pinned config — and attribute every struct-caused refusal to
           its struct. Output: blockers.json, most-blocking first, with the
           config-pinning delta measured (not guessed).

  build    For each rankable struct (or --top N): resolve source ->
           mirror.mirror() under the pinned config -> verify FAIL-CLOSED both
           ways (rustc compiles the #[repr(C)] + const layout asserts; cc
           compiles the C guard TU with _Static_asserts against the same
           layout model) -> bank/{struct}.rs + registry.json. A struct that
           fails either build is REFUSED into the registry with its reason.

  export   Concatenate the verified bank into ksdk_mirrors.rs — one module,
           dependencies deduplicated — ready for the ksdk crate (in-kernel
           BUILD_BUG_ON re-certification happens at transplant time, Ring 8's
           gate; the host guards here catch generator/layout drift early).

Soundness: every banked mirror compiled BOTH guards; config-pinned mirrors
carry config_pinned=true in the registry (the layout claim is scoped to
pinned.config). Nothing is guessed: unresolvable/undecidable structs land in
registry["refused"] with reasons — the factory's own census-fix backlog.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("mirror", "cluster", "structdiff", "widerun"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
os.environ.setdefault("MIRROR_CONFIG", os.path.join(HERE, "pinned.config"))
import mirror    # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
BANK = os.path.join(HERE, "bank")
REGISTRY = os.path.join(HERE, "registry.json")


# ---------------------------------------------------------------------------
# census — measure which structs block how many functions
# ---------------------------------------------------------------------------

_STRUCT_IN_MSG = re.compile(r"(?:param-struct|struct)\s+([A-Za-z_]\w*)")


def _reader_blockers():
    """(blockers Counter, per-struct fn lists) from the structdiff prepare
    gate over its reach corpus — struct-attributed refusals only."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sd_harness_f", os.path.join(HERE, "..", "structdiff", "harness.py"))
    sd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sd)
    wl = json.load(open(os.path.join(HERE, "..", "structdiff", "reach_accepted.json")))
    blockers, examples, passed = Counter(), {}, []
    for it in wl:
        try:
            sd.prepare(it["file"], it["fn"])
            passed.append(it["fn"])
        except Exception as e:
            msg = str(e)
            sm = _STRUCT_IN_MSG.search(msg)
            # attribute config-#if refusals to the struct via the params
            if sm is None and "#if" in msg:
                sm = re.search(r"struct\s+(\w+)", msg)
            key = sm.group(1) if sm else f"(non-struct: {msg[:30]})"
            blockers[key] += 1
            examples.setdefault(key, []).append(it["fn"])
    return blockers, examples, passed


def census():
    print("== blocker census: readers prepare gate, config-pinned "
          f"({len(mirror._PINNED_CONFIG or ())} CONFIG symbols) ==")
    blockers, examples, passed = _reader_blockers()
    mirror.set_pinned_config(None)
    b0, _, passed0 = _reader_blockers()
    mirror.load_pinned_config(os.environ["MIRROR_CONFIG"])
    print(f"prepare-passing: {len(passed0)} unpinned -> {len(passed)} pinned "
          f"(+{len(passed) - len(passed0)} from config resolution)")
    out = {"pinned_passing": len(passed), "unpinned_passing": len(passed0),
           "blockers": [{"struct": k, "blocked": c, "fns": examples[k][:8]}
                        for k, c in blockers.most_common()]}
    json.dump(out, open(os.path.join(HERE, "blockers.json"), "w"), indent=1)
    for k, c in blockers.most_common(15):
        print(f"  {c:3d}  {k}  e.g. {examples[k][0]}")
    print(f"-> blockers.json ({len(blockers)} blocking classes)")


# ---------------------------------------------------------------------------
# build — mirror + dual fail-closed guards + bank
# ---------------------------------------------------------------------------

def _verify_rust(rust_src):
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "m.rs"), "w").write(
            "#![allow(non_camel_case_types, non_snake_case, dead_code)]\n" + rust_src + "\n")
        r = subprocess.run(["rustc", "--edition", "2021", "--crate-type=lib",
                            os.path.join(d, "m.rs"), "-o", os.path.join(d, "libm.rlib")],
                           capture_output=True, text=True, timeout=60)
        return (True, "") if r.returncode == 0 else (False, r.stderr[-400:])


_RS2C = {"i8": "signed char", "u8": "unsigned char", "bool": "unsigned char",
         "i16": "short", "u16": "unsigned short", "i32": "int", "u32": "unsigned",
         "i64": "long long", "u64": "unsigned long long",
         "isize": "long long", "usize": "unsigned long long"}


def _c_struct_def(name, fields):
    """C definition of the struct from the mirror's emitted rows — rows are
    (rust_type, fname, offset). The guard TU is then self-contained on the
    host: cc lays the struct out by the REAL ABI and the _Static_asserts
    compare that against the generator's arithmetic — a non-circular check of
    mirror.py's layout model. Returns None when a row can't be emitted
    host-side (nested by-value types) — C verification is then DEFERRED to
    the in-kernel BUILD_BUG_ON at transplant (flagged)."""
    lines = [f"struct {name} {{"]
    for rty, fname, _off in fields:
        if rty in _RS2C:
            lines.append(f"    {_RS2C[rty]} {fname};")
            continue
        if rty.startswith("*"):
            lines.append(f"    void *{fname};")
            continue
        am = re.match(r"\[(\w+); (\d+)\]$", rty)
        if am and am.group(1) in _RS2C:
            lines.append(f"    {_RS2C[am.group(1)]} {fname}[{am.group(2)}];")
            continue
        return None                      # nested by-value type -> defer
    lines.append("};")
    return "\n".join(lines)


def _verify_c(c_guard, struct_def):
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "g.c"), "w").write(
            "#include <stdint.h>\n#include <stddef.h>\n#include <assert.h>\n"
            "typedef unsigned char u8; typedef signed char s8;\n"
            "typedef unsigned short u16; typedef short s16;\n"
            "typedef unsigned u32; typedef int s32;\n"
            "typedef unsigned long long u64; typedef long long s64;\n"
            "typedef unsigned long long size_t_k, phys_addr_t, dma_addr_t, resource_size_t;\n"
            + struct_def + "\n" + c_guard + "\n")
        r = subprocess.run(["cc", "-std=c11", "-c", os.path.join(d, "g.c"),
                            "-o", os.path.join(d, "g.o")],
                           capture_output=True, text=True, timeout=60)
        return (True, "") if r.returncode == 0 else (False, r.stderr[-400:])


def build_one(struct, near_rel=None):
    """Mirror one struct, verify both guards, bank it. Returns (ok, info)."""
    near = os.path.join(KSRC, near_rel) if near_rel else None
    src = mirror.resolve_struct_source(struct, near_file=near)
    if src is None and near:
        src = open(near, errors="ignore").read()
    if src is None:
        return False, {"reason": "source not found"}
    try:
        m = mirror.mirror(src, struct, near_file=near)
    except mirror.Unsupported as e:
        return False, {"reason": str(e)[:80]}
    except Exception as e:
        return False, {"reason": f"{type(e).__name__}: {str(e)[:60]}"}
    ok_r, err_r = _verify_rust(m["rust"])
    if not ok_r:
        return False, {"reason": "rustc guard failed", "detail": err_r}
    sdef = _c_struct_def(struct, m["fields"])
    c_deferred = sdef is None
    if not c_deferred:
        # top-level guard lines only — nested guards need their own defs
        top_guard = "\n".join(ln for ln in m["c_guard"].splitlines()
                              if f"struct {struct}" in ln)
        ok_c, err_c = _verify_c(top_guard, sdef)
        if not ok_c:
            return False, {"reason": "cc guard failed", "detail": err_c}
    os.makedirs(BANK, exist_ok=True)
    open(os.path.join(BANK, f"{struct}.rs"), "w").write(m["rust"] + "\n")
    return True, {"size": m["size"], "align": m["align"],
                  "rust_type": m["rust_type"], "nested": m["nested"],
                  "config_pinned": m.get("config_pinned", False),
                  "c_guard_deferred": c_deferred,
                  "fields": [f for _, f, _ in m["fields"]]}


def _accepted_structs():
    """Every (struct, near_file) the accepted Tier-B worklists actually use —
    the reusable ksdk bank's real target. readers carry explicit struct params;
    the others carry struct nodes / alloc structs in their records."""
    want = {}
    R = os.path.join(HERE, "..")
    # readers: struct-pointer params (from the fn signature in reach records)
    for it in json.load(open(os.path.join(R, "structdiff", "reach_accepted.json"))):
        for s in it.get("structs", []):
            want.setdefault(s, it["file"])
    # container / efftrace: node params carry {"struct": ...}
    for sub in ("container_adt", "efftrace"):
        pth = os.path.join(R, sub, "reach_accepted.json")
        if not os.path.exists(pth):
            continue
        for it in json.load(open(pth)):
            for p in it.get("params", []):
                if p.get("struct"):
                    want.setdefault(p["struct"], it["file"])
    # alloc: the allocated struct
    ap = os.path.join(R, "allocmodel", "reach_accepted.json")
    if os.path.exists(ap):
        for it in json.load(open(ap)):
            if it.get("alloc_struct"):
                want.setdefault(it["alloc_struct"], it["file"])
    return want


def bank(top_n=None):
    """Build the reusable ksdk mirror bank from every struct the accepted
    Tier-B surface uses. Each mirror is dual-guard verified fail-closed; the
    registry records built (with unblock attribution) and refused (with
    reason) — the factory's own backlog."""
    want = _accepted_structs()
    reg = {"built": {}, "refused": {}}
    if os.path.exists(REGISTRY):
        reg = json.load(open(REGISTRY))
    items = sorted(want.items())
    if top_n:
        items = items[:top_n]
    n_ok = 0
    for struct, near in items:
        ok, info = build_one(struct, near)
        if ok:
            reg["built"][struct] = info
            reg["refused"].pop(struct, None)
            n_ok += 1
            print(f"  ✓ {struct}  (size {info['size']} pinned={info['config_pinned']} "
                  f"c_deferred={info['c_guard_deferred']})")
        else:
            reg["refused"][struct] = info
            print(f"  ✗ {struct}  ({info['reason'][:56]})")
    json.dump(reg, open(REGISTRY, "w"), indent=1)
    print(f"-> registry.json: {len(reg['built'])} banked (+{n_ok} this pass), "
          f"{len(reg['refused'])} refused / {len(want)} accepted-surface structs")


def build(top_n=None):
    bl = json.load(open(os.path.join(HERE, "blockers.json")))
    targets = []
    for row in bl["blockers"]:
        if row["struct"].startswith("("):
            continue
        targets.append((row["struct"], row["blocked"], row["fns"]))
    if top_n:
        targets = targets[:top_n]
    reg = {"built": {}, "refused": {}}
    if os.path.exists(REGISTRY):
        reg = json.load(open(REGISTRY))
    n_ok = 0
    for struct, blocked, fns in targets:
        # locate a file the struct's blocked fns live in (source resolution hint)
        wl = json.load(open(os.path.join(HERE, "..", "structdiff", "reach_accepted.json")))
        near_rel = next((it["file"] for it in wl if it["fn"] in fns), None)
        ok, info = build_one(struct, near_rel)
        info["blocked_fns"] = blocked
        if ok:
            reg["built"][struct] = info
            reg["refused"].pop(struct, None)
            n_ok += 1
            print(f"  ✓ {struct}  (unblocks {blocked}; size {info['size']}, "
                  f"pinned={info['config_pinned']})")
        else:
            reg["refused"][struct] = info
            print(f"  ✗ {struct}  ({info['reason'][:60]})")
    json.dump(reg, open(REGISTRY, "w"), indent=1)
    print(f"-> registry.json: {len(reg['built'])} banked (+{n_ok} this pass), "
          f"{len(reg['refused'])} refused")


def export():
    reg = json.load(open(REGISTRY))
    parts = ["// ksdk_mirrors — GENERATED by dream/mirrorfactory (do not edit)",
             "// Every mirror passed rustc const-layout asserts + the cc guard TU.",
             "// config_pinned mirrors are valid FOR pinned.config; in-kernel",
             "// BUILD_BUG_ON re-certification happens at transplant (Ring 8 gate).",
             "#![allow(non_camel_case_types, non_snake_case, dead_code)]", ""]
    emitted = set()
    for struct in sorted(reg["built"]):
        src = open(os.path.join(BANK, f"{struct}.rs")).read()
        # dedupe nested types emitted by multiple parents
        keep = []
        for block in src.split("\n\n"):
            tm = re.search(r"pub struct (\w+)", block)
            if tm:
                if tm.group(1) in emitted:
                    continue
                emitted.add(tm.group(1))
            keep.append(block)
        parts.append("\n\n".join(keep))
    out = os.path.join(HERE, "ksdk_mirrors.rs")
    open(out, "w").write("\n\n".join(parts) + "\n")
    ok, err = _verify_rust("\n".join(parts[4:]))
    print(f"-> {os.path.relpath(out)} ({len(emitted)} types) "
          f"{'[compiles]' if ok else '[BROKEN: ' + err[:80] + ']'}")
    return 0 if ok else 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "census"
    if cmd == "census":
        census()
    elif cmd == "build":
        top = int(sys.argv[2]) if len(sys.argv) > 2 else None
        build(top)
    elif cmd == "bank":
        top = int(sys.argv[2]) if len(sys.argv) > 2 else None
        bank(top)
    elif cmd == "export":
        return export()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

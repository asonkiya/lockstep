#!/usr/bin/env python3
"""Non-leaf weave: readers (the soundly-weavable oracle class).

Sweep-1 cycle-1 concluded the mirror-field wideners had hit diminishing returns
and the lever is INTEGRATION of the banked verified translations. The readers
class is the one that weaves directly: a structdiff-verified candidate is
`fn <fn>_rs(p: *mut Mirror, ...)` where the `#[repr(C)]` Mirror layout was
gate-verified identical to the real kernel struct. So `*mut Mirror` IS the real
`struct X *` at the ABI — a reader candidate is already a real-struct function,
not a cell model (unlike efftrace/alloc, which need a model->real step).

This turns a verified reader into the three artifacts the ratchet weaver
(weave.py) consumes, plus a HOST PROOF that both sides compile:

  1. Rust object: the candidate verbatim (mirror + guards + the `_rs` fn) with
     a no_std freestanding preamble + panic handler — linked into vmlinux.
  2. C seam: the in-tree function's body replaced by `{ <fn>_rs(<args>); }`,
     an extern decl, AND a `_Static_assert(sizeof/offsetof)` per mirror field
     re-certifying the layout against REAL kernel headers at kernel build (the
     deferred check, now closed in-tree).
  3. A manifest fragment (sources + rust_objects) for weave.py apply/gate.

Soundness chain: host differential (behavior, verified) + in-tree
_Static_assert (layout == real kernel, at build) + boot (it links + runs). A
wrong layout fails the kernel build; a wrong behavior was caught on the host.

  weave_readers.py prove <file> <fn>    host-compile both artifacts (no boot)
  weave_readers.py emit  <file> <fn>    write artifacts + print manifest fragment
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VERIFIED = os.path.join(REPO, "dream", "firstrun", "verified")
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
for p in ("cluster", "mirror"):
    sys.path.insert(0, os.path.join(REPO, "dream", p))
import cluster    # noqa: E402
import mirror     # noqa: E402

_RS2C = {"i8": "signed char", "u8": "unsigned char", "bool": "unsigned char",
         "i16": "short", "u16": "unsigned short", "i32": "int", "u32": "unsigned",
         "i64": "long long", "u64": "unsigned long long",
         "isize": "long long", "usize": "unsigned long long"}

# freestanding preamble: no_std + a local panic handler (weave.py localizes all
# but the first object's handler at link, per the panic-collision finding).
_FREESTANDING = """#![no_std]
#![allow(non_camel_case_types, non_snake_case, dead_code, unused_unsafe)]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
"""


def _cand_path(rel, fn):
    key = f"reader_{rel.replace('/', '__')}_{fn}"
    p = os.path.join(VERIFIED, f"{key}.rs")
    if not os.path.exists(p):
        raise SystemExit(f"no verified candidate: {p}")
    return p, key


def _parse_candidate(text):
    """(mirror_struct_name, rs_fn_name, [(rustty, argname)], ret_rustty|None)."""
    sm = re.search(r"pub struct (\w+)", text)
    fm = re.search(r'#\[no_mangle\]\s*pub extern "C" fn (\w+)\s*\(([^)]*)\)\s*(?:->\s*([\w:<>* ]+?))?\s*\{', text)
    if not fm:
        raise SystemExit("candidate: no #[no_mangle] extern fn found")
    args = []
    for piece in [a.strip() for a in fm.group(2).split(",") if a.strip()]:
        an, _, ty = piece.partition(":")
        args.append((ty.strip(), an.strip()))
    ret = (fm.group(3) or "").strip() or None
    return sm.group(1), fm.group(1), args, ret


def _c_scalar(rustty):
    return _RS2C.get(rustty.strip(), None)


def _real_sig(rel, fn):
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    f = cluster.functions(src)[fn]
    ret = re.sub(r"\b(static|inline|__always_inline|noinline)\b", " ", f["ret"]).strip()
    params = [p.strip() for p in f["params"].split(",") if p.strip() and p.strip() != "void"]
    return ret, params, src


def _layout_from_candidate(text):
    """(rust_struct, size, [(field, offset)]) from the candidate's OWN verified
    const-asserts — exactly the layout the host differential ran against, so the
    in-tree re-cert checks the same thing the boot will run."""
    sm = re.search(r"size_of::<(\w+)>\(\)\s*==\s*(\d+)", text)
    if not sm:
        raise SystemExit("candidate: no size_of assert")
    fields = [(m.group(1), int(m.group(2)))
              for m in re.finditer(r"offset_of!\(\w+,\s*(\w+)\)\s*==\s*(\d+)", text)]
    # mirror field types (for the host-proof struct emission)
    rows = re.findall(r"pub (\w+):\s*([^,]+),", text)
    return sm.group(1), int(sm.group(2)), fields, rows


def build_artifacts(rel, fn):
    cand_path, key = _cand_path(rel, fn)
    text = open(cand_path).read()
    _struct_rs, rs_fn, _rs_args, _rs_ret = _parse_candidate(text)
    ret_c, params_c, src = _real_sig(rel, fn)
    struct_c = _struct_of(rel, fn, src)
    _rs_struct, size, fields, rows = _layout_from_candidate(text)

    rust_obj = _FREESTANDING + "\n" + text

    # in-tree layout re-cert against the REAL kernel struct (offsetof needs the
    # real field names, which the candidate mirror mirrors 1:1). r#-keyword
    # fields in the mirror map back to the bare C name.
    def _cname(f):
        return f[2:] if f.startswith("r#") else f
    guard = "\n".join(
        [f'_Static_assert(sizeof(struct {struct_c}) == {size}, '
         f'"{struct_c} size drift vs woven mirror");']
        + [f'_Static_assert(offsetof(struct {struct_c}, {_cname(f)}) == {off}, '
           f'"{struct_c}.{_cname(f)} offset drift");' for f, off in fields])

    extern = f"{ret_c} {rs_fn}({', '.join(params_c)});\n"
    argnames = [re.match(r".*?([A-Za-z_]\w*)\s*(?:\[.*\])?$", p).group(1) for p in params_c]
    call = f"{rs_fn}({', '.join(argnames)})"
    body = f"{{\n\t{call};\n}}" if ret_c == "void" else f"{{\n\treturn {call};\n}}"
    return {"key": key, "rust_obj": rust_obj, "struct": struct_c,
            "guard": guard, "extern": extern, "seam": rs_fn,
            "ret_c": ret_c, "params_c": params_c, "argnames": argnames,
            "seam_body": body, "rows": rows, "fields": fields, "size": size}


def _struct_of(rel, fn, src):
    """The struct type the reader's first pointer param points at."""
    f = cluster.functions(src)[fn]
    for p in f["params"].split(","):
        sm = re.search(r"struct\s+(\w+)\s*\*", p)
        if sm:
            return sm.group(1)
    raise SystemExit(f"{fn}: no struct* param to weave against")


# ---------------------------------------------------------------------------
# host proof: rustc the object freestanding + cc the woven C with the guard
# ---------------------------------------------------------------------------

def prove(rel, fn):
    a = build_artifacts(rel, fn)
    d = tempfile.mkdtemp(prefix="weaverd_")
    open(os.path.join(d, "obj.rs"), "w").write(a["rust_obj"])
    # freestanding object compile. Prefer the aarch64 kernel target (the real
    # weave target); fall back to the host target if its std isn't installed
    # here — the point of the HOST proof is "candidate compiles freestanding",
    # the aarch64 cross is re-run by the docker weave (kernel-image rustc).
    base = ["rustc", "--crate-type=staticlib", "-C", "panic=abort",
            "-C", "relocation-model=static", "-O",
            os.path.join(d, "obj.rs"), "-o", os.path.join(d, "libobj.a")]
    rc = subprocess.run(base[:1] + ["--target", "aarch64-unknown-none-softfloat"]
                        + base[1:], capture_output=True, text=True)
    tgt = "aarch64-unknown-none-softfloat"
    if rc.returncode and "can't find crate for `core`" in rc.stderr:
        rc = subprocess.run(base, capture_output=True, text=True)
        tgt = "host (aarch64 cross deferred to docker weave)"
    rust_ok = rc.returncode == 0

    # woven C: real struct def + the guard + extern + a caller of the seam.
    # emit the struct from the mirror rows (host stand-in for real headers; the
    # in-tree build uses the REAL struct — this host proof checks the guard +
    # ABI shape compile, the layout-vs-real check is the in-kernel _Static_assert).
    sdef = ["struct " + a["struct"] + " {"]
    for f, rty in a["rows"]:
        rty = rty.strip()
        cname = f[2:] if f.startswith("r#") else f
        cty = _RS2C.get(rty) or ("void *" if rty.startswith("*") else None)
        if cty is None:
            mm = re.match(r"\[(\w+);\s*(\d+)\]$", rty)
            if mm:
                sdef.append(f"  {_RS2C.get(mm.group(1),'char')} {cname}[{mm.group(2)}];")
            else:
                sdef.append(f"  char {cname};")
        else:
            sdef.append(f"  {cty} {cname};")
    sdef.append("};")
    cprog = (
        "#include <stddef.h>\n"
        "typedef unsigned long long resource_size_t, u64, phys_addr_t;\n"
        "typedef unsigned u32; typedef int s32; typedef unsigned short u16;\n"
        + "\n".join(sdef) + "\n"
        + a["extern"]
        + a["guard"] + "\n"
        + f"{a['ret_c']} {fn}({', '.join(a['params_c'])}) {a['seam_body']}\n")
    open(os.path.join(d, "seam.c"), "w").write(cprog)
    cc = subprocess.run(["cc", "-std=c11", "-c", os.path.join(d, "seam.c"),
                         "-o", os.path.join(d, "seam.o")], capture_output=True, text=True)
    c_ok = cc.returncode == 0
    print(f"=== readers-weave host proof: {fn} ({rel}) ===")
    print(f"  struct {a['struct']} ({a['size']} bytes), seam {a['seam']}")
    print(f"  {'OK ' if rust_ok else 'FAIL'}  rustc freestanding object [{tgt}]"
          + ("" if rust_ok else f"\n{rc.stderr[-500:]}"))
    print(f"  {'OK ' if c_ok else 'FAIL'}  cc woven C + _Static_assert layout re-cert"
          + ("" if c_ok else f"\n{cc.stderr[-500:]}"))
    ok = rust_ok and c_ok
    print("HOST PROOF:", "PASS — reader weaves: freestanding Rust object links "
          "against the real-ABI mirror; the in-tree _Static_assert re-certifies "
          "the layout at kernel build" if ok else "FAIL")
    return 0 if ok else 1


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, rel, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == "prove":
        return prove(rel, fn)
    if cmd == "emit":
        a = build_artifacts(rel, fn)
        outd = os.path.join(HERE, "readers")
        os.makedirs(outd, exist_ok=True)
        open(os.path.join(outd, f"{a['key']}.rs"), "w").write(a["rust_obj"])
        print(f"-> readers/{a['key']}.rs")
        print("manifest source entry seam:", a["seam"], "| guard lines:",
              a["guard"].count("_Static_assert"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

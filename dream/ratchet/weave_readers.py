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

import json
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
#![no_main]
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

    # forward-declare the struct FIRST so the extern's `struct X *` param binds
    # to the file-scope tag, not a fresh prototype-scoped incomplete type (which
    # a struct defined locally, after the leading includes, would otherwise
    # create -> "incompatible pointer type" at the seam call, verified on
    # bitmap-str.c's local `struct region`).
    extern = f"struct {struct_c};\n{ret_c} {rs_fn}({', '.join(params_c)});\n"
    argnames = [re.match(r".*?([A-Za-z_]\w*)\s*(?:\[.*\])?$", p).group(1) for p in params_c]
    call = f"{rs_fn}({', '.join(argnames)})"
    # the layout _Static_asserts go at BLOCK SCOPE inside the woven body: only
    # the extern decl (pointer param) is fine with an incomplete struct in the
    # leading include region; sizeof/offsetof need the COMPLETE struct, which is
    # guaranteed at the function's own site (the original body dereferenced it).
    guard_block = "\n".join("\t" + g for g in guard.splitlines())
    inner = f"{call};" if ret_c == "void" else f"return {call};"
    body = f"{{\n{guard_block}\n\t{inner}\n}}"
    # object/crate name must be a valid rust crate identifier (no '.', '/')
    objkey = re.sub(r"[^0-9A-Za-z_]", "_", key)
    return {"key": objkey, "rust_obj": rust_obj, "struct": struct_c,
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


# ---------------------------------------------------------------------------
# batch: assemble a manifest for N verified readers and drive weave.py's gate
# (excise + freestanding-obj compile + link + build + boot). The in-kernel
# boot capstone for the readers class.
# ---------------------------------------------------------------------------

# verified readers whose source .o is built in the pinned minimal config
_BATCH = [
    ("kernel/resource.c", "resource_clip"),
    ("lib/bitmap-str.c", "bitmap_check_region"),
    ("lib/linear_ranges.c", "linear_range_get_value"),
    ("kernel/dma/swiotlb.c", "wrap_area_index"),
    ("kernel/bpf/log.c", "bpf_vlog_update_len_max"),
    ("kernel/locking/lockdep.c", "lock_time_inc"),
    ("kernel/locking/lockdep.c", "lock_time_add"),
]


def _orig_signature(rel, fn):
    """The stock function's signature text (up to its opening brace)."""
    import weave as _w
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    orig = _w._function_source(src, fn)
    return orig[:orig.index("{")].rstrip()


VOL = "cgir-kbuild"
IMG = "cgir-kernel-gate"


def _built_reader_pairs():
    """All verified readers whose source .o is built in the pinned config
    (weaving an uncompiled file is a no-op). One docker query for all .o."""
    solved = json.load(open(os.path.join(REPO, "dream", "firstrun", "sweep", "solved.json")))
    pairs = []
    for r in solved["recs"]:
        if r["kind"] != "reader":
            continue
        cand = os.path.join(VERIFIED,
                            f"reader_{r['file'].replace('/', '__')}_{r['sym']}.rs")
        if os.path.exists(cand):
            pairs.append((r["file"], r["sym"]))
    os_list = "\n".join(sorted({f[:-2] + ".o" for f, _ in pairs}))
    # -i so the container receives the piped list on stdin (else `read` hits EOF)
    chk = subprocess.run(
        ["docker", "run", "--rm", "-i", "-v", f"{VOL}:/build", IMG, "bash", "-c",
         "while read o; do [ -f /build/linux/$o ] && echo $o; done"],
        input=os_list, capture_output=True, text=True)
    built = {ln.strip()[:-2] + ".c" for ln in chk.stdout.splitlines() if ln.strip()}
    return [(f, fn) for f, fn in pairs if f in built]


def _reset_stock(rels):
    for rel in rels:
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{VOL}:/build",
             "-v", f"{KSRC}:/ksrc:ro", "alpine", "sh", "-c",
             f"cp /ksrc/{rel} /build/linux/{rel}"], capture_output=True)


def _objkey(rel, fn):
    return re.sub(r"[^0-9A-Za-z_]", "_", f"reader_{rel.replace('/', '__')}_{fn}")


def _unwire(pairs):
    """Remove a dropped reader's Rust-object wiring: the `obj-y += key.o` line
    in its kbuild Makefile plus the key.o/.o_shipped. Otherwise a dropped
    file's orphan object still links (and its panic handler collides at the
    vmlinux link — verified)."""
    for rel, fn in pairs:
        d, key = os.path.dirname(rel), _objkey(rel, fn)
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
             f"cd /build/linux/{d} && sed -i '/obj-y += {key}.o/d' Makefile 2>/dev/null; "
             f"rm -f {key}.o {key}.o_shipped {key}.c"], capture_output=True)


def _assemble(pairs, W):
    import json as _json
    outd = os.path.join(HERE, "readers")
    os.makedirs(outd, exist_ok=True)
    sources, rust_objects, skipped = {}, {}, []
    for rel, fn in pairs:
        try:
            a = build_artifacts(rel, fn)
        except Exception as e:
            skipped.append((rel, fn, f"gen: {str(e)[:50]}"))
            continue
        open(os.path.join(outd, f"{a['key']}.rs"), "w").write(a["rust_obj"])
        sig = _orig_signature(rel, fn)
        src_entry = sources.setdefault(rel, {"extern_block": "\n", "functions": {}})
        src_entry["extern_block"] += a["extern"]
        src_entry["functions"][fn] = {
            "status": "rust", "tier": "tier-b", "gate": "differential",
            "verdict": "PASS", "seam": a["seam"], "shell": f"{sig}\n{a['seam_body']}"}
        rust_objects[a["key"]] = {"src": f"readers/{a['key']}.rs",
                                  "kbuild_dir": os.path.dirname(rel), "obj": a["key"]}
    manifest = {"config": "pinned minimal arm64", "generated_by": "weave_readers.batch",
                "config_enable": [], "sources": sources,
                "rust_objects": rust_objects, "verified_not_woven": []}
    mpath = os.path.join(outd, "batch_manifest.json")
    _json.dump(manifest, open(mpath, "w"), indent=1)
    W.MANIFEST = mpath
    return manifest, skipped


def _compile_check(rels):
    """make each woven .o individually; return the set that FAIL (so the full
    build isn't sunk by one bad reader — the _Static_assert / seam-type errors
    surface per-file and fast)."""
    targets = " ".join(f"{rel[:-2]}.o" for rel in rels)
    failed = set()
    for rel in rels:
        r = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
             f"cd /build/linux && rm -f {rel[:-2]}.o && make -s {rel[:-2]}.o 2>&1 | tail -3"],
            capture_output=True, text=True)
        if "Error" in r.stdout or "error:" in r.stdout:
            failed.add(rel)
            print(f"  ✗ {rel}: {r.stdout.strip().splitlines()[0][:90] if r.stdout.strip() else 'compile failed'}")
    return failed


def batch(pairs=None):
    sys.path.insert(0, HERE)
    import weave as W
    pairs = pairs or _built_reader_pairs()
    all_rels = sorted({rel for rel, _ in pairs})
    print(f"=== readers boot batch: {len(pairs)} verified readers in "
          f"{len(all_rels)} built source files ===")
    _reset_stock(all_rels)                          # clean base: stock .c ...
    _unwire(pairs)                                   # ... and no stale obj wiring
    _, skipped = _assemble(pairs, W)
    for rel, fn, why in skipped:
        print(f"  skip {fn} ({rel}): {why}")
    if W.cmd_apply() != 0:
        return 1
    # robustness: per-file compile check, drop failures, re-assemble survivors
    print("compile-checking each woven file...")
    failed = _compile_check(all_rels)
    if failed:
        dropped = [(rel, fn) for rel, fn in pairs if rel in failed]
        print(f"  dropping {len(failed)} file(s) that fail to compile; "
              f"reverting to stock + unwiring their objects, re-weaving survivors")
        _reset_stock(sorted(failed))
        _unwire(dropped)                                      # remove orphan objs
        survivors = [(rel, fn) for rel, fn in pairs if rel not in failed]
        _reset_stock(sorted({rel for rel, _ in survivors}))   # clean re-weave
        _assemble(survivors, W)
        if W.cmd_apply() != 0:
            return 1
    else:
        survivors = pairs
    # link-repair loop: a reader doing integer division emits references to
    # core::panicking::panic_const::* which only libcore resolves — undefined at
    # the vmlinux link (freestanding objects have no libcore). This surfaces only
    # at LINK, so the per-file compile-check can't catch it. Build; on an
    # undefined-reference to a reader object, drop it and rebuild (bounded).
    keymap = {_objkey(rel, fn): (rel, fn) for rel, fn in pairs}
    for _ in range(4):
        r = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
             "cd /build/linux && make -s -j$(nproc) Image 2>&1 | tail -60 && "
             "test -f arch/arm64/boot/Image && echo __BUILT__"],
            capture_output=True, text=True)
        if "__BUILT__" in r.stdout:
            break
        # match FULL keys against the link output (the '__' path separators broke
        # a regex; substring test on each known key is robust)
        bad = {keymap[k] for k in keymap if k in r.stdout}
        if not bad:
            print("  ✗ build failed (not an isolable reader link error):")
            print("   " + "\n   ".join(r.stdout.strip().splitlines()[-6:]))
            return 1
        print(f"  link-drop {len(bad)} reader(s) with undefined core refs "
              f"(division/overflow panic paths): {', '.join(fn for _, fn in bad)}")
        _reset_stock(sorted({rel for rel, _ in bad}))
        _unwire(sorted(bad))
        survivors = [(rel, fn) for rel, fn in survivors if (rel, fn) not in bad]
        _reset_stock(sorted({rel for rel, _ in survivors}))
        _assemble(survivors, W)
        if W.cmd_apply() != 0:
            return 1
    # HONEST metric: a woven+compiled reader is only really in the kernel if its
    # seam symbol survives into vmlinux. A leaf driver enabled =y still may not
    # land in built-in.a (parent-Kbuild descent / dead-driver elimination), so
    # "source-woven" over-counts. Report vmlinux-present as the headline.
    seams = {_objkey(rel, fn): fn for rel, fn in survivors}
    nm = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
         "cd /build/linux && nm vmlinux 2>/dev/null"], capture_output=True, text=True)
    present = {fn for rel, fn in survivors
              if f" {fn}_rs\n" in nm.stdout or f" {fn}_rs" in nm.stdout}
    n_src = len(survivors)
    print(f"BUILT: {n_src} readers source-woven; {len(present)} present in vmlinux "
          f"(the rest compiled but not linked into this config's vmlinux)")
    if present != {fn for _, fn in survivors}:
        gone = sorted({fn for _, fn in survivors} - present)
        print(f"  not-linked ({len(gone)}): {', '.join(gone)}")
    return W._boot_digest()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return batch()
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

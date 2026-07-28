#!/usr/bin/env python3
"""The struct-mirror library — generate a verified #[repr(C)] Rust mirror for a
kernel C struct, so the Tier-B middle (~48%, functions that read struct fields)
becomes transplantable. Ring 8 hand-wrote one mirror; this generates them and
proves each ABI-correct TWO independent ways:

  * Rust `const _: () = assert!(size_of/offset_of == EXPECTED)` — rustc's actual
    layout must match the generator's computed layout;
  * kernel `BUILD_BUG_ON(sizeof/offsetof == EXPECTED)` — the REAL kernel's layout
    (against real headers, at kernel build) must match it too.

Build passes iff rustc-layout == generator-model == kernel-layout, so a wrong
mirror (or a config that shifts a field) fails to build — the BUILD_BUG_ON the
research called load-bearing, now automatic. Conservative: anything it can't lay
out soundly (bitfields, unions, struct-by-value, #ifdef fields) is REFUSED, not
guessed.
"""
from __future__ import annotations

import re
import sys

# LP64 (arm64/x86-64) scalar sizes/aligns; (rust type, size, align)
SCALAR = {
    "char": ("i8", 1, 1), "signed char": ("i8", 1, 1), "unsigned char": ("u8", 1, 1),
    "u8": ("u8", 1, 1), "s8": ("i8", 1, 1), "__u8": ("u8", 1, 1), "__s8": ("i8", 1, 1), "bool": ("bool", 1, 1),
    "short": ("i16", 2, 2), "unsigned short": ("u16", 2, 2), "u16": ("u16", 2, 2), "s16": ("i16", 2, 2),
    "__u16": ("u16", 2, 2), "__s16": ("i16", 2, 2), "__le16": ("u16", 2, 2), "__be16": ("u16", 2, 2),
    "int": ("i32", 4, 4), "unsigned int": ("u32", 4, 4), "unsigned": ("u32", 4, 4),
    "u32": ("u32", 4, 4), "s32": ("i32", 4, 4), "__u32": ("u32", 4, 4), "__s32": ("i32", 4, 4),
    "__le32": ("u32", 4, 4), "__be32": ("u32", 4, 4),
    "long": ("i64", 8, 8), "unsigned long": ("u64", 8, 8), "long long": ("i64", 8, 8),
    "unsigned long long": ("u64", 8, 8), "u64": ("u64", 8, 8), "s64": ("i64", 8, 8),
    "__u64": ("u64", 8, 8), "__s64": ("i64", 8, 8), "__le64": ("u64", 8, 8), "__be64": ("u64", 8, 8),
    "size_t": ("usize", 8, 8), "ssize_t": ("isize", 8, 8), "phys_addr_t": ("u64", 8, 8),
    "dma_addr_t": ("u64", 8, 8), "resource_size_t": ("u64", 8, 8),
}
PTR = ("*mut core::ffi::c_void", 8, 8)


class Unsupported(Exception):
    pass


def norm(t):
    return re.sub(r"\s+", " ", t.replace("const", "").replace("volatile", "").strip())


def parse_struct(src, name):
    # closing `}` may carry trailing attributes before `;`
    # (e.g. `} ____cacheline_internodealigned_in_smp;`, `} __packed;`)
    m = re.search(rf"\bstruct\s+{re.escape(name)}\s*\{{(.*?)\n\}}[ \t\w()]*;", src, re.DOTALL)
    if not m:
        raise Unsupported(f"struct {name} not found")
    body = m.group(1)
    if "#if" in body or "#ifdef" in body or "#endif" in body:
        raise Unsupported("config-dependent (#if) fields — layout not fixed")
    if re.search(r"\bunion\b", body):
        raise Unsupported("contains a union — repr(C) union needs manual review")
    fields = []
    for raw in body.split(";"):
        decl = norm(re.sub(r"/\*.*?\*/", " ", raw, flags=re.DOTALL))
        if not decl:
            continue
        if ":" in decl:
            raise Unsupported(f"bitfield ({decl!r}) — Rust repr(C) has no bitfields")
        # function pointer:  ret (*name)(args)
        fp = re.match(r".+\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", decl)
        if fp:
            fields.append(("__ptr__", fp.group(1), None))
            continue
        # plain pointer:  type *name
        pm = re.match(r"(.+?)\*\s*([A-Za-z_]\w*)$", decl)
        if pm:
            fields.append(("__ptr__", pm.group(2), None))
            continue
        # array:  type name[N]
        am = re.match(r"(.+?\b)([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$", decl)
        if am:
            fields.append((norm(am.group(1)), am.group(2), int(am.group(3))))
            continue
        # scalar:  type name
        sm = re.match(r"(.+?\b)([A-Za-z_]\w*)$", decl)
        if not sm:
            raise Unsupported(f"unparsable field {decl!r}")
        fields.append((norm(sm.group(1)), sm.group(2), None))
    return fields


def layout(fields):
    """(rows, size) with rows = [(rustty, name, offset)]; LP64 packing rules."""
    off, align, rows = 0, 1, []
    for ctype, fname, arr in fields:
        if ctype == "__ptr__":
            rty, sz, al = PTR
        elif ctype in SCALAR:
            rty, sz, al = SCALAR[ctype]
        else:
            raise Unsupported(f"field {fname}: type {ctype!r} not a scalar/pointer "
                              f"(nested struct-by-value needs its own mirror)")
        if arr is not None:
            rty, sz = f"[{rty}; {arr}]", sz * arr
        off = (off + al - 1) // al * al   # pad to field alignment
        rows.append((rty, fname, off))
        off += sz
        align = max(align, al)
    size = (off + align - 1) // align * align
    return rows, size


def emit_rust(name, rows, size):
    rty = "".join(w.capitalize() for w in name.split("_"))
    lines = [f"#[repr(C)]", f"pub struct {rty} {{"]
    for r, fn, _ in rows:
        lines.append(f"    pub {fn}: {r},")
    lines.append("}")
    lines.append(f"const _: () = assert!(core::mem::size_of::<{rty}>() == {size});")
    for r, fn, off in rows:
        lines.append(f"const _: () = assert!(core::mem::offset_of!({rty}, {fn}) == {off});")
    return "\n".join(lines), rty


def emit_c_guard(name, rows, size):
    lines = [f"\tBUILD_BUG_ON(sizeof(struct {name}) != {size});"]
    for _, fn, off in rows:
        lines.append(f"\tBUILD_BUG_ON(offsetof(struct {name}, {fn}) != {off});")
    return "\n".join(lines)


def mirror(src, name):
    fields = parse_struct(src, name)
    rows, size = layout(fields)
    rust, rty = emit_rust(name, rows, size)
    guard = emit_c_guard(name, rows, size)
    return {"name": name, "rust_type": rty, "size": size, "rust": rust, "c_guard": guard,
            "fields": [(r, f, o) for r, f, o in rows]}


if __name__ == "__main__":
    import json
    src = sys.stdin.read()
    name = sys.argv[1]
    try:
        print(json.dumps(mirror(src, name)))
    except Unsupported as e:
        print(json.dumps({"name": name, "refused": str(e)}))

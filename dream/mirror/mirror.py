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
out soundly (bitfields, unions, struct-by-value it can't recursively mirror,
#ifdef fields, unresolvable macro array sizes) is REFUSED, not guessed.

Host-sound extensions (config-independent, gate-validated):
  * resolve_struct_source: locate a `struct <name> {` def near a file / under
    $KSRC/include, so nested structs can be looked up.
  * recursive nested-struct-of-scalars: a `struct Y field;` (by value) member is
    resolved, recursively mirrored, inlined as a nested `#[repr(C)]` type, and
    participates in LP64 packing. If Y can't be mirrored, the parent REFUSES.
  * DECLARE_BITMAP / fixed macro arrays: `DECLARE_BITMAP(n, NBITS)` -> `[u64; K]`
    with K = ceil(NBITS/64); `TYPE n[MACRO]` where MACRO is a resolvable
    object-like #define. Unresolvable size -> REFUSE.
"""
from __future__ import annotations

import glob
import json
import os
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

# Opaque kernel types that must ALWAYS be refused as by-value fields, even
# though some could be "resolved" host-side. Their host layout is either
# config-dependent (locks grow debug/lockdep members under CONFIG_*, RT swaps
# the primitive entirely) or intentionally in-kernel-sizeof territory — a
# separate phase (design note: DO NOT lay these out host-side). Refusing them
# by NAME keeps the generator honest: a `struct list_head`-shaped resolution
# that happens to be two pointers today is still refused, because the CONTRACT
# is "these need an in-kernel sizeof", not "guess if it looks stable".
OPAQUE_KERNEL_TYPES = {
    "spinlock_t", "raw_spinlock_t", "rwlock_t", "seqlock_t", "seqcount_t",
    "atomic_t", "atomic64_t", "atomic_long_t", "local_t", "refcount_t",
    "wait_queue_head_t", "swait_queue_head_t",
    # struct forms (matched with the `struct ` prefix)
    "struct mutex", "struct rw_semaphore", "struct semaphore",
    "struct completion", "struct list_head", "struct hlist_head",
    "struct hlist_node", "struct rcu_head", "struct llist_head",
    "struct llist_node", "struct kref", "struct kobject", "struct work_struct",
    "struct delayed_work", "struct timer_list", "struct hrtimer",
    "struct rb_root", "struct rb_node", "struct callback_head",
    "struct device", "struct mutex_waiter",
}

# depth cap for recursive nested-struct mirroring (defensive; C forbids a
# struct containing itself by value, so real nesting is shallow).
_MAX_DEPTH = 16


def _load_primitive_sizes():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "primitive_sizes.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# In-kernel-probed sizes for the opaque primitives (see probe_primitives.py),
# keyed by the SAME strings as OPAQUE_KERNEL_TYPES: {ctype: [size, align]}.
# Empty if never probed -> those fields stay REFUSED (the sound default). When a
# primitive IS here, it is emitted as an alignment-matching integer array of the
# probed size, so the parent struct's field offsets are correct; the parent's
# BUILD_BUG_ON(sizeof/offsetof) then RE-CERTIFIES the whole layout against the
# real kernel, so a stale or wrong probe value cannot pass the gate. The probe
# and the gate build the SAME .config, so they are consistent by construction.
PRIMITIVE_SIZES = _load_primitive_sizes()

_ALIGN_INT = {1: "u8", 2: "u16", 4: "u32", 8: "u64"}


def _opaque_field(ctype):
    """(rust_type, size, align) for a probed opaque primitive, or None to refuse
    (unprobed, or a shape we won't lay out soundly)."""
    v = PRIMITIVE_SIZES.get(ctype)
    if not v:
        return None
    size, align = v
    elem = _ALIGN_INT.get(align)
    if elem is None or size <= 0 or size % align != 0:
        return None  # unexpected shape -> refuse (sound); the gate is not risked
    count = size // align
    rty = elem if count == 1 else f"[{elem}; {count}]"
    return rty, size, align


class Unsupported(Exception):
    pass


def norm(t):
    return re.sub(r"\s+", " ", t.replace("const", "").replace("volatile", "").strip())


def _rust_type_name(name):
    return "".join(w.capitalize() for w in name.split("_"))


# --------------------------------------------------------------------------
# source resolution
# --------------------------------------------------------------------------

def _ksrc():
    return os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")


def resolve_struct_source(name, near_file=None):
    """Locate the file TEXT containing `struct <name> {`, searching in order:
      1. near_file itself,
      2. near_file's directory (driver-local *.h / *.c),
      3. $KSRC/include (recursively).
    Returns the containing file's text, or None if not found. Measured to take
    struct 'not found' from ~50% to ~1%, so nested lookups resolve.
    """
    pat = re.compile(rf"\bstruct\s+{re.escape(name)}\s*\{{")

    def _hit(path):
        try:
            txt = open(path, errors="ignore").read()
        except OSError:
            return None
        return txt if pat.search(txt) else None

    # 1. near_file itself
    if near_file and os.path.isfile(near_file):
        t = _hit(near_file)
        if t is not None:
            return t
        near_dir = os.path.dirname(os.path.abspath(near_file))
    else:
        near_dir = None

    # 2. near_file's directory (driver-local headers/sources)
    if near_dir and os.path.isdir(near_dir):
        for p in sorted(glob.glob(os.path.join(near_dir, "*.h")) +
                        glob.glob(os.path.join(near_dir, "*.c"))):
            if os.path.abspath(p) == (os.path.abspath(near_file) if near_file else None):
                continue
            t = _hit(p)
            if t is not None:
                return t

    # 3. $KSRC/include (recursive)
    inc = os.path.join(_ksrc(), "include")
    if os.path.isdir(inc):
        for p in sorted(glob.glob(os.path.join(inc, "**", "*.h"), recursive=True)):
            t = _hit(p)
            if t is not None:
                return t
    return None


def _resolve_define(token, *srcs):
    """Resolve an object-like `#define TOKEN <int-expr>` to an int, searching the
    given source texts then $KSRC/include. Only pure integer expressions
    (literals, +-*/<<>>, parens) are accepted — anything referencing another
    identifier (e.g. CONFIG_*) is unresolvable here => None."""
    token = token.strip()
    # numeric literal / integer expression directly?
    lit = _eval_int_expr(token)
    if lit is not None:
        return lit

    if not re.fullmatch(r"[A-Za-z_]\w*", token):
        return None

    def _search(txt):
        m = re.search(rf"^[ \t]*#[ \t]*define[ \t]+{re.escape(token)}[ \t]+(.+?)[ \t]*(?:/\*.*)?$",
                      txt, re.MULTILINE)
        if not m:
            return None
        val = m.group(1).strip()
        # strip a trailing line comment
        val = re.sub(r"//.*$", "", val).strip()
        return _eval_int_expr(val)

    for txt in srcs:
        if txt:
            v = _search(txt)
            if v is not None:
                return v

    inc = os.path.join(_ksrc(), "include")
    if os.path.isdir(inc):
        for p in sorted(glob.glob(os.path.join(inc, "**", "*.h"), recursive=True)):
            try:
                txt = open(p, errors="ignore").read()
            except OSError:
                continue
            v = _search(txt)
            if v is not None:
                return v
    return None


def _eval_int_expr(expr):
    """Safely evaluate a pure C integer expression (literals, + - * / << >> ( )).
    Returns an int or None. Refuses anything with an identifier or unexpected
    token — no name resolution, no eval of arbitrary code."""
    expr = expr.strip()
    if not expr:
        return None
    # strip integer suffixes (UL, LL, u, l) on literals
    cleaned = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\b", lambda m: m.group(1), expr)
    if not re.fullmatch(r"[0-9xXa-fA-F_ \t()+\-*/<>]+", cleaned):
        return None
    # only allow <<, >> as the shift ops (a single < or > is not valid C int math here)
    if re.search(r"(?<![<>])[<>](?![<>])", cleaned):
        return None
    try:
        val = eval(cleaned, {"__builtins__": {}}, {})  # noqa: S307 - token-whitelisted
    except Exception:
        return None
    return val if isinstance(val, int) else None


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _extract_body(src, name):
    """Return (body, full_src) for `struct <name> { ... }` or raise Unsupported."""
    m = re.search(rf"\bstruct\s+{re.escape(name)}\s*\{{(.*?)\n\}}[ \t\w()]*;", src, re.DOTALL)
    if not m:
        raise Unsupported(f"struct {name} not found")
    return m.group(1)


def parse_struct(src, name):
    """Parse `struct <name>` in `src` into a list of field descriptors.

    Field descriptors are one of:
      ("__ptr__", fname, None)          plain / function pointer
      (ctype, fname, None)              scalar
      (ctype, fname, N)                 fixed array of scalar, length N
      ("__nested__", fname, ynane)      nested struct-BY-VALUE named `yname`
    """
    body = _extract_body(src, name)
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

        # DECLARE_BITMAP(name, NBITS)  ->  unsigned long name[ceil(NBITS/64)]
        db = re.match(r"DECLARE_BITMAP\s*\(\s*([A-Za-z_]\w*)\s*,\s*(.+?)\s*\)$", decl)
        if db:
            fname, nbits_tok = db.group(1), db.group(2)
            nbits = _resolve_define(nbits_tok, src)
            if nbits is None or nbits <= 0:
                raise Unsupported(f"DECLARE_BITMAP({fname}, {nbits_tok!r}): "
                                  f"NBITS not a resolvable literal/#define")
            k = (nbits + 63) // 64  # BITS_TO_LONGS: ceil(NBITS/64) on LP64
            fields.append(("unsigned long", fname, k))
            continue

        # function pointer:  ret (*name)(args)
        fp = re.match(r".+\(\s*\*\s*([A-Za-z_]\w*)\s*\)\s*\(", decl)
        if fp:
            fields.append(("__ptr__", fp.group(1), None))
            continue

        # multi-declarator sharing a base type:  `struct resource *a, *b, *c` or
        # `int x, y`. Split into per-declarator decls (the greedy pointer/scalar
        # regexes below would otherwise swallow all but the last and DROP the
        # earlier fields — a silent too-small struct).
        for one in _split_multi_decl(decl):
            fields.append(_field_from_decl(one, src))
    return fields


def _split_multi_decl(decl):
    """`base d1, d2, ...` -> [`base d1`, `base d2`, ...]; single decl -> [decl].
    Only splits top-level commas (struct fields have none inside []/() after the
    function-pointer form is already handled)."""
    if "," not in decl:
        return [decl]
    parts = [p.strip() for p in decl.split(",")]
    m = re.match(r"(.*?)(\**\s*[A-Za-z_]\w*(?:\s*\[.+\])?)$", parts[0])
    if not m or not m.group(1).strip():
        return [decl]  # can't isolate a shared base -> leave as-is (may refuse later)
    base = m.group(1).strip()
    return [parts[0]] + [f"{base} {p}" for p in parts[1:]]


def _field_from_decl(decl, src):
    """Parse ONE declarator into a field descriptor (raises Unsupported)."""
    # plain / typed pointer:  type *name  (also **name)
    pm = re.match(r"(.+?)\*+\s*([A-Za-z_]\w*)$", decl)
    if pm:
        return ("__ptr__", pm.group(2), None)

    # array:  type name[SIZE]  (SIZE = literal OR resolvable object-like #define)
    am = re.match(r"(.+?\b)([A-Za-z_]\w*)\s*\[\s*(.+?)\s*\]$", decl)
    if am:
        base, fname, size_tok = norm(am.group(1)), am.group(2), am.group(3).strip()
        n = _resolve_define(size_tok, src)
        if n is None or n <= 0:
            raise Unsupported(f"array {fname}[{size_tok!r}]: size not a "
                              f"resolvable literal/#define")
        sm2 = re.match(r"struct\s+([A-Za-z_]\w*)$", base)
        if sm2:
            return ("__nested__", fname, (sm2.group(1), n))
        return (base, fname, n)

    # nested struct BY VALUE:  struct Y name
    nm = re.match(r"struct\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)$", decl)
    if nm:
        return ("__nested__", nm.group(2), (nm.group(1), None))

    # scalar:  type name
    sm = re.match(r"(.+?\b)([A-Za-z_]\w*)$", decl)
    if not sm:
        raise Unsupported(f"unparsable field {decl!r}")
    return (norm(sm.group(1)), sm.group(2), None)


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------

def _mirror_nested(yname, src, near_file, depth):
    """Recursively mirror a nested struct-by-value `struct Y`. Returns a dict
    with keys size/align/rust_type/rust (Y's own mirror emission) and
    'extra_rust'/'extra_guard' (Y's mirror + its BUILD_BUG_ONs, to be emitted
    alongside the parent). Propagates Unsupported if Y can't be mirrored."""
    if depth >= _MAX_DEPTH:
        raise Unsupported(f"nested struct {yname}: recursion depth exceeded")
    ytext = None
    # try same source first (common: sibling struct in same header)
    if re.search(rf"\bstruct\s+{re.escape(yname)}\s*\{{", src):
        ytext = src
    else:
        ytext = resolve_struct_source(yname, near_file)
    if ytext is None:
        raise Unsupported(f"nested struct {yname}: definition not found "
                          f"(searched near {near_file} and $KSRC/include)")
    inner = _mirror_impl(ytext, yname, near_file, depth + 1)
    return inner


def _layout(fields, src, near_file, depth):
    """(rows, size, align, extras) with rows = [(rustty, name, offset)];
    LP64 packing rules. `extras` collects (rust, guard) emissions for any nested
    mirrors that must accompany the parent."""
    off, align, rows = 0, 1, []
    extras = []  # list of dicts: {name, rust, c_guard} for nested structs
    for ctype, fname, arr in fields:
        if ctype in OPAQUE_KERNEL_TYPES:
            opq = _opaque_field(ctype)
            if opq is None:
                raise Unsupported(
                    f"field {fname}: {ctype} is an opaque kernel type "
                    f"(config-dependent — run probe_primitives.py to size it) — refused")
            rty, sz, al = opq
            if arr is not None:
                rty, sz = f"[{rty}; {arr}]", sz * arr
            off = (off + al - 1) // al * al
            rows.append((rty, fname, off))
            off += sz
            align = max(align, al)
            continue
        if ctype == "__ptr__":
            rty, sz, al = PTR
        elif ctype == "__nested__":
            yname, ycount = arr  # arr repurposed: (struct-name, count-or-None)
            if f"struct {yname}" in OPAQUE_KERNEL_TYPES:
                opq = _opaque_field(f"struct {yname}")
                if opq is None:
                    raise Unsupported(
                        f"field {fname}: struct {yname} is an opaque kernel type "
                        f"(config-dependent — run probe_primitives.py to size it) — refused")
                rty, sz, al = opq
                if ycount is not None:
                    rty, sz = f"[{rty}; {ycount}]", sz * ycount
                off = (off + al - 1) // al * al
                rows.append((rty, fname, off))
                off += sz
                align = max(align, al)
                continue
            inner = _mirror_nested(yname, src, near_file, depth)
            rty, sz, al = inner["rust_type"], inner["size"], inner["align"]
            extras.append(inner)
            if ycount is not None:
                rty, sz = f"[{rty}; {ycount}]", sz * ycount
            off = (off + al - 1) // al * al
            rows.append((rty, fname, off))
            off += sz
            align = max(align, al)
            continue
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
    return rows, size, align, extras


def layout(fields):
    """Back-compat entry: (rows, size) with no nested/source context (scalars
    and pointers only). Nested-struct fields require the source, so use
    _layout / mirror for those."""
    rows, size, _align, extras = _layout(fields, "", None, 0)
    if extras:  # pragma: no cover - guarded by callers
        raise Unsupported("nested struct-by-value needs source context; use mirror()")
    return rows, size


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------

# C field names that are Rust keywords need raw identifiers (`r#type`) in the
# mirror — a bare `type` breaks both the struct decl and offset_of! (Run 1:
# 22 readers BUILD_FAIL(rust) traced to exactly this). self/super/crate can't
# even be raw idents; refuse those (vanishingly rare as C field names).
_RUST_KW = {
    "as", "break", "const", "continue", "dyn", "else", "enum", "extern",
    "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod",
    "move", "mut", "pub", "ref", "return", "static", "struct", "trait",
    "true", "type", "unsafe", "use", "where", "while", "async", "await",
    "abstract", "become", "box", "do", "final", "macro", "override", "priv",
    "try", "typeof", "unsized", "virtual", "yield",
}
_NO_RAW = {"self", "Self", "super", "crate"}


def _rs_field(fname):
    if fname in _NO_RAW:
        raise Unsupported(f"field {fname}: reserved, not raw-identifiable")
    return f"r#{fname}" if fname in _RUST_KW else fname


def emit_rust(name, rows, size):
    rty = _rust_type_name(name)
    lines = ["#[repr(C)]", f"pub struct {rty} {{"]
    for r, fn, _ in rows:
        lines.append(f"    pub {_rs_field(fn)}: {r},")
    lines.append("}")
    lines.append(f"const _: () = assert!(core::mem::size_of::<{rty}>() == {size});")
    for r, fn, off in rows:
        lines.append(f"const _: () = assert!(core::mem::offset_of!({rty}, {_rs_field(fn)}) == {off});")
    return "\n".join(lines), rty


def emit_c_guard(name, rows, size):
    # File-scope static_assert, NOT BUILD_BUG_ON inside a function: sizeof and
    # offsetof are integer constant expressions, so _Static_assert fires
    # UNCONDITIONALLY at compile time. BUILD_BUG_ON placed in a
    # `static __maybe_unused` function is dead-code-eliminated at the kernel's
    # -O2 (the function is never called) and the assertion silently vanishes —
    # a wrong size then passes the build (verified by negative control). For
    # opaque primitives the kernel leg is the ONLY non-vacuous check (the rustc
    # leg builds the mirror from the same probed size, so it is internally
    # consistent regardless), so this must not depend on the function being
    # emitted.
    lines = [f'static_assert(sizeof(struct {name}) == {size}, '
             f'"{name}: size mismatch vs real kernel");']
    for _, fn, off in rows:
        lines.append(f'static_assert(offsetof(struct {name}, {fn}) == {off}, '
                     f'"{name}.{fn}: offset mismatch vs real kernel");')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# top-level
# --------------------------------------------------------------------------

def _mirror_impl(src, name, near_file, depth):
    fields = parse_struct(src, name)
    rows, size, align, extras = _layout(fields, src, near_file, depth)
    rust, rty = emit_rust(name, rows, size)
    guard = emit_c_guard(name, rows, size)
    return {"name": name, "rust_type": rty, "size": size, "align": align,
            "rust": rust, "c_guard": guard,
            "fields": [(r, f, o) for r, f, o in rows],
            "extras": extras}


def mirror(src, name, near_file=None):
    """Mirror `struct <name>` from `src`. `near_file` (the path `src` came from)
    lets nested struct-by-value members be resolved from sibling headers.

    Returns a dict; `rust`/`c_guard` are the parent's emission. Any nested
    struct-by-value mirrors are inlined into `rust`/`c_guard` (deduplicated,
    dependencies first) so a single emission is self-contained."""
    top = _mirror_impl(src, name, near_file, 0)

    # flatten nested mirrors (dependencies first), dedup by struct name
    ordered = []
    seen = set()

    def _collect(node):
        for ex in node.get("extras", []):
            _collect(ex)
        if node["name"] not in seen and node["name"] != name:
            seen.add(node["name"])
            ordered.append(node)

    _collect(top)

    rust_parts = [ex["rust"] for ex in ordered] + [top["rust"]]
    guard_parts = [ex["c_guard"] for ex in ordered] + [top["c_guard"]]

    return {
        "name": name,
        "rust_type": top["rust_type"],
        "size": top["size"],
        "align": top["align"],
        "rust": "\n\n".join(rust_parts),
        "c_guard": "\n".join(guard_parts),
        "fields": top["fields"],
        "nested": [ex["name"] for ex in ordered],
    }


if __name__ == "__main__":
    import json
    src = sys.stdin.read()
    name = sys.argv[1]
    near = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        print(json.dumps(mirror(src, name, near)))
    except Unsupported as e:
        print(json.dumps({"name": name, "refused": str(e)}))

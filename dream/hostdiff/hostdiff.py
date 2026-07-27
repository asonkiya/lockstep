#!/usr/bin/env python3
"""hostdiff — the T0 boot-free oracle from COSTDOWN §1.

Verify a Rust transplant of a PURE kernel function against the REAL kernel C on
the host, no kernel build, no QEMU, no boot: compile the actual kernel TU with a
shim (`kshim.h`), compile the candidate `.rs` as a staticlib, auto-generate a
differential probe from the C signature, run millions of cases in well under a
second. ~900x cheaper per verification than a boot-cycle (0.28 s vs 247 s,
measured) with the SAME bit-exact oracle. This is what makes retries free — and
therefore what makes free synthesizers (c2rust / local models) viable.

Soundness scope: T0 = functions the purity router already classes as pure
(args+locals only, no state/effects). The host is arm64 LP64, the SAME widths
and ABI class as the arm64 kernel target, so arithmetic/bit semantics carry.
Anything impure routes to the trace oracle / boot tiers, never here.

Usage:
  hostdiff.py FILE.c FUNC --cand CAND.rs [--deps other.c ...] [--rand N]
  (paths relative to the kernel tree, $KSRC or the default checkout)

Exit 0 = MATCH (bit-identical over the whole probe), 1 = DIVERGE/error.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KSRC_DEFAULT = "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"
sys.path.insert(0, os.path.join(HERE, "..", "cluster"))
import cluster  # noqa: E402  (functions() parser, reused)

# C scalar type -> (host C type for the probe, unsigned?, bits)
CTYPES = {
    "unsigned long": ("unsigned long", True, 64),
    "unsigned long long": ("unsigned long long", True, 64),
    "long": ("long", False, 64),
    "long long": ("long long", False, 64),
    "u64": ("uint64_t", True, 64), "__u64": ("uint64_t", True, 64), "s64": ("int64_t", False, 64),
    "unsigned int": ("uint32_t", True, 32), "unsigned": ("uint32_t", True, 32),
    "u32": ("uint32_t", True, 32), "__u32": ("uint32_t", True, 32),
    "int": ("int32_t", False, 32), "s32": ("int32_t", False, 32),
    "u16": ("uint16_t", True, 16), "s16": ("int16_t", False, 16),
    "u8": ("uint8_t", True, 8), "s8": ("int8_t", False, 8),
    "bool": ("uint8_t", True, 1),
    "size_t": ("size_t", True, 64), "ssize_t": ("ssize_t", False, 64),
}

EDGES64 = "0,1,2,3,4,6,7,8,15,16,17,31,32,63,64,255,256,4095,65535,65536,0xFFFFFFFFull,0x100000000ull,0x7FFFFFFFFFFFFFFFull,0xFFFFFFFFFFFFFFFEull,0xFFFFFFFFFFFFFFFFull"
EDGES32 = "0,1,2,3,4,7,8,15,16,17,31,32,63,64,255,256,4095,65535,65536,0x7FFFFFFF,0xFFFFFFFE,0xFFFFFFFF"


def norm_type(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("const", "").strip())


def parse_sig(src: str, func: str) -> tuple[str, list[tuple[str, str]]]:
    """(return ctype, [(ctype, name), ...]) for FUNC, from the real TU text."""
    fns = cluster.functions(src)
    if func not in fns:
        raise SystemExit(f"hostdiff: {func} not defined in TU")
    f = fns[func]
    ret = norm_type(f["ret"].replace("static", "").replace("inline", ""))
    params: list[tuple[str, str]] = []
    p = f["params"].strip()
    if p and p != "void":
        for piece in p.split(","):
            ids = re.findall(r"[A-Za-z_]\w*", piece)
            name = ids[-1]
            ty = norm_type(piece[: piece.rfind(name)])
            params.append((ty, name))
    for ty, name in [(ret, "<ret>")] + params:
        if ty not in CTYPES:
            raise SystemExit(f"hostdiff: type {ty!r} ({name}) not scalar-mappable — not a T0 function")
    return ret, params


def shim_tu(path: str, ksrc: str, protos: str = "") -> str:
    """Real kernel TU -> host TU: strip kernel includes, inject kshim + dep protos."""
    src = open(os.path.join(ksrc, path)).read()
    src = re.sub(r'#include\s+[<"](?:linux|asm|vdso|uapi)/[^>"]+[>"][^\n]*\n', "", src)
    return f'#include "kshim.h"\n{protos}\n' + src


def extern_protos(src: str) -> str:
    """Prototypes for every non-static function a dep TU defines."""
    out = []
    for name, f in cluster.functions(src).items():
        if not f["static"]:
            out.append(f"{f['ret']} {name}({f['params']});")
    return "\n".join(out)


def rust_export(cand_path: str) -> str:
    """The #[no_mangle] exported symbol in the candidate."""
    src = open(cand_path).read()
    m = re.search(r"#\[no_mangle\]\s*pub\s+extern\s+\"C\"\s+fn\s+(\w+)", src)
    if not m:
        raise SystemExit("hostdiff: candidate exports no #[no_mangle] pub extern \"C\" fn")
    return m.group(1)


def gen_probe(func: str, rust_sym: str, ret: str, params: list[tuple[str, str]], nrand: int) -> str:
    """Differential probe: edge cross-product + xorshift random tuples."""
    rc, _, _ = CTYPES[ret]
    decls, edges, dims = [], [], []
    for i, (ty, _n) in enumerate(params):
        pc, _, bits = CTYPES[ty]
        decls.append(pc)
        edges.append(EDGES64 if bits >= 64 else EDGES32)
        dims.append(f"E{i}")
    argl = ", ".join(f"{decls[i]} a{i}" for i in range(len(params))) or "void"
    argn = ", ".join(f"a{i}" for i in range(len(params)))
    lines = [
        '#include <stdio.h>', '#include <stdint.h>', '#include <stddef.h>', "",
        f"extern {CTYPES[ret][0]} {func}({argl});        /* real kernel C (shimmed TU) */",
        f"extern {CTYPES[ret][0]} {rust_sym}({argl});    /* Rust candidate */", "",
        "static unsigned long long cases, bad;",
        f"static {rc} fg, fe; static unsigned long long fa[8];",
        f"static void chk({argl}) {{",
        f"    {rc} g = {rust_sym}({argn}), e = {func}({argn});",
        "    cases++;",
        "    if (g != e && bad++ == 0) { fg = g; fe = e; "
        + " ".join(f"fa[{i}] = (unsigned long long)a{i};" for i in range(len(params))) + " }",
        "}", "",
        "int main(void) {",
    ]
    for i, e in enumerate(edges):
        lines.append(f"    static const {decls[i]} E{i}[] = {{{e}}};")
    # edge cross product
    idx = "".join(
        f"    for (size_t i{i} = 0; i{i} < sizeof(E{i})/sizeof(E{i}[0]); i{i}++)\n" for i in range(len(params))
    )
    lines.append(idx + "        chk(" + ", ".join(f"({decls[i]})E{i}[i{i}]" for i in range(len(params))) + ");")
    # random sweep
    lines += [
        "    unsigned long long s = 0x9E3779B97F4A7C15ull;",
        f"    for (long k = 0; k < {nrand}; k++) {{",
    ]
    for i in range(len(params)):
        lines += [
            "        s ^= s << 13; s ^= s >> 7; s ^= s << 17;",
            f"        {decls[i]} a{i} = ({decls[i]})s;",
        ]
    lines += [
        f"        chk({argn});" if params else "        chk();",
        "    }",
        '    printf("HOSTDIFF %s cases=%llu bad=%llu verdict=%s\\n",',
        f'           "{func}", cases, bad, bad ? "DIVERGE" : "MATCH");',
        "    if (bad) {",
        '        printf("  first: %s(", "' + func + '");',
    ]
    for i in range(len(params)):
        sep = ", " if i else ""
        lines.append(f'        printf("{sep}%llu", fa[{i}]);')
    lines += [
        '        printf(") got=%llu exp=%llu\\n", (unsigned long long)fg, (unsigned long long)fe);',
        "    }",
        "    return bad ? 1 : 0;",
        "}",
    ]
    return "\n".join(lines)


def run(path: str, func: str, cand: str, deps: list[str], ksrc: str, nrand: int,
        workdir: str | None = None, quiet: bool = False) -> dict:
    t0 = time.time()
    w = workdir or f"/tmp/hostdiff_{func}"
    subprocess.run(["rm", "-rf", w], check=True)
    os.makedirs(w)
    subprocess.run(["cp", os.path.join(HERE, "kshim.h"), w], check=True)

    # dep TUs first (their exported prototypes feed the main TU)
    protos, objs = [], []
    for i, d in enumerate(deps):
        dsrc = shim_tu(d, ksrc)
        open(f"{w}/dep{i}.c", "w").write(dsrc)
        protos.append(extern_protos(open(os.path.join(ksrc, d)).read()))
        objs.append(f"{w}/dep{i}.o")
        r = subprocess.run(["cc", "-O2", f"-I{w}", "-c", f"{w}/dep{i}.c", "-o", objs[-1]],
                           capture_output=True, text=True)
        if r.returncode:
            return {"verdict": "CC_DEP_FAIL", "detail": r.stderr[:400]}

    main_src = shim_tu(path, ksrc, "\n".join(protos))
    open(f"{w}/tu.c", "w").write(main_src)
    r = subprocess.run(["cc", "-O2", f"-I{w}", "-c", f"{w}/tu.c", "-o", f"{w}/tu.o"],
                       capture_output=True, text=True)
    if r.returncode:
        return {"verdict": "CC_TU_FAIL", "detail": r.stderr[:400]}

    ret, params = parse_sig(open(os.path.join(ksrc, path)).read(), func)
    rust_sym = rust_export(cand)
    open(f"{w}/probe.c", "w").write(gen_probe(func, rust_sym, ret, params, nrand))

    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        "-C", "panic=abort", cand, "-o", f"{w}/libcand.a"],
                       capture_output=True, text=True)
    if r.returncode:
        return {"verdict": "RUSTC_FAIL", "detail": r.stderr[:600]}

    r = subprocess.run(["cc", "-O2", f"-I{w}", f"{w}/probe.c", f"{w}/tu.o", *objs,
                        f"{w}/libcand.a", "-o", f"{w}/diff"], capture_output=True, text=True)
    if r.returncode:
        return {"verdict": "LINK_FAIL", "detail": r.stderr[:400]}

    r = subprocess.run([f"{w}/diff"], capture_output=True, text=True, timeout=120)
    dt = time.time() - t0
    out = r.stdout.strip()
    if not quiet:
        print(out)
    verdict = "MATCH" if r.returncode == 0 else "DIVERGE"
    m = re.search(r"cases=(\d+) bad=(\d+)", out)
    return {"verdict": verdict, "cases": int(m.group(1)) if m else 0,
            "bad": int(m.group(2)) if m else -1, "secs": round(dt, 2), "out": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("func")
    ap.add_argument("--cand", required=True)
    ap.add_argument("--deps", nargs="*", default=[])
    ap.add_argument("--rand", type=int, default=2_000_000)
    ap.add_argument("--ksrc", default=os.environ.get("KSRC", KSRC_DEFAULT))
    a = ap.parse_args()
    res = run(a.file, a.func, a.cand, a.deps, a.ksrc, a.rand)
    if res["verdict"] not in ("MATCH", "DIVERGE"):
        print(f"hostdiff: {res['verdict']}\n{res.get('detail','')}", file=sys.stderr)
        return 1
    print(f"  ({res['secs']}s end-to-end: shim+cc+rustc+probe-gen+{res['cases']:,} cases)")
    return 0 if res["verdict"] == "MATCH" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""weave_realized.py — weave a REALIZED (model->real) efftrace fn into the kernel.

realize.py turns a sweep-verified efftrace candidate into a real-struct
function and re-certifies it with the same differential (host, reduced-layout
arena). This module closes the remaining gap to a booting kernel: the REAL
struct layout. The host proof used a reduced struct (accessed scalar fields
only, sorted); in-tree, the fields live at config-dependent offsets inside the
full struct. So:

  1. PROBE the real offsets in-kernel: append `char cgir_off_<f>[offsetof+1]`
     arrays to the subject .c, build its .o with kbuild (real headers, real
     .config), read the sizes back with nm -S. No execution — the compiler
     itself reports the layout (the opaque-primitive-probe trick).
  2. Emit a MINIMAL PADDED MIRROR: only the accessed fields, at the probed
     offsets, `#[repr(C)]` with explicit `[u8; N]` padding. Dual guards:
     rustc const-asserts (offset_of! == probed) in the object AND C
     `_Static_assert(offsetof == probed)` in the woven file — layout drift
     fails EITHER compile, never boots wrong.
  3. Weave through the ratchet (weave.py): seam call + extern decl, the
     freestanding object wired into kbuild, nm presence check, boot digest.

The behavioral claim was settled on the host by the differential; the guards
carry the layout claim; the boot carries integration. Same soundness chain as
the readers class, now for a realized state-transition function.

  weave_realized.py probe <file> <fn>   # print the probed field offsets
  weave_realized.py emit  <file> <fn>   # print rust object + C guard
  weave_realized.py gate  <file> <fn>   # weave on top of the readers batch + boot
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


realize = _load("realize_wr", os.path.join(REPO, "dream", "realize", "realize.py"))
sys.path.insert(0, HERE)
import weave_readers as WR   # noqa: E402  (VOL/IMG/_reset_stock/_unwire/_real_sig)
import weave as W            # noqa: E402

_ALIGN = {"i8": 1, "u8": 1, "i16": 2, "u16": 2, "i32": 4, "u32": 4,
          "i64": 8, "u64": 8}

# the 10 readers proven present+booting on the defconfig base (RUN-DEFCONFIG);
# the realized fn is gated CUMULATIVELY on top of them — the ratchet grows.
_READERS_BASE = [
    ("kernel/resource.c", "resource_clip"),
    ("lib/bitmap-str.c", "bitmap_check_region"),
    ("lib/linear_ranges.c", "linear_range_get_value"),
    ("kernel/dma/swiotlb.c", "wrap_area_index"),
    ("kernel/bpf/log.c", "bpf_vlog_update_len_max"),
    ("mm/page_vma_mapped.c", "step_forward"),
    ("drivers/pinctrl/renesas/core.c", "sh_pfc_enum_in_range"),
    ("drivers/pwm/core.c", "pwm_check_rounding"),
    ("drivers/pwm/core.c", "pwmwfcmp"),
    ("drivers/thermal/devfreq_cooling.c", "_normalize_load"),
]


def probe_offsets(rel, struct, fields):
    """Real (offset, field_size) per field + sizeof(struct), measured by the
    KERNEL's own compiler under the volume's .config. Restores stock after."""
    stock = open(os.path.join(KSRC, rel), errors="ignore").read()
    probe = ["\n/* cgir layout probe (temporary) */"]
    probe.append(f"char cgir_szs[sizeof(struct {struct})];")
    for f in fields:
        probe.append(f"char cgir_off_{f}[__builtin_offsetof(struct {struct}, {f}) + 1];")
        probe.append(f"char cgir_fsz_{f}[sizeof(((struct {struct} *)0)->{f})];")
    tmp = os.path.join(HERE, "out")
    os.makedirs(tmp, exist_ok=True)
    pname = os.path.basename(rel)
    open(os.path.join(tmp, pname), "w").write(stock + "\n".join(probe) + "\n")
    obj = rel[:-2] + ".o"
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", "-v", f"{tmp}:/w:ro",
         WR.IMG, "bash", "-c",
         f"cp /w/{pname} /build/linux/{rel} && cd /build/linux && rm -f {obj} && "
         f"make -s {obj} 2>&1 | tail -3; nm -S {obj} 2>/dev/null | grep cgir_"],
        capture_output=True, text=True)
    WR._reset_stock([rel])
    out = {}
    for m in re.finditer(r"([0-9a-f]+)\s+([0-9a-f]+)\s+\w\s+(cgir_\w+)", r.stdout):
        out[m.group(3)] = int(m.group(2), 16)
    if "cgir_szs" not in out:
        raise SystemExit(f"probe failed:\n{r.stdout[-600:]}")
    layout = {f: (out[f"cgir_off_{f}"] - 1, out[f"cgir_fsz_{f}"]) for f in fields}
    return layout, out["cgir_szs"]


def build_realized_artifacts(file, fn):
    rec, _prep, tr = realize.realize(file, fn)
    if tr["uses_globals"] or tr["uses_outp"]:
        raise SystemExit("v1 weaves node-param-only realized fns (globals/outp: worklist)")
    node_ps = tr["node_params"]
    if len({p["struct"] for p in node_ps}) != len(node_ps):
        raise SystemExit("two node params of one struct: worklist")

    accessed = {}
    for pname, f in re.findall(r"\(\*(\w+)\)\.(\w+)", tr["fn_src"]):
        p = next(p for p in node_ps if p["name"] == pname)
        accessed.setdefault(p["struct"], set()).add(f)

    mirrors, rust_guards, c_guards = [], [], []
    for p in node_ps:
        struct = p["struct"]
        fields = sorted(accessed.get(struct, ()))
        if not fields:
            raise SystemExit(f"{struct}: no accessed fields — nothing to mirror")
        layout, ssz = probe_offsets(file, struct, fields)
        mn = struct.capitalize() + "Mirror"
        rows, pos, pad = [], 0, 0
        for f in sorted(fields, key=lambda x: layout[x][0]):
            off, fsz = layout[f]
            rty = realize.rust_ty(p["scalar_fields"][f])
            need = _ALIGN[rty]
            if off % need or off < pos:
                raise SystemExit(f"{struct}.{f}: unalignable offset {off}")
            if off > pos:
                rows.append(f"    _p{pad}: [u8; {off - pos}],")
                pad += 1
            exp = int(rty[1:]) // 8
            if fsz != exp:
                raise SystemExit(f"{struct}.{f}: field size {fsz} != {rty}")
            rows.append(f"    pub {f}: {rty},")
            pos = off + fsz
            rust_guards.append(
                f"const _: () = assert!(core::mem::offset_of!({mn}, {f}) == {off});")
            c_guards.append(
                f'_Static_assert(__builtin_offsetof(struct {struct}, {f}) == {off}, '
                f'"{struct}.{f} offset drift vs realized mirror");')
            c_guards.append(
                f'_Static_assert(sizeof(((struct {struct} *)0)->{f}) == {fsz}, '
                f'"{struct}.{f} size drift vs realized mirror");')
        mirrors.append("#[repr(C)]\npub struct " + mn + " {\n" + "\n".join(rows) + "\n}")

    rust_obj = (WR._FREESTANDING + "\n" + "\n".join(mirrors) + "\n"
                + "\n".join(rust_guards) + "\n\n" + tr["fn_src"])

    ret_c, params_c, src = WR._real_sig(file, fn)
    argnames = [re.match(r".*?([A-Za-z_]\w*)\s*(?:\[.*\])?$", p).group(1) for p in params_c]
    seam = f"{fn}_rs"
    extern = "".join(f"struct {p['struct']};\n" for p in node_ps) \
        + f"{ret_c} {seam}({', '.join(params_c)});\n"
    call = f"{seam}({', '.join(argnames)})"
    guard_block = "\n".join("\t" + g for g in c_guards)
    inner = f"{call};" if ret_c == "void" else f"return {call};"
    body = f"{{\n{guard_block}\n\t{inner}\n}}"
    key = re.sub(r"[^0-9A-Za-z_]", "_",
                 f"realized_{file.replace('/', '__')}_{fn}")
    return {"key": key, "rust_obj": rust_obj, "seam": seam, "seam_body": body,
            "extern": extern, "ret_c": ret_c, "params_c": params_c}


def cmd_probe(file, fn):
    rec, _prep, tr = realize.realize(file, fn)
    for p in tr["node_params"]:
        fields = sorted({f for pn, f in re.findall(r"\(\*(\w+)\)\.(\w+)", tr["fn_src"])
                         if pn == p["name"]})
        layout, ssz = probe_offsets(file, p["struct"], fields)
        print(f"struct {p['struct']}: sizeof={ssz}")
        for f in fields:
            print(f"  .{f}: offset={layout[f][0]} size={layout[f][1]}")
    return 0


def cmd_emit(file, fn):
    a = build_realized_artifacts(file, fn)
    print(a["rust_obj"])
    print("---- C seam body ----")
    print(a["seam_body"])
    return 0


def cmd_gate(file, fn):
    """Cumulative gate: the 10-reader defconfig weave + this realized fn, one
    kernel, nm presence for ALL seams, boot digest."""
    a = build_realized_artifacts(file, fn)
    pairs = [p for p in _READERS_BASE]
    all_rels = sorted({rel for rel, _ in pairs} | {file})
    WR._reset_stock(all_rels)
    WR._unwire(pairs + [(file, fn)])
    manifest, skipped = WR._assemble(pairs, W)
    if skipped:
        print("readers skipped:", skipped)
    # append the realized entry
    outd = os.path.join(HERE, "readers")
    open(os.path.join(outd, f"{a['key']}.rs"), "w").write(a["rust_obj"])
    sig = WR._orig_signature(file, fn)
    entry = manifest["sources"].setdefault(file, {"extern_block": "\n", "functions": {}})
    entry["extern_block"] += a["extern"]
    entry["functions"][fn] = {
        "status": "rust", "tier": "tier-b", "gate": "differential",
        "verdict": "PASS", "seam": a["seam"], "shell": f"{sig}\n{a['seam_body']}"}
    manifest["rust_objects"][a["key"]] = {
        "src": f"readers/{a['key']}.rs", "kbuild_dir": os.path.dirname(file),
        "obj": a["key"]}
    mpath = os.path.join(outd, "batch_manifest.json")
    json.dump(manifest, open(mpath, "w"), indent=1)
    W.MANIFEST = mpath
    if W.cmd_apply() != 0:
        return 1
    failed = WR._compile_check(all_rels)
    if failed:
        print(f"✗ compile-check failures: {failed}")
        return 1
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", WR.IMG,
         "bash", "-eo", "pipefail", "-c",
         "cd /build/linux && rm -f arch/arm64/boot/Image && "
         "make -s -j$(nproc) Image 2>&1 | tail -25; "
         "test -f arch/arm64/boot/Image && echo __BUILT__"],
        capture_output=True, text=True)
    if "__BUILT__" not in r.stdout:
        print("✗ build failed:\n" + r.stdout[-800:])
        return 1
    nm = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", WR.IMG, "bash", "-c",
         "cd /build/linux && nm vmlinux 2>/dev/null"], capture_output=True, text=True)
    seams = [f"{fnm}_rs" for _, fnm in pairs] + [a["seam"]]
    present = [s for s in seams if f" {s}" in nm.stdout]
    print(f"present in vmlinux: {len(present)}/{len(seams)} seams "
          f"(realized {a['seam']}: {'YES' if a['seam'] in present else 'NO'})")
    if a["seam"] not in present:
        return 1
    return W._boot_digest()


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, file, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    return {"probe": cmd_probe, "emit": cmd_emit, "gate": cmd_gate}[cmd](file, fn)


if __name__ == "__main__":
    raise SystemExit(main())

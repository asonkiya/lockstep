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


def probe_many(items):
    """items: {rel: {struct: set(fields)}} -> {(rel, struct): (layout, ssz)}.
    ONE docker call: probe arrays appended to every file, all .o built -j, nm
    read back per object. Restores stock after. Files whose probe .o fails are
    simply absent from the result (their candidates skip, tallied upstream)."""
    tmp = os.path.join(HERE, "out", "probe")
    os.makedirs(tmp, exist_ok=True)
    names = {}
    for rel, structs in items.items():
        stock = open(os.path.join(KSRC, rel), errors="ignore").read()
        probe = ["\n/* cgir layout probe (temporary) */"]
        for struct, fields in structs.items():
            probe.append(f"char cgir_z_{struct}[sizeof(struct {struct})];")
            for f in sorted(fields):
                probe.append(f"char cgir_o_{struct}__{f}"
                             f"[__builtin_offsetof(struct {struct}, {f}) + 1];")
                probe.append(f"char cgir_s_{struct}__{f}"
                             f"[sizeof(((struct {struct} *)0)->{f})];")
        pname = re.sub(r"[^0-9A-Za-z_.]", "_", rel)
        names[rel] = pname
        open(os.path.join(tmp, pname), "w").write(stock + "\n".join(probe) + "\n")
    cps = " && ".join(f"cp /w/{names[rel]} /build/linux/{rel}" for rel in items)
    objs = " ".join(rel[:-2] + ".o" for rel in items)
    rms = " ".join(rel[:-2] + ".o" for rel in items)
    nms = " ; ".join(
        f"echo '==={rel}===' ; nm -S {rel[:-2]}.o 2>/dev/null | grep cgir_ || true"
        for rel in items)
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", "-v", f"{tmp}:/w:ro",
         WR.IMG, "bash", "-c",
         f"{cps} && cd /build/linux && rm -f {rms} && "
         f"make -s -j$(nproc) {objs} 2>&1 | tail -5 ; {nms}"],
        capture_output=True, text=True)
    WR._reset_stock(list(items))
    out = {}
    cur = None
    for ln in r.stdout.splitlines():
        m = re.match(r"===(.+)===", ln)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"([0-9a-f]+)\s+([0-9a-f]+)\s+\w\s+(cgir_\w+)", ln)
        if m and cur:
            out[(cur, m.group(3))] = int(m.group(2), 16)
    res = {}
    for rel, structs in items.items():
        for struct, fields in structs.items():
            if (rel, f"cgir_z_{struct}") not in out:
                continue
            try:
                layout = {f: (out[(rel, f"cgir_o_{struct}__{f}")] - 1,
                              out[(rel, f"cgir_s_{struct}__{f}")]) for f in fields}
            except KeyError:
                continue
            res[(rel, struct)] = (layout, out[(rel, f"cgir_z_{struct}")])
    return res


class Skip(Exception):
    pass


def accessed_fields(tr):
    """{struct: set(fields)} actually touched by the realized body (r# stripped)."""
    node_ps = tr["node_params"]
    acc = {}
    for pname, f in re.findall(r"\(\*(\w+)\)\.(?:r#)?(\w+)", tr["fn_src"]):
        p = next(p for p in node_ps if p["name"] == pname)
        acc.setdefault(p["struct"], set()).add(f)
    return acc


def build_realized_artifacts(file, fn, rec=None, tr=None, probes=None):
    if tr is None:
        rec, tr = realize.realize_light(file, fn)
    if tr["uses_globals"] or tr["uses_outp"]:
        raise Skip("globals_or_outp")
    node_ps = tr["node_params"]
    if len({p["struct"] for p in node_ps}) != len(node_ps):
        raise Skip("two_node_params_one_struct")

    accessed = accessed_fields(tr)

    mirrors, rust_guards, c_guards = [], [], []
    for p in node_ps:
        struct = p["struct"]
        fields = sorted(accessed.get(struct, ()))
        if not fields:
            raise Skip(f"no_accessed_fields:{struct}")
        if probes is not None:
            if (file, struct) not in probes:
                raise Skip(f"probe_failed:{struct}")
            layout, ssz = probes[(file, struct)]
        else:
            layout, ssz = list(probe_many({file: {struct: set(fields)}}).values())[0]
        mn = struct.capitalize() + "Mirror"
        rows, pos, pad = [], 0, 0
        for f in sorted(fields, key=lambda x: layout[x][0]):
            off, fsz = layout[f]
            rty = realize.rust_ty(p["scalar_fields"][f])
            need = _ALIGN[rty]
            if off % need or off < pos:
                raise Skip(f"unalignable:{struct}.{f}@{off}")
            if off > pos:
                rows.append(f"    _p{pad}: [u8; {off - pos}],")
                pad += 1
            exp = int(rty[1:]) // 8
            if fsz != exp:
                raise Skip(f"field_size_mismatch:{struct}.{f}:{fsz}")
            rows.append(f"    pub {realize.rid(f)}: {rty},")
            pos = off + fsz
            rust_guards.append(
                f"const _: () = assert!(core::mem::offset_of!({mn}, {realize.rid(f)}) == {off});")
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
    _rec, tr = realize.realize_light(file, fn)
    acc = accessed_fields(tr)
    res = probe_many({file: {s: set(fs) for s, fs in acc.items()}})
    for (rel, struct), (layout, ssz) in res.items():
        print(f"struct {struct}: sizeof={ssz}")
        for f, (off, fsz) in sorted(layout.items(), key=lambda kv: kv[1][0]):
            print(f"  .{f}: offset={off} size={fsz}")
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


def _scrub_realized():
    """Remove ALL stale realized_* wiring in the volume (batch is idempotent)."""
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", WR.IMG, "bash", "-c",
         'cd /build/linux && grep -rl "obj-y += realized_" --include=Makefile . '
         '2>/dev/null | while read mk; do sed -i "/obj-y += realized_/d" "$mk"; done; '
         'find . -name "realized_*.o_shipped" -delete; find . -name "realized_*.o" -delete'],
        capture_output=True)


def _add_entry(manifest, file, fn, a):
    sig = WR._orig_signature(file, fn)
    entry = manifest["sources"].setdefault(file, {"extern_block": "\n", "functions": {}})
    entry["extern_block"] += a["extern"]
    entry["functions"][fn] = {
        "status": "rust", "tier": "tier-b", "gate": "differential",
        "verdict": "PASS", "seam": a["seam"], "shell": f"{sig}\n{a['seam_body']}"}
    manifest["rust_objects"][a["key"]] = {
        "src": f"readers/{a['key']}.rs", "kbuild_dir": os.path.dirname(file),
        "obj": a["key"]}


def cmd_batch():
    """Batch-weave every weave-eligible realized fn (census MATCH, node-only,
    file built in this volume's config) CUMULATIVELY with the 10-reader base.
    Full honest funnel: skips tallied by reason, per-file compile-check drops,
    link-repair drops, nm presence headline, boot digest."""
    elig = json.load(open(os.path.join(REPO, "dream", "realize", "weave_eligible.json")))
    pairs = [tuple(k.rsplit(":", 1)) for k in elig]
    # a fn already woven via the readers base would emit a SECOND object with
    # the same <fn>_rs symbol -> link collision; the readers version stands
    base_set = set(_READERS_BASE)
    dup = [p for p in pairs if p in base_set]
    if dup:
        print(f"  skip {len(dup)} already in readers base: {[fn for _, fn in dup]}")
        pairs = [p for p in pairs if p not in base_set]
    print(f"=== realized batch: {len(pairs)} census-verified candidates in "
          f"{len({f for f, _ in pairs})} built files ===")

    # 1. transpile (light) + collect probe worklist
    skips, light = [], {}
    probe_items = {}
    for file, fn in pairs:
        try:
            rec, tr = realize.realize_light(file, fn)
            if tr["uses_globals"] or tr["uses_outp"]:
                raise Skip("globals_or_outp")
            acc = accessed_fields(tr)
            if not acc:
                raise Skip("no_accessed_fields")
            light[(file, fn)] = (rec, tr)
            for s, fs in acc.items():
                probe_items.setdefault(file, {}).setdefault(s, set()).update(fs)
        except (Skip, realize.Refused, Exception) as e:
            skips.append((file, fn, str(e)[:60]))
    print(f"probing {sum(len(v) for v in probe_items.values())} structs "
          f"across {len(probe_items)} files (one kbuild pass)...")
    probes = probe_many(probe_items)
    print(f"  probed {len(probes)} (rel,struct) layouts")

    # 2. artifacts
    arts = {}
    for (file, fn), (rec, tr) in light.items():
        try:
            arts[(file, fn)] = build_realized_artifacts(file, fn, rec, tr, probes)
        except Skip as e:
            skips.append((file, fn, str(e)))
    for file, fn, why in skips:
        print(f"  skip {fn} ({file}): {why}")
    print(f"artifacts: {len(arts)} realized fns weave-ready, {len(skips)} skipped")

    # 3. assemble: readers base + realized entries; clean tree
    survivors = dict(arts)
    readers = list(_READERS_BASE)
    for round_ in range(5):
        all_rels = sorted({r for r, _ in readers} | {f for f, _ in survivors})
        WR._reset_stock(all_rels)
        WR._unwire(readers)
        _scrub_realized()
        manifest, rskip = WR._assemble(readers, W)
        if rskip:
            print("readers skipped:", rskip)
        outd = os.path.join(HERE, "readers")
        for (file, fn), a in survivors.items():
            open(os.path.join(outd, f"{a['key']}.rs"), "w").write(a["rust_obj"])
            _add_entry(manifest, file, fn, a)
        mpath = os.path.join(outd, "batch_manifest.json")
        json.dump(manifest, open(mpath, "w"), indent=1)
        W.MANIFEST = mpath
        if W.cmd_apply() != 0:
            return 1
        if round_ == 0:
            print("compile-checking woven files...")
            failed = WR._compile_check(all_rels)
            if failed:
                dropped = [(f, fn) for (f, fn) in survivors if f in failed]
                base_hit = [p for p in readers if p[0] in failed]
                print(f"  dropping {len(dropped)} realized in {len(failed)} failing "
                      f"file(s): {sorted(fn for _, fn in dropped)}")
                if base_hit:
                    print(f"  (readers-base files also failing: {base_hit})")
                    readers = [p for p in readers if p[0] not in failed]
                for k in dropped:
                    survivors.pop(k)
                continue
        # 4. build with link-repair (drop div-panic objects by key, err lines only)
        keymap = {a["key"]: (f, fn) for (f, fn), a in survivors.items()}
        r = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", WR.IMG,
             "bash", "-eo", "pipefail", "-c",
             "cd /build/linux && rm -f arch/arm64/boot/Image && "
             "make -s -j$(nproc) Image 2>&1 | tail -60; "
             "test -f arch/arm64/boot/Image && echo __BUILT__"],
            capture_output=True, text=True)
        if "__BUILT__" in r.stdout:
            break
        err_text = "\n".join(ln for ln in r.stdout.splitlines() if "warning:" not in ln)
        bad = {keymap[k] for k in keymap if k in err_text}
        if not bad:
            print("  ✗ build failed (not an isolable realized link error):")
            print("   " + "\n   ".join(r.stdout.strip().splitlines()[-8:]))
            return 1
        print(f"  link-drop {len(bad)} realized fn(s): {sorted(fn for _, fn in bad)}")
        for k in bad:
            survivors.pop(k)
    else:
        print("  ✗ did not converge")
        return 1

    # 5. honest metric + boot
    nm = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{WR.VOL}:/build", WR.IMG, "bash", "-c",
         "cd /build/linux && nm vmlinux 2>/dev/null"], capture_output=True, text=True)
    r_seams = {f"{fn}_rs" for _, fn in readers}
    z_seams = {a["seam"]: (f, fn) for (f, fn), a in survivors.items()}
    r_present = [s for s in sorted(r_seams) if f" {s}" in nm.stdout]
    z_present = [s for s in sorted(z_seams) if f" {s}" in nm.stdout]
    print(f"BUILT: {len(survivors)} realized source-woven; "
          f"PRESENT in vmlinux: {len(z_present)} realized + {len(r_present)} readers "
          f"= {len(z_present) + len(r_present)} Rust fns")
    gone = sorted(set(z_seams) - set(z_present))
    if gone:
        print(f"  realized not-linked ({len(gone)}): {', '.join(gone)}")
    return W._boot_digest()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return cmd_batch()
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, file, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    return {"probe": cmd_probe, "emit": cmd_emit, "gate": cmd_gate}[cmd](file, fn)


if __name__ == "__main__":
    raise SystemExit(main())

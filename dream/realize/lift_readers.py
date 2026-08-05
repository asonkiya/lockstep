#!/usr/bin/env python3
"""A3 — safety lift for the READER class (structdiff-verified candidates).

Readers are model-WRITTEN Rust (not deterministic transpiles like efftrace),
but their bodies use exactly one pointer idiom — `(*p).field` for struct params
and `*outp` for scalar out-params — so they lift DETERMINISTICALLY too (no
model, $0), gated by re-running the readers' OWN oracle (structdiff.harness).

The lift is the same tier-(b) shape as realize's (A1): the verified logic moves
into a `#![forbid(unsafe_code)]` core taking one reference per accessed field
(`&mut`/`&` by write-status; `&` for every field of a `*const` param — sound
even if two `*const` params alias, since shared refs may alias), plus a
boundary whose whole unsafe surface is per-field `&mut/& (*p).field`. NO
whole-struct borrow.

Soundness gates, all preserved:
  * the lifted candidate must MATCH the SAME structdiff differential (behavior
    pinned across the lift);
  * rustc forbids unsafe in the core (machine-checked);
  * the per-field concurrency audit (realize.field_audit) demotes any fn whose
    accessed struct field is touched locklessly anywhere in the tree.

Refuses (fail-closed → stays tier-a): 2+ `*mut` STRUCT pointers (cross-struct
&mut could alias); any pointer use that is not `(*p).field` / `*outp`; a write
through a `*const` param.

  lift_readers.py <rel> <fn>   host re-gate one reader's lift
  lift_readers.py batch        re-gate + tier-report the woven reader set
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VERIFIED = os.path.join(REPO, "dream", "firstrun", "verified")


def _load(name, rel):
    path = os.path.join(REPO, "dream", rel)
    d = os.path.dirname(path)
    if d not in sys.path:
        sys.path.insert(0, d)
    # sibling helper dirs the module may import (cluster, mirror, cfg)
    for sib in ("cluster", "mirror"):
        sd = os.path.join(REPO, "dream", sib)
        if sd not in sys.path:
            sys.path.insert(0, sd)
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


realize = _load("realize_lr", "realize/realize.py")
sd = _load("structdiff_lr", "structdiff/harness.py")


class Refused(Exception):
    pass


def _cand_text(rel, fn):
    key = f"reader_{rel.replace('/', '__')}_{fn}"
    p = os.path.join(VERIFIED, f"{key}.rs")
    return open(p).read(), key


_FN_RE = r'#\[no_mangle\]\s*pub extern "C" fn {fn}_rs\s*\(([^)]*)\)\s*(->[^{{]*?)?\{{'


def _split_fn(text, fn):
    """Return (prefix, params, ret, body, suffix) splitting the reader fn.
    body is the inner code (the reader wraps it in `unsafe {{ ... }}`)."""
    m = re.search(_FN_RE.format(fn=re.escape(fn)), text)
    if not m:
        raise Refused("fn_header_not_found")
    # brace-match the fn body
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    fn_full = text[m.start():j + 1]
    params, ret = m.group(1).strip(), (m.group(2) or "").strip()
    inner = text[i + 1:j].strip()
    # unwrap a leading `unsafe { ... }` wrapper if present
    um = re.match(r"unsafe\s*\{(.*)\}\s*$", inner, re.DOTALL)
    body = um.group(1).strip() if um else inner
    return text[:m.start()], params, ret, body, text[j + 1:], fn_full


def _parse_params(params):
    """[{name, kind: struct|outp|scalar, ty, const}]."""
    out = []
    for piece in [p.strip() for p in params.split(",") if p.strip()]:
        name, _, ty = piece.partition(":")
        name, ty = name.strip(), ty.strip()
        m = re.match(r"\*(mut|const)\s+(\w+)$", ty)
        if m:
            out.append({"name": name, "ty": ty, "ptr": m.group(2),
                        "const": m.group(1) == "const", "kind": "ptr?"})
        else:
            out.append({"name": name, "ty": ty, "kind": "scalar"})
    return out


def lift_reader(rel, fn):
    text, key = _cand_text(rel, fn)
    prefix, params_s, ret, body, suffix, _full = _split_fn(text, fn)
    params = _parse_params(params_s)

    ptrs = [p for p in params if p["kind"] == "ptr?"]
    # classify each pointer: struct (has (*p).field) vs scalar out-param (*p =)
    accessed = {}          # pname -> {field: is_written}
    outps = []
    struct_muts = 0
    for p in ptrs:
        pn = p["name"]
        name_occ = len(re.findall(rf"\b{re.escape(pn)}\b", body))
        fields = re.findall(rf"\(\*{re.escape(pn)}\)\.(\w+)", body)
        if fields:                                   # struct pointer
            # EVERY occurrence of the name must be part of `(*p).field`
            if name_occ != len(fields):
                raise Refused(f"non_field_use_of_struct_ptr:{pn}")
            fmap = {}
            for f in set(fields):
                written = bool(re.search(
                    rf"\(\*{re.escape(pn)}\)\.{f}\s*(?:[-+*/|&^]?=)(?!=)", body))
                if written and p["const"]:
                    raise Refused(f"write_through_const:{pn}.{f}")
                fmap[f] = written
            accessed[pn] = fmap
            if any(fmap.values()) and not p["const"]:
                struct_muts += 1
        else:                                        # scalar out-param
            deref = len(re.findall(rf"\*{re.escape(pn)}\b", body))
            if deref == 0:
                raise Refused(f"unused_ptr_param:{pn}")
            outps.append(p)
    if struct_muts > 1:
        raise Refused("multiple_mut_struct_ptrs")     # cross-struct &mut aliasing
    if not accessed:
        raise Refused("no_struct_field_access")

    # concurrency audit over the accessed struct fields (out-params excluded:
    # a caller-provided output location, exclusive by the C ABI contract)
    all_fields = {f for fm in accessed.values() for f in fm}
    racy = realize.field_audit(all_fields)
    if racy:
        return None, "a-mirror", sorted(all_fields), sorted(racy)

    # --- build the field-granular safe form ------------------------------
    core_sig, call_args, subs = [], [], []
    for p in ptrs:
        pn = p["name"]
        if pn in accessed:
            for f in sorted(accessed[pn]):
                mut = accessed[pn][f] and not p["const"]
                cn = f"f_{pn}_{f}"
                core_sig.append(f"{cn}: &{'mut ' if mut else ''}{_field_ty(text, p['ptr'], f)}")
                call_args.append(f"&{'mut ' if mut else ''}(*{pn}).{f}")
                subs.append((rf"\(\*{re.escape(pn)}\)\.{f}\b", f"(*{cn})"))
        else:                                        # out-param
            cn = f"o_{pn}"
            oty = p["ty"].split()[-1]
            core_sig.append(f"{cn}: &mut {oty}")
            call_args.append(f"&mut *{pn}")
            subs.append((rf"\(\*{re.escape(pn)}\)", f"(*{cn})"))
            subs.append((rf"(?<![\w.])\*{re.escape(pn)}\b", f"*{cn}"))
    for p in params:
        if p["kind"] == "scalar":
            core_sig.append(f"{p['name']}: {p['ty']}")
            call_args.append(p["name"])

    core_body = body
    for pat, rep in subs:
        core_body = re.sub(pat, rep, core_body)
    ret_ty = ret[2:].strip() if ret.startswith("->") else ""
    ret_sig = f" -> {ret_ty}" if ret_ty else ""
    mod = f"{fn.lstrip('_')}_reader_core"
    core = (f"mod {mod} {{\n    #![forbid(unsafe_code)]\n"
            f"    pub fn core({', '.join(core_sig)}){ret_sig} {{\n"
            f"{_indent(core_body)}\n    }}\n}}\n")
    call = f"{mod}::core({', '.join(call_args)})"
    boundary = (f'#[no_mangle]\npub unsafe extern "C" fn {fn}_rs({params_s}){ret}'
                + " {\n    " + (f"{call}" if not ret_ty else f"{call}") + "\n}\n")
    new_fn = core + boundary
    new_cand = prefix + new_fn + suffix
    return new_cand, "b-safe-core", sorted(all_fields), []


def _field_ty(text, struct_rs, field):
    """Rust type of `field` from the candidate's mirror struct def."""
    m = re.search(rf"pub struct {struct_rs}\b.*?\{{(.*?)\}}", text, re.DOTALL)
    if m:
        fm = re.search(rf"\b(?:r#)?{re.escape(field)}\s*:\s*([\w:]+)", m.group(1))
        if fm:
            return fm.group(1)
    return "i64"      # fallback (the differential will catch a wrong width)


def _indent(s, n=8):
    pad = " " * n
    return "\n".join(pad + ln if ln.strip() else ln for ln in s.splitlines())


def prove(rel, fn):
    cand, tier, fields, racy = lift_reader(rel, fn)
    if cand is None:
        print(f"LIFT-READER {rel}:{fn} -> DEMOTED (tier a); racy fields: {racy}")
        return 0
    d = tempfile.mkdtemp(prefix="liftrd_")
    v, out = sd.close(rel, fn, cand, d)
    tag = out.split("verdict=")[-1][:40] if "verdict=" in out else out[:60]
    print(f"LIFT-READER {rel}:{fn} tier={tier} fields={fields} -> {v}  [{tag}]")
    if v != "MATCH":
        print(f"  dir={d}\n{out[:400]}")
    return 0 if v == "MATCH" else 1


# the 10 readers woven in the defconfig kernel (weave_realized._READERS_BASE)
_WOVEN = [
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


def batch():
    tb = ta = fail = 0
    for rel, fn in _WOVEN:
        try:
            cand, tier, fields, racy = lift_reader(rel, fn)
        except Refused as e:
            print(f"  {fn:30s} REFUSED(tier a): {e}")
            ta += 1
            continue
        if cand is None:
            print(f"  {fn:30s} DEMOTED(tier a) — racy: {racy}")
            ta += 1
            continue
        d = tempfile.mkdtemp(prefix="liftrd_")
        v, out = sd.close(rel, fn, cand, d)
        if v == "MATCH":
            print(f"  {fn:30s} tier b — safe core, structdiff MATCH ({len(fields)} fields)")
            tb += 1
        else:
            print(f"  {fn:30s} LIFT FAILED: {v}  (dir={d})")
            fail += 1
    print(f"\nreaders lift: {tb} tier-b (machine-checked safe core, structdiff-MATCH), "
          f"{ta} tier-a (refused/demoted), {fail} failures")
    return 1 if fail else 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return batch()
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    return prove(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())

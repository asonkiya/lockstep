#!/usr/bin/env python3
"""Containers weave — kernel-form emission for chain/composed-verified
container fns (Summit 1.1, PREREG-CWEAVE.md, frozen D=40).

The gate-time realizer verified the OP-SEQUENCE translation (both
differential sides model list ops + free events only). A WOVEN function
replaces the whole C body in the kernel, so this module emits the FULL body:

  * list ops as faithful pointer surgery at PROBED offsets (no mirror struct
    needed — we never read non-list fields; addresses are base + offset,
    guarded by in-tree _Static_asserts, the load-bearing check);
  * `kfree` and locks as REAL kernel symbols called in the original order
    (mutex_lock/mutex_unlock; spin_* via the _raw_spin_* symbol layer with a
    seam _Static_assert that rlock sits at offset 0);
  * file-static heads/locks passed by the C seam (it has file scope);
  * iteration as the cached-next (_safe) or read-after-body (plain) walk the
    C chose, with container_of arithmetic for kfree(cursor);
  * LIST_POISON values pinned by seam _Static_asserts against linux/poison.h.

Fail-closed: any argument form, lock placement, or statement this module
cannot reproduce exactly -> Skip(reason), tallied, never guessed.

  weave_containers.py parse <rel> <fn>    # show the emission IR
  weave_containers.py emit <rel> <fn>     # show rust_obj + seam
  weave_containers.py negctl              # sabotaged _Static_assert MUST fail the build
  weave_containers.py batch               # full cumulative weave (readers + realized + containers)
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


WZ = _load("weave_realized_wc", os.path.join(HERE, "weave_realized.py"))
WR = WZ.WR                                   # weave_readers
W = WZ.W                                     # weave
CR = _load("container_realize_wc",
           os.path.join(REPO, "dream", "container_adt", "container_realize.py"))
sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import cluster  # noqa: E402

Skip = WZ.Skip

# concrete C call -> (rust extern symbol, kind). Locks go through the real
# symbol layer (spin_lock is inline; _raw_spin_lock is the exported function).
_LOCKS = {
    "mutex_lock": "mutex_lock", "mutex_unlock": "mutex_unlock",
    "spin_lock": "_raw_spin_lock", "spin_unlock": "_raw_spin_unlock",
    "spin_lock_irq": "_raw_spin_lock_irq", "spin_unlock_irq": "_raw_spin_unlock_irq",
    "spin_lock_bh": "_raw_spin_lock_bh", "spin_unlock_bh": "_raw_spin_unlock_bh",
    "spin_lock_irqsave": "_raw_spin_lock_irqsave",
    "spin_unlock_irqrestore": "_raw_spin_unlock_irqrestore",
}
_ASSERTS = ("lockdep_assert_held", "assert_spin_locked")   # no-op when LOCKDEP=n
_LIST = {k: v for k, v in CR._C_OPS.items() if k != "kfree"}
_SPIN_SYMS = [s for s in _LOCKS.values() if s.startswith("_raw_spin")]


# ---------------------------------------------------------------------------
# parse -> emission IR
# ---------------------------------------------------------------------------

def _params_of(params_c):
    """[(struct_name|None, argname)] from C decl strings."""
    out = []
    for p in params_c:
        m = re.match(r".*?([A-Za-z_]\w*)\s*(?:\[.*\])?$", p)
        sm = re.search(r"\bstruct\s+(\w+)", p)
        out.append((sm.group(1) if sm else None, m.group(1)))
    return out


def parse(rel, fn):
    ret_c, params_c, _src = WR._real_sig(rel, fn)
    if ret_c != "void":
        raise Skip("nonvoid_return")
    params = _params_of(params_c)
    pnames = {n for _, n in params}
    ptypes = {n: s for s, n in params}
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    text = cluster.functions(src)[fn]["text"]
    body = text[text.index("{"):]

    # iteration extent
    it = None
    lm = re.search(r"\blist_for_each_entry(_safe)?\s*\(", body)
    if lm:
        largs, mend = CR._split_call(body, lm.end() - 1)
        safe = bool(lm.group(1))
        cursor = largs[0].strip()
        head_expr = largs[2 if safe else 1].strip()
        member = largs[-1].strip()
        cm = re.search(rf"\bstruct\s+(\w+)\s*\*\s*{cursor}\b", body)
        if not cm:
            raise Skip("cursor_type_not_found")
        i = mend
        while i < len(body) and body[i] in " \t\n":
            i += 1
        if body[i] == "{":
            d, j = 0, i
            while j < len(body):
                d += body[j] == "{"
                d -= body[j] == "}"
                j += 1
                if d == 0:
                    break
            extent = (i, j)
        else:
            extent = (i, body.index(";", i) + 1)
        it = {"safe": safe, "cursor": cursor, "head": head_expr,
              "member": member, "cstruct": cm.group(1), "extent": extent,
              "macro": (lm.start(), mend)}

    # ordered events
    names = sorted(list(_LIST) + ["kfree"] + list(_LOCKS) + list(_ASSERTS),
                   key=len, reverse=True)
    events = []
    for m in re.finditer(r"\b(" + "|".join(names) + r")\s*\(", body):
        if it and it["macro"][0] <= m.start() < it["macro"][1]:
            continue
        args, _ = CR._split_call(body, m.end() - 1)
        name = m.group(1)
        if name in _ASSERTS:
            continue                          # LOCKDEP=n no-ops, elided
        seg = "line"
        if it:
            seg = ("inner" if it["extent"][0] <= m.start() < it["extent"][1]
                   else ("pre" if m.start() < it["extent"][0] else "post"))
            if name in _LOCKS and seg == "inner":
                raise Skip("lock_in_loop")
        events.append({"pos": m.start(), "name": name,
                       "args": [a.strip() for a in args], "seg": seg})
    if not events:
        raise Skip("no_events")
    # list_empty guards (the gate-proven class). Guard structure comes from
    # the SAME parser the differential gate proved (CR._parse_guard), never a
    # re-derivation; the weave bar is whole-body reproduction, so the guard's
    # early-return IS the reproduced control flow (not an unsupported return).
    guard = None
    if re.search(r"\bif\b|\?", body):
        try:
            guard = CR._parse_guard(body, {"safe": True} if it else None)
        except CR.Refused as e:
            raise Skip(f"guard:{str(e)[:50]}")
        if it:
            it["loop_guard"] = guard["loop_guard"]
        else:
            inv = {"empty": "not_empty", "not_empty": "empty"}
            for e in events:
                if guard["extent"][0] <= e["pos"] < guard["extent"][1]:
                    e["cond"] = guard["pol"]
                elif e["pos"] >= guard["extent"][1] and guard["inverts_rest"]:
                    e["cond"] = inv[guard["pol"]]
                else:
                    e["cond"] = None
            guard["pred_r"] = None           # resolved below with the others
    if_extent = None
    if guard:
        gm = re.search(r"\bif\s*\(", body)
        _pa, pend = CR._split_call(body, gm.end() - 1)
        k = pend
        while k < len(body) and body[k] in " \t\n":
            k += 1
        if body[k] == "{":
            d, j2 = 0, k
            while j2 < len(body):
                d += body[j2] == "{"
                d -= body[j2] == "}"
                j2 += 1
                if d == 0:
                    break
            if_extent = (k, j2)
        else:
            if_extent = (k, body.index(";", k) + 1)
    for rm_ in re.finditer(r"\breturn\b", body):
        if if_extent and if_extent[0] <= rm_.start() < if_extent[1]:
            continue                 # the guard's own early return: reproduced
        if any(e["pos"] > rm_.start() for e in events):
            raise Skip("early_return")

    # resolve every referenced object; collect probes + statics
    probes, statics = set(), []

    def resolve(e):
        e = e.strip()
        amp = e.startswith("&")
        e2 = e.lstrip("&").strip()
        if re.fullmatch(r"[A-Za-z_]\w*", e2):
            if it and e2 == it["cursor"]:
                probes.add((it["cstruct"], it["member"]))
                return ("cursor",)
            if e2 in pnames:
                return ("param", e2)
            if e2 not in statics:
                statics.append(e2)
            return ("static", e2)
        mm = re.fullmatch(r"([A-Za-z_]\w*)\s*->\s*(\w+)", e2)
        if mm and amp:
            base, field = mm.group(1), mm.group(2)
            if it and base == it["cursor"]:
                probes.add((it["cstruct"], it["member"]))
                probes.add((it["cstruct"], field))
                return ("cursor_member", field)
            if base in pnames and ptypes.get(base):
                probes.add((ptypes[base], field))
                return ("member", base, ptypes[base], field)
        raise Skip(f"arg_form:{e[:30]}")

    for ev in events:
        if ev["name"] == "kfree":
            ev["r"] = [resolve(ev["args"][0])]
            if ev["r"][0][0] not in ("cursor", "param"):
                raise Skip("kfree_target:" + ev["r"][0][0])
        elif ev["name"] in _LOCKS:
            ev["r"] = [resolve(ev["args"][0])]
            if "irqsave" in ev["name"] or "irqrestore" in ev["name"]:
                fl = ev["args"][1].strip()
                if not re.fullmatch(r"[A-Za-z_]\w*", fl):
                    raise Skip("flags_form")
                ev["flags"] = fl
        else:                                 # list ops
            ev["r"] = [resolve(a) for a in ev["args"]]
    head_r = resolve(it["head"]) if it else None
    guard_res = None
    if guard and not it:
        guard_res = resolve("&" + guard["pred"])
    return {"rel": rel, "fn": fn, "ret_c": ret_c, "params_c": params_c,
            "params": params, "it": it, "events": events, "head_r": head_r,
            "guard": guard, "guard_res": guard_res,
            "probes": sorted(probes), "statics": statics}


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

_OPS_RS = """
#[repr(C)] pub struct LH { pub next: *mut LH, pub prev: *mut LH }
unsafe fn lh_init(l: *mut LH) { (*l).next = l; (*l).prev = l; }
unsafe fn __list_add(n: *mut LH, p: *mut LH, x: *mut LH) {
    (*x).prev = n; (*n).next = x; (*n).prev = p; (*p).next = n;
}
unsafe fn list_add(n: *mut LH, h: *mut LH) { __list_add(n, h, (*h).next); }
unsafe fn list_add_tail(n: *mut LH, h: *mut LH) { __list_add(n, (*h).prev, h); }
unsafe fn __list_del(p: *mut LH, x: *mut LH) { (*x).prev = p; (*p).next = x; }
unsafe fn list_del(e: *mut LH) {
    __list_del((*e).prev, (*e).next);
    (*e).next = POISON1 as *mut LH; (*e).prev = POISON2 as *mut LH;
}
unsafe fn list_del_init(e: *mut LH) { __list_del((*e).prev, (*e).next); lh_init(e); }
unsafe fn list_move(e: *mut LH, h: *mut LH) { __list_del((*e).prev, (*e).next); list_add(e, h); }
unsafe fn list_move_tail(e: *mut LH, h: *mut LH) { __list_del((*e).prev, (*e).next); list_add_tail(e, h); }
"""


def emit(ir, probes, poison, sabotage=None):
    rel, fn = ir["rel"], ir["fn"]
    offs, guards = {}, []
    for struct, field in ir["probes"]:
        key = (rel, struct)
        if key not in probes or field not in probes[key][0]:
            raise Skip(f"probe_failed:{struct}.{field}")
        off = probes[key][0][field][0]
        offs[(struct, field)] = off
        goff = off + 8 if sabotage == "bad_offset" else off
        guards.append(f'_Static_assert(__builtin_offsetof(struct {struct}, {field}) '
                      f'== {goff}, "cgir cweave layout {struct}.{field}");')
    uses_del = any(e["name"] in ("list_del", "list_del_init", "list_move",
                                 "list_move_tail") for e in ir["events"])
    if uses_del:
        guards.append(f'_Static_assert((unsigned long)LIST_POISON1 == {poison["poison1"]:#x}UL, '
                      f'"cgir cweave poison1");')
        guards.append(f'_Static_assert((unsigned long)LIST_POISON2 == {poison["poison2"]:#x}UL, '
                      f'"cgir cweave poison2");')
    if any(e["name"].startswith("spin") or e["name"].startswith("raw_spin")
           for e in ir["events"]):
        guards.append('_Static_assert(__builtin_offsetof(spinlock_t, rlock) == 0, '
                      '"cgir cweave rlock");')

    statics = ir["statics"]
    sargs = {s: f"cw_{s}" for s in statics}
    it = ir["it"]

    def lh(r, cur="pos"):
        """rust expression: *mut LH address for a resolved entity."""
        if r[0] == "param":
            return f"{r[1]} as *mut LH"
        if r[0] == "static":
            return f"{sargs[r[1]]} as *mut LH"
        if r[0] == "cursor":
            return cur
        if r[0] == "cursor_member":
            base = f"(({cur} as usize) - {offs[(it['cstruct'], it['member'])]})"
            return f"(({base} + {offs[(it['cstruct'], r[1])]}) as *mut LH)"
        if r[0] == "member":
            return f"((({r[1]} as usize) + {offs[(r[2], r[3])]}) as *mut LH)"
        raise Skip(f"lh_form:{r[0]}")

    externs, lines_by_seg = set(), {"pre": [], "inner": [], "post": [], "line": []}
    seq = []                                 # straight-line: (cond, text)
    flags_locals = set()

    def _ap(seg, ev, text):
        if seg == "line":
            seq.append((ev.get("cond"), text))
        else:
            lines_by_seg[seg].append(text)

    for ev in ir["events"]:
        seg = ev["seg"]
        n = ev["name"]
        if n == "kfree":
            externs.add("fn kfree(p: *mut u8);")
            r = ev["r"][0]
            if r[0] == "cursor":
                base = f"((pos as usize) - {offs[(it['cstruct'], it['member'])]})"
                _ap(seg, ev, f"kfree({base} as *mut u8);")
            else:
                _ap(seg, ev, f"kfree({r[1]} as *mut u8);")
        elif n in _LOCKS:
            sym = _LOCKS[n]
            addr = lh(ev["r"][0]).replace("*mut LH", "*mut u8")
            if "irqsave" in n:
                externs.add(f"fn {sym}(l: *mut u8) -> u64;")
                flags_locals.add(ev["flags"])
                _ap(seg, ev, f"{ev['flags']} = {sym}({addr});")
            elif "irqrestore" in n:
                externs.add(f"fn {sym}(l: *mut u8, f: u64);")
                _ap(seg, ev, f"{sym}({addr}, {ev['flags']});")
            else:
                externs.add(f"fn {sym}(l: *mut u8);")
                _ap(seg, ev, f"{sym}({addr});")
        else:                                 # list op
            rs = _LIST[n][1]
            rfn = {"INIT_LIST_HEAD": "lh_init"}.get(rs, rs)
            args = [lh(r) for r in ev["r"]]
            _ap(seg, ev, f"{rfn}({', '.join(args)});")

    body = []
    for f in sorted(flags_locals):
        body.append(f"    let mut {f}: u64 = 0; let _ = {f};")
    ind = "    "
    if it is None:
        # group consecutive same-cond runs into ONE guard block: the C
        # evaluates the predicate once, and per-line wrapping would re-test a
        # condition the earlier lines may have just changed
        i = 0
        while i < len(seq):
            cond = seq[i][0]
            j = i
            while j < len(seq) and seq[j][0] == cond:
                j += 1
            chunk = [t for _, t in seq[i:j]]
            if cond:
                gp = lh(ir["guard_res"])
                cmp_ = "==" if cond == "empty" else "!="
                body.append(f"    let gp: *mut LH = {gp};")
                body.append(f"    if (*gp).next {cmp_} gp {{")
                body += ["        " + t for t in chunk]
                body.append("    }")
            else:
                body += [ind + t for t in chunk]
            i = j
    else:
        seg_lines = [ind + l for l in lines_by_seg["pre"]]
        head = lh(ir["head_r"])
        inner = "\n".join("        " + l for l in lines_by_seg["inner"])
        if it["safe"]:
            seg_lines.append(f"""    let head: *mut LH = {head};
    let mut pos = (*head).next;
    while pos != head {{
        let n = (*pos).next;
{inner}
        pos = n;
    }}""")
        else:
            seg_lines.append(f"""    let head: *mut LH = {head};
    let mut pos = (*head).next;
    while pos != head {{
{inner}
        pos = (*pos).next;
    }}""")
        seg_lines += [ind + l for l in lines_by_seg["post"]]
        if it.get("loop_guard"):
            # `if (list_empty(head)) return;` guards EVERYTHING after it —
            # locks included — so the whole body goes in one block (per-event
            # wrapping would re-test after the flush emptied the list and
            # skip the unlock: a deadlock)
            gh = lh(ir["head_r"])
            body.append(f"    let gh: *mut LH = {gh};")
            body.append("    if (*gh).next != gh {")
            body += ["    " + l for l in seg_lines]
            body.append("    }")
        else:
            body += seg_lines

    rparams = [f"{n}: *mut u8" for _, n in ir["params"]] \
        + [f"{sargs[s]}: *mut u8" for s in statics]
    rust_obj = (WR._FREESTANDING
                + f"\nconst POISON1: usize = {poison['poison1']:#x};"
                + f"\nconst POISON2: usize = {poison['poison2']:#x};\n"
                + _OPS_RS
                + "\nextern \"C\" { " + " ".join(sorted(externs)) + " }\n"
                + f"\n#[no_mangle]\npub unsafe extern \"C\" fn {fn}_rs("
                + ", ".join(rparams) + ") {\n" + "\n".join(body) + "\n}\n")

    seam = f"{fn}_rs"
    argnames = [n for _, n in ir["params"]] + [f"(void *)&{s}" for s in statics]
    # forward struct decls: the extern block lands at FILE TOP, before types
    # defined later in the file (stop_machine.c taught this under -Werror)
    fwd = "".join(f"struct {s};\n" for s, _ in ir["params"] if s)
    extern = (fwd + f"void {seam}({', '.join(ir['params_c'] + ['void *' + sargs[s] for s in statics])});\n")
    guard_block = "\n".join("\t" + g for g in guards)
    seam_body = f"{{\n{guard_block}\n\t{seam}({', '.join(argnames)});\n}}"
    key = re.sub(r"[^0-9A-Za-z_]", "_", f"realized_{rel.replace('/', '__')}_{fn}")
    return {"key": key, "rust_obj": rust_obj, "seam": seam,
            "seam_body": seam_body, "extern": extern, "ret_c": "void",
            "params_c": ir["params_c"], "tier": "a-mirror", "cls": "container",
            "metrics": WZ.metrics.fn_metrics(rust_obj, "a-mirror")}


# ---------------------------------------------------------------------------
# drivers
# ---------------------------------------------------------------------------

def _eligible():
    d = json.load(open(os.path.join(HERE, "cweave_denominator.json")))
    return [(r["rel"], r["fn"]) for r in d["weave_eligible"]]


def build_artifacts(subset=None, sabotage=None):
    pairs = subset or _eligible()
    irs, skips, probe_items = {}, [], {}
    for rel, fn in pairs:
        try:
            ir = parse(rel, fn)
            irs[(rel, fn)] = ir
            for struct, field in ir["probes"]:
                probe_items.setdefault(rel, {}).setdefault(struct, set()).add(field)
        except (Skip, CR.Refused) as e:
            skips.append((rel, fn, str(e)[:60]))
        except Exception as e:
            skips.append((rel, fn, f"error/{type(e).__name__}:{str(e)[:40]}"))
    print(f"parsed {len(irs)}/{len(pairs)}; probing "
          f"{sum(len(v) for v in probe_items.values())} structs (one kbuild pass)...")
    probes = WZ.probe_many(probe_items) if probe_items else {}
    poison = CR.LM.probe_layout()
    arts = {}
    for (rel, fn), ir in irs.items():
        try:
            arts[(rel, fn)] = emit(ir, probes, poison, sabotage=sabotage)
        except Skip as e:
            skips.append((rel, fn, str(e)[:60]))
    for rel, fn, why in skips:
        print(f"  skip {fn} ({rel}): {why}")
    print(f"container artifacts: {len(arts)} weave-ready, {len(skips)} skipped")
    return arts


def cmd_parse(rel, fn):
    ir = parse(rel, fn)
    print(json.dumps({k: v for k, v in ir.items() if k != "params"},
                     indent=1, default=str))
    return 0


def cmd_emit(rel, fn):
    arts = build_artifacts([(rel, fn)])
    a = arts[(rel, fn)]
    print(a["rust_obj"])
    print("---- C seam ----")
    print(a["extern"] + a["seam_body"])
    return 0


def cmd_negctl():
    """Sabotaged _Static_assert offset MUST fail the kernel compile — proves
    the in-tree guard is load-bearing before any real weave is trusted."""
    pairs = _eligible()[:1]
    arts = build_artifacts(pairs, sabotage="bad_offset")
    if not arts:
        print("negctl: no artifact built")
        return 1
    (rel, fn), a = next(iter(arts.items()))
    WR._reset_stock([rel])
    manifest = {"sources": {}, "rust_objects": {}}
    outd = os.path.join(HERE, "readers")
    open(os.path.join(outd, f"{a['key']}.rs"), "w").write(a["rust_obj"])
    WZ._add_entry(manifest, rel, fn, a)
    mpath = os.path.join(outd, "negctl_manifest.json")
    json.dump(manifest, open(mpath, "w"), indent=1)
    W.MANIFEST = mpath
    if W.cmd_apply() != 0:
        return 1
    failed = WR._compile_check([rel])
    WR._reset_stock([rel])
    WZ._scrub_realized()
    if failed:
        print(f"✓ negctl: sabotaged offset guard FAILED the build for {rel} "
              f"(fail-closed, as pre-registered)")
        return 0
    print("✗ negctl: build PASSED with a wrong offset — guard is NOT load-bearing")
    return 1


def cmd_batch():
    arts = build_artifacts()
    if not arts:
        return 1
    return WZ.cmd_batch(lift=True, extra=arts)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return cmd_batch()
    if len(sys.argv) >= 2 and sys.argv[1] == "negctl":
        return cmd_negctl()
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, rel, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    return {"parse": cmd_parse, "emit": cmd_emit}[cmd](rel, fn)


if __name__ == "__main__":
    raise SystemExit(main())

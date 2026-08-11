"""Model->real realization for efftrace candidates (dream/realize/realize.py).

A sweep-verified efftrace candidate's logic lives in a closed helper vocabulary
over a cell model whose indices were derived from the real struct fields, so it
transpiles deterministically to a real-struct function — which is then re-gated
by the SAME differential the model passed (zero-trust transpiler). Pins:
  * the transpile emits a real-signature fn with the verified value-logic;
  * the realized fn passes the full efftrace differential (MATCH);
  * the differential is LOAD-BEARING over the realized output — a corrupted
    store must DIVERGE (transpiler bugs cannot certify);
  * out-of-vocabulary bodies are REFUSED, not mis-translated.
Needs cc + rustc + $KSRC + the banked candidate; skipped otherwise.
"""
import importlib.util
import os
import re
import shutil

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
_CAND = os.path.join(_HERE, "..", "firstrun", "verified",
                     "efftrace_block__bdev.c_bdev_block_writes.rs")

_R = None
try:
    _spec = importlib.util.spec_from_file_location(
        "realize_t", os.path.join(_HERE, "..", "realize", "realize.py"))
    _R = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_R)
except Exception:
    _R = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R
         and os.path.isdir(_KSRC) and os.path.exists(_CAND)),
    reason="needs cc + rustc + $KSRC + the bdev_block_writes verified candidate")

_FILE, _FN = "block/bdev.c", "bdev_block_writes"


@pytest.fixture(scope="module")
def realized():
    rec, prep, tr = _R.realize(_FILE, _FN)
    return rec, prep, tr


def test_transpile_shape(realized):
    _, _, tr = realized
    src = tr["fn_src"]
    # real signature: no_mangle extern "C", mirror-typed pointer param
    assert f'extern "C" fn {_FN}_rs(' in src
    assert "*mut Block_deviceMirror" in src
    # the verified decrement survives, retargeted at the real field
    assert re.search(r"\(\*bdev\)\.bd_writers\s*=", src)
    # arithmetic is emitted in EXPLICIT wrapping form (A4): the decrement
    # survives as wrapping_sub, not a bare `-` (which would panic on overflow
    # with checks on, i.e. hang a freestanding kernel object).
    flat = src.replace("\n", " ")
    assert "wrapping_sub" in flat and "bd_writers" in flat
    # no cell-model residue
    assert "S[" not in src and "set_field" not in src


def test_realized_passes_the_same_differential(realized):
    rec, prep, tr = realized
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_differential_is_load_bearing_over_realized_output(realized):
    # corrupt the realized store by +1 (a transpiler-bug surrogate): the SAME
    # differential must now DIVERGE — certification never rests on the
    # transpiler being correct.
    rec, prep, tr = realized
    m = re.search(r"= (\(.*\)) as (\w+);", tr["fn_src"])
    assert m, "expected a store to sabotage"
    sab = dict(tr)
    sab["fn_src"] = tr["fn_src"].replace(
        m.group(0), f"= ({m.group(1)} + 1) as {m.group(2)};", 1)
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab))
    assert r["verdict"].startswith("DIVERGE"), r


def test_out_of_vocabulary_is_refused(realized):
    rec, _, _ = realized
    # NOTE: `return 3;` moved IN-vocabulary in v2 (label-break-value); the
    # still-refused return form is the bare `return;` (no i64 to yield).
    for bad in ("unsafe { S[0] }\n0", "return;\n0",
                "set_field(F0_BD_WRITERS, a0 + 1, 1);\n0"):
        with pytest.raises(_R.Refused):
            _R.transpile(rec, bad)


# ---------------------------------------------------------------------------
# v2: early-return realization (the 79-fn refusal class)
# ---------------------------------------------------------------------------

_ER_FILE, _ER_FN = "block/blk-rq-qos.c", "rq_depth_calc_max_depth"


def test_v2_early_return_realizes_and_passes_differential():
    # v1 refused `return` outright (forbidden_token:return, 79 fns): the body
    # is wrapped in `let __r: i64 = { .. }` and a mid-body return would escape
    # the single cast site with the wrong type. v2 lowers `return X;` to
    # label-break-value (`break 'cgir (X);`) so the value still flows through
    # the cast — and the realized fn is re-gated by the SAME differential.
    rec, prep, tr = _R.realize(_ER_FILE, _ER_FN)
    assert "'cgir: {" in tr["fn_src"] and "break 'cgir" in tr["fn_src"]
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_v2_no_return_emission_is_unchanged(realized):
    # fns without returns must emit EXACTLY as v1 did — the 480 verified
    # census results stay byte-stable.
    _, _, tr = realized
    assert "'cgir" not in tr["fn_src"]


# ---------------------------------------------------------------------------
# v2: field-helper DIALECT canonicalization (the `non_const_field_base` class,
# 37 fns). The model arena's helper is `S[base + slot]` — commutative — so the
# synthesizer emitted BOTH argument orders and both verified. The transpiler
# canonicalizes the measured dialects (swapped args, `as`-casts,
# `.try_into().unwrap()` decoration, globals written through the field helper,
# literal field bases resolved via the model's own F-const decls) and stays
# fail-closed on everything else.
# ---------------------------------------------------------------------------

def test_dialect_swapped_args_realizes_and_passes_differential():
    # `set_field(a0 as usize, F0___RB_PARENT_COLOR as i64, ...)` — handle
    # first, field-const second. Must transpile to the SAME real-struct store
    # as the canonical order and re-certify through the differential.
    rec, prep, tr = _R.realize("lib/rbtree.c", "rb_set_black")
    assert "__rb_parent_color" in tr["fn_src"]
    assert "set_field" not in tr["fn_src"]
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_dialect_swap_diverges_on_wrong_field():
    # negative control ON THE NEW PATH: realize a swapped-dialect fn with two
    # fields (update_load_set writes .weight=a1 and .inv_weight=0), then
    # corrupt the canonicalized output by swapping the VALUES routed to the
    # two fields — type-correct wrong-cell routing, the exact failure a bad
    # dialect canonicalization would produce. The same differential must
    # DIVERGE (the gate sees through the dialect).
    rec, prep, tr = _R.realize("kernel/sched/fair.c", "update_load_set")
    src = tr["fn_src"]
    mw = re.search(r"\.weight = \((.+?)\) as (\w+);", src)
    mi = re.search(r"\.inv_weight = \((.+?)\) as (\w+);", src)
    assert mw and mi, src
    sab = dict(tr)
    sab["fn_src"] = (src
        .replace(mw.group(0), f".weight = ({mi.group(1)}) as {mw.group(2)};")
        .replace(mi.group(0), f".inv_weight = ({mw.group(1)}) as {mi.group(2)};"))
    assert sab["fn_src"] != src
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab))
    assert r["verdict"].startswith("DIVERGE"), r


def test_dialect_try_into_decoration(realized):
    # `F0_X.try_into().unwrap()` on the field const (the stmmac family shape)
    rec, _, _ = realized
    tr = _R.transpile(
        rec, "set_field(a0 as usize, F0_BD_WRITERS.try_into().unwrap(), 1);\n0")
    assert re.search(r"\(\*bdev\)\.bd_writers\s*=", tr["fn_src"])


def test_dialect_global_via_field_helper():
    # `set_field(G_X, 0, v)` writes global cell G_X+0 — reroutes to set_g.
    rec, prep, tr = _R.realize("drivers/clocksource/nomadik-mtu.c",
                               "nmdk_clkevt_set_oneshot")
    assert "GV_clkevt_periodic" in tr["fn_src"] or "GV_" in tr["fn_src"]
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_dialect_literal_field_base():
    # `set_field(0, a0, ...)` — the model inlined the F-const's VALUE. Resolved
    # against the model's own `const F0_X: usize = 0;` decls (unambiguous: cell
    # indexes are unique in the flat vector). acquire_probe_locked is the one
    # census instance.
    rec, prep, tr = _R.realize("kernel/trace/ftrace.c", "acquire_probe_locked")
    assert "set_field" not in tr["fn_src"] and "field(" not in tr["fn_src"]
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_dialect_stays_fail_closed(realized):
    rec, _, _ = realized
    for bad in (
        # both args field consts — genuinely ambiguous, refuse
        "set_field(F0_BD_WRITERS, F0_BD_WRITERS, 1);\n0",
        # neither arg a recognizable const/slot — computed base, refuse
        "set_field(a0 + 1, F0_BD_WRITERS, 1);\n0",
        # literal base with no fconsts context — refuse, never guess
        "set_field(0, a0, 1);\n0",
        # swapped dialect but the slot is not the param's own handle
        "set_field(a5 as usize, F0_BD_WRITERS as i64, 1);\n0",
    ):
        with pytest.raises(_R.Refused):
            _R.transpile(rec, bad)


# ---------------------------------------------------------------------------
# slot-handle ALIASING (the `slot_not_own_param` class, 23 fns). The
# synthesizer binds a node handle to a readable local — `let rqd = a0;` — and
# uses the LOCAL as the field slot: `field(F0_X, rqd)`. That is trivial
# aliasing (immutable `let` => NAME === aK), not a foreign access. The
# transpiler resolves the alias, then STRIPS the consumed binding (it
# references the unbound handle aK and usually collides with the real pointer
# param name). 22 of the 23 are this shape; 1 (`a0 + F0_RDESC_SIZE`) is genuine
# slot arithmetic and stays refused by name. Every genuine foreign/scalar slot
# still fails the own-slot check (fail-closed).
# ---------------------------------------------------------------------------

def test_xslot_alias_realizes_and_passes_differential():
    # 1-node: `let rqd = a0;` then all field ops on `rqd`. The consumed binding
    # is stripped (no `let rqd = a0;`, no bare node handle survives) and the
    # real pointer param is dereferenced directly.
    rec, prep, tr = _R.realize("block/blk-rq-qos.c", "rq_depth_scale_up")
    src = tr["fn_src"]
    assert "let rqd = a0" not in src
    assert re.search(r"\(\*rqd\)\.\w+", src)          # derefs the real param
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_xslot_alias_two_node_realizes():
    # 2 node params (a0, a2); the alias `region = a2` resolves to node 1's OWN
    # slot — cross-NODE misrouting is impossible here because the two mirrors
    # are distinct #[repr(C)] types (a wrong-node deref would not compile).
    rec, prep, tr = _R.realize("drivers/mtd/nand/spi/dosilicon.c",
                               "ds35xx_ooblayout_free")
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_xslot_differential_is_load_bearing():
    # negative control ON THE RESOLVED OUTPUT: realize an alias fn, then corrupt
    # one alias-resolved store by +1 (compile-clean). The SAME differential must
    # DIVERGE — the alias resolution produced live code, not an inert body.
    rec, prep, tr = _R.realize("mm/percpu.c", "pcpu_block_update")
    src = tr["fn_src"]
    m = re.search(r"= \((.+?)\) as (\w+);", src)
    assert m, src
    sab = dict(tr)
    sab["fn_src"] = src.replace(m.group(0), f"= (({m.group(1)}) + 1) as {m.group(2)};", 1)
    assert sab["fn_src"] != src
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab))
    assert r["verdict"].startswith("DIVERGE"), r


def test_xslot_arithmetic_stays_refused():
    # the 1 genuine slot-arithmetic fn: `field(F0_RDESC_SIZE, a0 + ...)` — an
    # address computation on the handle, never realized.
    with pytest.raises(_R.Refused) as e:
        _R.realize("drivers/hid/bpf/progs/Huion__KeydialK20-Bluetooth.bpf.c",
                   "probe")
    assert "slot_handle_arithmetic" in str(e.value)


def test_xslot_alias_fail_closed(realized):
    rec, _, _ = realized     # bdev_block_writes: 1 node (a0), field BD_WRITERS
    for bad in (
        # `let mut` alias — reassignable, never resolved
        "let mut h = a0;\nset_field(F0_BD_WRITERS, h, 1);\n0",
        # shadowed alias (bound twice) — ambiguous, never resolved
        "let h = a0;\nlet h = a0;\nset_field(F0_BD_WRITERS, h, 1);\n0",
        # alias used as a VALUE (not only as a slot) — handle-as-value
        "let h = a0;\nlet keep = h;\nset_field(F0_BD_WRITERS, h, 1);\nkeep",
    ):
        with pytest.raises(_R.Refused):
            _R.transpile(rec, bad)


def test_xslot_alias_resolves_only_to_own_slot(realized):
    # a resolved alias that points at a FOREIGN slot (not the F-const's own
    # node) is still refused — resolution never launders a cross-node access.
    rec, _, _ = realized
    with pytest.raises(_R.Refused):
        # a7 is no param's node slot -> resolves to a7 != a0 -> refused
        _R.transpile(rec, "let h = a7;\nset_field(F0_BD_WRITERS, h, 1);\n0")

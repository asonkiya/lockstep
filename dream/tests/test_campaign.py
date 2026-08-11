"""Pins for the campaign playbook + runner (dream/campaign/).

Fixture-based — no gates run here. Pins: tier classification, prereg
freeze-before-run (incl. the refuse-to-overwrite rule), grading
(SUCCESS/PARTIAL/FAIL + bar accounting), disposition diffing, packet
emission from a ledger row, and the dry-run plan shape.
"""
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _load(name, *rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, *rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PB = _load("playbook_t", "dream", "campaign", "playbook.py")
LG = _load("ledger_t", "dream", "ratchet", "ledger.py")


# ---------------- tier classification ----------------

def test_research_classes_are_research():
    for cls in ("multi_member_ops", "multi_head_iteration",
                "plain_iteration_with_mutation", "cross_list_move"):
        assert LG.classify_tier("realize/containers-t3", cls) == "research"


def test_extension_classes_are_agent():
    assert LG.classify_tier("realize/efftrace", "slot_not_own_param") == "agent"
    assert LG.classify_tier("realize/efftrace", "BUILD_FAIL_RS") == "agent"
    assert LG.classify_tier("weave/containers", "residual:if") == "agent"


def test_committed_ledger_rows_all_tiered():
    led = json.load(open(os.path.join(REPO, "dream", "ratchet", "ledger.json")))
    assert led["rows"], "empty ledger"
    for r in led["rows"]:
        assert r.get("tier") in ("auto", "agent", "research"), r


# ---------------- prereg ----------------

def test_prereg_written_and_frozen(tmp_path):
    bars = [{"id": "a", "bar": "must hold"}]
    p = PB.prereg_write(str(tmp_path), "t", bars, denominator=7,
                        sabotages=["wrong_x must DIVERGE"])
    text = open(p).read()
    assert "Frozen denominator:** 7" in text and "wrong_x" in text
    with pytest.raises(RuntimeError):        # bars are frozen: no rewrite
        PB.prereg_write(str(tmp_path), "t", bars)


# ---------------- grading ----------------

def test_grade_success_partial_fail():
    bars = [{"id": "a", "bar": "x"}, {"id": "b", "bar": "y"}]
    ok = {"a": {"pass": True, "measured": "1"},
          "b": {"pass": True, "measured": "2"}}
    assert PB.grade(bars, ok)["overall"] == "SUCCESS"
    half = {"a": {"pass": True, "measured": "1"}}    # b not measured -> fail
    g = PB.grade(bars, half)
    assert g["overall"] == "PARTIAL" and g["passed"] == 1
    assert g["bars"][1]["measured"] == "NOT MEASURED"
    none = {}
    assert PB.grade(bars, none)["overall"] == "FAIL"


# ---------------- disposition diff ----------------

def test_disposition_diff_moves_and_counts():
    before = {"population": 10, "front_accepted": 8, "gate_match": 7,
              "gate_refusals": {"op_count": ["a.c:f", "b.c:g"]}}
    after = {"population": 10, "front_accepted": 9, "gate_match": 9,
             "gate_refusals": {"op_count": ["b.c:g"]}}
    d = PB.disposition_diff(before, after)
    assert d["counts"]["gate_match"]["delta"] == 2
    assert d["moved"]["gate_refusals:op_count"]["gone"] == ["a.c:f"]
    assert not d["moved"]["gate_refusals:op_count"]["new"]


# ---------------- packet emission ----------------

def test_emit_packet_freezes_enumeration(tmp_path, monkeypatch):
    RN = _load("runner_t", "dream", "campaign", "runner.py")
    monkeypatch.setattr(RN, "QUEUE", str(tmp_path))
    row = {"refusal_class": "slot_not_own_param", "stage": "realize/efftrace",
           "tier": "agent", "count": 23, "unlock_estimate": 23,
           "metric": "realized_fns", "fns": ["k1", "k2"]}
    st = {"packets": {}}
    p = RN.emit_packet(row, st)
    j = json.load(open(p.replace(".md", ".json")))
    assert j["frozen_denominator"] == 23 and j["fns"] == ["k1", "k2"]
    assert "prereg_skeleton" in j and j["tier"] == "agent"
    assert "slot_not_own_param" in st["packets"]
    md = open(p).read()
    assert "FROZEN" in md and "- `k1`" in md


# ---------------- dry-run plan shape ----------------

def test_plan_routes_by_tier():
    RN = _load("runner_t2", "dream", "campaign", "runner.py")
    steps = RN.plan(full=False)
    kinds = [k for k, _ in steps]
    assert kinds[0] == "auto"                       # cheap cycle first
    assert "packet" in kinds                        # agent rows -> packets
    assert "flag" in kinds                          # research rows -> flags
    for kind, item in steps:
        if kind == "flag":
            assert item["tier"] == "research"
        if kind == "packet":
            assert item["tier"] == "agent"
            assert item["count"] >= RN.PACKET_MIN   # tail stays refuse-by-name


def test_plan_aggregates_shared_classes_and_tails():
    RN = _load("runner_t4", "dream", "campaign", "runner.py")
    steps = RN.plan(full=False)
    packets = [i for k, i in steps if k == "packet"]
    conds = [p for p in packets if p["refusal_class"] == "conditional_body"]
    assert len(conds) == 1 and conds[0]["count"] == 6   # t2(3) + t3(3) merged
    assert "realize/containers-t2" in conds[0]["stage"]
    assert "realize/containers-t3" in conds[0]["stage"]
    assert any(k == "tail" for k, _ in steps)           # honest tail line


def test_cycles_registry_shape():
    RN = _load("runner_t3", "dream", "campaign", "runner.py")
    for cid, c in RN.CYCLES.items():
        assert c["cost_usd"] == 0.0                 # deterministic re-runs
        assert callable(c["prereg"]) and callable(c["execute"])

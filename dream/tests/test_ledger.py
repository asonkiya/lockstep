"""The refusal ledger (generalization slice B) — aggregation pins.

The ledger reads PERSISTED artifacts only (measure-once) and ranks refusal
classes by functions-unlocked. Pins: per-fn named aggregation across front and
gate maps, the efftrace census.jsonl "result"-key dialect, the measured
weave-eligibility fraction applied to presence-stage estimates (never to
realize-stage ones), ranking, graceful degradation when a source is absent,
and the honest tail line.
"""
import importlib.util
import json
import os

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "ledger_t", os.path.join(_HERE, "..", "ratchet", "ledger.py"))
LG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LG)


def _repo(tmp_path, with_efftrace=True, with_cweave=True):
    (tmp_path / "dream" / "realize").mkdir(parents=True)
    (tmp_path / "dream" / "ratchet").mkdir(parents=True)
    json.dump({"population": 10, "front_accepted": 7,
               "front_refusals": {"multi_member_ops": ["a.c:f1", "b.c:f2"],
                                  "conditional_body": ["c.c:f3"]},
               "gate_match": 6,
               "gate_refusals": {"coverage": ["d.c:f4"]},
               "provenance": "test"},
              open(tmp_path / "dream" / "realize" / "container_census_t2.json", "w"))
    if with_efftrace:
        with open(tmp_path / "dream" / "realize" / "census.jsonl", "w") as f:
            f.write(json.dumps({"key": "x.c:g1", "result": "MATCH"}) + "\n")
            f.write(json.dumps({"key": "x.c:g2",
                                "result": "REFUSED:non_const_field_base"}) + "\n")
            f.write(json.dumps({"key": "x.c:g3",
                                "result": "REFUSED:non_const_field_base"}) + "\n")
            f.write(json.dumps({"key": "x.c:g4", "result": "BUILD_FAIL_RS"}) + "\n")
    if with_cweave:
        json.dump({"total_verified": 100,
                   "weave_eligible": [{} for _ in range(20)],
                   "residual_leaders": {"someresidual": 30}},
                  open(tmp_path / "dream" / "ratchet" / "cweave_denominator.json", "w"))
    return str(tmp_path)


def _row(led, cls):
    return next(r for r in led["rows"] if r["refusal_class"] == cls)


def test_collect_all_sources(tmp_path):
    led = LG.collect(_repo(tmp_path))
    r = _row(led, "multi_member_ops")
    assert r["count"] == 2 and r["fns"] == ["a.c:f1", "b.c:f2"]
    assert r["metric"] == "realized_fns" and r["unlock_estimate"] == 2
    assert _row(led, "coverage")["stage"] == "realize/containers-t2"
    eff = _row(led, "non_const_field_base")
    assert eff["count"] == 2 and eff["stage"] == "realize/efftrace"


def test_presence_stage_uses_measured_fraction(tmp_path):
    led = LG.collect(_repo(tmp_path))
    assert led["eligibility_fraction"] == 0.2
    w = _row(led, "residual:someresidual")
    assert w["metric"] == "present_fns"
    assert w["count"] == 30 and w["unlock_estimate"] == 6.0   # 30 x 0.2


def test_ranking_is_by_unlock_desc(tmp_path):
    led = LG.collect(_repo(tmp_path))
    ests = [r["unlock_estimate"] for r in led["rows"]]
    assert ests == sorted(ests, reverse=True)


def test_missing_sources_degrade_gracefully(tmp_path):
    led = LG.collect(_repo(tmp_path, with_efftrace=False, with_cweave=False))
    stages = {r["stage"] for r in led["rows"]}
    assert stages == {"realize/containers-t2"}
    assert led["eligibility_fraction"] is None


def test_levers_prints_tail_honestly(tmp_path, capsys):
    led = LG.collect(_repo(tmp_path))
    LG.levers(led, top=2)
    out = capsys.readouterr().out
    assert "tail:" in out and "rule:" in out
    assert out.count("\n") < 12

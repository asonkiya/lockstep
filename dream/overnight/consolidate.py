"""Consolidate the overnight suite's reports into one SUMMARY.md to read over
coffee. Reads each job's summary.json; degrades gracefully if a job didn't run."""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "reports")


def load(p):
    try:
        return json.load(open(os.path.join(R, p)))
    except Exception:
        return None


def main():
    out = ["# Overnight suite — morning report", "",
           f"_generated {time.strftime('%Y-%m-%d %H:%M')}_", ""]

    # headline soundness across everything
    cen = load("recorder_census/summary.json")
    meg = load("soundness_megatest/summary.json")
    total_adv = (cen or {}).get("false_pass_count", 0) * 0  # census fp count is fns, add mutant count below
    fp_total = 0
    adv_total = 0
    if cen:
        fp_total += cen.get("false_pass_count", 0)
    if meg:
        fp_total += meg.get("total_false_passes", 0)
        adv_total += meg.get("total_adversarial_candidates", 0)
    out += ["## Headline: soundness at scale", ""]
    out += [f"- **Total false passes across every job: {fp_total}** "
            f"{'✅ (zero — the core claim held)' if fp_total == 0 else '❌ INVESTIGATE'}"]
    if meg:
        out += [f"- Adversarial candidates hammered at the two oracles: **{adv_total:,}** "
                f"(recorder {meg['recorder']['mutants_tested']:,} mutants + hostdiff "
                f"{meg['hostdiff']['candidates_tested']:,} wrong candidates)"]
    out += [""]

    # 1 recorder census
    out += ["## 1. Recorder census (whole tree)", ""]
    if cen:
        out += [f"- Register functions scanned: **{cen['reg_fns_scanned']:,}**",
                f"- Closed (extractable today): **{cen['closed']} ({cen['coverage_pct']}%)**",
                f"- Refused: {cen['refused']:,} · emit-gap anomalies: {cen.get('harness_anomaly', 0)}",
                f"- False passes: **{cen['false_pass_count']}**", ""]
    else:
        out += ["- (did not run)", ""]

    # 2 refusal taxonomy / next increment
    tax = load("analysis/refusal_taxonomy.json")
    out += ["## 2. Next-increment backlog (ranked by addressable functions)", ""]
    if tax:
        out += ["| increment | fns | % of register mass |", "|---|---|---|"]
        for row in tax.get("next_increment_ranked", []):
            out.append(f"| {row['increment']} | {row['addressable_fns']:,} | {row['pct_of_register_mass']}% |")
        out += ["", f"- Emit-gap (extracted but candidate won't compile — the cheap win): "
                f"**{tax.get('harness_anomaly_emit_gap', 0)}** fns", ""]
    else:
        out += ["- (did not run)", ""]

    # 3 soundness megatest detail
    out += ["## 3. Soundness megatest", ""]
    if meg:
        out += [f"- Recorder: {meg['recorder']['closed_fns']} closed fns × mutants = "
                f"{meg['recorder']['mutants_tested']:,} tested, {meg['recorder']['false_passes']} false",
                f"- Hostdiff: {meg['hostdiff']['pure_fns']} pure leaves, "
                f"{meg['hostdiff']['candidates_tested']:,} wrong candidates, {meg['hostdiff']['false_passes']} false", ""]
    else:
        out += ["- (did not run)", ""]

    # 4 synth grind (progress)
    sg = load("synth_grind/summary.json")
    out += ["## 4. Synth grind — real progress at $0 (local model)", ""]
    if sg:
        out += [f"- Harvested {sg['harvested']} · pure {sg['pure']} · host-reachable {sg['reachable']}",
                f"- **Verified bit-identical: {sg['verified']}** ({sg['by_rung']}) at **${sg['external_cost_usd']}**", ""]
        if sg.get("verified_fns"):
            out += ["  Verified functions:"] + [f"  - `{v['fn']}` ({v['file']}, {v['rung']})"
                                                 for v in sg["verified_fns"][:40]] + [""]
    else:
        out += ["- (did not run)", ""]

    # 5 tree census
    tc = load("analysis/tree_census.json")
    out += ["## 5. Tree-wide purity / tier census", ""]
    if tc:
        out += [f"- Scalar leaves harvested: {tc['harvested_scalar_leaves']}",
                f"- Census tiers: {tc['census_tiers']}",
                f"- Pure fraction (T0/T1 domain): **{tc['pure_fraction']}%** ({tc['pure_count']} fns)", ""]
    else:
        out += ["- (did not run)", ""]

    out += ["---", "_reports in `dream/overnight/reports/*/`; suite log in "
            "`reports/suite.log`._"]
    print("\n".join(out))


if __name__ == "__main__":
    main()

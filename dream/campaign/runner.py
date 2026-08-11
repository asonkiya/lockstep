#!/usr/bin/env python3
"""The campaign runner — the loop that used to be a human.

Reads the refusal ledger, tier-classifies every lever, and:
  * AUTO cycles (its own maintenance registry, below): executes end-to-end —
    prereg frozen BEFORE the run, solo-locked + log-to-file + runtime-capped
    execution (playbook.run_logged), graded against the frozen bars,
    committed BY THE RUNNER with the standard identity.
  * agent levers (realizer/gate extensions): PREPARES the slice — emits a
    packet to dream/campaign/queue/ with the strict enumeration, the frozen
    denominator, a PREREG skeleton and the standard-liturgy brief — ready for
    an agent session that only writes the novel code.
  * research levers (new oracle types): REFUSES to dispatch; flags them for a
    human, per STRATEGY.md §4.

  runner.py --dry-run              # print the full plan, execute nothing
  runner.py                        # ledger-refresh cycle + emit packets
  runner.py --cycle containers-census-t2   # force one named auto cycle
  runner.py --full                 # all auto cycles (census re-passes too)

Budget + wall-clock caps are enforced before each dispatch; state
(dream/campaign/state.json, gitignored) makes re-runs resumable — a completed
run_id is not repeated. Auto cycles here are $0 by construction (deterministic
re-runs of existing gates); the budget field exists for future paid cycles
(harvest sweeps) and is reported in every run's REPORT.md.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
QUEUE = os.path.join(HERE, "queue")
STATE = os.path.join(HERE, "state.json")

_spec = importlib.util.spec_from_file_location(
    "playbook", os.path.join(HERE, "playbook.py"))
PB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PB)

_lspec = importlib.util.spec_from_file_location(
    "ledger_cr", os.path.join(REPO, "dream", "ratchet", "ledger.py"))
LG = importlib.util.module_from_spec(_lspec)
_lspec.loader.exec_module(LG)

PY = PB.PY
BUDGET_CAP_USD = float(os.environ.get("CAMPAIGN_BUDGET_USD", "5.0"))
WALL_CAP_H = float(os.environ.get("CAMPAIGN_CAP_HOURS", "8"))


# ---------------- state ----------------

def state_load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"runs": {}, "spend_usd": 0.0, "packets": {}}


def state_save(st):
    json.dump(st, open(STATE, "w"), indent=1)


# ---------------- the auto-cycle registry (tier-1) ----------------
# Each cycle: id, resource lock, runtime cap, cost estimate ($0: deterministic
# re-runs), prereg(ctx) -> bars frozen BEFORE the run, execute(run_dir) ->
# results keyed by bar id, artifacts() -> repo paths the run may change.

def _census_cycle(tier):
    up = tier.upper()
    jpath = os.path.join(REPO, "dream", "realize",
                         f"container_census_{tier}.json")

    def prereg(ctx):
        before = PB.census_snapshot(jpath) or {}
        ctx["before"] = before
        return [
            {"id": "rc", "bar": "census exits 0 within the runtime cap"},
            {"id": "no-regression",
             "bar": f"gate_match >= frozen baseline ({before.get('gate_match')})"
                    f" and population unchanged ({before.get('population')})"},
            {"id": "no-diverge",
             "bar": "zero DIVERGE-class gate refusals (named refusals only)"},
        ]

    def execute(run_dir, ctx):
        script = os.path.join(REPO, "dream", "container_adt",
                              f"{tier}_census.py")
        with PB.solo_lock("host-gates"):
            r = PB.run_logged([PY, script, "--gate"],
                              os.path.join(run_dir, f"{tier}_census.log"),
                              cap_s=7200)
        after = PB.census_snapshot(jpath) or {}
        before = ctx["before"]
        diverges = [c for c in (after.get("gate_refusals") or {})
                    if "DIVERGE" in c.upper()]
        diff = PB.disposition_diff(before, after)
        return {
            "rc": {"pass": r["rc"] == 0 and not r["timed_out"],
                   "measured": f"rc={r['rc']} in {r['secs']}s"
                               f"{' TIMED OUT' if r['timed_out'] else ''}"},
            "no-regression": {
                "pass": (after.get("gate_match") or -1) >= (before.get("gate_match") or 0)
                        and after.get("population") == before.get("population"),
                "measured": f"gate_match {before.get('gate_match')} -> "
                            f"{after.get('gate_match')}, population "
                            f"{after.get('population')}"},
            "no-diverge": {"pass": not diverges,
                           "measured": f"diverge classes: {diverges or 'none'}"},
        }, f"Disposition diff: {json.dumps(diff['counts'])}\n" + (
            f"Moved fns: {json.dumps(diff['moved'], indent=1)}"
            if diff["moved"] else "No per-fn movement.")

    return {"id": f"containers-census-{tier}", "cost_usd": 0.0,
            "desc": f"{up} container census --gate re-pass (solo, capped)",
            "prereg": prereg, "execute": execute,
            "artifacts": [f"dream/realize/container_census_{tier}.json"]}


def _bank_reverify_cycle():
    rpath = os.path.join(REPO, "dream", "container_adt", "reverify_report.json")

    def prereg(ctx):
        return [
            {"id": "rc", "bar": "reverify exits 0 within the cap"},
            {"id": "bank-clean",
             "bar": "0 fails across the whole bank (post-repair state: 344/344"
                    " clean); ANY fail is a named new finding -> escalate"},
        ]

    def execute(run_dir, ctx):
        with PB.solo_lock("host-gates"):
            r = PB.run_logged(
                [PY, os.path.join(REPO, "dream", "container_adt", "reverify.py")],
                os.path.join(run_dir, "reverify.log"), cap_s=7200)
        rep = PB.census_snapshot(rpath) or {}
        fails = rep.get("fails", [])
        return {
            "rc": {"pass": r["rc"] == 0 and not r["timed_out"],
                   "measured": f"rc={r['rc']} in {r['secs']}s"},
            "bank-clean": {"pass": rep.get("total") and not fails,
                           "measured": f"{rep.get('total')} models, "
                                       f"{len(fails)} fails"
                                       + (f": {[f['fn'] for f in fails][:6]}"
                                          if fails else "")},
        }, ""

    return {"id": "bank-reverify", "cost_usd": 0.0,
            "desc": "whole-bank ADT re-verification (no resynth)",
            "prereg": prereg, "execute": execute, "artifacts": []}


def _ledger_refresh_cycle():
    led_path = os.path.join(REPO, "dream", "ratchet", "ledger.json")

    def prereg(ctx):
        ctx["before"] = PB.census_snapshot(led_path) or {"rows": []}
        return [
            {"id": "regen", "bar": "ledger regenerates from persisted artifacts"
                                   " and every row carries a valid tier tag"},
            {"id": "funnel-consistent",
             "bar": "every funnel constant matches its live-countable source"},
            {"id": "top-lever", "bar": "a top lever is computed and its tier"
                                       " routes it (agent->packet, research->flag)"},
        ]

    def execute(run_dir, ctx):
        led = PB.regen_ledger()
        tiers_ok = all(r.get("tier") in ("auto", "agent", "research")
                       for r in led["rows"])
        checks = PB.funnel_invariants()
        top = led["rows"][0] if led["rows"] else None
        before_top = (ctx["before"]["rows"] or [{}])[0]
        drift = (before_top.get("refusal_class"),
                 before_top.get("count")) != (
                 (top or {}).get("refusal_class"), (top or {}).get("count"))
        body = ("Funnel invariants:\n" +
                "\n".join(f"  - {c['id']}: {'OK' if c['ok'] else 'MISMATCH'}"
                          f" ({c['detail']})" for c in checks) +
                f"\n\nTop lever: {top['refusal_class']} "
                f"({top['count']} fns, tier {top['tier']})"
                f"{' — CHANGED since last ledger' if drift else ' — unchanged'}"
                if top else "empty ledger")
        return {
            "regen": {"pass": bool(led["rows"]) and tiers_ok,
                      "measured": f"{len(led['rows'])} rows, tiers "
                                  f"{'all valid' if tiers_ok else 'INVALID'}"},
            "funnel-consistent": {
                "pass": all(c["ok"] for c in checks),
                "measured": f"{sum(c['ok'] for c in checks)}/{len(checks)} hold"},
            "top-lever": {"pass": top is not None,
                          "measured": f"{top['refusal_class']} tier={top['tier']}"
                                      if top else "none"},
        }, body

    return {"id": "ledger-refresh", "cost_usd": 0.0,
            "desc": "regenerate ledger + verify funnel invariants",
            "prereg": prereg, "execute": execute,
            "artifacts": ["dream/ratchet/ledger.json"]}


CYCLES = {c["id"]: c for c in [
    _ledger_refresh_cycle(),
    _census_cycle("t2"),
    _census_cycle("t3"),
    _bank_reverify_cycle(),
]}
CHEAP = ["ledger-refresh"]                     # default plan
FULL = ["ledger-refresh", "containers-census-t2", "containers-census-t3",
        "bank-reverify"]


# ---------------- tier-2 packet emission ----------------

PACKET_LITURGY = """\
## The liturgy (the harness runs everything but step 3)

1. Enumerate STRICTLY from the fns below; classify by sub-shape; expect the
   census-shrinkage law (2-5x on contact); refuse out-of-scope shapes by name.
2. Fill the PREREG skeleton (frozen ladder, blind bars, sabotage list) BEFORE
   writing code — playbook.prereg_write refuses to overwrite a frozen prereg.
3. Write the novel realizer/gate code, red-green (failing pins first). This is
   the only step the harness cannot do.
4. Negative controls: every sabotage must DIVERGE (or refuse) MEASURED, on a
   compile-clean control — a control that only BUILD_FAILs proves nothing.
5. Re-pass via `runner.py --cycle <census>` (solo-locked, capped, graded).
6. Disposition, funnel, ledger, commit: playbook handles all of it.
"""


def emit_packet(row, st):
    os.makedirs(QUEUE, exist_ok=True)
    slug = re.sub(r"[^a-z0-9_]+", "-", row["refusal_class"].lower())
    base = os.path.join(QUEUE, slug)
    payload = {
        "class": row["refusal_class"], "stage": row["stage"],
        "tier": row["tier"], "count": row["count"],
        "unlock_estimate": row["unlock_estimate"], "metric": row["metric"],
        "frozen_denominator": row["count"],
        "frozen_at": str(datetime.date.today()),
        "fns": row["fns"],
        "prereg_skeleton": {
            "ladder": f"census {row['count']} -> <shapes on contact> -> "
                      f"<in-scope> -> <verified>",
            "blind_bars": ["<n>/<in-scope> MATCH", "zero unexplained diverges",
                           "all prior census totals non-decreasing"],
            "sabotages": ["<class-specific, must DIVERGE, compile-clean>"],
        },
    }
    json.dump(payload, open(base + ".json", "w"), indent=1)
    md = [f"# Slice packet — `{row['refusal_class']}` "
          f"({row['stage']}, tier {row['tier']})", "",
          f"Frozen {payload['frozen_at']}: **{row['count']} fns**, unlock "
          f"estimate {row['unlock_estimate']} {row['metric']}. This packet is "
          f"the slice brief — the enumeration and denominator are FROZEN "
          f"here; the agent session starts at the sub-shape census.", "",
          PACKET_LITURGY, "## The frozen enumeration", ""]
    md += [f"- `{fn}`" for fn in row["fns"]]
    open(base + ".md", "w").write("\n".join(md) + "\n")
    st["packets"][row["refusal_class"]] = {"at": payload["frozen_at"],
                                           "count": row["count"]}
    return base + ".md"


# ---------------- the loop ----------------

PACKET_MIN = int(os.environ.get("CAMPAIGN_PACKET_MIN", "3"))


def plan(full=False, force_cycle=None):
    """Build the run plan: auto cycles + ledger walk. Agent rows are
    AGGREGATED by refusal class across stages (t2+t3 share classes) and only
    classes with count >= PACKET_MIN become packets — the singleton tail
    keeps its honest disposition (refuse-by-name), listed as 'tail'."""
    led = PB.census_snapshot(
        os.path.join(REPO, "dream", "ratchet", "ledger.json")) or {"rows": []}
    cycles = ([force_cycle] if force_cycle
              else FULL if full else CHEAP)
    steps = [("auto", CYCLES[c]) for c in cycles]
    agents = {}
    for row in led["rows"]:
        t = row.get("tier", LG.classify_tier(row["stage"], row["refusal_class"]))
        if t == "research":
            steps.append(("flag", row))
        elif t == "agent":
            a = agents.setdefault(row["refusal_class"], {
                "refusal_class": row["refusal_class"], "tier": "agent",
                "stage": row["stage"], "count": 0, "fns": [],
                "unlock_estimate": 0, "metric": row["metric"]})
            a["count"] += row["count"]
            a["fns"] = sorted(set(a["fns"]) | set(row["fns"]))
            a["unlock_estimate"] += row["unlock_estimate"]
            if row["stage"] not in a["stage"]:
                a["stage"] += f"+{row['stage']}"
    big = sorted(agents.values(), key=lambda r: -r["unlock_estimate"])
    tail = [r for r in big if r["count"] < PACKET_MIN]
    for r in big:
        if r["count"] >= PACKET_MIN:
            steps.append(("packet", r))
    if tail:
        steps.append(("tail", {"classes": len(tail),
                               "fns": sum(r["count"] for r in tail)}))
    return steps


def main(argv):
    dry = "--dry-run" in argv
    full = "--full" in argv
    force = None
    if "--cycle" in argv:
        force = argv[argv.index("--cycle") + 1]
        if force not in CYCLES:
            print(f"unknown cycle {force}; have: {sorted(CYCLES)}")
            return 2
    st = state_load()
    t0 = time.time()
    steps = plan(full, force)

    if dry:
        print("=== campaign plan (dry run) ===")
        for kind, item in steps:
            if kind == "auto":
                print(f"  RUN    {item['id']:<24} {item['desc']} "
                      f"(est ${item['cost_usd']:.2f})")
            elif kind == "packet":
                mark = ("fresh" if item["refusal_class"] not in st["packets"]
                        else f"queued {st['packets'][item['refusal_class']]['at']}")
                print(f"  PACKET {item['refusal_class']:<24} "
                      f"{item['count']} fns [{mark}]")
            elif kind == "tail":
                print(f"  TAIL   {item['classes']} classes / {item['fns']} fns"
                      f" below packet threshold ({PACKET_MIN}) — "
                      f"refuse-by-name stands")
            else:
                print(f"  FLAG   {item['refusal_class']:<24} "
                      f"{item['count']} fns — research (human-driven, "
                      f"STRATEGY §4: runner refuses to dispatch)")
        print(f"budget ${st['spend_usd']:.2f}/{BUDGET_CAP_USD:.2f} spent; "
              f"wall cap {WALL_CAP_H}h")
        return 0

    committed, flagged, packets = [], [], []
    for kind, item in steps:
        if time.time() - t0 > WALL_CAP_H * 3600:
            print("WALL-CLOCK CAP reached — stopping cleanly (state saved)")
            break
        if kind == "auto":
            if st["spend_usd"] + item["cost_usd"] > BUDGET_CAP_USD:
                print(f"BUDGET CAP: skipping {item['id']}")
                continue
            run_id = f"{item['id']}-{datetime.datetime.now():%Y%m%dT%H%M%S}"
            run_dir = PB.new_run_dir(run_id)
            ctx = {}
            bars = item["prereg"](ctx)
            PB.prereg_write(run_dir, item["desc"], bars,
                            notes=f"cycle `{item['id']}`, est cost "
                                  f"${item['cost_usd']:.2f}, run_id {run_id}")
            print(f"[{item['id']}] prereg frozen -> running...")
            results, body = item["execute"](run_dir, ctx)
            graded = PB.grade(bars, results)
            body += (f"\n\nBudget: ${item['cost_usd']:.2f} this run, "
                     f"${st['spend_usd'] + item['cost_usd']:.2f} campaign total"
                     f" (cap ${BUDGET_CAP_USD:.2f}).")
            PB.report_write(run_dir, item["desc"], graded, body)
            st["spend_usd"] += item["cost_usd"]
            st["runs"][run_id] = {"cycle": item["id"],
                                  "overall": graded["overall"]}
            state_save(st)
            rel_run = os.path.relpath(run_dir, REPO)
            h = PB.git_commit(
                [f"{rel_run}/PREREG.md", f"{rel_run}/REPORT.md"]
                + item["artifacts"],
                f"campaign: {item['id']} — {graded['overall']} "
                f"({graded['passed']}/{graded['total']} bars)",
                f"Run {run_id}, graded against the frozen PREREG by "
                f"dream/campaign/runner.py.")
            committed.append((item["id"], graded["overall"], h))
            print(f"[{item['id']}] {graded['overall']} "
                  f"({graded['passed']}/{graded['total']}) "
                  f"commit {h or '(no drift)'}")
            if graded["overall"] == "FAIL":
                print(f"[{item['id']}] FAIL — stopping the loop; "
                      f"a failing maintenance cycle means the world changed "
                      f"under us. Read {run_dir}/REPORT.md")
                break
        elif kind == "packet":
            if item["refusal_class"] in st["packets"]:
                continue
            p = emit_packet(item, st)
            state_save(st)
            packets.append(p)
            print(f"[packet] {item['refusal_class']} -> {p}")
        elif kind == "tail":
            print(f"[tail] {item['classes']} classes / {item['fns']} fns "
                  f"below packet threshold — refuse-by-name stands")
        else:
            flagged.append(item)

    if packets:
        rel = [os.path.relpath(p, REPO) for p in packets]
        rel += [p.replace(".md", ".json") for p in rel]
        h = PB.git_commit(rel, f"campaign: queue {len(packets)} slice "
                          f"packet(s) with frozen enumerations",
                          "Emitted by dream/campaign/runner.py from the "
                          "ledger's agent-tier levers; each packet freezes "
                          "the denominator and enumeration for its slice.")
        print(f"[packets] committed {h}")
    if flagged:
        print("\nresearch levers (human-driven — runner refuses these):")
        for r in flagged:
            print(f"  {r['refusal_class']:<32} {r['count']} fns ({r['stage']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

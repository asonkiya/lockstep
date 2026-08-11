# NEXT — the queued sequence (frozen 2026-08-11, for the next agent)

Written as a handoff: any agent (Opus-class is fine for 1–4) picks these up in
order. Standing rules live in CAMPAIGN.md (tier routing, prereg-before-run,
override-in-writing) and LESSONS.md (operational traps). Funnel today:
~1,130 banked / 877 realized / 107 present / 38 tier-b of 24,194.
HEAD at freeze: ec720df.

## 1. Drain the campaign queue (agent-tier packets)

`dream/campaign/queue/` holds 7 frozen packets, cross-slot
(`slot_not_own_param`, 23 fns) first. Per packet: the packet IS the brief —
the agent writes only the novel realizer/gate code; `playbook.py` runs prereg,
censuses (solo-locked), disposition accounting, funnel/ledger, commit.
Expected: ~+50–70 realized total, $0. After each packet: `runner.py` re-ranks.

## 2. Config-coverage campaign (Summit 2.3 — the presence multiplier)

Presence is throttled by defconfig linking (289 realized containers → 44
eligible). Build 2–3 additional kbuild volumes (driver-heavy config; an
allyesconfig-buildable subset), measure per-volume eligibility of the realized
pool (cweave_census.py with WEAVE_VOL), re-freeze, batch-weave per volume.
Report per-volume AND union presence, never conflated. This also carries the
first in-kernel use of the guard-aware emission path (its negctl requirement
is already written — guard-drop must be caught; undetected green boot = gate
hole, stop and report).

## 3. Harvest sweep cycle 2 (Summit 2.1)

overnight.py over the standing subsystems with the coverage-gated pipeline +
the 3.1 routing recoveries (strict +375 / diag +807 newly-bounded fns —
routing only; they must pass harvest + zero-trust verification to bank).
Ladder local-first; ~$2 Haiku tail. If the 3080 box is still lent, use
`infra/gpu3080/push_3080.sh` (needs user for ssh address). One census-fix
cycle on the top refusal class per the measured +10–40 fns/agent-hour lever.

## 4. Weave re-freeze + batch (after 1–3 land)

Runner auto-tier: re-freeze the eligible denominator (expect growth from new
realized + new volumes), batch only if the set changed, blind bars first,
negctls per emission path. Update funnel present/tier-b with provenance.

## 5. Summit 3.2 proof rung — the ceiling-breaker (HAND-DRIVEN, top model)

The 54% unbounded tail needs the in-kernel state-differential-under-workload
oracle. Scope for the first rung: synthetic subject only (proof.py pattern) —
same deterministic workload over C and Rust builds, memory-state snapshots at
seam boundaries compared as effect streams; correct→PASS, sabotaged
state→REJECT, plus a vacuity check (a control the oracle can't kill doesn't
count). Prereg with blind bars before the real-tree pilot. This is research —
reserve the strongest model; do not let the runner auto-dispatch it.

## 6. Housekeeping (cheap, any agent)

- playbook doc note: `run_logged` cap_s silently kills long greps (measured in
  3.1's first (c) attempt) — document; prefer single-pass alternation greps.
- Promote bank-reverify to a scheduled runner auto cycle.
- 8% decision-gate bookkeeping: the pre-registered Summit-2 landing check
  (~1,950 verified) — keep the marginal-yield-per-cycle numbers as sweeps run.

## Budget note

Items 1–4 are packet/runner-driven and cheap in agent tokens (orchestration
economy: terse I/O, measure-once). Item 5 is where model quality matters.
Campaign $ ledger continues; $40 hard stop without user sign-off stands.

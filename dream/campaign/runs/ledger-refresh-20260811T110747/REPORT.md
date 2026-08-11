# REPORT — regenerate ledger + verify funnel invariants

Graded 2026-08-11T11:07:47 against the frozen PREREG. **SUCCESS** (3/3 bars).

| bar | pre-registered | measured | verdict |
|---|---|---|---|
| regen | ledger regenerates from persisted artifacts and every row carries a valid tier tag | 33 rows, tiers all valid | PASS |
| funnel-consistent | every funnel constant matches its live-countable source | 3/3 hold | PASS |
| top-lever | a top lever is computed and its tier routes it (agent->packet, research->flag) | slot_not_own_param tier=agent | PASS |

Funnel invariants:
  - funnel.t2==census.t2: OK (funnel 180 vs census 180)
  - funnel.t3==census.t3: OK (funnel 109 vs census 109)
  - funnel.efftrace==census.jsonl: OK (funnel 583 vs census.jsonl 583)

Top lever: slot_not_own_param (23 fns, tier agent) — unchanged

Budget: $0.00 this run, $0.00 campaign total (cap $5.00).

# CAMPAIGN — the liturgy, and who runs which part of it

Written 2026-08-11 (the generalization slice, items #3+#4). The class-by-class
sessions of 2026-08-07..09 converged on one fixed ritual; this package is that
ritual as code, so kernel-scale passes stop being hand-written briefs.

## The tier rule (who does what)

Every ledger row carries a `tier` (`ratchet/ledger.py:classify_tier`):

- **auto** — deterministic loops that already exist as code: census
  re-passes, bank re-verification, ledger + funnel refresh, (future: weave
  batches against frozen bars, harvest sweeps). `runner.py` executes these
  END-TO-END: prereg frozen before the run, solo-locked capped execution,
  graded against the frozen bars, committed by the runner itself.
- **agent** — realizer/gate EXTENSIONS (a new dialect, a new emission shape).
  The runner cannot write novel code; it PREPARES the slice instead: a packet
  in `queue/` with the strict enumeration, the frozen denominator, a PREREG
  skeleton and the liturgy checklist. An agent session takes the packet,
  writes ONLY the novel code, and closes out through the same harness.
- **research** — new oracle TYPES (multi-member/multi-head arenas,
  cursor-over-mutated-list semantics, Summit-3 state differentials). The
  runner REFUSES to dispatch these (STRATEGY.md §4): a human designs the
  oracle and reads the negative controls.

## Standing rules (inherited, not new)

- **Next slice = the ledger's top lever**, unless a human overrides with a
  written reason recorded in STRATEGY.md §4. (This package itself was such an
  override: playbook+runner over `slot_not_own_param`, justification — every
  subsequent lever gets cheaper.)
- **Prereg before run.** `playbook.prereg_write` refuses to overwrite a
  frozen prereg. Bars written after the run are not bars.
- **Two-partials rule.** A second PARTIAL on the same lever means stop and
  re-plan; a third try without a design change is rationalization.
- **Honest accounting invariants.** Denominators travel with every headline;
  realized / present / tier-b are different currencies, never summed; a
  batch against an unchanged frozen denominator is SKIPPED (it proves
  nothing); every refusal is named; negative controls must be compile-clean
  (a control that only BUILD_FAILs proves nothing) and must be SEEN to fail.
- **A failing auto cycle stops the loop.** Maintenance cycles re-measure
  invariants that were true at last commit; a FAIL means the world changed
  under us — that is a finding to read, not a step to retry.

## Run management (the operational lessons, enforced)

`playbook.run_logged` owns every subprocess synchronously: caffeinate, logs
to files (never tail pipes), hard runtime caps. `playbook.solo_lock` makes
shared-resource runs (host gate compilers, docker volumes) exclusive by
construction. The stranded-watcher failure mode — a worker parked on a
detached run that can never wake it, four occurrences in one week — is
impossible here: nothing the harness starts is ever detached.

## Artifacts: committed vs ignored

Following the funnel.json / census.jsonl precedent — artifacts-with-provenance
are committed, bulk regenerables are ignored:

- **committed:** `runs/<id>/PREREG.md` + `runs/<id>/REPORT.md` (the frozen
  bars and the graded outcome ARE the record), `queue/*.{md,json}` packets
  (frozen enumerations), `ratchet/ledger.json` when it drifts.
- **gitignored:** `runs/**/*.log` (bulk), `state.json` (mutable runner
  state), `.locks/`.

## Budget

Every run's REPORT.md carries its cost and the campaign running total.
Auto cycles are $0 by construction today; the budget machinery
(`CAMPAIGN_BUDGET_USD`, default $5) exists for future paid cycles (harvest
sweeps with the Haiku tail). The $40 project hard stop still applies above
this cap.

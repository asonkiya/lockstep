# HANDOFF — lockstep dream, safe-Rust campaign (2026-08-05)

Written for a fresh operator (Opus) taking over cold. Read this top to bottom
before touching anything. Companion docs, in read order:
`STRATEGY.md` (the frame + odds), `RESEARCH-SAFE-RUST.md` (the amendments
queue this doc executes), `realize/REALIZE.md` (the model→real mechanism),
`ratchet/WEAVE-READERS.md` (the weave mechanism). Memory index has
[[lockstep-status]] and [[lockstep-strategy]].

────────────────────────────────────────────────────────────────────────────
## 0. ENVIRONMENT — exact, non-negotiable

- **Python**: `/Users/aryaman/Documents/Programming/llm-semantic-compilers/.venv/bin/python3`
  (NOT bare python3). All scripts import via `importlib` from sibling dirs.
- **KSRC** (pristine kernel source, read-only reference): env var, default
  `/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux`. Linux 7.2-rc4. Every script
  reads `KSRC=... `. If that job dir is gone, KSRC must be re-pointed to a
  clean Linux 7.2-rc4 checkout and `_reset_stock` sources come from it.
- **Docker volumes** (the build trees — these ARE the state, not in git):
  - `cgir-kbuild` — the pinned MINIMAL arm64 config (original readers base).
  - `cgir-kbuild-defconfig` — arm64 `make defconfig`, THE ACTIVE VOLUME for all
    realize/lift weaves. Select it with `WEAVE_VOL=cgir-kbuild-defconfig`.
- **Docker image**: `cgir-kernel-gate` (has aarch64 cross-toolchain + rustc).
- **Host tools**: `cc`, `rustc` on PATH (host aarch64 = kernel target arch;
  host differentials compile natively). ollama/c2rust exist but NOT needed for
  the realize/lift path (transpile is deterministic, $0).
- **Commit identity** (ALWAYS, lockstep is asonkiya's):
  `git -c user.name="Aryaman Sonkiya" -c user.email="asonkiya@unc.edu" commit`
  with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- **API key**: throwaway in `llm-semantic-compilers/.env` (gitignored). NEVER
  commit it. The safe-Rust path needs NO model calls anyway.
- **NEVER commit generated artifacts**: `census.jsonl`, `fn_fields.json`,
  `lift_verified.json`, `audit_preview.json`, `readers/` dir, `out/` dir,
  `batch_manifest.json`. `weave_eligible.json` IS committed (it's a manifest).
  Add the three loose json caches to `dream/realize/.gitignore` before your
  first commit (currently only `census.jsonl` is there).
- **Account creation / cloud provisioning / Oracle grinder = OFF LIMITS**
  (cannot create accounts). Everything here runs locally on this host.

────────────────────────────────────────────────────────────────────────────
## 1. WHERE THINGS STAND (as of commit 7bba005; A1+A2 done)

The pipeline: sweep-verified C→Rust candidates (host differential, 0 false
passes) → `realize.py` transpiles efftrace cell-models to real-struct fns →
`weave_realized.py` weaves them into a booting kernel → the safety LIFT turns
them into machine-checked-safe cores.

**Banked verified translations** (`firstrun/verified/*.rs`): 104 reader, 635
efftrace, 344 container, 41 alloc = ~1,124.

**Realize census** (`realize/census.jsonl`, efftrace only): 480/635 MATCH
(realized to real-struct + re-verified by the same differential, 0 diverges),
144 refused, 11 build-tail. Refusals itemized: 79 early-`return`, 36
non-const field base, 18 cross-slot, ~11 misc.

**Woven into the booting `cgir-kbuild-defconfig` vmlinux RIGHT NOW**: 64 Rust
fns = 54 realized + 10 readers, boot-digest green. After A1 (sound), **31 of
the 54 realized carry tier-(b) machine-checked safe cores** (field-granular:
`#![forbid(unsafe_code)]` core taking `&mut TY` per field + a boundary of
per-field `&mut (*p).field`); 23 are tier-(a) (20 concurrency-audit-demoted —
fields touched locklessly somewhere in the tree — + 3 multi-node copiers).
7 of the 10 readers are ALSO tier-(b) (A3 lift, LIFT_READERS=1); 3 audit-demoted.

**Safety-tier dashboard**: 38 tier-b + 26 tier-a = 64 (31 realized + 7 reader
safe cores, all field-granular + structdiff/differential-MATCH + boot green;
weave with LIFT_READERS=1). **A2 metrics**: 32%
safe-logic (139/434 translated LOC in forbid cores), 214 raw-derefs all in
field-scoped boundaries / 0 in cores.

**Reach ceiling for efftrace safe-lift**: of the 480 realized MATCH, 317 are
single-node liftable, **199 (63%) pass the concurrency audit = tier-b
eligible** (the honest safe-Rust ceiling for this class). Grow the woven
tier-b count by weaving more of those 199 (config-permitting) + lifting
tier-a classes.

**Last verify**: `weave_realized.py batch --lift` booted green (commit
ef8578a, A1). Test suite green (`pytest dream/tests/` with `KSRC=...`);
test_lift.py 10, test_metrics.py 4, test_realize.py 4.

────────────────────────────────────────────────────────────────────────────
## 2. A1 — DONE (commit ef8578a). Kept here as the design record.

**STATUS: A1 is implemented, tested, woven, and booted.** Field-granular
boundary + per-field concurrency audit are live. Sound tier-b count = 31 (was
an over-claimed 51). Skip to §3 for the remaining queue. The rest of this
section is the design record / rationale (still accurate).

The boundary is `core(&mut (*p).field1, ...)` — field-scoped, no offset math
needed (the layout-guarded padded mirror places each field correctly, so
`(*p).field` resolves to the right bytes with a field-scoped borrow). The
audit is `realize.field_audit(fields)` (fixed-marker grep, self-proves
non-vacuous). `realize.lift_gate(tr)` combines structural + audit. Offline
census: `dream/realize/audit_scan.py`. DO NOT reintroduce the mega-regex with
`\w` inside a `[...]` bracket class — it silently matches nothing (vacuous
zero); this cost real debugging time.

--- original A1 spec below (design record) ---
## THE ONE THING THAT MUST HAPPEN FIRST — A1 (soundness)

The research pass (RESEARCH-SAFE-RUST.md) found the shipped tier-(b) boundary
OVER-CLAIMS. This is the top priority and blocks growing the lifted set.

**The bug**: `&mut Mirror` asserts LLVM `noalias` over EVERY byte of Mirror
for the whole call. Our padded mirrors span offset 0 → last-accessed field, so
the `[u8; N]` PADDING covers OTHER REAL kernel fields. A concurrent access to
any padding-covered field during the call is UB — even though our code never
touches it. Also: kernel-"benign" races (`data_race()`/KCSAN-tolerated) are
still Rust UB. UB is not boot-detectable, so the green boot does NOT clear it.

**The fix (A1)** — field-granular borrows + per-field concurrency audit:
1. In `realize.py`, change the safe form (`fn_src_safe`, built in `transpile`
   when `liftable`) so the boundary derefs EACH accessed field individually
   and the safe core takes per-field `&mut i32`-style refs, NOT `&mut Mirror`.
   Exclusivity then covers exactly the bytes the C body accessed (the
   Rust-for-Linux `Guard` precedent) and the padding hazard vanishes
   structurally. The core stays `#![forbid(unsafe_code)]`.
   - CAUTION: two `&mut` to two fields of the SAME struct in one call is fine
     ONLY if the fields are disjoint (they are — distinct offsets); still,
     construct them from a single `p` without creating an intermediate
     `&mut *p` (that would re-assert whole-struct exclusivity). Build each ref
     as `&mut *((p as *mut u8).add(OFF) as *mut TY)` inside the one boundary
     `unsafe` block. Offsets come from the in-kernel probe (weave_realized
     already has them; realize.py's HOST differential uses the reduced arena so
     for the host re-gate you can keep field refs into the arena struct).
2. Per-field concurrency audit (fail-closed): for each accessed field, grep the
   kernel tree for `READ_ONCE`/`WRITE_ONCE`/`data_race` on `->fieldname`. Any
   hit → the field is touched locklessly somewhere → DEMOTE that fn to tier-(a)
   (mirror/unsafe, no safety claim) and tally it. This is a NAME-LEVEL
   conservative over-approximation (a same-named field on a different struct
   triggers demotion — that's the safe direction).
   - A measurement harness already exists: `/Users/aryaman/.claude/jobs/
     feda8087/tmp/audit_preview2.py` (chunked grep + non-vacuous error check;
     writes `realize/audit_preview.json`). **Preliminary result: see §5.** Fold
     it into `realize.py` as `audit_field_safe(field) -> bool` and gate the
     lift on it. IMPORTANT: the FIRST audit run reported "0 racy fields" but
     that grep was VACUOUS (a manual test found `READ_ONCE(ns->flags)` etc. the
     script had missed) — the chunked rerun (audit_preview2.py) is the trusted
     one. ALWAYS prove an audit non-vacuous before trusting a zero.
3. Re-gate: re-run the lift census (the `liftcheck.py` pattern in
   `/Users/aryaman/.claude/jobs/feda8087/tmp/liftcheck.py`, but pointed at the
   new field-granular safe form) — every lifted form must still MATCH the
   differential. Then `weave_realized.py batch --lift` on
   `cgir-kbuild-defconfig`, confirm boot green + nm presence.
4. Tests: extend `dream/tests/test_lift.py` — the existing 5 tests
   (forbid-present, one-unsafe-block, differential-MATCH, sabotage→DIVERGE,
   raw-ptr-smuggle→rustc-reject, multi-node-not-liftable) must still pass; ADD
   one pinning that a field flagged racy by the audit is DEMOTED (build a
   synthetic racy-field case, assert `liftable` becomes False / tier=a).
5. Update `REALIZE.md` tier dashboard + commit. Update the honest claim: the
   boundary invariant is now "audited-minimal per-field exclusivity under the
   caller's locking discipline," not "assumed."

**Honest-reporting rule** (keep this discipline — it's why this project is
trusted): report verified separately from present-in-vmlinux separately from
tier. Never conflate "boots" with "sound." State the boundary invariant per
function.

────────────────────────────────────────────────────────────────────────────
## 3. AMENDMENTS QUEUE (after A1) — ordered, from RESEARCH-SAFE-RUST.md

- **A2 metrics**: DONE (aa08c77). `dream/realize/metrics.py` +
  test_metrics.py; wired into `batch --lift` dashboard. Fleet: 32% safe-logic
  (139/434 LOC in forbid cores), 214 raw-derefs all in boundaries.
- **A3 readers lift**: DONE (944ffe8) — and it needed NO model. Every verified
  reader uses one pointer idiom ((*p).field / *outp), so lift_readers.py lifts
  them DETERMINISTICALLY, re-gated by structdiff.harness.close. Batch: 7/10
  tier-b (safe core + structdiff MATCH), 3 audit-demoted. NEXT (mechanical,
  optional): WEAVE the 7 lifted readers → 38 tier-b present-in-vmlinux. The
  lifted candidate is a drop-in source swap (same _rs ABI + mirror + guards);
  integrate into weave_readers._cand_path behind a LIFT env flag, then
  `weave_realized.py batch --lift`, boot. Deferred to avoid late-session boot
  risk. If a reader ever appears that lift_readers REFUSES (2+ *mut structs /
  non-field use), THAT is the case that needs the model (SACTOR-2 + PR2).
- **A4 Kani rung**: DONE (4115380) + finding FIXED (ed63173).
  `dream/formal/lift_proof.py` + LIFT-PROOFS.md + test_lift_proof.py.
  **14/14 woven tier-b PROVEN** (lift equivalence + panic-freedom over the FULL
  domain), 0 PANIC_RISK, 0 LIFT_FAILED. The overflow-panic finding
  (seqbuf_seek) is CLOSED: `realize.wrapify` now pins wrapping arithmetic in
  the source (conservative, precedence-aware); re-census 480 MATCH unchanged.
- **A5 preprint**: DONE (6ba313d) — `dream/PREPRINT.md`, complete draft, every
  headline number grounded in a committed script. States plainly what is not
  novel vs the four contributions (in-kernel acceptance / manufactured oracles /
  differential-gated lifting / machine-checked safety tier), with an
  honest-limits and a method-notes (negative results) section. NOT submitted.

- **Containers realization**: feasibility MEASURED (ec610b4), not built —
  `dream/realize/CONTAINERS-FEASIBILITY.md` + `container_feas.py`. The
  effect-trace shortcut does NOT transfer (their model is an abstract ADT, not a
  flat field table): T2 needs real list_del/list_add pointer surgery 184 (53%),
  T3 needs allocator composition 131 (38%), only 5 (1%) realize with existing
  machinery. Recommendation + cheapest-first sequencing in the doc. Treat as a
  multi-session build with a research edge, NOT an extension of realize.py.

────────────────────────────────────────────────────────────────────────────
## 4. REACH — what % of the kernel into safe Rust, honestly

Multiply the funnel; every stage-number has historically SHRUNK 2–5× on
contact (the "census-shrinkage law" — see STRATEGY.md odds). Do not quote a
single number without the funnel.

- Census (all fns, from SWEEP.md): ~24,194; ~89% reachable-in-principle, ~17%
  strongly-verifiable with today's oracles, ~11% C-forever floor.
- Verified banked so far: ~1,124 (4.6% of census).
- efftrace realized+reverified: 480/635 (75.6%).
- Single-node LIFTABLE class (tier-b eligible): 317 of the 480 (the rest are
  multi-node / globals / outp → tier-a). [measured this session]
- Of those, AUDIT-PASS (survive the A1 concurrency audit): **see §5 for the
  measured number** — this is the true tier-b ceiling for the efftrace class.
- WEAVE-eligible (file built in defconfig): ~15% of any class (config
  coverage is the binding integration lever, proven LEVER-DEAD for readers).

**The honest target to aim at**: containers (344) + alloc (41) still need
their realize step (analogous to efftrace's, not yet built); the v2 transpiler
(A-queue extension) recovers ~130 of the 144 efftrace refusals (79
early-return via labeled-block transform, 36 computed-field-base, 18
cross-slot). Realistic tier-b-safe reach with current+queued machinery is
low-thousands of VERIFIED fns (a few % of the kernel), of which the
PRESENT-in-one-vmlinux subset is config-bounded (~hundreds). The STRATEGY.md
pre-registered landing is 8–12% verified before a new reach mechanism
(interprocedural depth) is needed for 17%. Safe-Rust (tier-b) is a subset of
that: everything single-node + audit-clean.

**Metric to report going forward**: %-Rust × safety-tier, three tiers
(a mirror-unsafe / b machine-checked-safe-core / c fully idiomatic). Tier-b %
is the memory-safety mission metric; tier-a % is pipeline progress only.

────────────────────────────────────────────────────────────────────────────
## 5. THE IN-FLIGHT MEASUREMENT (fold in, then delete the TODO)

A concurrency-audit demotion-rate measurement was running at handoff time:
`audit_preview2.py` over the 317 single-node lifted-class candidates. It
answers "what fraction survive the A1 audit" = the true tier-b ceiling for
efftrace. Result file: `dream/realize/audit_preview.json`
{n_singlenode, demoted[], racy_fields[]}. Read it and its stdout
(`.../tasks/b37sceski.output`). **The FIRST run's zero was vacuous (grep bug);
trust only audit_preview2.py's chunked-with-error-check result.** Record the
number in REALIZE.md §reach and in [[lockstep-status]] memory. If the file is
absent/incomplete, rerun `.venv/bin/python3
/Users/aryaman/.claude/jobs/feda8087/tmp/audit_preview2.py` (uses fn_fields.json
cache, ~15 min for the greps).

────────────────────────────────────────────────────────────────────────────
## 6. GOTCHAS LEARNED THIS SESSION (do not re-discover the hard way)

1. **Stale-Image false pass**: a failed relink can leave an old
   `arch/arm64/boot/Image` that satisfies `test -f`, so the boot gate boots a
   kernel with ZERO weave content. weave_realized/weave_readers now
   `rm -f Image` + use `pipefail`. The `nm vmlinux` presence check is
   LOAD-BEARING — never trust a green boot without it.
2. **`.eh_frame` orphan warnings**: defconfig's linker names every reader
   object in a WARNING line; the link-repair key-match must filter to
   non-warning lines only or it drops all objects.
3. **Symbol collision**: a fn woven via BOTH the readers base and the realized
   batch emits two `<fn>_rs` symbols → link error. `cmd_batch` dedupes against
   `_READERS_BASE`; keep that.
4. **Match-pattern binding trap**: an unresolved ALL-CAPS const in a Rust
   `match` becomes a catch-all BINDING (compiles clean, wrong behavior). The
   differential caught it live; `transpile` now refuses unknown-ALLCAPS and
   emits defines as fn-local consts. Do not weaken this.
5. **`type`/keyword fields** → `r#` escape (realize.rid). **bool params** →
   `u8` not `u1` (realize.wty). Both were census build-fail classes.
6. **ProcessPoolExecutor + `python3 - <<EOF`**: spawn can't re-import stdin →
   BrokenProcessPool. Write worker scripts to a FILE (see the tmp/*.py
   helpers), don't heredoc them.
7. **Multiprocessing workers re-import realize.py per call** — slow on huge
   source files (amdgpu). Cache results (fn_fields.json pattern).
8. **Audit zeros must be proven non-vacuous** — grep patterns silently miss;
   always validate against a known-positive before trusting a zero.

────────────────────────────────────────────────────────────────────────────
## 7. KEY FILES (all under `dream/`)

- `realize/realize.py` — transpile (cell-model→real-struct) + host re-gate +
  `fn_src_safe` (the lift form, A1 edits go here) + census. Entry: `realize`,
  `realize_light`, `transpile`, `close_realized`, `rust_host_tu(...,safe=)`.
- `ratchet/weave_realized.py` — kernel weave: in-kernel offset probe
  (`probe_many`), padded mirror + dual guards (rustc `offset_of!` + in-tree
  `_Static_assert`), `cmd_batch(lift=)`. Entry: `weave_realized.py batch [--lift]`.
- `ratchet/weave_readers.py` — reader weave (`_READERS_BASE`, funnel,
  link-repair, nm presence). `weave.py` — the underlying manifest weaver.
- `realize/REALIZE.md` — mechanism + census + tier dashboard (update on each run).
- `tests/test_lift.py`, `tests/test_realize.py`, `tests/test_weave_readers.py`.
- Loose caches (gitignore, don't commit): `census.jsonl`, `fn_fields.json`,
  `lift_verified.json`, `audit_preview.json`.
- tmp helpers (reference, not in repo): `~/.claude/jobs/feda8087/tmp/
  {liftcheck.py, audit_preview2.py}`.

────────────────────────────────────────────────────────────────────────────
## 8. FIRST MOVES FOR OPUS (in order)

1. `cd lockstep && git log --oneline -3` (confirm at 86b1d65 or later);
   `pytest dream/tests/ -q` with `KSRC` set (expect all green).
2. Read `dream/realize/audit_preview.json` — record the tier-b ceiling number
   (§5), update REALIZE.md + memory, delete the §5 TODO from this file.
3. Add the 3 loose json caches to `dream/realize/.gitignore`.
4. Execute A1 (§2) end-to-end: edit realize.py safe form → field-granular +
   audit gate → re-census lift → `WEAVE_VOL=cgir-kbuild-defconfig
   weave_realized.py batch --lift` → boot green + nm presence → extend
   test_lift.py → REALIZE.md → commit (identity + trailer per §0).
5. Then A2→A5 as usage allows. Keep the honest-reporting discipline: verified
   ≠ present ≠ sound; state boundary invariants; prove audits non-vacuous;
   0 false passes is the invariant that must never break.

# The soundness test suite — the pre-run false-pass hunt

Before running the pipeline at scale, its one non-negotiable property must be
pinned: **a wrong Rust candidate is never credited as equivalent to the C**
(zero false passes). The hardening pass showed the gates had gameable holes; this
suite turns "zero false passes" from an anecdote into a reproducible, adversarial
test battery that actively tries to break every oracle and every extractor.

Design principle throughout: a **false accept is a soundness failure; a false
reject is merely conservative.** So every battery hunts the accept direction —
wrong candidates that must be rejected, effectful bodies that must quarantine,
lossy extractions that must refuse.

## Run it

```bash
export KSRC=/path/to/linux
export PATH="$HOME/.cargo/bin:$PATH"        # rustc for the toolchain-gated tests

python3 -m pytest dream/tests/ -q           # 145 tests
bash dream/tests/run_all_gates.sh           # the 6 end-to-end gates
```

Toolchain-gated tests skip cleanly without `rustc`/`cc`; docker-dependent gates
(mirror's kernel leg) skip cleanly without docker. Everything else is
deterministic and self-contained.

## What's covered

| file | tests | the soundness hole it pins |
|---|---|---|
| `test_soundness.py` | 8 | **The centerpiece.** 13 deliberately-wrong candidates (delegation-to-C, constant, identity, off-by-one, wrong-algorithm, non-terminating, effect-dropping) across gcd / int_pow / __sw_hweight32 through the hostdiff oracle — **0 may MATCH**; correct candidates must MATCH (non-vacuous); delegation caught as its own verdict. |
| `test_purity.py` | 46 | **The false-PURE hunt.** 28 state-reading/effectful bodies (`counter++`, `sbox[x]`, `table[i]=v`, atomics, per-cpu, static-key reads, opaque calls) must classify **impure** — a false-pure routes an effect-dropping candidate to a value differential that passes it. 14 genuinely-pure must stay pure. |
| `test_mmiogen.py` | 32 | Control flow (`if`/`for`/`while`/`switch`/`goto`) in real driver bodies must **REFUSE**, not silently self-match a lossy `;`-split extraction; computed/helper offsets refuse; the mutant DIVERGEs; 10 fuzz bodies refuse-or-sane. |
| `test_mirror_cluster.py` | 59 | Bitfield/union/`#if`/nested-by-value structs REFUSED; a corrupted mirror is convicted by rustc; the comment-mask regression (a doc comment's `(C) 2013` swallowing the next definition) pinned; a shared static helper isn't wrongly pulled into a cluster; 29 fuzz inputs safe. |
| `run_all_gates.sh` | 6 gates | End-to-end regression: hostdiff, cluster, mirror, mmiogen, recorder (correct + subtle) each print their PASS line, or the runner exits non-zero. |

## Result (this repo, full toolchain)

```
pytest dream/tests/        145 passed
run_all_gates.sh           6 passed, 0 failed
false-pass battery         13 wrong candidates, 0 false passes
false-PURE battery         28 state bodies, 0 false pure
```

## Why this is the right pre-run gate

The pipeline's value is entirely in its soundness — cheap synthesis and a
booting kernel mean nothing if the oracle can be fooled. This suite is the
regression wall that keeps every future change honest: if a refactor reopens a
hole (a lossy extractor, an over-generous purity rule, a delegation path), one of
these adversarial cases turns red before a wrong candidate is ever woven into a
kernel. Run it before every scale run.

#!/usr/bin/env python3
"""Produce a behaviorally-WRONG-but-non-crashing variant of the verified Rust
region cluster — the differential oracle's negative control.

The whole point of oracle-manufacturing is to catch the bug class the weak gate
(boot-survival + KCSAN) cannot: a transplant that computes the wrong answer but
neither crashes nor races. So the mutation must be exactly that — a subtle
arithmetic error, not a crash and not a data race:

  adjtime-drift : each adjtime adds an extra +1000 ns. The clock stays monotone
                  and plausible (a weak gate sees nothing wrong), but every
                  post-adjtime observable diverges from the C original.

Usage: mutate.py <winner.rs> <out.rs> [--bug adjtime-drift|adjfine-noadj]
"""

from __future__ import annotations

import argparse
import sys

MUTATIONS = {
    # off-by-a-constant in the signed nsec add: non-crashing, non-racy, wrong.
    "adjtime-drift": (
        "(*tc).nsec = (*tc).nsec.wrapping_add_signed(delta);",
        "(*tc).nsec = (*tc).nsec.wrapping_add_signed(delta).wrapping_add(1000);",
    ),
    # drop the frequency adjustment: mult never changes. Boots fine, wrong rate.
    "adjfine-noadj": (
        "(*cc).mult = ((MOCK_PHC_CC_MULT as i64) + adj) as u32;",
        "(*cc).mult = MOCK_PHC_CC_MULT;",
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("winner")
    ap.add_argument("out")
    ap.add_argument("--bug", default="adjtime-drift", choices=list(MUTATIONS))
    args = ap.parse_args()

    src = open(args.winner).read()
    old, new = MUTATIONS[args.bug]
    if old not in src:
        print(f"!! mutation site not found for {args.bug!r}: {old!r}", file=sys.stderr)
        return 1
    mutated = src.replace(
        old,
        "// [negative control: {} — wrong but non-crashing]\n        {}".format(args.bug, new),
        1,
    )
    with open(args.out, "w") as fh:
        fh.write(mutated)
    print(f"wrote {args.bug} variant -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

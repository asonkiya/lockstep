"""Worklist sharding for multi-box grinds (burst.sh / the Oracle grinder).

Every box runs the same phases over the same deterministic worklists;
shard(items, k, m) gives box k (1-based) of m its modulo slice. Union across
boxes is exhaustive, slices are pairwise disjoint, sizes differ by <= 1.

Soundness note: shard the RAW worklist before any per-box filtering (done-set,
N_LEAVES cap). Per-box state differs, so filtering first would shift indices
and make two boxes claim overlapping slices.
"""
from __future__ import annotations

import os


def shard(items, k: int, m: int) -> list:
    """Slice for 1-based shard k of m. m <= 1 is the identity."""
    if m <= 1:
        if k != 1 and m == 1:
            raise ValueError(f"shard {k} of {m}: only shard 1 exists")
        return list(items)
    if not 1 <= k <= m:
        raise ValueError(f"shard {k} of {m}: shard must be in 1..{m}")
    return [w for i, w in enumerate(items) if i % m == k - 1]


def shard_env(items, env=None) -> list:
    """shard() driven by GRIND_SHARD/GRIND_OF (grind.sh writes these).
    Both unset -> identity; one set without the other -> error (a silently
    unsharded box would duplicate another box's work)."""
    env = os.environ if env is None else env
    s, of = env.get("GRIND_SHARD"), env.get("GRIND_OF")
    if s is None and of is None:
        return list(items)
    if s is None or of is None:
        raise ValueError("GRIND_SHARD and GRIND_OF must be set together")
    return shard(items, int(s), int(of))

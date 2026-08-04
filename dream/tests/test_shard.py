"""Pin the worklist sharding contract (dream/firstrun/shardlib.py).

Burst boxes all run the SAME phases over the SAME deterministic worklists;
GRIND_SHARD/GRIND_OF slice them modulo so the union across boxes is exhaustive
and pairwise disjoint. The slice must happen on the RAW list — before per-box
done-filtering — or index drift between boxes silently overlaps shards.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "shardlib", os.path.join(os.path.dirname(__file__), "..", "firstrun", "shardlib.py"))
_S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_S)


def test_default_is_identity():
    assert _S.shard(["a", "b", "c"], 1, 1) == ["a", "b", "c"]


def test_partition_exhaustive_and_disjoint():
    items = [f"fn{i}" for i in range(23)]
    shards = [_S.shard(items, k, 4) for k in (1, 2, 3, 4)]
    flat = [x for s in shards for x in s]
    assert sorted(flat) == sorted(items)          # exhaustive
    assert len(flat) == len(set(flat))            # disjoint
    # near-even: modulo slicing differs by at most one item
    sizes = [len(s) for s in shards]
    assert max(sizes) - min(sizes) <= 1


def test_deterministic_same_input_same_slice():
    items = list(range(100))
    assert _S.shard(items, 3, 8) == _S.shard(items, 3, 8)


def test_env_reading():
    env = {"GRIND_SHARD": "2", "GRIND_OF": "3"}
    assert _S.shard_env(["a", "b", "c", "d", "e"], env) == ["b", "e"]
    assert _S.shard_env(["a", "b"], {}) == ["a", "b"]   # unset -> identity


def test_invalid_config_raises():
    import pytest
    with pytest.raises(ValueError):
        _S.shard([1, 2], 0, 2)      # shard is 1-based
    with pytest.raises(ValueError):
        _S.shard([1, 2], 3, 2)      # shard > of
    with pytest.raises(ValueError):
        _S.shard_env([1], {"GRIND_SHARD": "2"})   # shard set without of

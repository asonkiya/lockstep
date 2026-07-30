#!/usr/bin/env python3
"""Entanglement router — classify a kernel function by WHAT makes it hard to
verify, and route it to the oracle that can gate its C->Rust rewrite. Run over
the minimal-kernel core, it measures the core's composition: how much is
auto-rewritable NOW, how much the effect-trace oracle would unlock, and how much
is the genuine pointer-graph / arch-asm floor.

The 7 classes and their oracles (status: what exists in this project):

  pure_leaf      scalar differential            DONE      (hostdiff)
  struct_reader  mirror differential            DONE      (structdiff)
  mmio           record/replay register trace   DONE      (Ring 3-5 + family)
  concurrent     loom (exhaustive) + KCSAN      PROTOTYPED (M0-M2 / concgate)
  bounded_state  EFFECT-TRACE oracle            BUILD     <- the linchpin gap
  unbounded_state in-kernel differential/workld HARD      (fallback, weaker)
  arch_asm       keep as unsafe/global_asm!     FLOOR     (RfL pattern; unsafe)

Classification is signature-based (regex over the body, reusing purity.py) — a
conservative MEASUREMENT, honest about "other". The bounded_state vs
unbounded_state split is the one a pure signature scan estimates roughly; CGIR's
`effects` layer (read-set / write-set over the PDG) is the precise refinement and
the exact footprint the effect-trace oracle records — see route_detail().
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "widerun"))
sys.path.insert(0, os.path.join(HERE, "..", "cluster"))
import purity  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

_ASM = re.compile(r"\basm\s+goto\b|\basm\s+volatile\b|\b__asm__\b|(?<![A-Za-z_])asm\s*\(")
_MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread\d*|iowrite\d*|in[bwl]|out[bwl])\b")
_CONC = re.compile(
    r"\b(spin_lock\w*|spin_unlock\w*|raw_spin\w*|mutex_lock|mutex_unlock|down(?:_\w+)?|up|"
    r"rcu_read_lock|rcu_read_unlock|rcu_dereference\w*|synchronize_rcu|call_rcu|"
    r"read_lock\w*|write_lock\w*|seqlock\w*|read_seqcount\w*|write_seqcount\w*|raw_write_seqcount\w*|"
    r"preempt_disable|preempt_enable|local_irq_save|local_irq_restore|"
    r"smp_mb|smp_rmb|smp_wmb|smp_load_acquire|smp_store_release|"
    r"this_cpu_\w+|__this_cpu_\w+|get_cpu\w*|per_cpu\w*|__percpu|"
    r"wait_event\w*|wake_up\w*|schedule|complete)\s*\(")
# pointer-graph mutation / allocation / chasing -> unbounded footprint
_GRAPH = re.compile(
    r"\b(list_add\w*|list_del\w*|list_move\w*|list_splice\w*|hlist_add\w*|hlist_del\w*|"
    r"rb_insert\w*|rb_erase\w*|rb_link_node|rb_replace\w*|"
    r"k[mzv]alloc\w*|kfree|kmem_cache_\w+|vmalloc|vfree|free_pages?|alloc_pages?|"
    r"container_of|list_for_each\w*|hlist_for_each\w*)\b"
    r"|->\s*[A-Za-z_]\w*\s*->")  # a->b->c pointer chain

_STRUCT_DEREF = re.compile(r"\b([A-Za-z_]\w*)\s*->\s*[A-Za-z_]\w*")


# ---------------------------------------------------------------------------

def classify(body: str, path: str = "", name: str = "") -> tuple[str, str]:
    """Return (class, reason). Routed by STRONGEST entanglement first."""
    scan = purity.mask(body)  # strip strings/comments so tokens are real code

    if _ASM.search(scan) or "arch/arm64/kernel/" in path or "arch/arm64/mm/" in path:
        return "arch_asm", "inline asm / arch low-level"
    if _MMIO.search(scan):
        return "mmio", f"MMIO ({_MMIO.search(scan).group(0)})"
    if _CONC.search(scan):
        return "concurrent", f"concurrency ({_CONC.search(scan).group(1)})"
    if _GRAPH.search(scan):
        return "unbounded_state", f"pointer-graph/alloc ({_GRAPH.search(scan).group(0)[:24]})"

    # no lock/mmio/graph left: split pure vs struct-reader vs bounded-state.
    effect = purity.has_effect(scan)
    derefs = set(_STRUCT_DEREF.findall(scan))
    if not effect and not derefs:
        return "pure_leaf", "no effect, no deref"
    if not effect and derefs:
        return "struct_reader", f"reads struct fields ({', '.join(sorted(derefs)[:3])})"
    # effectful but no pointer-graph mutation -> a BOUNDED state footprint:
    # writes land on named scalars/counters/fields (globals, per-fn statics,
    # param->scalar). This is the effect-trace oracle's target class.
    return "bounded_state", "writes a bounded (non-graph) state footprint"


ROUTE = {
    "pure_leaf":       ("scalar differential (hostdiff)",            "DONE"),
    "struct_reader":   ("mirror differential (structdiff)",          "DONE"),
    "mmio":            ("record/replay trace (Ring 3-5 + family)",   "DONE"),
    "concurrent":      ("loom + KCSAN (concgate)",                   "PROTOTYPED"),
    "bounded_state":   ("effect-trace oracle (CGIR effects + replay)", "BUILD"),
    "unbounded_state": ("in-kernel differential under workload",     "HARD"),
    "arch_asm":        ("keep as unsafe/global_asm!, boot-digest",   "FLOOR"),
}
# rollup buckets for the headline
AUTO_NOW = {"pure_leaf", "struct_reader", "mmio", "concurrent"}
UNLOCKS = {"bounded_state"}
FLOOR = {"unbounded_state", "arch_asm"}


def route_detail(klass: str) -> str:
    oracle, status = ROUTE[klass]
    note = ""
    if klass == "bounded_state":
        note = (" — CGIR's effects layer computes the exact read-set/write-set "
                "footprint the oracle records; this is the CGIR<->lockstep seam.")
    return f"{oracle} [{status}]{note}"


# ---------------------------------------------------------------------------

CORE_DIRS = ["kernel", "mm", "lib", "arch/arm64", "block", "fs/libfs.c",
             "security", "ipc"]


def _core_files():
    import glob
    files = []
    for d in CORE_DIRS:
        p = os.path.join(KSRC, d)
        if os.path.isfile(p):
            files.append(p)
        else:
            files += glob.glob(os.path.join(p, "**", "*.c"), recursive=True)
    return files


def scan():
    import cluster
    tally = Counter()
    examples = {}
    nfun = 0
    limit = int(os.environ.get("LIMIT", "0"))
    files = _core_files()
    if limit:
        files = files[:limit]
    for p in files:
        rel = os.path.relpath(p, KSRC)
        try:
            funcs = cluster.functions(open(p, errors="ignore").read())
        except Exception:
            continue
        for name, f in funcs.items():
            nfun += 1
            try:
                klass, reason = classify(f["text"], rel, name)
            except Exception:
                tally["error"] += 1
                continue
            tally[klass] += 1
            examples.setdefault(klass, []).append(f"{name} ({rel})")
    return tally, examples, nfun, len(files)


def main() -> int:
    tally, examples, nfun, nfiles = scan()
    total = sum(tally[k] for k in ROUTE)
    print(f"=== entanglement composition of the minimal-kernel core "
          f"({nfun} fns in {nfiles} files) ===\n")
    for klass, _ in sorted(ROUTE.items(), key=lambda kv: -tally.get(kv[0], 0)):
        n = tally.get(klass, 0)
        pct = 100 * n / total if total else 0
        print(f"  {n:6d}  {pct:5.1f}%  {klass:16s} -> {route_detail(klass)}")
        for ex in examples.get(klass, [])[:2]:
            print(f"                         e.g. {ex}")
    auto = sum(tally.get(k, 0) for k in AUTO_NOW)
    unlk = sum(tally.get(k, 0) for k in UNLOCKS)
    flr = sum(tally.get(k, 0) for k in FLOOR)
    print(f"\n  ROLLUP of {total} classified core functions:")
    print(f"    auto-rewritable NOW (oracle exists): {auto:6d}  {100*auto/total:5.1f}%")
    print(f"    unlocked by the effect-trace oracle: {unlk:6d}  {100*unlk/total:5.1f}%")
    print(f"    hard tail + arch-asm floor:          {flr:6d}  {100*flr/total:5.1f}%")
    print(f"\n  => effect-trace oracle is the single highest-leverage core build "
          f"(+{100*unlk/total:.0f}pp), CGIR effects layer is its footprint source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

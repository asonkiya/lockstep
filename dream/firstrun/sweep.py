#!/usr/bin/env python3
"""Full tree-wide sweep (local M2 Max grinder).

Run 1/2 measured the machine on the minimal-config gate-admitted set (~300 fns).
This widens the reach gates over the WHOLE kernel tree (37k .c files, drivers
unsampled) to harvest a much larger worklist, then solves it with the ksdk
mirror bank active. Checkpointed both phases: a kill costs only in-flight work.

  sweep.py harvest         run all 4 reach gates tree-wide -> sweep/*.json
  sweep.py harvest <gate>  one gate (readers|containers|efftrace|alloc)
  sweep.py solve           solve the harvested worklists (budget-capped)
  sweep.py status          progress + spend

Harvest parallelizes across files (process pool); solve across functions
(thread pool, the existing overnight machinery). Both write incremental
checkpoints so the sweep survives sleep/interruption — rerun the phase.
"""
from __future__ import annotations

import concurrent.futures as cf
import glob
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
SWEEP = os.path.join(HERE, "sweep")
os.makedirs(SWEEP, exist_ok=True)

# gate key -> (reach module path, cluster-functions source)
GATES = {
    "readers":    os.path.join(HERE, "..", "structdiff", "reach.py"),
    "containers": os.path.join(HERE, "..", "container_adt", "reach.py"),
    "efftrace":   os.path.join(HERE, "..", "efftrace", "reach.py"),
    "alloc":      os.path.join(HERE, "..", "allocmodel", "reach.py"),
}

# tree-wide corpus: all subsystems, drivers UNSAMPLED. (arch/ is excluded — it's
# per-arch asm-heavy and not the transplant target for a generic minimal kernel.)
_SUBS = ("lib", "kernel", "mm", "fs", "block", "crypto", "security", "net",
         "drivers", "sound", "ipc", "init")


def _corpus():
    files = []
    for sub in _SUBS:
        files += glob.glob(os.path.join(KSRC, sub, "**", "*.c"), recursive=True)
    return sorted(set(files))


_WORKER = {}          # per-process: gate key -> (module, functions-parser)


def _load_gate(key):
    if key in _WORKER:
        return _WORKER[key]
    for p in ("cluster", "mirror", "widerun", "router", "structdiff",
              "container_adt", "efftrace", "mmiogen"):
        sp = os.path.join(HERE, "..", p)
        if sp not in sys.path:
            sys.path.insert(0, sp)
    spec = importlib.util.spec_from_file_location(f"sweep_reach_{key}", GATES[key])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import cluster
    _WORKER[key] = (mod, cluster)
    return _WORKER[key]


def _scan_file(args):
    """Run one gate over every function in one file. Returns (accepted, nfns)."""
    key, rel = args
    mod, cluster = _load_gate(key)
    try:
        src = open(os.path.join(KSRC, rel), errors="ignore").read()
        fns = cluster.functions(src)
    except Exception:
        return [], 0
    accepted = []
    for fn in fns:
        try:
            rec = mod.gate(rel, fn)
            accepted.append(rec)
        except mod.Refused:
            pass
        except Exception:
            pass
    return accepted, len(fns)


def harvest(gate_keys=None, workers=10):
    gate_keys = gate_keys or list(GATES)
    files = [os.path.relpath(p, KSRC) for p in _corpus()]
    print(f"[harvest] {len(files)} files tree-wide, gates={gate_keys}, workers={workers}",
          flush=True)
    for key in gate_keys:
        out_path = os.path.join(SWEEP, f"{key}.json")
        done_files = set()
        accepted = []
        if os.path.exists(out_path):                     # resume
            prev = json.load(open(out_path))
            accepted = prev["accepted"]
            done_files = set(prev.get("scanned_files", []))
        todo = [(key, rel) for rel in files if rel not in done_files]
        t0, n_fns = time.time(), 0
        print(f"[harvest:{key}] {len(todo)} files to scan "
              f"({len(done_files)} already done, {len(accepted)} accepted)", flush=True)
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            for i, (rel, (acc, nf)) in enumerate(
                    zip((t[1] for t in todo), ex.map(_scan_file, todo, chunksize=8))):
                accepted += acc
                done_files.add(rel)
                n_fns += nf
                if (i + 1) % 500 == 0 or i + 1 == len(todo):
                    json.dump({"accepted": accepted,
                               "scanned_files": sorted(done_files)},
                              open(out_path, "w"))
                    el = time.time() - t0
                    print(f"[harvest:{key}] {i+1}/{len(todo)} files, "
                          f"{len(accepted)} accepted, {n_fns} fns scanned, "
                          f"{el:.0f}s", flush=True)
        print(f"[harvest:{key}] DONE {len(accepted)} accepted -> {os.path.relpath(out_path)}",
              flush=True)


def solve(budget=5.0, runtime_h=18.0):
    sys.path.insert(0, HERE)
    os.environ.setdefault("BUDGET_CAP", str(budget))
    os.environ.setdefault("RUNTIME_CAP_H", str(runtime_h))
    import overnight
    overnight._t0[0] = time.time()
    solvers = {
        "readers":    (overnight.solve_reader,    "reader"),
        "containers": (overnight.solve_container, "container"),
        "efftrace":   (overnight.solve_efftrace,  "efftrace"),
        "alloc":      (overnight.solve_alloc,     "alloc"),
    }
    ck = os.path.join(SWEEP, "solved.json")
    solved = json.load(open(ck)) if os.path.exists(ck) else {"done": [], "recs": []}
    done = set(solved["done"])
    for key, (fn, kind) in solvers.items():
        wl_path = os.path.join(SWEEP, f"{key}.json")
        if not os.path.exists(wl_path):
            print(f"[solve:{key}] no worklist — skip", flush=True)
            continue
        items = json.load(open(wl_path))["accepted"]
        print(f"[solve:{key}] {len(items)} accepted fns "
              f"(${overnight.spent():.3f} spent so far)", flush=True)
        with cf.ThreadPoolExecutor(max_workers=overnight.WORKERS) as ex:
            futs = {ex.submit(fn, it, done): it for it in items}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    k = overnight._key(kind, r["file"], r["sym"])
                    if k not in done:
                        done.add(k)
                        solved["done"].append(k)
                        solved["recs"].append({"kind": kind, "sym": r["sym"],
                                               "file": r["file"], "model": r["model"],
                                               "cost": r["cost"]})
                        json.dump(solved, open(ck, "w"))
                if overnight.time_left() < 300:
                    print(f"[solve:{key}] runtime cap — stopping", flush=True)
                    break
        print(f"[solve:{key}] cumulative solved={len(solved['done'])} "
              f"${overnight.spent():.3f}", flush=True)
    print(f"[solve] DONE {len(solved['done'])} solved, ${overnight.spent():.4f}", flush=True)


def status():
    for key in GATES:
        p = os.path.join(SWEEP, f"{key}.json")
        if os.path.exists(p):
            d = json.load(open(p))
            print(f"  harvest {key:11s}: {len(d['accepted']):5d} accepted, "
                  f"{len(d.get('scanned_files', [])):5d} files scanned")
        else:
            print(f"  harvest {key:11s}: (not started)")
    ck = os.path.join(SWEEP, "solved.json")
    if os.path.exists(ck):
        s = json.load(open(ck))
        cost = sum(r["cost"] for r in s["recs"])
        from collections import Counter
        by = Counter(r["kind"] for r in s["recs"])
        print(f"  SOLVED: {len(s['done'])}  (${cost:.4f})  {dict(by)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "harvest":
        harvest([sys.argv[2]] if len(sys.argv) > 2 else None)
    elif cmd == "solve":
        solve()
    else:
        status()

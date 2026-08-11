#!/usr/bin/env python3
"""The playbook — the class-build liturgy as a harness.

Every slice since the conditional classes has followed the same ritual:
enumerate strictly, FREEZE the denominator, pre-register blind bars, run the
gates solo, account per-fn dispositions, refresh funnel + ledger, commit with
the standard identity. This module is that ritual as code, so an agent session
only writes the novel realizer/gate code and a runner can execute the
deterministic cycles end-to-end.

Design rules (each one earned — see dream/LESSONS.md, operational section):
  * The harness OWNS its subprocesses synchronously: Popen + wait with a
    runtime cap, logs captured to files (never tail pipes). The
    stranded-watcher failure mode (4 occurrences in one week: a worker parked
    on a detached run that could never wake it) is impossible by construction.
  * Shared resources are solo by construction: fcntl.flock on a named lock,
    held for the duration of the run (the docker-volume collision killed a
    census pass once).
  * PREREG is written BEFORE the run, graded after, and both are committed
    artifacts-with-provenance (runs/<id>/PREREG.md + REPORT.md); raw logs are
    bulk regenerables and stay gitignored.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VENV_PY = "/Users/aryaman/Documents/Programming/llm-semantic-compilers/.venv/bin/python3"
PY = VENV_PY if os.path.exists(VENV_PY) else "python3"
RUNS = os.path.join(HERE, "runs")
LOCKS = os.path.join(HERE, ".locks")

GIT_ID = ["-c", "user.name=Aryaman Sonkiya", "-c", "user.email=asonkiya@unc.edu"]
TRAILER = "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"


# ---------------- run management ----------------

@contextmanager
def solo_lock(resource: str):
    """Exclusive lock on a named shared resource (e.g. 'docker-kbuild',
    'host-cc'). Blocks until free — runs are solo by construction."""
    os.makedirs(LOCKS, exist_ok=True)
    path = os.path.join(LOCKS, resource + ".lock")
    fd = open(path, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def run_logged(cmd, log_path, cap_s=3600, cwd=REPO, env_extra=None):
    """Run cmd synchronously; stdout+stderr to log_path; hard kill at cap_s.
    Returns {"rc", "secs", "log", "timed_out"}. caffeinate wraps the command
    on darwin so the box can't sleep mid-run."""
    if shutil.which("caffeinate"):
        cmd = ["caffeinate", "-i"] + list(cmd)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    t0 = time.time()
    timed_out = False
    with open(log_path, "w") as log:
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                             cwd=cwd, env=env)
        try:
            p.wait(timeout=cap_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            p.kill()
            p.wait()
    return {"rc": p.returncode, "secs": round(time.time() - t0, 1),
            "log": log_path, "timed_out": timed_out}


def new_run_dir(run_id: str) -> str:
    d = os.path.join(RUNS, run_id)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------- pre-registration + grading ----------------

def prereg_write(run_dir, title, bars, denominator=None, sabotages=None,
                 notes=""):
    """Write PREREG.md BEFORE the run. bars: [{id, bar}] — the blind
    thresholds. Returns the path. Refuses to overwrite an existing prereg
    (a bar written after the run is not a bar)."""
    path = os.path.join(run_dir, "PREREG.md")
    if os.path.exists(path):
        raise RuntimeError(f"prereg already exists: {path} — bars are frozen")
    lines = [f"# PREREG — {title}", "",
             f"Frozen {datetime.datetime.now().isoformat(timespec='seconds')}, "
             f"BEFORE the run. Graded in REPORT.md.", ""]
    if denominator is not None:
        lines += [f"**Frozen denominator:** {denominator}", ""]
    lines += ["## Blind bars", ""]
    lines += [f"- **{b['id']}**: {b['bar']}" for b in bars]
    if sabotages:
        lines += ["", "## Required negative controls", ""]
        lines += [f"- {s}" for s in sabotages]
    if notes:
        lines += ["", notes]
    open(path, "w").write("\n".join(lines) + "\n")
    return path


def grade(bars, results):
    """Grade bar results (dict id -> {"pass": bool, "measured": str}).
    Overall: SUCCESS all pass / PARTIAL >=half pass / FAIL. The two-partials
    rule is the caller's memory: a second PARTIAL on the same lever means
    stop and re-plan, not a third try."""
    rows = []
    npass = 0
    for b in bars:
        r = results.get(b["id"], {"pass": False, "measured": "NOT MEASURED"})
        npass += bool(r["pass"])
        rows.append({"id": b["id"], "bar": b["bar"],
                     "measured": r["measured"], "pass": bool(r["pass"])})
    overall = ("SUCCESS" if npass == len(bars)
               else "PARTIAL" if npass * 2 >= len(bars) else "FAIL")
    return {"overall": overall, "bars": rows,
            "passed": npass, "total": len(bars)}


def report_write(run_dir, title, graded, body=""):
    path = os.path.join(run_dir, "REPORT.md")
    lines = [f"# REPORT — {title}", "",
             f"Graded {datetime.datetime.now().isoformat(timespec='seconds')} "
             f"against the frozen PREREG. **{graded['overall']}** "
             f"({graded['passed']}/{graded['total']} bars).", "",
             "| bar | pre-registered | measured | verdict |", "|---|---|---|---|"]
    for r in graded["bars"]:
        lines.append(f"| {r['id']} | {r['bar']} | {r['measured']} | "
                     f"{'PASS' if r['pass'] else 'FAIL'} |")
    if body:
        lines += ["", body]
    open(path, "w").write("\n".join(lines) + "\n")
    return path


# ---------------- disposition accounting ----------------

def census_snapshot(path):
    """Load a container census json (or None)."""
    try:
        return json.load(open(path))
    except Exception:
        return None


def disposition_diff(before, after):
    """Per-class, per-fn diff of two census dicts (front_refusals +
    gate_refusals are {class: [fns]}; gate_match/front_accepted are counts).
    Returns {"counts": {...}, "moved": {class: {"gone": [...], "new": [...]}}}."""
    out = {"counts": {}, "moved": {}}
    for k in ("population", "front_accepted", "gate_match"):
        b = (before or {}).get(k)
        a = (after or {}).get(k)
        out["counts"][k] = {"before": b, "after": a,
                            "delta": (a - b) if (a is not None and b is not None) else None}
    for key in ("front_refusals", "gate_refusals"):
        bmap = (before or {}).get(key, {}) or {}
        amap = (after or {}).get(key, {}) or {}
        for cls in sorted(set(bmap) | set(amap)):
            bs, as_ = set(bmap.get(cls, [])), set(amap.get(cls, []))
            if bs != as_:
                out["moved"][f"{key}:{cls}"] = {
                    "gone": sorted(bs - as_), "new": sorted(as_ - bs)}
    return out


# ---------------- funnel + ledger refresh ----------------

def regen_ledger():
    """Regenerate ledger.json from persisted artifacts (measure-once).
    Returns the ledger dict."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ledger_pb", os.path.join(REPO, "dream", "ratchet", "ledger.py"))
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    led = L.collect()
    json.dump(led, open(os.path.join(REPO, "dream", "ratchet", "ledger.json"),
                        "w"), indent=1)
    return led


def funnel_load():
    return json.load(open(os.path.join(REPO, "dream", "ratchet", "funnel.json")))


def funnel_invariants(funnel=None):
    """Cross-check funnel constants against live-countable sources. Returns
    [{id, ok, detail}]. These are consistency invariants, not re-measurements."""
    f = funnel or funnel_load()
    checks = []
    t2 = census_snapshot(os.path.join(REPO, "dream", "realize",
                                      "container_census_t2.json"))
    t3 = census_snapshot(os.path.join(REPO, "dream", "realize",
                                      "container_census_t3.json"))
    if t2:
        ok = f["realized"]["containers_t2"] == t2["gate_match"]
        checks.append({"id": "funnel.t2==census.t2", "ok": ok,
                       "detail": f"funnel {f['realized']['containers_t2']} vs "
                                 f"census {t2['gate_match']}"})
    if t3:
        ok = f["realized"]["containers_t3"] == t3["gate_match"]
        checks.append({"id": "funnel.t3==census.t3", "ok": ok,
                       "detail": f"funnel {f['realized']['containers_t3']} vs "
                                 f"census {t3['gate_match']}"})
    cj = os.path.join(REPO, "dream", "realize", "census.jsonl")
    if os.path.exists(cj):
        n = sum(1 for ln in open(cj) if '"result": "MATCH"' in ln)
        checks.append({"id": "funnel.efftrace==census.jsonl", "ok":
                       f["realized"]["efftrace"] == n,
                       "detail": f"funnel {f['realized']['efftrace']} vs "
                                 f"census.jsonl {n}"})
    return checks


# ---------------- commit ----------------

def git_commit(paths, subject, body="", push=False):
    """Stage paths, commit with the standard identity + trailer. Returns the
    short hash, or None if nothing changed."""
    subprocess.run(["git", "add", "--"] + list(paths), cwd=REPO, check=True)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if staged.returncode == 0:
        return None
    msg = subject + ("\n\n" + body if body else "") + "\n\n" + TRAILER
    subprocess.run(["git"] + GIT_ID + ["commit", "-q", "-m", msg],
                   cwd=REPO, check=True)
    h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                       capture_output=True, text=True).stdout.strip()
    if push:
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=REPO,
                       check=True)
    return h

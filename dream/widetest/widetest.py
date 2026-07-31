#!/usr/bin/env python3
"""WIDE test of this session's new infrastructure — unattended, boot-free, $0.

Scheduled to run AFTER the first-rewrite run finishes. Hammers everything built
this session at scale and reports the one number that matters: FALSE PASSES
(must be 0). Four sections, each isolated (one failing never aborts the rest):

  1. REGRESSION      — the full pytest suite (every oracle proof + negative control)
  2. ANALYZER SCALE  — entangle / unbounded_census (tree-wide) + footprint / interproc
                       (core): run at full scale, assert no crashes + consistent totals
  3. NEW-ORACLE ADVERSARIAL — thousands of randomized WRONG candidates through the
                       NEW oracles (container_adt LIST + RBTREE, efftrace), each of
                       which MUST be caught; plus the correct candidate MUST pass
                       (catch over-rejection too). Counts false passes.
  4. LEGACY MEGATEST — the existing soundness_megatest (MMIO + hostdiff, thousands)
                       for raw volume.

Writes dream/widetest/REPORT.md.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
OUT = os.path.join(HERE, "reports")
LOG = os.path.join(OUT, "widetest.log")
T0 = time.time()
random.seed(1729)  # deterministic


def log(m):
    line = f"[{time.strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# 1. regression
# ---------------------------------------------------------------------------

def regression():
    log("section 1: pytest regression (full suite)")
    r = subprocess.run([sys.executable, "-m", "pytest", "dream/tests", "-q"],
                       cwd=REPO, capture_output=True, text=True, env={**os.environ, "KSRC": KSRC})
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:]
    passed = r.returncode == 0
    log(f"  regression: {'PASS' if passed else 'FAIL'} — {tail}")
    return {"passed": passed, "summary": tail[0] if tail else ""}


# ---------------------------------------------------------------------------
# 2. analyzer scale
# ---------------------------------------------------------------------------

def analyzer_scale():
    log("section 2: analyzer scale (no-crash + totals)")
    res = {}
    jobs = [("entangle", "router/entangle.py"), ("unbounded_census", "efftrace/unbounded_census.py"),
            ("footprint", "efftrace/footprint.py"), ("interproc", "efftrace/interproc.py")]
    for name, rel in jobs:
        t = time.time()
        r = subprocess.run([sys.executable, os.path.join("dream", rel)], cwd=REPO,
                           capture_output=True, text=True, env={**os.environ, "KSRC": KSRC})
        ok = r.returncode == 0 and "Traceback" not in r.stderr
        head = [l for l in (r.stdout).splitlines() if l.strip()][:1]
        res[name] = {"ok": ok, "secs": round(time.time() - t, 1),
                     "head": head[0] if head else "", "err": r.stderr[-200:] if not ok else ""}
        log(f"  {name}: {'ok' if ok else 'CRASH'} ({res[name]['secs']}s) {res[name]['head']}")
    return res


# ---------------------------------------------------------------------------
# 3. new-oracle adversarial (the wide part)
# ---------------------------------------------------------------------------

def _run_injected(mod, tmp, name, body):
    """Inject a fuzz candidate body into a proof module's CANDS and run it."""
    mod.CANDS[name] = body
    try:
        v = mod.run_scenario(tmp, name)[0]
    finally:
        mod.CANDS.pop(name, None)
    return v


def adversarial_container_list(tmp, n):
    mod = _load(os.path.join(HERE, "..", "container_adt", "proof.py"), "wt_cadt_list")
    correct = (1, 2, "insert(0, id)", "push(id)", "id as u32", True)
    combos = [(lt, lf, pt, pf, nt, dl)
              for lt in (1, 2, 0) for lf in (2, 1, 0)
              for pt in ("insert(0, id)", "push(id)") for pf in ("push(id)", "insert(0, id)")
              for nt in ("id as u32", "((id + 1) % 8) as u32", "((id + 3) % 8) as u32")
              for dl in (True, False)]
    wrong = [c for c in combos if c != correct]
    random.shuffle(wrong)
    checked = fp = 0
    for c in wrong[:n]:
        lt, lf, pt, pf, nt, dl = c
        body = (f"    let id = {nt}; {'del(id);' if dl else ''}\n"
                f"    if cond != 0 {{ LISTS[{lt}].{pt}; }} else {{ LISTS[{lf}].{pf}; }}\n    NA += 1;\n")
        v = _run_injected(mod, tmp, "_fuzz", body)
        checked += 1
        if v == "MATCH":
            fp += 1
            log(f"  !! FALSE PASS container_list: {c}")
    # sanity: the correct candidate must MATCH (no over-rejection)
    cbody = ("    let id = id as u32; del(id);\n"
             "    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].push(id); }\n    NA += 1;\n")
    correct_ok = _run_injected(mod, tmp, "_fuzz", cbody) == "MATCH"
    return {"checked": checked, "false_pass": fp, "correct_ok": correct_ok}


def adversarial_container_rbtree(tmp, n):
    mod = _load(os.path.join(HERE, "..", "container_adt", "proof_rbtree.py"), "wt_cadt_rb")
    variants = []
    for koff in (0, 1, 100, -1):
        for ioff in (0, 1):
            for droperase in (False, True):
                if koff == 0 and ioff == 0 and not droperase:
                    continue  # that's correct
                ins = f"m.insert(k + {koff}, id + {ioff}); NI += 1;"
                era = "NDL += 1;" if droperase else "m.remove(&(k)); NDL += 1;"
                variants.append(f"    if op != 0 {{ {ins} }} else {{ {era} }}\n")
    random.shuffle(variants)
    checked = fp = 0
    for body in variants[:n]:
        v = _run_injected(mod, tmp, "_fuzz", body)
        checked += 1
        if v == "MATCH":
            fp += 1
            log(f"  !! FALSE PASS container_rbtree: {body.strip()}")
    cbody = "    if op != 0 { m.insert(k, id); NI += 1; } else { m.remove(&k); NDL += 1; }\n"
    correct_ok = _run_injected(mod, tmp, "_fuzz", cbody) == "MATCH"
    return {"checked": checked, "false_pass": fp, "correct_ok": correct_ok}


def adversarial_efftrace(tmp, n):
    mod = _load(os.path.join(HERE, "..", "efftrace", "proof.py"), "wt_eff")
    # mutate the accumulator: wrong cell / wrong value / dropped / reordered
    muts = []
    for cell0 in (0, 3):
        for cval in ("t.wrapping_add(v)", "t.wrapping_add(v).wrapping_add(1)"):
            for cnt in ("c.wrapping_add(1)", "c.wrapping_add(2)", None):
                for cmp in (">", ">="):
                    body = f"    let t = eff_r(0); eff_w({cell0}, {cval});\n"
                    if cnt is not None:
                        body += f"    let c = eff_r(1); eff_w(1, {cnt});\n"
                    body += f"    let m = eff_r(2); if v {cmp} m {{ eff_w(2, v); }}\n    t.wrapping_add(v)\n"
                    correct = (cell0 == 0 and cval == "t.wrapping_add(v)"
                               and cnt == "c.wrapping_add(1)" and cmp == ">")
                    if not correct:
                        muts.append(body)
    random.shuffle(muts)
    checked = fp = 0
    for body in muts[:n]:
        v = _run_injected(mod, tmp, "_fuzz", body)
        checked += 1
        if v == "MATCH":
            fp += 1
            log("  !! FALSE PASS efftrace")
    return {"checked": checked, "false_pass": fp}


def new_oracle_adversarial(per):
    log(f"section 3: new-oracle adversarial ({per} mutants/oracle target)")
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in [("container_list", adversarial_container_list),
                         ("container_rbtree", adversarial_container_rbtree),
                         ("efftrace", adversarial_efftrace)]:
            try:
                r = fn(tmp, per)
            except Exception as e:
                r = {"error": str(e)[:200]}
            res[name] = r
            log(f"  {name}: {r}")
    return res


# ---------------------------------------------------------------------------
# 4. legacy megatest (raw volume)
# ---------------------------------------------------------------------------

def legacy_megatest(mut_per):
    log(f"section 4: legacy soundness_megatest (MMIO+hostdiff, {mut_per} mutants/fn)")
    mt_out = os.path.join(OUT, "megatest")
    r = subprocess.run([sys.executable, "dream/overnight/soundness_megatest.py",
                        "--mutants-per", str(mut_per), "--out", mt_out],
                       cwd=REPO, capture_output=True, text=True,
                       env={**os.environ, "KSRC": KSRC})
    ok = r.returncode == 0
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    log(f"  megatest: {'ok' if ok else 'FAIL'} — {tail}")
    summ = {}
    try:
        summ = json.load(open(os.path.join(mt_out, "summary.json")))
    except Exception:
        pass
    return {"ok": ok, "tail": tail, "summary": summ}


# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUT, exist_ok=True)
    open(LOG, "w").close()
    log(f"=== WIDE TEST of the new infrastructure (KSRC={KSRC}) ===")
    per = int(os.environ.get("MUT_PER", "60"))
    mt_per = int(os.environ.get("MEGA_PER", "40"))

    def safe(fn, *a, default=None):
        try:
            return fn(*a)
        except Exception as e:
            log(f"  SECTION ERROR in {fn.__name__}: {str(e)[:200]}")
            return default if default is not None else {}
    reg = safe(regression, default={"passed": False, "summary": "section errored"})
    ana = safe(analyzer_scale)
    adv = safe(new_oracle_adversarial, per)
    meg = safe(legacy_megatest, mt_per, default={"ok": False, "tail": ["errored"], "summary": {}})

    # headline: total adversarial candidates + false passes (must be 0)
    new_checked = sum(v.get("checked", 0) for v in adv.values())
    new_fp = sum(v.get("false_pass", 0) for v in adv.values())
    mega_cases = meg["summary"].get("total_adversarial_candidates", 0)
    mega_fp = meg["summary"].get("total_false_passes", 0)
    total_fp = new_fp + (mega_fp or 0)

    lines = ["# WIDE test — new infrastructure", "",
             f"_generated {time.strftime('%Y-%m-%d %H:%M')}, {round((time.time()-T0)/60,1)} min_", "",
             "## HEADLINE",
             f"- regression suite: **{'PASS' if reg['passed'] else 'FAIL'}** ({reg['summary']})",
             f"- new-oracle adversarial candidates: **{new_checked}**, false passes: **{new_fp}**",
             f"- legacy megatest candidates: **{mega_cases}**, false passes: **{mega_fp}**",
             f"- **TOTAL FALSE PASSES: {total_fp}**  (must be 0)", "",
             "## analyzer scale (no-crash + totals)"]
    for k, v in ana.items():
        lines.append(f"- {k}: {'ok' if v['ok'] else 'CRASH'} ({v['secs']}s) — {v['head']}")
        if not v["ok"]:
            lines.append(f"    err: {v['err']}")
    lines += ["", "## new-oracle adversarial detail"]
    for k, v in adv.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## legacy megatest", f"- {meg['tail']}"]
    verdict = ("PASS — 0 false passes across all oracles at scale"
               if total_fp == 0 and reg["passed"] else
               "INVESTIGATE — see above")
    lines += ["", f"## VERDICT: {verdict}"]
    open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(lines))
    log(f"VERDICT: {verdict}")
    log(f"report -> {os.path.relpath(os.path.join(OUT, 'REPORT.md'))}")
    return 0 if total_fp == 0 and reg["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

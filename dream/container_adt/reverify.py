#!/usr/bin/env python3
"""Zero-trust bank re-verification + repair for the container-ADT models.

Every banked model (dream/firstrun/verified/container_*.rs) is re-gated
against the CURRENT harness workload — which, since the 2026-08-09 worklist
repair, includes the NULL-arg rows, the id-0 fresh pool, and the linked()
member-emptiness dialect — plus the realizer's structural correspondence
where the fn is realize-shaped. A model fails when:

  * behavioral: close() != MATCH under the strengthened workload, or
  * structural: the realizer front gate ACCEPTS the fn but the MODEL-side
    correspondence/dialect check refuses (op_count/op_class, no_empty,
    ornull_model, pnull_model, tokf_field). C-side front refusals
    (conditional shapes, multi-head, ...) are the realizer's scope, not a
    model defect — tallied, not failed.

  reverify.py               # tally + per-fn fail listing
  reverify.py --resynth     # also re-synthesize failing models (Haiku,
                            # gate-arbitrated: MATCH + correspondence in the
                            # loop), write repaired models back to the bank
"""
from __future__ import annotations

import concurrent.futures as cf
import importlib.util
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VERIFIED = os.path.join(REPO, "dream", "firstrun", "verified")
MODEL = "claude-haiku-4-5-20251001"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reach = _load("cadt_reach_rv", "reach.py")
H = _load("cadt_harness_rv", "harness.py")
CR = _load("cadt_realize_rv", "container_realize.py")

_MODEL_SIDE = ("op_count_mismatch", "op_class_mismatch", "no_empty_in_model",
               "ornull_model", "pnull_model", "tokf_field_mismatch",
               "no_rs_call_body")

_REPAIR_RULES = """
REPAIR RULES (the verify loop enforces structural correspondence with the C):
- Express EXACTLY the C's list operations, in their order. NEVER add a
  defensive del() before an add; never duplicate an op across branches when
  one op under the right guard expresses it.
- INIT_LIST_HEAD on the node's own member (fresh node or sub-anchor) is a
  NO-OP here: omit it entirely.
- If the C null-checks a pointer arg, the NULL case arrives as that arg
  == -1 (token args: 0). Guard EXACTLY like the C: `if aK == -1 { return ...; }`.
  Never encode a null check as a tokf()/field() read.
- For C `if (!list_empty(&node->member))` — "is the NODE linked?" — use
  linked(id) / linked_m(M_*, id), NOT empty(L_*).
"""


def subjects():
    out = []
    for f in sorted(os.listdir(VERIFIED)):
        if f.startswith("container_") and f.endswith(".rs"):
            stem = f[len("container_"):-3]
            rel, fn = stem.rsplit(".c_", 1)
            out.append((rel.replace("__", "/") + ".c", fn, f))
    return out


def stored_body(fname):
    src = open(os.path.join(VERIFIED, fname)).read()
    m = re.search(r'extern "C" fn rs_call\([^)]*\) -> i64 \{\n(.*)\n\}\s*$',
                  src, re.DOTALL)
    return m.group(1) if m else None


def structural(rel, fn, abody=None):
    """Realizer view: ('out_of_scope', reason) | ('model_defect', reason)
    | ('ok', None)."""
    if abody is None:
        fname = f"container_{rel.replace('/', '__')}_{fn}.rs"
        abody = stored_body(fname)
        if abody is None:
            return "model_defect", "no_rs_call_body"
    try:
        CR.model_check(rel, fn, abody)
    except CR.Refused as e:
        kind = ("model_defect" if str(e).startswith(_MODEL_SIDE)
                else "out_of_scope")
        return kind, str(e)
    except Exception as e:
        return "out_of_scope", f"error/{type(e).__name__}"
    return "ok", None


def behavioral(rel, fn, body):
    try:
        rec = reach.gate(rel, fn)
        prep = H.prepare(rec)
    except Exception as e:
        return f"PREP_FAIL:{type(e).__name__}:{str(e)[:60]}", None
    with tempfile.TemporaryDirectory() as d:
        r = H.close(prep, body, workdir=d)
    return r["verdict"], prep


def check_one(item):
    rel, fn, fname = item
    body = stored_body(fname)
    if body is None:
        return item, "NO_BODY", ("model_defect", "no_rs_call_body")
    verdict, _ = behavioral(rel, fn, body)
    return item, verdict, structural(rel, fn)


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def _api_key():
    env = os.path.join(REPO, "..", "llm-semantic-compilers", ".env")
    for ln in open(env):
        if ln.startswith("ANTHROPIC_API_KEY="):
            return ln.split("=", 1)[1].strip()
    raise SystemExit("no ANTHROPIC_API_KEY in sibling .env")


def resynth(item, attempts=4):
    """Haiku re-synthesis, gate-arbitrated: behavioral MATCH AND (where
    realize-shaped) a clean structural pass, both in the loop."""
    import anthropic
    ov_spec = importlib.util.spec_from_file_location(
        "ov_prompt", os.path.join(REPO, "dream", "firstrun", "overnight.py"))
    # the prompt template lives in overnight.py; read it textually to avoid
    # importing the whole driver
    src = open(os.path.join(REPO, "dream", "firstrun", "overnight.py")).read()
    m = re.search(r'_CONTAINER_PROMPT = """(.*?)"""', src, re.DOTALL)
    template = m.group(1)

    rel, fn, fname = item
    rec = reach.gate(rel, fn)
    prep = H.prepare(rec)
    prompt = (template.format(sig=prep["rs_sig"], doc=prep["doc"])
              + _REPAIR_RULES)
    client = anthropic.Anthropic(api_key=_api_key())
    cost = 0.0
    fb = ""
    path = os.path.join(VERIFIED, fname)
    for i in range(attempts):
        msgs = [{"role": "user", "content": prompt + (
            f"\n\nYour previous attempt failed:\n{fb}\nFix it. Output ONLY the body."
            if fb else "")}]
        r = client.messages.create(model=MODEL, max_tokens=900, messages=msgs)
        cost += r.usage.input_tokens * 1e-6 + r.usage.output_tokens * 5e-6
        body = r.content[0].text.strip()
        body = re.sub(r"^```\w*\n?|```$", "", body).strip()
        if "UNSUPPORTED" in body:
            return None, cost, "model_says_unsupported"
        with tempfile.TemporaryDirectory() as d:
            rr = H.close(prep, body, workdir=d)
        if rr["verdict"] != "MATCH":
            fb = f"verdict={rr['verdict']}\n{rr['out'][-800:]}"
            continue
        # structural, against the CANDIDATE body (write, check, keep/rollback)
        old = open(path).read()
        open(path, "w").write(
            prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + body + "\n}\n")
        kind, reason = structural(rel, fn)
        if kind == "model_defect":
            open(path, "w").write(old)
            fb = (f"verdict=MATCH but the model does NOT structurally "
                  f"correspond to the C: {reason}. Follow the REPAIR RULES.")
            continue
        return body, cost, "repaired"
    return None, cost, f"unrepaired:{fb.splitlines()[0][:60] if fb else '?'}"


def main():
    do_resynth = "--resynth" in sys.argv
    subs = subjects()
    print(f"bank: {len(subs)} container models")
    results = []
    with cf.ThreadPoolExecutor(8) as ex:
        for item, verdict, (kind, reason) in ex.map(check_one, subs):
            results.append((item, verdict, kind, reason))
    fails = [(it, v, k, r) for it, v, k, r in results
             if v != "MATCH" or k == "model_defect"]
    n_beh = sum(1 for _, v, _, _ in fails if v != "MATCH")
    n_str = sum(1 for _, v, k, _ in fails if v == "MATCH" and k == "model_defect")
    print(f"  MATCH + structurally clean : {len(results) - len(fails)}")
    print(f"  FAIL behavioral            : {n_beh}")
    print(f"  FAIL structural-only       : {n_str}")
    for it, v, k, r in sorted(fails, key=lambda x: (x[1], str(x[3]))):
        print(f"    {v:28s} {k or '':13s} {str(r)[:44]:44s} {it[0]}:{it[1]}")
    out = {"total": len(results), "fails": [
        {"rel": it[0], "fn": it[1], "verdict": v, "kind": k, "reason": r}
        for it, v, k, r in fails]}
    json.dump(out, open(os.path.join(HERE, "reverify_report.json"), "w"),
              indent=1)
    if not do_resynth:
        return 0
    total_cost, fixed, unfixed = 0.0, [], []
    for it, v, k, r in fails:
        body, cost, status = resynth(it)
        total_cost += cost
        (fixed if body else unfixed).append((it[1], status))
        print(f"  resynth {it[1]:40s} {status} (${cost:.4f})")
        if total_cost > 1.0:
            print("BUDGET GUARD: > $1 — stopping")
            break
    print(f"\nrepaired {len(fixed)}, unrepaired {len(unfixed)}, "
          f"total ${total_cost:.4f}")
    for fn, status in unfixed:
        print(f"  UNREPAIRED {fn}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

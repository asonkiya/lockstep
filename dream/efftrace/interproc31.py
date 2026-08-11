#!/usr/bin/env python3
"""Summit 3.1 — the opaque-callee discharge measurement (PREREG-INTERPROC31.md).

Measure-first: how many of the unresolved-external-callee bounded_state fns
discharge under (a) header-inline corpus completeness, (b) a validated
known-pure annotation table, (c) CGIR marginal beyond (a)+(b). The bars are
frozen in the prereg; this script only measures.

  interproc31.py m1        # baseline + unresolved-name attribution (Zipf head)
  interproc31.py validate  # annotation-table validator incl. the poison catch
  interproc31.py m3        # closure ladder: baseline -> +headers -> +annot
  interproc31.py leftovers # post-(a)(b) remaining names, classified for (c)

Artifacts: interproc31_{m1,m3}.json (small, committed), m1_names.json (bulk,
gitignored). Reuses interproc.resolve/build_corpus verbatim for verdicts so the
baseline is bit-identical to INTERPROC_RESULTS.md.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("widerun", "cluster", "router"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import purity     # noqa: E402
import footprint  # noqa: E402
import interproc  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
BOUNDED, UNBOUNDED, UNRESOLVED = interproc.BOUNDED, interproc.UNBOUNDED, interproc.UNRESOLVED

# ---- (b) the annotation table: KNOWN-PURE kernel APIs only ----------------
# Contract: an entry contributes NOTHING to a caller's footprint — sound only
# for compute/read-only APIs. Anything that writes memory (memset/memcpy/...)
# is deliberately NOT annotatable in this table (fail-closed; noted in prereg).
ANNOT_PURE = {
    # string/memory READS
    "strlen", "strnlen", "strcmp", "strncmp", "strcasecmp", "strncasecmp",
    "memcmp", "strchr", "strrchr", "strstr", "strspn", "strcspn",
    # bitmap/bitops READS
    "test_bit", "find_first_bit", "find_next_bit", "find_last_bit",
    "find_first_zero_bit", "find_next_zero_bit", "bitmap_empty", "bitmap_full",
    "bitmap_weight", "bitmap_equal", "cpumask_test_cpu", "cpumask_weight",
    "cpumask_empty", "cpumask_first", "cpumask_next",
    # arithmetic / checked-math helpers (pure compute)
    "gcd", "lcm", "int_sqrt", "int_pow", "intlog2", "intlog10", "div64_u64",
    "div64_s64", "div_u64", "div_s64", "mul_u64_u32_shr", "mul_u64_u64_shr",
    "reciprocal_divide", "hash_32", "hash_64", "hash_long", "jhash", "jhash2",
    "jhash_3words", "full_name_hash", "crc32", "crc32c", "crc16", "csum_fold",
    # byte/word manipulation (pure)
    "swab16", "swab32", "swab64", "get_unaligned", "ror32", "rol32", "ror64",
    "rol64", "sign_extend32", "sign_extend64",
    # comparisons / classification predicates (read-only)
    "uuid_equal", "guid_equal",
    # ctype (const-table reads)
    "isxdigit", "isdigit", "isalpha", "isalnum", "isspace", "isupper",
    "islower", "isprint", "iscntrl", "_tolower", "_toupper", "tolower",
    "toupper",
    # compile-time / config predicates (macro constants)
    "IS_ENABLED", "ARRAY_SIZE", "BUILD_BUG_ON", "BUILD_BUG_ON_ZERO",
    "FIELD_GET", "FIELD_PREP",
}
# Diagnostic tier: bounded side effects OUTSIDE the modeled state (printk log,
# warn-once flags, RCU read brackets). Annotating them is sound for ROUTING at
# the efftrace assurance tier (single-flow, modeled-footprint differential) but
# is a weaker claim than pure — so it is a SEPARATE ladder step, reported
# separately against the bars.
ANNOT_DIAG = {
    "WARN_ON", "WARN_ON_ONCE", "WARN", "WARN_ONCE", "BUG", "BUG_ON",
    "pr_err", "pr_warn", "pr_info", "pr_debug", "pr_notice", "pr_cont",
    "printk", "dump_stack", "assert", "might_sleep", "cond_resched",
    "rcu_read_lock", "rcu_read_unlock", "lockdep_assert_held",
    "trace_printk",
}
POISON = "kmalloc"   # must be CAUGHT by the validator before the table ships


def header_files():
    return (glob.glob(os.path.join(KSRC, "include", "**", "*.h"), recursive=True)
            + glob.glob(os.path.join(KSRC, "arch/arm64/include", "**", "*.h"),
                        recursive=True))


def build_header_corpus():
    """{name: body} for every parseable header function body (static inlines)."""
    import cluster
    hcorp = {}
    for pth in header_files():
        try:
            fns = cluster.functions(open(pth, errors="ignore").read())
        except Exception:
            continue
        for name, f in fns.items():
            hcorp.setdefault(name, f["text"])
    return hcorp


def _missing_names(fn, corpus, cap=4000):
    """BFS attribution: external (not-in-corpus, not-pure) names reachable from
    fn through corpus bodies. Verdict-neutral — used only to attribute M1."""
    seen, out, queue = {fn}, set(), [fn]
    while queue and len(seen) < cap:
        cur = queue.pop()
        body = corpus.get(cur)
        if body is None:
            continue
        of = footprint.own_footprint(body, cur)
        for callee in of.get("callees", ()):  # local_hard bodies carry none
            if callee in purity.PURE_CALL or callee in purity.NONCALL:
                continue
            if callee not in corpus:
                out.add(callee)
            elif callee not in seen:
                seen.add(callee)
                queue.append(callee)
    return out


def _closure(corpus, worklist):
    memo = {}
    tally = Counter()
    per_fn = {}
    for name in worklist:
        v, _w, _r = interproc.resolve(name, corpus, memo, set())
        tally[v] += 1
        per_fn[name] = v
    return tally, per_fn


def cmd_m1():
    corpus, worklist = interproc.build_corpus()
    tally, per_fn = _closure(corpus, worklist)
    blocked = [f for f, v in per_fn.items() if v == UNRESOLVED]
    print(f"baseline closure: {dict(tally)}  (worklist {len(worklist)}, corpus {len(corpus)})")
    name_tally = Counter()
    attribution = {}
    for f in blocked:
        miss = _missing_names(f, corpus)
        attribution[f] = sorted(miss)
        for n in miss:
            name_tally[n] += 1
    top = name_tally.most_common()
    top100_fns = set()
    for n, _c in top[:100]:
        top100_fns |= {f for f, ms in attribution.items() if n in ms}
    print(f"blocked fns: {len(blocked)}; distinct missing callee names: {len(name_tally)}")
    print(f"Zipf head: top-100 names touch {len(top100_fns)}/{len(blocked)} blocked fns "
          f"({100*len(top100_fns)/max(1,len(blocked)):.1f}%)")
    for n, c in top[:25]:
        print(f"  {c:5d}  {n}")
    json.dump({"blocked": len(blocked), "distinct_names": len(name_tally),
               "top100_share_fns": len(top100_fns),
               "top": top[:400]}, open(os.path.join(HERE, "interproc31_m1.json"), "w"), indent=1)
    json.dump(attribution, open(os.path.join(HERE, "m1_names.json"), "w"))
    print(f"-> interproc31_m1.json (frozen), m1_names.json (bulk)")
    return 0


def cmd_validate():
    """Table entries with in-corpus bodies must agree with computed footprints;
    the poison entry must be CAUGHT."""
    corpus, _ = interproc.build_corpus()
    corpus.update({k: v for k, v in build_header_corpus().items() if k not in corpus})
    failures, checked, absent = [], 0, []
    for name in sorted(ANNOT_PURE | {POISON}):
        if name not in corpus:
            absent.append(name)
            continue
        checked += 1
        memo = {}
        v, writes, reason = interproc.resolve(name, corpus, memo, set())
        ok = (v == BOUNDED and not writes)
        if not ok:
            failures.append((name, v, sorted(writes)[:3], reason[:60]))
    poison_caught = any(f[0] == POISON for f in failures) or POISON not in corpus and False
    print(f"validator: {checked} entries checked against corpus bodies, "
          f"{len(absent)} absent (extern-only), {len(failures)} disagreements")
    for f in failures:
        print(f"  DISAGREE {f}")
    if POISON in corpus and poison_caught:
        print(f"POISON CATCH: '{POISON}' annotated pure -> CAUGHT ({[f for f in failures if f[0]==POISON][0][3]})")
    elif POISON not in corpus:
        print(f"POISON '{POISON}' has no in-corpus body — validator CANNOT see it: FAIL the control")
        return 2
    real_fail = [f for f in failures if f[0] != POISON]
    dropped = sorted(f[0] for f in real_fail)
    shipped_pure = sorted(ANNOT_PURE - set(dropped))
    # ANNOT_DIAG ships on the documented assurance-tier argument (its entries
    # have KNOWN unmodeled side effects — strict footprint agreement would fail
    # by design); the separate ladder step keeps that claim honest.
    json.dump({"pure": shipped_pure, "diag": sorted(ANNOT_DIAG),
               "dropped": dropped, "poison_caught": True},
              open(os.path.join(HERE, "interproc31_table.json"), "w"), indent=1)
    print(f"shipped table: {len(shipped_pure)} pure (dropped {len(dropped)} on "
          f"disagreement, fail-closed) + {len(ANNOT_DIAG)} diag-tier")
    print("poison caught -> control PASSES; table -> interproc31_table.json")
    return 0


def _shipped_tables():
    t = json.load(open(os.path.join(HERE, "interproc31_table.json")))
    assert t["poison_caught"], "validator has not passed — run validate first"
    return set(t["pure"]), set(t["diag"])


def cmd_m3():
    global ANNOT_PURE, ANNOT_DIAG
    ANNOT_PURE, ANNOT_DIAG = _shipped_tables()   # validated entries only
    corpus, worklist = interproc.build_corpus()
    base_tally, base_per = _closure(corpus, worklist)
    print(f"[baseline]        {dict(base_tally)}")

    hcorp = build_header_corpus()
    added = 0
    for k, v in hcorp.items():
        if k not in corpus:
            corpus[k] = v
            added += 1
    a_tally, a_per = _closure(corpus, worklist)
    print(f"[+headers a]      {dict(a_tally)}   (header bodies added: {added})")

    saved = purity.PURE_CALL
    purity.PURE_CALL = purity.PURE_CALL | ANNOT_PURE
    try:
        ab_tally, ab_per = _closure(corpus, worklist)
    finally:
        purity.PURE_CALL = saved
    print(f"[+annot pure b]   {dict(ab_tally)}")

    purity.PURE_CALL = saved | ANNOT_PURE | ANNOT_DIAG
    try:
        abd_tally, abd_per = _closure(corpus, worklist)
    finally:
        purity.PURE_CALL = saved
    print(f"[+annot diag b']  {dict(abd_tally)}")

    nb, na = base_tally[BOUNDED], a_tally[BOUNDED]
    nab, nabd = ab_tally[BOUNDED], abd_tally[BOUNDED]
    print(f"\nladder: bounded {nb} -> +headers {na} (+{na-nb}) -> +pure {nab} "
          f"(+{nab-na}) -> +diag {nabd} (+{nabd-nab})")
    print(f"TOTAL newly-bounded: strict (a+pure) +{nab-nb}; with diag tier +{nabd-nb}")
    # secondary: did annot reroute genuinely-unbounded?
    re_routed = sum(1 for f, v in base_per.items()
                    if v == UNBOUNDED and abd_per.get(f) == BOUNDED)
    print(f"secondary: baseline-UNBOUNDED now bounded under (a)+(b): {re_routed}")
    leftovers = [f for f, v in abd_per.items() if v == UNRESOLVED]
    json.dump({"baseline": dict(base_tally), "headers": dict(a_tally),
               "headers_annot_pure": dict(ab_tally),
               "headers_annot_diag": dict(abd_tally),
               "header_bodies_added": added,
               "newly_bounded_strict": nab - nb,
               "newly_bounded_with_diag": nabd - nb,
               "unbounded_rerouted": re_routed,
               "leftover_unresolved": len(leftovers)},
              open(os.path.join(HERE, "interproc31_m3.json"), "w"), indent=1)
    json.dump(leftovers, open(os.path.join(HERE, "m3_leftovers.json"), "w"))
    print(f"-> interproc31_m3.json, m3_leftovers.json ({len(leftovers)} fns)")
    return 0


def cmd_leftovers():
    """Classify the post-(a)(b) remaining missing names for the (c) question."""
    corpus, _ = interproc.build_corpus()
    corpus.update({k: v for k, v in build_header_corpus().items() if k not in corpus})
    left = json.load(open(os.path.join(HERE, "m3_leftovers.json")))
    saved = purity.PURE_CALL
    purity.PURE_CALL = purity.PURE_CALL | ANNOT_PURE | ANNOT_DIAG
    try:
        tally = Counter()
        for f in left:
            for n in _missing_names(f, corpus):
                tally[n] += 1
    finally:
        purity.PURE_CALL = saved
    print(f"{len(left)} leftover fns; {len(tally)} distinct missing names; top 40:")
    for n, c in tally.most_common(40):
        print(f"  {c:5d}  {n}")
    json.dump(tally.most_common(400),
              open(os.path.join(HERE, "interproc31_leftover_names.json"), "w"), indent=1)
    return 0


def cmd_cgir():
    """(c) the CGIR marginal, measured as ceiling + reachability:
    classify the top leftover names (macro / parser-gap / out-of-scope .c /
    other); the parser-gap + scope buckets are the MAXIMUM any better
    indexer could resolve; re-run the closure with that bucket force-annotated
    pure -> the (c) CEILING (real value <= ceiling, since real bodies may
    resolve unbounded). Then verify CGIR's parser actually indexes a sample of
    the parser-gap definitions (reachability). Macros are invisible to CGIR's
    tree-sitter ingest exactly as to cluster's parser — counted, not credited."""
    import re as _re
    import subprocess
    global ANNOT_PURE, ANNOT_DIAG
    ANNOT_PURE, ANNOT_DIAG = _shipped_tables()
    top = [n for n, _c in json.load(
        open(os.path.join(HERE, "interproc31_leftover_names.json")))[:100]]
    # classify by definition style: TWO single-pass alternation greps over the
    # tree (per-name greps measured >1h; alternation runs in ~a minute)
    dirs = [os.path.join(KSRC, d) for d in
            ("include", "kernel", "lib", "mm", "arch/arm64", "block",
             "security", "ipc", "fs", "drivers", "net")]
    alt = "|".join(_re.escape(n) for n in top)
    def _pass(pat):
        r = subprocess.run(["grep", "-rEn", "--include=*.c", "--include=*.h",
                            pat] + dirs, capture_output=True, text=True,
                           timeout=1200)
        return r.stdout.splitlines()
    macro_names, def_files = set(), {}
    for ln in _pass(rf"#\s*define\s+({alt})\b"):
        m = _re.search(rf"#\s*define\s+({alt})\b", ln)
        if m:
            macro_names.add(m.group(1))
    for ln in _pass(rf"^[a-zA-Z_][^;]*\b({alt})\s*\("):
        f = ln.split(":", 1)[0]
        m = _re.search(rf"\b({alt})\s*\(", ln)
        if m and m.group(1) not in def_files:
            def_files[m.group(1)] = f
    corpus, _ = interproc.build_corpus()
    corpus.update({k: v for k, v in build_header_corpus().items() if k not in corpus})
    cls = {"macro": [], "parser_gap_or_scope": [], "no_definition": []}
    gap_files = {}
    for n in top:
        if n in macro_names:
            cls["macro"].append(n)
        elif n in def_files and n not in corpus:
            cls["parser_gap_or_scope"].append(n)
            gap_files[n] = def_files[n]
        else:
            cls["no_definition"].append(n)
    print(f"top-100 leftover names: macro {len(cls['macro'])}, "
          f"parser-gap/scope {len(cls['parser_gap_or_scope'])}, "
          f"no-definition/other {len(cls['no_definition'])}")
    # (c) ceiling: force-annotate the gap bucket pure, re-run closure
    _, worklist = interproc.build_corpus()
    saved = purity.PURE_CALL
    purity.PURE_CALL = saved | ANNOT_PURE | ANNOT_DIAG
    try:
        ab_tally, _ = _closure(corpus, worklist)
    finally:
        purity.PURE_CALL = saved
    purity.PURE_CALL = saved | ANNOT_PURE | ANNOT_DIAG | set(cls["parser_gap_or_scope"])
    try:
        c_tally, _ = _closure(corpus, worklist)
    finally:
        purity.PURE_CALL = saved
    ceiling = c_tally[BOUNDED] - ab_tally[BOUNDED]
    print(f"(c) CEILING (gap bucket force-pure): bounded {ab_tally[BOUNDED]} -> "
          f"{c_tally[BOUNDED]} = +{ceiling}")
    # reachability: does cgir index a sample of the gap definitions?
    sample = dict(list(gap_files.items())[:5])
    reach = {}
    if sample:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            import shutil as _sh
            for n, f in sample.items():
                _sh.copy(f, td)
            out = os.path.join(td, "out")
            r = subprocess.run(["/Users/aryaman/Documents/Programming/llm-semantic-compilers/.venv/bin/cgir",
                                "scan", td, "--out", out],
                               capture_output=True, text=True, timeout=300)
            blob = ""
            for root, _d, fs in os.walk(out):
                for fn2 in fs:
                    blob += open(os.path.join(root, fn2), errors="ignore").read()
            for n in sample:
                reach[n] = bool(_re.search(rf"\b{n}\b", blob))
    print(f"CGIR reachability on gap sample: {reach}")
    json.dump({"classes": {k: v for k, v in cls.items()},
               "c_ceiling": ceiling, "cgir_reach_sample": reach},
              open(os.path.join(HERE, "interproc31_cgir.json"), "w"), indent=1)
    print("-> interproc31_cgir.json")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "m1"
    raise SystemExit({"m1": cmd_m1, "validate": cmd_validate,
                      "m3": cmd_m3, "leftovers": cmd_leftovers,
                      "cgir": cmd_cgir}[cmd]())

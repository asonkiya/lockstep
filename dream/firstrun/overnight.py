#!/usr/bin/env python3
"""First official minimal rewrite — unattended, guarded, overnight.

PHASE 1 (boot-free, host cc+rustc — the bulk, machine-light):
  A. GPIO template family   — template_synth ($0) -> gpio_family trace oracle.
  C. scalar exported leaves  — ladder synth (local Qwen $0 -> Haiku, budget-capped)
                               -> hostdiff boot-free differential.
  Every verified function's candidate is checkpointed under verified/.

PHASE 2 (ONE batched Docker build + QEMU boot):
  weave the verified freestanding scalar-leaf set into vmlinux and boot-verify
  (reuses the proven widerun build_boot/probe) -> a booting kernel carrying the
  Rust set. Phase 1 results are already banked; a Phase-2 hiccup can't lose them.

GUARDS (all env-overridable):
  BUDGET_CAP=7.5     hard ceiling on Haiku $ (balance ~$7.75); Haiku is SKIPPED once
                     hit — never exceeded. Local Qwen + templates are $0.
  RUNTIME_CAP_H=7    wall-clock ceiling; stop gracefully + report.
  WORKERS=4          of 12 cores; launch under `nice` so the machine stays usable.
  Resumable: verified/ + progress.json; a re-launch skips completed work.
  Rolling log run.log; morning report REPORT.md.

Sound by construction: every candidate faces a gate that catches wrong output
(hostdiff / trace oracle, 0 false passes across all prior runs); a worse/cheaper
synthesizer only costs retries, never correctness.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
for p in ("widerun", "hostdiff", "family", "structdiff"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
import hostdiff            # noqa: E402
import widerun             # noqa: E402  (harvest, SCALAR, rsig, build_boot, probe, PRELUDE)
import template_synth      # noqa: E402
import gpio_family         # noqa: E402
import harness as sd_harness  # noqa: E402  (structdiff: prepare/close for struct-readers)

BUDGET_CAP = float(os.environ.get("BUDGET_CAP", "7.5"))
RUNTIME_CAP_H = float(os.environ.get("RUNTIME_CAP_H", "7"))
WORKERS = int(os.environ.get("WORKERS", "4"))
N_LEAVES = int(os.environ.get("N_LEAVES", "80"))
LOCAL_MODEL = os.environ.get("LOCAL_MODEL", "qwen2.5-coder:7b")
OLLAMA = "http://localhost:11434/api/generate"
# Haiku price per token ($1/MTok in, $5/MTok out) for the budget accounting.
_HAIKU_IN, _HAIKU_OUT = 1.0 / 1e6, 5.0 / 1e6

VERIFIED = os.path.join(HERE, "verified")
LOG = os.path.join(HERE, "run.log")
PROGRESS = os.path.join(HERE, "progress.json")
REPORT = os.path.join(HERE, "REPORT.md")

_lock = threading.Lock()
_spent = [0.0]
_t0 = [0.0]


def spend(c):
    with _lock:
        _spent[0] += c


def spent():
    with _lock:
        return _spent[0]


def budget_left():
    return BUDGET_CAP - spent()


def time_left():
    return RUNTIME_CAP_H * 3600 - (time.time() - _t0[0])


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with _lock:
        print(line, flush=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")


# ---------------------------------------------------------------------------
# the synth ladder: local Qwen ($0) -> Haiku (budget-capped). Gate-arbitrated.
# ---------------------------------------------------------------------------

def _extract_rust(text):
    m = re.search(r"```(?:rust)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def _ollama(prompt):
    body = json.dumps({"model": LOCAL_MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.1}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return _extract_rust(json.loads(r.read())["response"])


def _haiku(prompt):
    import anthropic
    from synthesize import _api_key
    # bounded timeout + retries so a stalled socket can NEVER hang a worker thread
    # (the 5h freeze we hit was a Haiku call with no timeout).
    cl = anthropic.Anthropic(api_key=_api_key(), timeout=60.0, max_retries=2)
    m = cl.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1200,
                           messages=[{"role": "user", "content": prompt}])
    cost = m.usage.input_tokens * _HAIKU_IN + m.usage.output_tokens * _HAIKU_OUT
    spend(cost)
    return _extract_rust(m.content[0].text), cost


def ladder(prompt, gate):
    """gate(candidate_body:str) -> bool. Try local first ($0); if it fails and
    budget remains, escalate to Haiku. Returns (body|None, model, cost)."""
    try:
        body = _ollama(prompt)
        if gate(body):
            return body, "local", 0.0
    except Exception as e:
        log(f"  local synth error: {str(e)[:80]}")
    if budget_left() <= 0.02:               # keep a cushion; never exceed the cap
        return None, "budget", 0.0
    try:
        body, cost = _haiku(prompt)
        if gate(body):
            return body, "haiku", cost
    except Exception as e:
        log(f"  haiku synth error: {str(e)[:80]}")
    return None, "none", 0.0


# ---------------------------------------------------------------------------
# corpus C: scalar exported leaves, gated boot-free by hostdiff
# ---------------------------------------------------------------------------

_LEAF_PROMPT = """Reimplement this Linux kernel function as a single self-contained Rust function.
Signature EXACTLY:
{sig} {{
Rules: no_std-safe (no std/alloc/externs/panics), wrapping arithmetic
(wrapping_add/mul/etc.), integer division ONLY if the divisor is a nonzero
constant. If it reads any global/per-cpu/clock/mmio state, reply with exactly
`// UNSUPPORTED`. Output ONLY the function in a ```rust block.

C source:
{body}
"""


def leaf_gate_factory(w):
    """Return a gate(body)->bool that host-differentials `body` via hostdiff."""
    def gate(body):
        if "UNSUPPORTED" in body or "cgir_" + w["sym"] not in body:
            return False
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cand = os.path.join(d, "c.rs")
            open(cand, "w").write(body)           # host staticlib form (plain fn)
            res = hostdiff.run(os.path.join(KSRC, w["file"]), w["sym"], cand,
                               [], KSRC, 200_000, quiet=True)
        return res.get("verdict") == "MATCH"
    return gate


def solve_leaf(w, done):
    if w["sym"] in done:
        return None
    if time_left() < 300:
        return None
    sig = widerun.rsig(w)
    prompt = _LEAF_PROMPT.format(sig=sig, body=w["body"])
    body, model, cost = ladder(prompt, leaf_gate_factory(w))
    if body:
        open(os.path.join(VERIFIED, f"{w['sym']}.rs"), "w").write(body)
        log(f"  ✓ leaf {w['sym']} via {model} (${cost:.4f}) [spent ${spent():.3f}]")
        return {"sym": w["sym"], "kind": "scalar-leaf", "model": model, "cost": cost,
                "file": w["file"]}
    log(f"  ✗ leaf {w['sym']} unsolved ({model})")
    return None


# ---------------------------------------------------------------------------
# corpus A: GPIO template family — $0, deterministic, host trace oracle
# ---------------------------------------------------------------------------

def solve_family():
    out = []
    import tempfile
    for name in template_synth.DRIVER_SPECS:
        cand = template_synth.synth_for(name)          # $0, no model
        with tempfile.TemporaryDirectory() as d:
            v, _ = gpio_family.close(name, wrong=False, workdir=d, cand_override=cand)
        ok = v == "DIFF_PASS"
        log(f"  {'✓' if ok else '✗'} family {name} template-synth -> {v} ($0)")
        if ok:
            out.append({"sym": name, "kind": "gpio-family", "model": "template", "cost": 0.0})
    return out


# ---------------------------------------------------------------------------
# corpus B: pure struct-readers, gated boot-free by structdiff (the big class)
# ---------------------------------------------------------------------------

_READER_PROMPT = """Reimplement this Linux kernel struct-reader as the BODY of a Rust function.
Exact signature (write ONLY the code that goes inside its braces):
{sig}
The struct(s) are ALREADY defined as these #[repr(C)] mirrors — read fields via
(*ptr).field using the SAME field names, and use the SAME parameter names as the
signature:
{mirror}
Rules: no_std-safe, wrapping arithmetic (wrapping_add/mul/...), integer division
only by a nonzero constant, no external calls / alloc / panics. Read only the
passed struct(s) + scalar params. If it reads any global / mmio / clock / lock
state, reply with exactly `// UNSUPPORTED`. Output ONLY the body (statements +
final expression), no signature, no outer braces, no ``` fences.

C source:
{csrc}
"""


def _reader_cand(p, body):
    return p["mirror_rust"] + "\n" + p["sig"] + " { unsafe {\n" + body + "\n}}\n"


def solve_reader(item, done):
    rel, fn = item["file"], item["fn"]
    if fn in done or time_left() < 300:
        return None
    try:
        p = sd_harness.prepare(rel, fn)
    except Exception as e:
        log(f"  ✗ reader {fn} prepare-fail ({str(e)[:40]})")
        return None
    prompt = _READER_PROMPT.format(sig=p["sig"], mirror=p["mirror_rust"], csrc=p["csrc"])

    def gate(body):
        if "UNSUPPORTED" in body:
            return False
        with tempfile.TemporaryDirectory() as d:
            v, _ = sd_harness.close(rel, fn, _reader_cand(p, body), d)
        return v == "MATCH"

    body, model, cost = ladder(prompt, gate)
    if body:
        open(os.path.join(VERIFIED, f"reader_{fn}.rs"), "w").write(_reader_cand(p, body))
        log(f"  ✓ reader {fn} via {model} (${cost:.4f}) [spent ${spent():.3f}]")
        return {"sym": fn, "kind": "struct-reader", "model": model, "cost": cost, "file": rel}
    log(f"  ✗ reader {fn} unsolved ({model})")
    return None


# ---------------------------------------------------------------------------
# phase 2: weave verified freestanding leaves into a booting kernel (one boot)
# ---------------------------------------------------------------------------

def phase2_boot(leaf_syms):
    if not leaf_syms:
        log("phase 2: no freestanding leaves verified — skipping boot")
        return {"attempted": False}
    try:
        # build_boot reads candidates from widerun's own cand/ dir and takes the
        # harvested w-dicts; write each verified body in FREESTANDING form (PRELUDE
        # + no_std) for the in-kernel weave.
        cand_dir = os.path.join(os.path.dirname(widerun.__file__), "cand")
        os.makedirs(cand_dir, exist_ok=True)
        batch = []
        for w in widerun.harvest():
            if w["sym"] in leaf_syms:
                body = open(os.path.join(VERIFIED, f"{w['sym']}.rs")).read()
                open(os.path.join(cand_dir, f"{w['sym']}.rs"), "w").write(widerun.PRELUDE + body)
                batch.append(w)
        pmax = int(os.environ.get("PHASE2_MAX", "40"))
        batch = batch[:pmax]
        log(f"phase 2: weaving {len(batch)} verified leaves into vmlinux (chunks of 40)...")
        n_boot = dropped_total = 0
        for i in range(0, len(batch), 40):
            chunk = batch[i:i + 40]
            verd, dropped = widerun.build_boot(chunk, f"firstrun{i}")
            nb = sum(1 for v in (verd or {}).values() if "PASS" in str(v))
            n_boot += nb
            dropped_total += len(dropped or {})
            log(f"  phase 2 chunk {i//40}: boot-verified {nb}/{len(chunk)}, dropped {len(dropped or {})}")
        return {"attempted": True, "woven": len(batch), "boot_verified": n_boot,
                "dropped": dropped_total}
    except Exception as e:
        log(f"phase 2 error (Phase-1 results safe): {str(e)[:120]}")
        return {"attempted": True, "error": str(e)[:200]}


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def write_report(solved, phase2):
    by = {}
    for s in solved:
        by.setdefault(s["kind"], []).append(s)
    lines = ["# First official minimal rewrite — overnight report", "",
             f"- runtime: {round((time.time()-_t0[0])/60,1)} min "
             f"(cap {RUNTIME_CAP_H}h)",
             f"- Haiku spent: ${spent():.4f} of ${BUDGET_CAP:.2f} cap",
             f"- verified functions: {len(solved)}", ""]
    for kind, items in sorted(by.items()):
        models = {}
        for i in items:
            models[i["model"]] = models.get(i["model"], 0) + 1
        lines.append(f"## {kind}: {len(items)}  ({models})")
        for i in items[:40]:
            lines.append(f"  - {i['sym']}  [{i['model']}]")
        lines.append("")
    lines.append("## phase 2 (boot-weave)")
    lines.append(f"  {json.dumps(phase2)}")
    open(REPORT, "w").write("\n".join(lines))
    log(f"report -> {os.path.relpath(REPORT)}")


def _watchdog(grace=180):
    """Daemon backstop: at the runtime cap + grace, force-exit even if a worker
    thread is hung (a blocked network/subprocess call), writing a report from the
    checkpoint. This is what the 5h freeze needed — the in-loop cap check can't
    fire while `as_completed` is blocked on a stuck future."""
    time.sleep(RUNTIME_CAP_H * 3600 + grace)
    log(f"WATCHDOG: runtime cap +{grace}s exceeded — a worker hung; forcing exit "
        f"(results are checkpointed)")
    try:
        done = json.load(open(PROGRESS)).get("done", []) if os.path.exists(PROGRESS) else []
        open(REPORT, "w").write(
            "# Overnight report (WATCHDOG force-exit)\n\n"
            f"- runtime cap {RUNTIME_CAP_H}h exceeded — a synth/gate call hung.\n"
            f"- Haiku spent: ${spent():.4f}\n"
            f"- verified (checkpointed, safe): {len(done)}\n"
            f"  {', '.join(sorted(done))}\n")
    except Exception as e:  # never let the exit path itself throw
        log(f"  watchdog report error: {str(e)[:80]}")
    os._exit(2)


def main():
    _t0[0] = time.time()
    threading.Thread(target=_watchdog, daemon=True).start()
    os.makedirs(VERIFIED, exist_ok=True)
    done = set()
    if os.path.exists(PROGRESS):
        done = set(json.load(open(PROGRESS)).get("done", []))
    log(f"=== first official minimal rewrite: budget ${BUDGET_CAP} / {RUNTIME_CAP_H}h "
        f"/ {WORKERS} workers / resume={len(done)} done ===")
    solved = []

    # corpus A — $0, guaranteed, always runs first so there is always a result
    log("phase 1A: GPIO template family ($0)")
    solved += solve_family()

    # corpus B — pure struct-readers via structdiff (READERS=1). The big clean
    # class (reach_accepted.json); boot-free, gate-arbitrated like everything else.
    if os.environ.get("READERS") == "1":
        rj = os.path.join(HERE, "..", "structdiff", "reach_accepted.json")
        readers = json.load(open(rj)) if os.path.exists(rj) else []
        log(f"phase 1B: {len(readers)} struct-readers (structdiff, boot-free, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(solve_reader, it, done): it for it in readers}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    solved.append(r)
                    done.add(r["sym"])
                    json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
                if time_left() < 300:
                    break

    # corpus C — scalar leaves via the ladder, boot-free hostdiff gate
    log("phase 1C: harvesting scalar exported leaves...")
    work = [w for w in widerun.harvest() if w["sym"] not in done][:N_LEAVES]
    log(f"phase 1C: {len(work)} leaves to solve (workers={WORKERS})")
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(solve_leaf, w, done): w for w in work}
        for fut in cf.as_completed(futs):
            r = fut.result()
            if r:
                solved.append(r)
                done.add(r["sym"])
                json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
            if budget_left() <= 0.02:
                log("BUDGET CAP reached — no more Haiku; finishing local/queued work")
            if time_left() < 300:
                log("RUNTIME CAP approaching — stopping synth")
                break

    leaf_syms = {s["sym"] for s in solved if s["kind"] == "scalar-leaf"}
    # include freestanding leaves banked in PRIOR (resumed) sessions too, so a
    # resumed run weaves the FULL accumulated set, not just this session's new
    # solves (else phase 2 under-weaves after a resume).
    for f in os.listdir(VERIFIED):
        if f.endswith(".rs") and not f.startswith("reader_"):
            leaf_syms.add(f[:-3])
    log(f"phase 1 done: {len(solved)} new this session; {len(leaf_syms)} freestanding "
        f"leaves total to weave (${spent():.4f} spent).")
    if os.environ.get("PHASE2", "1") == "0":
        phase2 = {"attempted": False, "reason": "PHASE2=0 (smoke test)"}
    elif time_left() > 900:
        log("starting phase 2 (boot-weave)...")
        phase2 = phase2_boot(leaf_syms)
    else:
        phase2 = {"attempted": False, "reason": "no time left for boot"}
    write_report(solved, phase2)
    log("=== DONE ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

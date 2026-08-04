#!/usr/bin/env python3
"""First official minimal rewrite — unattended, guarded, overnight.

PHASE 1 (boot-free, host cc+rustc — the bulk, machine-light):
  A.  GPIO template family   — template_synth ($0) -> gpio_family trace oracle.
  B.  struct-readers         — (READERS=1) structdiff mirror differential.
  B2. container-ADT mutators — (CONTAINERS=1) representation-independent ADT
                               differential (container_adt reach+harness).
  B3. bounded-state fns      — (EFFTRACE=1) per-call full-footprint state
                               differential (efftrace reach+harness).
  C.  scalar exported leaves — ladder synth (local Qwen $0 -> Haiku, budget-capped)
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
import purity              # noqa: E402  (leaf front gate: purity routing)
import widerun             # noqa: E402  (harvest, SCALAR, rsig, build_boot, probe, PRELUDE)
import template_synth      # noqa: E402
import gpio_family         # noqa: E402
import harness as sd_harness  # noqa: E402  (structdiff: prepare/close for struct-readers)

# container_adt ships modules named reach/harness too — load by explicit path
# under unique names (the proof.py-collision lesson), never via sys.path.
import importlib.util          # noqa: E402


def _load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cadt_harness = _load_by_path(
    "cadt_harness", os.path.join(HERE, "..", "container_adt", "harness.py"))
eff_harness = _load_by_path(
    "eff_harness", os.path.join(HERE, "..", "efftrace", "harness.py"))
alloc_harness = _load_by_path(
    "alloc_harness", os.path.join(HERE, "..", "allocmodel", "harness.py"))
shardlib = _load_by_path("shardlib", os.path.join(HERE, "shardlib.py"))

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


def _repair_prompt(prompt, body, feedback):
    return (prompt + "\n\nYour previous attempt:\n" + body[:2000]
            + "\n\nIt FAILED the gate with:\n" + feedback[:1500]
            + "\n\nOutput the corrected body ONLY (same rules).")


def ladder(prompt, gate, repair=False):
    """gate(candidate_body:str) -> bool | (bool, feedback:str). Try local first
    ($0); if it fails and budget remains, escalate to Haiku. With repair=True,
    a failing rung whose gate returned non-empty feedback gets ONE re-prompt
    carrying that feedback (compile-error/diff repair round — the gate stays
    the arbiter, so repair only costs retries, never soundness).
    Returns (body|None, model, cost)."""
    def _check(body):
        r = gate(body)
        return r if isinstance(r, tuple) else (r, "")

    try:
        body = _ollama(prompt)
        ok, fb = _check(body)
        if ok:
            return body, "local", 0.0
        if repair and fb:
            body = _ollama(_repair_prompt(prompt, body, fb))
            if _check(body)[0]:
                return body, "local+fix", 0.0
    except Exception as e:
        log(f"  local synth error: {str(e)[:80]}")
    if budget_left() <= 0.02:               # keep a cushion; never exceed the cap
        return None, "budget", 0.0
    try:
        body, cost = _haiku(prompt)
        ok, fb = _check(body)
        if ok:
            return body, "haiku", cost
        if repair and fb and budget_left() > 0.02:
            body, c2 = _haiku(_repair_prompt(prompt, body, fb))
            cost += c2
            if _check(body)[0]:
                return body, "haiku+fix", cost
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


def _key(kind, rel, fn):
    """File-qualified checkpoint key. Same-named fns in different files MUST
    NOT collide — Run 1's invariant-4 breach was exactly this (cache_contiguous
    in two files silently skipped under the bare-name key)."""
    return f"{kind}_{rel.replace('/', '__')}_{fn}"


def _reader_cand(p, body):
    return p["mirror_rust"] + "\n" + p["sig"] + " { unsafe {\n" + body + "\n}}\n"


def solve_reader(item, done):
    rel, fn = item["file"], item["fn"]
    key = _key("reader", rel, fn)
    if key in done or time_left() < 300:
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
        open(os.path.join(VERIFIED, f"{key}.rs"), "w").write(_reader_cand(p, body))
        log(f"  ✓ reader {fn} via {model} (${cost:.4f}) [spent ${spent():.3f}]")
        return {"sym": fn, "kind": "struct-reader", "model": model, "cost": cost,
                "file": rel, "key": key}
    log(f"  ✗ reader {fn} unsolved ({model})")
    return None


# ---------------------------------------------------------------------------
# corpus B2: container-ADT mutators, gated boot-free by the ADT differential
# ---------------------------------------------------------------------------

_CONTAINER_PROMPT = """Translate this Linux kernel LIST-mutating function into the BODY of a Rust
function operating on an abstract-data-type model (lists = ordered sequences of
node ids, node fields = tables). Exact signature (write ONLY the code inside
its braces; it must end in an i64 value — use 0 for void):
{sig}

{doc}

Helper signatures (EXACT — these are the ONLY functions that exist):
  fn iter(l: usize) -> Vec<u32>        // snapshot of list l; `for id in iter(L_X)` gives id: u32
  fn empty(l: usize) -> bool           // list_empty
  fn del(id: u32)                      // list_del: unlink id from whichever list holds it
  fn push_back(l: usize, id: u32)      // list_add_tail(&node, list)
  fn push_front(l: usize, id: u32)     // list_add(&node, list)
  fn move_tail(l: usize, id: u32)      // list_move_tail
  fn move_front(l: usize, id: u32)     // list_move
  fn field(id: u32, f: usize) -> i64   // read node SCALAR field F_*
  fn set_field(id: u32, f: usize, v: i64)
  fn tokf(id: u32, t: usize) -> i64    // read node POINTER field T_* as an opaque token
  fn tok_field(h: i64, f: usize) -> i64 // read scalar field P_* of a token-object arg
  fn retire(id: u32)                   // kfree(node) — call exactly where the C frees

Rules: use ONLY these helpers, the F_*/T_*/P_*/L_* constants, and the a0..aN
args (pointer-struct args are opaque tokens: compare with `tokf(id, T_X) == aN`,
read their fields with tok_field). iter() is a snapshot — del() while walking is
safe. Locks in the C are already handled outside the model: IGNORE lock/unlock
calls. PRESERVE C SEMANTICS EXACTLY: if the C compares `unsigned` values, cast
both sides `as u64` before comparing (an i64 -1 is a HUGE unsigned); kernel
error returns are numeric (-EINVAL = -22, -ENOMEM = -12, -EBUSY = -16,
-ENOENT = -2, -EEXIST = -17). No unsafe, no statics, no external calls, no
panics. If the C does something the helpers can't express, reply exactly
`// UNSUPPORTED`. Output ONLY the body, no signature, no outer braces, no
``` fences.
"""


def solve_container(item, done):
    fn, rel = item["fn"], item["file"]
    key = _key("container", rel, fn)
    if key in done or time_left() < 300:
        return None
    try:
        prep = cadt_harness.prepare(item)
    except Exception as e:
        log(f"  ✗ container {fn} prepare-refuse ({str(e)[:48]})")
        return None
    prompt = _CONTAINER_PROMPT.format(sig=prep["rs_sig"], doc=prep["doc"])

    def gate(body):
        if "UNSUPPORTED" in body:
            return False, ""
        with tempfile.TemporaryDirectory() as d:
            r = cadt_harness.close(prep, body, workdir=d)
        if r["verdict"] == "MATCH":
            return True, ""
        # feed compile errors / divergence back for the repair round; coverage
        # refusals are the WORKLOAD's fault, not the candidate's — no repair.
        fb = r["out"] if r["verdict"].startswith(("BUILD_FAIL", "DIVERGE")) else ""
        return False, fb

    body, model, cost = ladder(prompt, gate, repair=True)
    if body:
        open(os.path.join(VERIFIED, f"{key}.rs"), "w").write(
            prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + body + "\n}\n")
        fl = prep["flags"]
        log(f"  ✓ container {fn} via {model} (${cost:.4f}) "
            f"[locks_stripped={fl['locks_stripped']} alloc_stripped={fl['alloc_stripped']}]")
        return {"sym": fn, "kind": "container-adt", "model": model, "cost": cost,
                "file": rel, "flags": fl}
    log(f"  ✗ container {fn} unsolved ({model})")
    return None


# ---------------------------------------------------------------------------
# corpus B3: bounded-state fns, gated boot-free by the state differential
# ---------------------------------------------------------------------------

_EFFTRACE_PROMPT = """Translate this Linux kernel state-mutating function into the BODY of a Rust
function operating on a flat state-cell model (every global / out-param /
struct field the C touches is an i64 cell). Exact signature (write ONLY the
code inside its braces; it must end in an i64 value — use 0 for void):
{sig}

{doc}

Helper signatures (EXACT — these are the ONLY functions that exist):
  fn g(ix: usize) -> i64            // read global cell G_*
  fn set_g(ix: usize, v: i64)
  fn out(ix: usize) -> i64          // read out-param cell OUT_*
  fn set_out(ix: usize, v: i64)
  fn field(base: usize, slot: i64) -> i64   // struct-param field F*_X at slot
  fn set_field(base: usize, slot: i64, v: i64)

Rules: use ONLY these helpers, the documented constants, and the a0..aN args.
When the C branches on a named constant (F_RDLCK, RB_BLACK, ...), USE that
named constant from the list above — do NOT hardcode its numeric value (it may
not be what you'd guess). PRESERVE C SEMANTICS EXACTLY on the i64 cells:
replicate the C's arithmetic
(+= is not |=), C unsigned comparisons cast both sides `as u64`, C integer
truncation/width effects matter only as far as the C itself exhibits them.
A store to a narrow field WRAPS to that width: if the C uses a value AFTER
writing it to a u8/u16/u32 field (e.g. `x += n; if (x >= lim)`), wrap it
(`(x as u32) as i64`) before the later use, or re-read it via field().
Kernel error returns are numeric (-EINVAL = -22, -ENOMEM = -12, -EBUSY = -16).
Locks in the C are handled outside the model: IGNORE lock/unlock calls. No
unsafe, no statics, no external calls, no panics. If the C does something the
helpers can't express, reply exactly `// UNSUPPORTED`. Output ONLY the body,
no signature, no outer braces, no ``` fences.
"""


def solve_efftrace(item, done):
    fn, rel = item["fn"], item["file"]
    key = _key("efftrace", rel, fn)
    if key in done or time_left() < 300:
        return None
    try:
        prep = eff_harness.prepare(item)
        # directed workload synthesis: drive conditional writes the undirected
        # workload can't reach (guarded stores, switch constants, per-slot
        # writes) so they don't spuriously REFUSE_COVERAGE. Best-effort; only
        # ADDS calls, so it can't weaken the gate.
        prep = eff_harness.with_directed(prep)
    except Exception as e:
        log(f"  ✗ efftrace {fn} prepare-refuse ({str(e)[:48]})")
        return None
    prompt = _EFFTRACE_PROMPT.format(sig=prep["rs_sig"], doc=prep["doc"])

    def gate(body):
        if "UNSUPPORTED" in body:
            return False, ""
        with tempfile.TemporaryDirectory() as d:
            r = eff_harness.close(prep, body, workdir=d)
        if r["verdict"] == "MATCH":
            return True, ""
        fb = r["out"] if r["verdict"].startswith(("BUILD_FAIL", "DIVERGE")) else ""
        return False, fb

    body, model, cost = ladder(prompt, gate, repair=True)
    if body:
        open(os.path.join(VERIFIED, f"{key}.rs"), "w").write(
            prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + body + "\n}\n")
        log(f"  ✓ efftrace {fn} via {model} (${cost:.4f}) "
            f"[locks_stripped={prep['flags']['locks_stripped']}]")
        return {"sym": fn, "kind": "efftrace", "model": model, "cost": cost,
                "file": rel, "flags": prep["flags"]}
    log(f"  ✗ efftrace {fn} unsolved ({model})")
    return None


# ---------------------------------------------------------------------------
# corpus B4: alloc-init fns, gated boot-free by the fresh-slot differential
# ---------------------------------------------------------------------------

_ALLOC_PROMPT = """Translate this Linux kernel allocate-and-initialize function into the BODY
of a Rust function operating on a flat state-cell model with a fresh-slot
allocator. Exact signature (write ONLY the code inside its braces; it must
end in an i64 value — the allocated slot id for `return p`, or -1 for a NULL
return):
{sig}

{doc}

Helper signatures (EXACT — these are the ONLY functions that exist):
  fn alloc() -> i64                        // fresh ZEROED slot id; NEVER fails
  fn af(base: usize, id: i64) -> i64       // read allocated-object field A_*
  fn set_af(base: usize, id: i64, v: i64)  // write allocated-object field A_*
  fn g(ix: usize) -> i64                   // read global cell G_*
  fn set_g(ix: usize, v: i64)
  fn field(base: usize, slot: i64) -> i64  // struct-param field F*_X at slot
  fn set_field(base: usize, slot: i64, v: i64)

Rules: call alloc() exactly once per C allocation, at the same point in the
control flow. The C's `if (!p) return NULL` allocation-failure branch is DEAD
in this model (alloc never fails) — OMIT it. kzalloc ZEROES: fields you don't
write stay 0. kfree is a no-op. gfp flag arguments are irrelevant. Use ONLY
the helpers, the documented constants, and the a0..aN args. When the C
branches on a named constant, USE the named constant from the list — do NOT
hardcode a guessed value. PRESERVE C SEMANTICS EXACTLY on the i64 cells
(+= is not |=; C unsigned comparisons cast both sides `as u64`). A store to a
narrow field WRAPS to that width — replicate the C's VALUE logic only.
No unsafe, no statics, no external calls, no panics. If the C does something
the helpers can't express, reply exactly `// UNSUPPORTED`. Output ONLY the
body, no signature, no outer braces, no ``` fences.
"""


def solve_alloc(item, done):
    fn, rel = item["fn"], item["file"]
    key = _key("alloc", rel, fn)
    if key in done or time_left() < 300:
        return None
    try:
        prep = alloc_harness.prepare(item)
    except Exception as e:
        log(f"  ✗ alloc {fn} prepare-refuse ({str(e)[:48]})")
        return None
    prompt = _ALLOC_PROMPT.format(sig=prep["rs_sig"], doc=prep["doc"])

    def gate(body):
        if "UNSUPPORTED" in body:
            return False, ""
        with tempfile.TemporaryDirectory() as d:
            r = alloc_harness.close(prep, body, workdir=d)
        if r["verdict"] == "MATCH":
            return True, ""
        fb = r["out"] if r["verdict"].startswith(("BUILD_FAIL", "DIVERGE")) else ""
        return False, fb

    body, model, cost = ladder(prompt, gate, repair=True)
    if body:
        open(os.path.join(VERIFIED, f"{key}.rs"), "w").write(
            prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + body + "\n}\n")
        fl = prep["flags"]
        log(f"  ✓ alloc {fn} via {model} (${cost:.4f}) "
            f"[kmalloc_zero_modeled={fl['kmalloc_zero_modeled']}]")
        return {"sym": fn, "kind": "alloc-init", "model": model, "cost": cost,
                "file": rel, "flags": fl}
    log(f"  ✗ alloc {fn} unsolved ({model})")
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
    _sh = os.environ.get("GRIND_SHARD"), os.environ.get("GRIND_OF")
    log(f"=== first official minimal rewrite: budget ${BUDGET_CAP} / {RUNTIME_CAP_H}h "
        f"/ {WORKERS} workers / resume={len(done)} done"
        f"{f' / shard {_sh[0]}/{_sh[1]}' if _sh[0] else ''} ===")
    solved = []

    # corpus A — $0, guaranteed, always runs first so there is always a result
    log("phase 1A: GPIO template family ($0)")
    solved += solve_family()

    # corpus B — pure struct-readers via structdiff (READERS=1). The big clean
    # class (reach_accepted.json); boot-free, gate-arbitrated like everything else.
    if os.environ.get("READERS") == "1":
        rj = os.path.join(HERE, "..", "structdiff", "reach_accepted.json")
        readers = shardlib.shard_env(json.load(open(rj)) if os.path.exists(rj) else [])
        log(f"phase 1B: {len(readers)} struct-readers (structdiff, boot-free, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(solve_reader, it, done): it for it in readers}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    solved.append(r)
                    done.add(_key("reader", r["file"], r["sym"]))
                    json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
                if time_left() < 300:
                    break

    # corpus B2 — container-ADT list mutators (CONTAINERS=1). Boot-free ADT
    # differential over reach_accepted.json; verdict flags name the stripped
    # halves (locks -> concgate, alloc -> allocator model).
    if os.environ.get("CONTAINERS") == "1":
        cj = os.path.join(HERE, "..", "container_adt", "reach_accepted.json")
        citems = shardlib.shard_env(json.load(open(cj)) if os.path.exists(cj) else [])
        log(f"phase 1B2: {len(citems)} container-ADT mutators "
            f"(ADT differential, boot-free, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(solve_container, it, done): it for it in citems}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    solved.append(r)
                    done.add(_key("container", r["file"], r["sym"]))
                    json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
                if time_left() < 300:
                    break

    # corpus B3 — bounded-state fns (EFFTRACE=1). Boot-free per-call
    # full-footprint state differential over efftrace/reach_accepted.json.
    if os.environ.get("EFFTRACE") == "1":
        ej = os.path.join(HERE, "..", "efftrace", "reach_accepted.json")
        eitems = shardlib.shard_env(json.load(open(ej)) if os.path.exists(ej) else [])
        log(f"phase 1B3: {len(eitems)} bounded-state fns "
            f"(state differential, boot-free, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(solve_efftrace, it, done): it for it in eitems}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    solved.append(r)
                    done.add(_key("efftrace", r["file"], r["sym"]))
                    json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
                if time_left() < 300:
                    break

    # corpus B4 — alloc-init fns (ALLOCMODEL=1). Boot-free fresh-slot
    # differential over allocmodel/reach_accepted.json.
    if os.environ.get("ALLOCMODEL") == "1":
        aj = os.path.join(HERE, "..", "allocmodel", "reach_accepted.json")
        aitems = shardlib.shard_env(json.load(open(aj)) if os.path.exists(aj) else [])
        log(f"phase 1B4: {len(aitems)} alloc-init fns "
            f"(fresh-slot differential, boot-free, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(solve_alloc, it, done): it for it in aitems}
            for fut in cf.as_completed(futs):
                r = fut.result()
                if r:
                    solved.append(r)
                    done.add(_key("alloc", r["file"], r["sym"]))
                    json.dump({"done": sorted(done)}, open(PROGRESS, "w"))
                if time_left() < 300:
                    break

    # corpus C — scalar leaves via the ladder, boot-free hostdiff gate.
    # LEAF FRONT GATE (Run 2): only purity-pure, TU-liftable leaves enter the
    # denominator. Run 1 measured the unscoped harvest — 2/72, with most
    # misses CC_TU_FAIL or impure fns the router correctly refuses: honest
    # refusals in a dishonest denominator. Refusals are logged by class.
    log("phase 1C: harvesting scalar exported leaves...")
    raw = shardlib.shard_env(widerun.harvest()) if N_LEAVES else []
    work, scope_refused = [], {}
    for w in raw:
        cls, why = purity.classify(w["body"], set())
        if cls != "pure":
            scope_refused.setdefault(f"impure: {why[:36]}", []).append(w["sym"])
            continue
        ok, detail = hostdiff.tu_compiles(w["file"], KSRC, w["sym"])
        if not ok:
            scope_refused.setdefault(f"tu: {detail.splitlines()[-1][:44] if detail else '?'}",
                                     []).append(w["sym"])
            continue
        work.append(w)
    for k, v in sorted(scope_refused.items(), key=lambda kv: -len(kv[1])):
        log(f"  leaf-scope refuse {len(v):3d}  {k}  e.g. {v[0]}")
    log(f"phase 1C: scoped denominator {len(work)}/{len(raw)}")
    # sharding/scoping on the RAW list above; now done-filter + cap
    work = [w for w in work if w["sym"] not in done][:N_LEAVES]
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
    # solves (else phase 2 under-weaves after a resume). reader_/container_
    # artifacts are boot-free oracle candidates, NOT freestanding kernel objects.
    for f in os.listdir(VERIFIED):
        if f.endswith(".rs") and not f.startswith(("reader_", "container_", "efftrace_", "alloc_")):
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

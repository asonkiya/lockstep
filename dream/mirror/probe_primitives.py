#!/usr/bin/env python3
"""In-kernel opaque-primitive sizing.

The opaque kernel primitives (spinlock_t, struct mutex, atomic_t, list_head, ...)
have CONFIG-DEPENDENT sizes — spinlock_t is 4 bytes with no debug options, but
grows under PROVE_LOCKING/DEBUG_SPINLOCK/LOCKDEP. So the mirror generator cannot
guess their layout on the host; it REFUSES them. This module measures the REAL
sizes from the REAL kernel build, soundly:

  1. Emit a tiny probe.c compiled *inside the kernel build* (real headers, real
     .config) with, per primitive T:
         char cgir_sz_<tag>[sizeof(T)];
         char cgir_al_<tag>[__alignof__(T)];
     Each array's *symbol size* in the ELF table equals the value we want —
     no printf, no execution, just compile + read the symbol table.
  2. Read the symbol sizes with `nm --print-size` on the resulting object.
  3. Write primitive_sizes.json = { "spinlock_t": [size, align], ... }.

The mirror generator then emits an opaque field as an array of the alignment-
matching integer ([u64; size/8] for align-8, [u32; size/4] for align-4, ...),
giving BOTH correct size AND correct alignment so the *parent* struct's field
offsets are right. The existing two-legged mirror gate (rustc const-asserts +
kernel BUILD_BUG_ON on the parent's sizeof/offsetof) then RE-CERTIFIES every
emitted layout against the real kernel — a wrong probe value cannot pass the
gate. The kernel itself is the oracle for these sizes.

Soundness note: the measured sizes are valid for the .config they were probed
against — the SAME .config the mirror gate's kernel leg builds with (the
cgir-kbuild volume). Probe and re-certification are on one config, so they are
consistent by construction. Re-probe on a config change.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
CACHE = os.path.join(HERE, "primitive_sizes.json")

# (C type, header that defines it). tag = sanitized type name.
PRIMITIVES = [
    ("spinlock_t", "linux/spinlock.h"),
    ("raw_spinlock_t", "linux/spinlock.h"),
    ("rwlock_t", "linux/rwlock.h"),
    ("seqlock_t", "linux/seqlock.h"),
    ("seqcount_t", "linux/seqlock.h"),
    ("atomic_t", "linux/atomic.h"),
    ("atomic64_t", "linux/atomic.h"),
    ("atomic_long_t", "linux/atomic.h"),
    ("refcount_t", "linux/refcount.h"),
    ("wait_queue_head_t", "linux/wait.h"),
    ("struct mutex", "linux/mutex.h"),
    ("struct rw_semaphore", "linux/rwsem.h"),
    ("struct semaphore", "linux/semaphore.h"),
    ("struct completion", "linux/completion.h"),
    ("struct list_head", "linux/types.h"),
    ("struct hlist_head", "linux/types.h"),
    ("struct hlist_node", "linux/types.h"),
    ("struct rcu_head", "linux/types.h"),
    ("struct llist_head", "linux/llist.h"),
    ("struct llist_node", "linux/llist.h"),
    ("struct kref", "linux/kref.h"),
    ("struct work_struct", "linux/workqueue.h"),
    ("struct delayed_work", "linux/workqueue.h"),
    ("struct timer_list", "linux/timer.h"),
    ("struct hrtimer", "linux/hrtimer.h"),
    ("struct rb_root", "linux/rbtree.h"),
    ("struct rb_node", "linux/rbtree.h"),
    ("struct callback_head", "linux/types.h"),
    # --- Milestone-B census additions: the top opaque field types blocking the
    # accepted Tier-B surface. Structs sized as layout blobs; scalar/pointer
    # typedefs sized as the alignment-matching integer. All measured in-kernel,
    # re-certified by the parent's BUILD_BUG_ON at transplant. ---
    ("struct kobject", "linux/kobject.h"),
    ("struct kset", "linux/kobject.h"),
    ("struct device", "linux/device.h"),
    ("struct device_node", "linux/of.h"),
    ("struct cpumask", "linux/cpumask.h"),
    ("ktime_t", "linux/ktime.h"),
    ("kuid_t", "linux/uidgid.h"),
    ("kgid_t", "linux/uidgid.h"),
    ("pgoff_t", "linux/types.h"),
    ("loff_t", "linux/types.h"),
    ("acpi_handle", "linux/acpi.h"),
]

# Extra headers for the census additions (broad, fail-loud at compile).
_CENSUS_HEADERS = [
    "linux/kobject.h", "linux/device.h", "linux/of.h", "linux/cpumask.h",
    "linux/ktime.h", "linux/uidgid.h",
]


def census_extra_types(registry_path=None):
    """Opaque field types the mirror factory REFUSED — so the probe list is
    driven by measured demand, not a static guess. Returns [(ctype, header?)]
    for struct/typedef refusals not already covered."""
    import json as _json
    registry_path = registry_path or os.path.join(
        HERE, "..", "mirrorfactory", "registry.json")
    if not os.path.isfile(registry_path):
        return []
    reg = _json.load(open(registry_path))
    have = {c for c, _ in PRIMITIVES}
    seen, out = set(have), []
    for info in reg.get("refused", {}).values():
        m = re.search(r"type '([^']+)'", info.get("reason", ""))
        if not m:
            continue
        t = m.group(1).strip()
        # only plain struct/typedef names (no ptr/enum/array/fn-ptr) — those
        # are other wideners; the probe sizes concrete named types.
        if (t in seen or "*" in t or "enum " in t or "[" in t
                or "(" in t or "#" in t or " " in t.replace("struct ", "")):
            continue
        seen.add(t)
        out.append((t, None))
    return out

# Umbrella headers pull in almost everything above transitively; probing inside
# a real kernel build these resolve. Kept explicit and broad so a missing type
# fails loudly at compile rather than silently vanishing.
HEADERS = [
    "linux/kernel.h", "linux/types.h", "linux/spinlock.h", "linux/rwlock.h",
    "linux/seqlock.h", "linux/mutex.h", "linux/rwsem.h", "linux/semaphore.h",
    "linux/completion.h", "linux/atomic.h", "linux/refcount.h", "linux/wait.h",
    "linux/list.h", "linux/llist.h", "linux/rbtree.h", "linux/kref.h",
    "linux/workqueue.h", "linux/timer.h", "linux/hrtimer.h",
] + _CENSUS_HEADERS


def tag_of(ctype):
    return re.sub(r"\W+", "_", ctype).strip("_")


def emit_probe_c(primitives=PRIMITIVES):
    lines = ["/* AUTO-GENERATED by probe_primitives.py — in-kernel size probe. */"]
    lines += [f"#include <{h}>" for h in HEADERS]
    lines.append("")
    for ctype, _ in primitives:
        t = tag_of(ctype)
        lines.append(f"char cgir_sz_{t}[sizeof({ctype})];")
        lines.append(f"char cgir_al_{t}[__alignof__({ctype})];")
    return "\n".join(lines) + "\n"


def parse_nm(nm_out, primitives=PRIMITIVES):
    """nm --print-size lines: `<value> <size> <type> <name>`. Symbol size = value."""
    sizes = {}
    for line in nm_out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            sz = int(parts[1], 16)
        except ValueError:
            continue
        sizes[parts[-1]] = sz
    result = {}
    for ctype, _ in primitives:
        t = tag_of(ctype)
        s, a = sizes.get(f"cgir_sz_{t}"), sizes.get(f"cgir_al_{t}")
        if s is not None and a is not None:
            result[ctype] = [s, a]
    return result


_UNDECLARED = re.compile(r"error: '([^']+)' undeclared")


def _build_and_read(primitives):
    """Emit + build the probe once. Returns (nm_output, compile_errors)."""
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "probe.c"), "w").write(emit_probe_c(primitives))
    vol, img, gate = "cgir-kbuild", "cgir-kernel-gate", "crypto/lockstep_gate"
    subprocess.run([
        "docker", "run", "--rm", "-v", f"{vol}:/build", "-v", f"{OUT}:/o:ro", img,
        "bash", "-euc",
        f"cd /build/linux; mkdir -p {gate}; "
        f"grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile; "
        f"cd {gate}; rm -f probe.c probe.o; cp /o/probe.c .; printf 'obj-y := probe.o\\n' > Kbuild",
    ], check=True)
    r = subprocess.run([
        "docker", "run", "--rm", "-v", f"{vol}:/build", img,
        "bash", "-uc",
        f"cd /build/linux && make -s {gate}/probe.o 2>&1 | tail -60 && "
        f"nm --print-size --defined-only {gate}/probe.o 2>/dev/null || true",
    ], capture_output=True, text=True)
    return r.stdout


def run_probe(primitives=None, max_drops=6):
    """Build the probe inside the kernel and read the symbol table. Returns dict.
    An UNDECLARED type (subsystem typedef whose header isn't pulled in, or a
    config-gated type) drops that type and re-probes — the probe measures
    whatever the real build actually declares, and honestly reports the rest as
    missing. Fail-closed: an undecidable type is dropped, never guessed."""
    if primitives is None:
        primitives = PRIMITIVES + census_extra_types()
    prims = list(primitives)
    for _ in range(max_drops):
        out = _build_and_read(prims)
        bad = set(_UNDECLARED.findall(out))
        # map an undeclared token back to the primitive whose tag/name uses it
        if bad:
            keep = [(c, h) for c, h in prims
                    if c not in bad and c.replace("struct ", "") not in bad]
            dropped = [c for c, _ in prims if (c, None) not in [(k, None) for k, _ in keep]]
            sys.stderr.write(f"  dropping undeclared: {', '.join(sorted(bad))[:120]}\n")
            if len(keep) == len(prims):    # couldn't attribute -> stop
                break
            prims = keep
            continue
        sizes = parse_nm(out, prims)
        if sizes:
            return sizes
        sys.stderr.write("probe produced no sizes:\n" + out[-800:] + "\n")
        raise SystemExit(1)
    # final attempt after drops
    sizes = parse_nm(_build_and_read(prims), prims)
    if not sizes:
        raise SystemExit(1)
    return sizes


def load_cache():
    if os.path.isfile(CACHE):
        return json.load(open(CACHE))
    return {}


def main():
    primitives = PRIMITIVES + census_extra_types()
    new_sizes = run_probe(primitives)
    sizes = load_cache()
    sizes.update(new_sizes)           # merge: never lose a prior measurement
    json.dump(sizes, open(CACHE, "w"), indent=2, sort_keys=True)
    print(f"-> {os.path.relpath(CACHE)}  ({len(new_sizes)} probed this run, "
          f"{len(sizes)} total)")
    for ctype, (sz, a) in sorted(new_sizes.items()):
        print(f"   {ctype:28s} size={sz:3d} align={a}")
    missing = [c for c, _ in primitives if c not in new_sizes]
    if missing:
        print("   MISSING (did not compile/resolve):", ", ".join(missing))


if __name__ == "__main__":
    main()

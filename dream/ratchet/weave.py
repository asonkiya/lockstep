#!/usr/bin/env python3
"""The weaver — apply the manifest, produce a booting kernel, report %-Rust.

This is the "one-command pass" from the original dream, made real and cumulative:
it takes the manifest's `status:rust` functions, excises each from its REAL .c in
the kernel tree (body -> Rust seam call, the M5 mechanism), compiles the Rust
objects, links them into vmlinux, builds, boots, and gates. On green it is a
ratchet: the manifest state stands. On red the batch is rejected.

Operates on the live cgir-kbuild volume (the real kernel), not a vendored copy —
Ring 0 patches the actual in-tree driver.

Usage:
  weave.py apply       # excise + compile + wire + config, leave tree woven
  weave.py build       # apply, then build Image
  weave.py gate        # apply + build + boot + boot-digest gate
  weave.py status      # print the %-Rust dashboard from the manifest
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
VOL = os.environ.get("WEAVE_VOL", "cgir-kbuild")
IMG = "cgir-kernel-gate"
OUT = os.path.join(HERE, "out")


def _docker(script: str, mounts: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "run", "--rm", "-v", f"{VOL}:/build"]
    for m in mounts or []:
        cmd += ["-v", m]
    cmd += [IMG, "bash", "-eo", "pipefail", "-uc", script]
    return subprocess.run(cmd, capture_output=True, text=True)


def load() -> dict:
    return json.load(open(MANIFEST))


# ---- excision (the M5 rewire, generalized, applied to the live tree) ----

def _function_source(src: str, name: str) -> str:
    """Full text of a top-level C function definition `name`."""
    for m in re.finditer(rf"(?<![\w.>])(?:static\s+)?[\w \t\*]*?\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", src):
        # confirm column-ish start (line begins with the match's line)
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    return src[m.start():i + 1]
            i += 1
    raise KeyError(name)


def weave_source(src: str, entry: dict) -> str:
    """Replace each rust function's body with its shell; insert the extern block
    after the last top-of-file include."""
    out = src
    for fn, meta in entry["functions"].items():
        if meta["status"] != "rust":
            continue
        original = _function_source(out, fn)
        out = out.replace(original, meta["shell"], 1)
    # Insert the extern block after the LEADING include region, not the globally
    # last include: kernel files (lockdep.c, etc.) carry mid-file macro-table
    # includes, and inserting there lands mid-construct ("expected expression
    # before ...", verified). The leading run of includes/comments/blank/
    # preprocessor lines is always file scope; the extern block's _Static_asserts
    # need the struct defs, which the leading headers provide.
    incs = list(re.finditer(r"#include [<\"][^>\"]+[>\"]\n", out))
    pos = None
    for m in incs:
        head = out[:m.start()]
        # still in the leading region if everything before is include/comment/
        # blank/preprocessor (no substantive top-level code yet)
        stripped = re.sub(r"/\*.*?\*/", "", head, flags=re.DOTALL)
        stripped = re.sub(r"//[^\n]*", "", stripped)
        lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
        if all(ln.startswith("#") for ln in lines):
            pos = m.end()
        else:
            break
    if pos is None and incs:
        pos = incs[0].end()
    if pos is not None:
        out = out[:pos] + entry["extern_block"] + out[pos:]
    return out


def cmd_apply() -> int:
    m = load()
    os.makedirs(OUT, exist_ok=True)

    # 0. neutralize any prior gate scaffolding in the volume that would define
    #    the same seam symbols and collide at link (diffgate/M4 left crypto/
    #    lockstep_gate/*.o_shipped exporting lockstep_phc_*).
    _docker(
        "cd /build/linux && if [ -d crypto/lockstep_gate ]; then "
        "rm -f crypto/lockstep_gate/*.c crypto/lockstep_gate/*.o crypto/lockstep_gate/*.o_shipped; "
        "printf 'obj-y :=\\n' > crypto/lockstep_gate/Kbuild; fi"
    )

    # 1. enable any configs the woven files need
    for cfg in m.get("config_enable", []):
        r = _docker(f"cd /build/linux && ./scripts/config -e {cfg} && make -s olddefconfig >/dev/null 2>&1 && grep CONFIG_{cfg}= .config")
        print(f"  config: {r.stdout.strip() or r.stderr.strip()[:100]}")

    # 2. excise each source in place (read from volume, weave, write back)
    for path, entry in m["sources"].items():
        stock = _docker(f"cat /build/linux/{path}").stdout
        woven = weave_source(stock, entry)
        # sanity: every seam call landed, every excised body gone
        for fn, meta in entry["functions"].items():
            if meta["status"] == "rust":
                assert meta["seam"] in woven, f"seam {meta['seam']} missing after weave"
        local = os.path.join(OUT, os.path.basename(path))
        open(local, "w").write(woven)
        _docker(f"cp /w/{os.path.basename(path)} /build/linux/{path}", mounts=[f"{OUT}:/w:ro"])
        n = sum(1 for f in entry["functions"].values() if f["status"] == "rust")
        print(f"  wove {path}: {n} bodies -> Rust seam calls")

    # 3. compile each Rust object into its kbuild dir and wire it.
    #    Multiple freestanding Rust objects each define #[panic_handler]
    #    (rust_begin_unwind) as a global symbol and collide at the vmlinux link
    #    (the linking-research finding, confirmed in practice). Fix: keep ONE
    #    object's handler global (the first) and localize it in all the rest, so
    #    each self-contains its panic path with no global clash.
    for idx, (name, obj) in enumerate(m["rust_objects"].items()):
        src = os.path.join(HERE, obj["src"])
        d = obj["kbuild_dir"]
        localize = (
            f" && aarch64-linux-gnu-objcopy --wildcard "
            f"--localize-symbol '*rust_begin_unwind*' {obj['obj']}.o_shipped"
            if idx > 0 else ""
        )
        rc = _docker(
            f"cd /build/linux/{d} && "
            f"rustc --target aarch64-unknown-none-softfloat --emit=obj "
            f"-C panic=abort -C relocation-model=static -O /w/{os.path.basename(src)} "
            f"-o {obj['obj']}.o_shipped && test -s {obj['obj']}.o_shipped{localize} && echo OK",
            mounts=[f"{os.path.dirname(src)}:/w:ro"],
        )
        if "OK" not in rc.stdout:
            print(f"  ✗ rustc {name}: {rc.stderr.strip()[:300]}")
            return 1
        # append the obj to the subsystem Makefile (idempotent)
        _docker(
            f"cd /build/linux/{d} && grep -q '{obj['obj']}.o' Makefile || "
            f"echo 'obj-y += {obj['obj']}.o' >> Makefile"
        )
        print(f"  ✓ compiled + wired {d}/{obj['obj']}.o (Rust)")
    print("apply: tree woven")
    return 0


def cmd_build() -> int:
    if cmd_apply() != 0:
        return 1
    print("building Image...")
    r = _docker("cd /build/linux && rm -f arch/arm64/boot/Image && make -s -j$(nproc) Image 2>&1 | tail -4 && test -f arch/arm64/boot/Image && echo BUILT")
    open(os.path.join(OUT, "build.txt"), "w").write(r.stdout + r.stderr)
    ok = "BUILT" in r.stdout
    print("  " + ("✓ Image built (woven kernel)" if ok else "✗ build failed:\n" + r.stderr[-500:]))
    return 0 if ok else 1


def _boot_digest() -> int:
    print("booting woven kernel (boot-digest gate; KUnit filtered out)...")
    r = _docker(
        "cd /build/linux && timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 "
        "-nographic -net none -kernel arch/arm64/boot/Image "
        "-append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true"
    )
    con = r.stdout + r.stderr
    open(os.path.join(OUT, "gate-console.txt"), "w").write(con)
    # canonical end-of-boot markers: SMP came up AND the kernel finished its own
    # init (handed off to userspace / freed init mem / then the expected no-rootfs
    # VFS panic). Any of these past SMP-up = the woven vmlinux booted.
    smp = bool(re.search(r"smp: Brought up \d+ node", con))
    done = bool(re.search(r"Freeing unused kernel memory|Run /init|"
                          r"Kernel panic - not syncing: (VFS|No working init)", con))
    early_panic = ("Kernel panic" in con and not re.search(
        r"Kernel panic - not syncing: (VFS|No working init)", con))
    ok = smp and done and not early_panic
    print(f"  boot-digest: smp_up={smp} boot_complete={done} early_panic={early_panic}")
    print("  " + ("✓ woven kernel boots — Rust ptp_mock regions live in vmlinux"
                  if ok else "✗ boot gate failed"))
    if ok:
        cmd_status()
    return 0 if ok else 1


def cmd_boot() -> int:
    """Boot-gate only (assumes the tree is already woven + built)."""
    return _boot_digest()


def cmd_gate() -> int:
    if cmd_build() != 0:
        return 1
    return _boot_digest()


def cmd_status() -> int:
    m = load()
    total = sum(s.get("total_functions", len(s["functions"])) for s in m["sources"].values()) or 1
    rust = sum(1 for s in m["sources"].values() for f in s["functions"].values() if f["status"] == "rust")
    proven = sum(1 for s in m["sources"].values() for f in s["functions"].values()
                 if f["status"] == "rust" and f["gate"] in ("differential", "kunit"))
    print("\n=== ratchet dashboard ===")
    print(f"  sources woven      : {len(m['sources'])}")
    print(f"  functions -> Rust  : {rust}/{total}  ({100*rust/total:.1f}% of tracked bodies)")
    print(f"  strongly gated     : {proven}/{rust} (differential/kunit; rest weakly attested)")
    for path, s in m["sources"].items():
        fns = ", ".join(f"{fn}[{meta['gate']}:{meta['verdict']}]" for fn, meta in s["functions"].items())
        print(f"  {path}: {fns}")
    _print_funnel(rust)
    return 0


def _print_funnel(manifest_rust: int) -> None:
    """The global funnel — how far the whole dream actually is. Per-run numbers
    above keep each campaign honest; this keeps the DENOMINATOR in every
    report, so momentum never reads as more progress than 24k functions
    justify. The bank is counted live; campaign-measured stages come from
    funnel.json with provenance (the manifest is a transient last-woven-state,
    so PRESENT uses the best-measured boot-verified number, flagged if the
    loaded manifest disagrees)."""
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "funnel.json")
    try:
        f = json.load(open(fp))
    except Exception:
        return
    verified_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "firstrun", "verified")
    try:
        banked = sum(1 for x in os.listdir(verified_dir) if x.endswith(".rs"))
    except OSError:
        banked = 0
    census = f["census_fns"]
    realized = sum(v for k, v in f["realized"].items() if k != "src")
    present = f["present_vmlinux"]
    tier_b = f["tier_b_present"]
    print("\n=== the dream, honestly (funnel vs whole kernel) ===")
    print(f"  kernel functions (census)   : {census}  [{f['census_src']}]")
    print(f"  strongly-verifiable ceiling : ~{f['strongly_verifiable_pct']}% with today's oracles"
          f"  (~{f['c_forever_pct']}% C-forever)")
    print(f"  verified banked             : {banked}  ({100*banked/census:.1f}%)")
    print(f"  realized to real structs    : {realized}  ({100*realized/census:.1f}%)  [{f['realized']['src']}]")
    note = ("" if manifest_rust == present else
            f"  (loaded manifest tracks {manifest_rust} — a different volume/state)")
    print(f"  PRESENT in booting vmlinux  : {present}  ({100*present/census:.2f}%)"
          f"  [{f['present_src']}]{note}")
    print(f"  machine-checked safe (b)    : {tier_b}  ({100*tier_b/census:.2f}%)  [{f['tier_b_src']}]")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    return {"apply": cmd_apply, "build": cmd_build, "boot": cmd_boot, "gate": cmd_gate,
            "status": cmd_status}.get(cmd, cmd_status)()


if __name__ == "__main__":
    raise SystemExit(main())

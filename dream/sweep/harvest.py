#!/usr/bin/env python3
"""Harvest real pure-scalar exported leaf functions (Tier A) that fit the generic
differential harness — scalar args, scalar return, exported symbol. Emits a
worklist (name, ret, args, body) for the solve-rate fleet."""
from __future__ import annotations

import json
import os
import re
import sys

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
DIRS = ["lib", "lib/math", "kernel/time", "crypto"]

# scalar C types -> (rust type, probe C type, is_signed)
SCALAR = {
    "int": ("i32", "int"), "unsigned int": ("u32", "unsigned int"), "unsigned": ("u32", "unsigned"),
    "long": ("i64", "long"), "unsigned long": ("u64", "unsigned long"),
    "long long": ("i64", "long long"), "unsigned long long": ("u64", "unsigned long long"),
    "u8": ("u8", "u8"), "u16": ("u16", "u16"), "u32": ("u32", "u32"), "u64": ("u64", "u64"),
    "s8": ("i8", "s8"), "s16": ("i16", "s16"), "s32": ("i32", "s32"), "s64": ("i64", "s64"),
    "bool": ("bool", "bool"), "size_t": ("usize", "size_t"),
}
EXPORT = re.compile(r"EXPORT_SYMBOL(?:_GPL)?\(([A-Za-z_]\w*)\)")


def norm(t):
    t = t.replace("const", "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def parse_sig(src, name):
    """Return (ret, [args]) of scalar types, or None if not all-scalar / not found."""
    m = re.search(rf"\n([A-Za-z_][\w \t]*?)\b{re.escape(name)}\s*\(([^;{{)]*)\)\s*\n?\{{", src)
    if not m:
        return None
    ret = norm(m.group(1))
    if ret not in SCALAR:
        return None
    argstr = m.group(2).strip()
    if argstr in ("void", ""):
        return None  # want at least one input to drive
    args = []
    for a in argstr.split(","):
        a = a.strip()
        am = re.match(r"(.+?)\b([A-Za-z_]\w*)$", a)
        if not am:
            return None
        at = norm(am.group(1))
        if at not in SCALAR:
            return None
        args.append((at, am.group(2)))
    if len(args) > 3:
        return None  # keep the input space bounded
    return ret, args


def body_of(src, name):
    m = re.search(rf"\n[A-Za-z_][\w \t]*?\b{re.escape(name)}\s*\([^;{{)]*\)\s*\n?\{{", src)
    if not m:
        return ""
    i = src.index("{", m.start())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    return ""


def main():
    seen, work = set(), []
    for d in DIRS:
        root = os.path.join(KSRC, d)
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith(".c"):
                    continue
                p = os.path.join(dp, fn)
                src = open(p, errors="ignore").read()
                for em in EXPORT.finditer(src):
                    name = em.group(1)
                    if name in seen:
                        continue
                    sig = parse_sig(src, name)
                    if not sig:
                        continue
                    body = body_of(src, name)
                    if not body or body.count("\n") > 40:
                        continue
                    # skip obvious impurity (globals/io/alloc/lock)
                    if re.search(r"\b(kmalloc|kfree|readl|writel|spin_lock|mutex_lock|printk|memcpy|EXPORT)\b", body):
                        continue
                    seen.add(name)
                    ret, args = sig
                    work.append({"sym": name, "file": os.path.relpath(p, KSRC),
                                 "ret": ret, "args": args, "body": body})
    work.sort(key=lambda w: w["sym"])
    json.dump(work, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "worklist.json"), "w"), indent=1)
    print(f"harvested {len(work)} pure-scalar exported leaves:", file=sys.stderr)
    for w in work:
        print(f"  {w['sym']}({', '.join(a[0] for a in w['args'])}) -> {w['ret']}  [{w['file']}]", file=sys.stderr)
    print(len(work))


if __name__ == "__main__":
    main()

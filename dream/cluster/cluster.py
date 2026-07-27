#!/usr/bin/env python3
"""Static-function cluster weaving — the third critical-path library.

A kernel translation unit usually exposes an *exported entry* function plus a
handful of file-local `static` helpers only it reaches. Weaving the entry ALONE
(Ring 2's `gcd`) orphans the helper: nothing C-side calls the `static` function
any more, so `-Werror=unused-function` fails the build. That is *exactly* why the
ratchet manifest recorded `gcd` as `verified_not_woven` — verified-equal but
un-weavable.

The fix is to weave the whole **cluster**: the entry plus its transitive `static`
callees, as ONE Rust object.

  * the entry becomes `#[no_mangle] pub extern "C"` (keeps the ABI symbol callers
    link against);
  * each `static` helper becomes a **private** Rust `fn` (no exported symbol, so
    no link collision, and it *is* called by the entry, so no dead-code warning);
  * ALL cluster members are excised from the C — the entry's body forwards to the
    Rust symbol, and every `static` helper definition is *removed* (no orphan).

Verification stays at the exported boundary: the `static` helpers are unreachable
by name (private in Rust, gone from C), but a differential over the *entry*
exercises them transitively — so proving the entry equals its C original proves
the whole cluster does.

This module is the analysis + excision engine (pure, no toolchain). It:
  1. lists top-level function definitions and whether each is `static`;
  2. computes a function's static-only call cluster (BFS over intra-TU callees);
  3. rewrites the C: entry -> forwarding shell, static cluster members -> removed.

Conservative: a `static` callee whose definition isn't in this TU (a macro, an
inline from a header, an arch intrinsic) is a *boundary* symbol, left alone — the
Rust reimplements or `extern`-calls it, exactly as the leaf transplants already do.
"""
from __future__ import annotations

import re

# C keywords that look like calls `kw(` but aren't function calls.
_NOT_CALLS = {
    "if", "for", "while", "switch", "return", "sizeof", "do", "else",
    "swap", "min", "max", "static_branch_likely", "static_branch_unlikely",
    "unlikely", "likely",
}


def functions(src: str) -> dict[str, dict]:
    """name -> {static: bool, ret: str, params: str, text: str, start, end}.

    Matches top-level definitions `[static] RET name(params) {...}` and
    brace-balances to the closing `}`. Skips prototypes (no body).
    """
    out: dict[str, dict] = {}
    for m in re.finditer(
        r"(?m)^(?P<static>static\s+)?(?P<ret>[\w \t\*]+?)\b(?P<name>\w+)\s*\((?P<params>[^;{}]*)\)\s*\{",
        src,
    ):
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        name = m.group("name")
        out[name] = {
            "static": bool(m.group("static")),
            "ret": m.group("ret").strip(),
            "params": m.group("params").strip(),
            "text": src[m.start(): i + 1],
            "start": m.start(),
            "end": i + 1,
        }
    return out


def callees(body: str) -> set[str]:
    """Identifiers invoked as `name(` in a function body (minus C keywords)."""
    inner = body[body.index("{"):]
    names = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", inner))
    return {n for n in names if n not in _NOT_CALLS}


def static_cluster(src: str, entry: str) -> list[str]:
    """The entry plus every `static` function (defined in this TU) transitively
    reachable from it. Ordered entry-first. Non-static / out-of-TU callees are
    boundary symbols and are not pulled in."""
    fns = functions(src)
    if entry not in fns:
        raise KeyError(f"entry {entry!r} not defined in this translation unit")
    seen, order, stack = {entry}, [entry], [entry]
    while stack:
        cur = stack.pop()
        for c in callees(fns[cur]["text"]):
            if c in fns and fns[c]["static"] and c not in seen:
                seen.add(c)
                order.append(c)
                stack.append(c)
    return order


def _arg_names(params: str) -> list[str]:
    """Best-effort parameter names from a C parameter list."""
    if params.strip() in ("", "void"):
        return []
    args = []
    for p in params.split(","):
        ids = re.findall(r"[A-Za-z_]\w*", p.replace("*", " "))
        args.append(ids[-1])  # last identifier is the parameter name
    return args


def forwarding_shell(entry_fn: dict, seam: str) -> str:
    """The excised entry's replacement: keep the C prototype, forward to Rust."""
    args = ", ".join(_arg_names(entry_fn["params"]))
    ret = "" if entry_fn["ret"].replace(" ", "") == "void" else "return "
    return (f"{entry_fn['ret']} {_entry_name(entry_fn)}({entry_fn['params']})\n"
            f"{{\n\t{ret}{seam}({args});\n}}")


def _entry_name(entry_fn: dict) -> str:
    # the name isn't stored on the fn dict directly; recovered by callers
    return entry_fn["_name"]


def cluster_weave(src: str, entry: str, seam: str, extern_decl: str) -> str:
    """Rewrite the TU so the whole static cluster leaves C:
      * entry definition -> forwarding shell calling `seam`;
      * every static cluster member definition -> removed (no orphan);
      * `extern_decl` (the Rust seam prototype) inserted after the last include.
    """
    fns = functions(src)
    members = static_cluster(src, entry)
    fns[entry]["_name"] = entry
    out = src
    # remove the static helpers first (deepest cluster members), then the entry
    for name in members:
        if name == entry:
            continue
        out = out.replace(fns[name]["text"], "", 1)
    out = out.replace(fns[entry]["text"], forwarding_shell(fns[entry], seam), 1)
    incs = list(re.finditer(r"#include [<\"][^>\"]+[>\"]\n", out))
    if incs:
        pos = incs[-1].end()
        out = out[:pos] + extern_decl + "\n" + out[pos:]
    return out


def naive_weave(src: str, entry: str, seam: str, extern_decl: str) -> str:
    """The BROKEN single-function weave: excise only the entry, LEAVE the static
    helpers. Exists to demonstrate the orphan (-Werror=unused-function) the
    cluster weave avoids."""
    fns = functions(src)
    fns[entry]["_name"] = entry
    out = src.replace(fns[entry]["text"], forwarding_shell(fns[entry], seam), 1)
    incs = list(re.finditer(r"#include [<\"][^>\"]+[>\"]\n", out))
    if incs:
        pos = incs[-1].end()
        out = out[:pos] + extern_decl + "\n" + out[pos:]
    return out


if __name__ == "__main__":
    import sys

    src = open(sys.argv[1]).read()
    entry = sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "report"
    if mode == "report":
        cl = static_cluster(src, entry)
        print(f"cluster({entry}) = {cl}")
        fns = functions(src)
        for n in cl:
            tag = "static" if fns[n]["static"] else "ENTRY "
            print(f"  [{tag}] {fns[n]['ret']} {n}({fns[n]['params']})")
    elif mode == "cluster":
        print(cluster_weave(src, entry, f"cgir_{entry}", f"extern {functions(src)[entry]['ret']} cgir_{entry}({functions(src)[entry]['params']});"))
    elif mode == "naive":
        print(naive_weave(src, entry, f"cgir_{entry}", f"extern {functions(src)[entry]['ret']} cgir_{entry}({functions(src)[entry]['params']});"))

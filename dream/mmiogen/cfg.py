#!/usr/bin/env python3
"""Tree-sitter structured parse for branch-aware MMIO extraction.

The flat `;`-split in mmio_harness.extract() cannot see control flow, so it
refuses any if/for/while/switch. This module parses one C function with the
tree-sitter C grammar and produces a shallow BLOCK TREE that preserves if/else
structure, so the emitter can render faithful conditional register programs.

v1 scope (per the plan): `if`/`else` + guard-clause `return`s only. Loops,
switch, goto, do-while RAISE Unsupported (their handlers are a v2 addition, not
a re-parse). Modeled on CGIR's src/cgir/analyses/cfg.py recursive-walk pattern,
but self-contained (no CGIR IR coupling).

This file is Build-Order Step 1: prove the parse gives clean block structure on
real kernel functions before the emitter is built on it. `report()` walks a
sample and classifies each function (clean if/else, refused-loop/switch, etc.).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import tree_sitter_c
from tree_sitter import Language, Node, Parser

try:  # share the tool's refusal type
    from mmio_harness import Unsupported
except Exception:  # pragma: no cover - standalone use
    class Unsupported(Exception):
        pass

_LANG = Language(tree_sitter_c.language())
_PARSER = Parser(_LANG)

# statement node types that, at v1, we cannot model -> refuse (kept explicit so
# adding a v2 handler is a one-line move, not a re-parse).
_REFUSE_STMT = {
    "for_statement", "while_statement", "do_statement", "switch_statement",
    "goto_statement", "labeled_statement", "seh_try_statement",
}


@dataclass
class Branch:
    cond: str                      # source text of the controlling condition
    then_blk: "Block"
    else_blk: "Block | None" = None


@dataclass
class Block:
    """A straight-line run of statements plus nested branches, in source order.
    `items` holds `Node` (a straight-line statement, later lowered to R/W/C/RET)
    and `Branch` objects, interleaved as they appear."""
    items: list = field(default_factory=list)


def parse_function(func_text: str) -> Node:
    """Return the function_definition node for a single C function's source."""
    tree = _PARSER.parse(func_text.encode())
    root = tree.root_node
    if root.has_error:
        raise Unsupported("tree-sitter parse error (macro/syntax not resolvable)")
    fn = next((c for c in root.children if c.type == "function_definition"), None)
    if fn is None:
        raise Unsupported("no function_definition at top level")
    return fn


def _body(fn: Node) -> Node:
    body = fn.child_by_field_name("body")
    if body is None or body.type != "compound_statement":
        raise Unsupported("function has no compound body")
    return body


def _cond_text(if_node: Node) -> str:
    c = if_node.child_by_field_name("condition")
    if c is None:
        raise Unsupported("if without condition")
    t = c.text.decode()
    return t[1:-1].strip() if t.startswith("(") and t.endswith(")") else t.strip()


def _build_block(node: Node) -> Block:
    """Walk a compound_statement (or single statement) into a Block."""
    blk = Block()
    stmts = node.named_children if node.type == "compound_statement" else [node]
    for st in stmts:
        if st.type == "comment":
            continue
        if st.type in _REFUSE_STMT:
            raise Unsupported(f"control flow `{st.type}` — not modellable at v1")
        if st.type == "if_statement":
            then_node = st.child_by_field_name("consequence")
            else_node = st.child_by_field_name("alternative")
            then_blk = _build_block(then_node) if then_node else Block()
            else_blk = None
            if else_node is not None:
                # else_clause wraps the alternative statement/block
                inner = else_node.named_children[-1] if else_node.type == "else_clause" else else_node
                else_blk = _build_block(inner)
            blk.items.append(Branch(_cond_text(st), then_blk, else_blk))
        else:
            # a straight-line statement (declaration / expression / return / ...)
            blk.items.append(st)
    return blk


def build(func_text: str) -> Block:
    """Parse a C function and return its shallow block tree (v1: if/else only)."""
    return _build_block(_body(parse_function(func_text)))


def dump(blk: Block, depth: int = 0) -> str:
    out = []
    pad = "  " * depth
    for it in blk.items:
        if isinstance(it, Branch):
            out.append(f"{pad}IF ({it.cond})")
            out.append(dump(it.then_blk, depth + 1))
            if it.else_blk is not None:
                out.append(f"{pad}ELSE")
                out.append(dump(it.else_blk, depth + 1))
        else:
            txt = " ".join(it.text.decode(errors="replace").split())
            out.append(f"{pad}· {it.type}: {txt[:70]}")
    return "\n".join(x for x in out if x)


def stats(blk: Block) -> dict:
    """Rough shape stats: branch count, max nesting, whether any condition reads
    a register (readl/ioread -> a v1 refuse signal)."""
    import re
    n_branch = [0]
    max_depth = [0]
    read_cond = [False]

    def rec(b: Block, d: int):
        max_depth[0] = max(max_depth[0], d)
        for it in b.items:
            if isinstance(it, Branch):
                n_branch[0] += 1
                if re.search(r"\b(readl|ioread|in[bwl])\w*\s*\(", it.cond):
                    read_cond[0] = True
                rec(it.then_blk, d + 1)
                if it.else_blk:
                    rec(it.else_blk, d + 1)
    rec(blk, 0)
    return {"branches": n_branch[0], "max_nest": max_depth[0], "read_cond": read_cond[0]}


# ---- Build-Order Step 1: prove the parse on a real sample ----

if __name__ == "__main__":
    import json
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cluster"))
    import cluster  # noqa: E402
    KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
    census = os.path.join(os.path.dirname(__file__), "..", "overnight",
                          "reports", "recorder_census", "sweep.jsonl")
    cf = []
    if os.path.exists(census):
        for l in open(census):
            r = json.loads(l)
            if r.get("verdict") == "REFUSED" and str(r.get("reason", "")).startswith("control flow"):
                cf.append((r["file"], r["fn"]))
    sample = cf[:: max(1, len(cf) // 60)][:60] if cf else []
    from collections import Counter
    tally = Counter()
    clean_ifelse = []
    for rel, fn in sample:
        p = os.path.join(KSRC, rel)
        try:
            src = open(p, errors="ignore").read()
            ftext = cluster.functions(src)[fn]["text"]
        except Exception:
            tally["no-source"] += 1
            continue
        try:
            blk = build(ftext)
            s = stats(blk)
            if s["branches"] == 0:
                tally["parsed-no-branch(other-cf?)"] += 1
            elif s["read_cond"]:
                tally["refuse:read-condition"] += 1
            elif s["max_nest"] > 3:
                tally["refuse:deep-nest"] += 1
            else:
                tally["CLEAN-if/else(v1 candidate)"] += 1
                clean_ifelse.append((rel, fn, s))
        except Unsupported as e:
            reason = str(e)[:34]
            tally[f"refuse:{reason}"] += 1
        except Exception as e:
            tally[f"ERROR:{type(e).__name__}"] += 1
    print(f"=== tree-sitter parse over {len(sample)} control-flow-refused fns ===")
    for k, c in tally.most_common():
        print(f"  {c:3d}  {k}")
    print(f"\nclean if/else v1 candidates: {len(clean_ifelse)}")
    for rel, fn, s in clean_ifelse[:12]:
        print(f"  {fn}  ({rel})  branches={s['branches']} nest={s['max_nest']}")

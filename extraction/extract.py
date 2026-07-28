"""M1 — concurrency-IR extraction: the static lock->data map.

For one kernel subsystem (a .c file), extract the pieces of the concurrency IR
that M1 targets:

  * lock-bearing structs        — `struct S { spinlock_t lock; ...fields }`
  * critical sections (regions) — `spin_lock(&x->lock) ... spin_unlock(&x->lock)`
  * the ``protects(lock, field)`` candidate map — struct fields accessed on the
    locked object *inside* a critical section
  * unprotected accesses        — fields of a lock-bearing struct touched with no
    lock held (surfaced so the map is honest about what it did NOT observe)

This is the STATIC half. The M1 proof cross-checks it against the runtime
sanitizers (lockdep sees the lock class exercised; KCSAN finds no race on the
fields the map claims are protected). Static analysis proposes; the kernel's own
observation confirms — see docs/design.md §6.

Regex + brace/paren matching (not a full C parse) — the approach CGIR's lifter
proved robust against the kernel's macro noise that defeats tree-sitter.
"""

from __future__ import annotations

import re
from typing import Any

# Lock field types we recognise on a struct.
_LOCK_TYPES = ("spinlock_t", "raw_spinlock_t", "rwlock_t", "seqlock_t", "struct mutex")

# Lock acquire / release call families. The captured group is the lock argument.
_ACQUIRE = re.compile(
    r"\b(?:raw_)?spin_lock(?:_irqsave|_irqrestore|_irq|_bh|_nested)?"
    r"|mutex_lock(?:_nested|_interruptible)?"
    r"|read_lock(?:_irqsave|_bh)?|write_lock(?:_irqsave|_bh)?"
    r"\b"
)
_RELEASE = re.compile(
    r"\b(?:raw_)?spin_unlock(?:_irqrestore|_irq|_bh)?"
    r"|mutex_unlock|read_unlock(?:_irqrestore|_bh)?|write_unlock(?:_irqrestore|_bh)?"
    r"\b"
)
# &obj->lock  /  &obj.lock  -> (obj, lockfield)
_LOCK_ARG = re.compile(r"&\s*([A-Za-z_]\w*)\s*(?:->|\.)\s*([A-Za-z_]\w*)")
# obj->field  /  obj.field
_FIELD_ACCESS = re.compile(r"\b([A-Za-z_]\w*)\s*(?:->|\.)\s*([A-Za-z_]\w*)")


def _mask_comments(text: str) -> str:
    """Comments and string/char literals -> same-length spaces (newlines kept),
    so a field name inside a comment or string never counts as an access."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            for j in range(i, end):
                out[j] = " "
            i = end
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for j in range(i, end):
                if out[j] != "\n":
                    out[j] = " "
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _match_braces(text: str, open_idx: int) -> int:
    """Index just past the ``}`` matching the ``{`` at ``open_idx``."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def lock_structs(masked: str) -> dict[str, dict[str, Any]]:
    """``{struct_name: {"locks": {lock_field: lock_type}, "fields": {field: type}}}``
    for every ``struct Name { ... }`` definition that contains a lock field."""
    out: dict[str, dict[str, Any]] = {}
    for m in re.finditer(r"\bstruct\s+([A-Za-z_]\w*)\s*\{", masked):
        name = m.group(1)
        body = masked[m.end() : _match_braces(masked, m.end() - 1) - 1]
        locks: dict[str, str] = {}
        fields: dict[str, str] = {}
        for decl in body.split(";"):
            decl = decl.strip()
            if not decl:
                continue
            fm = re.match(r"(.+?\b)([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$", decl)
            if not fm:
                continue
            ftype, fname = fm.group(1).strip(), fm.group(2)
            fields[fname] = ftype
            for lt in _LOCK_TYPES:
                if re.search(rf"\b{re.escape(lt)}\b", ftype):
                    locks[fname] = lt
        if locks:
            out[name] = {"locks": locks, "fields": fields}
    return out


def _functions(masked: str, src: str) -> list[tuple[str, str]]:
    """(function_name, body_text) for each top-level function definition."""
    funcs = []
    for m in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{", masked):
        # column-0 heuristic: a top-level def's line starts flush-left.
        ls = masked.rfind("\n", 0, m.start()) + 1
        if ls < len(masked) and masked[ls] in " \t#":
            continue
        end = _match_braces(masked, m.end() - 1)
        funcs.append((m.group(1), src[m.end() : end - 1]))
    return funcs


def critical_sections(masked: str, src: str) -> list[dict[str, Any]]:
    """Every ``acquire(&obj->lock) ... release(&obj->lock)`` region, as
    ``{function, base, lock_field, body}``. Pairs an acquire with the next
    release of the SAME lock argument in the same function."""
    regions: list[dict[str, Any]] = []
    for fname, body in _functions(masked, src):
        mbody = _mask_comments(body)
        acquires = [
            (mm.start(), _LOCK_ARG.search(mbody, mm.end(), mm.end() + 80))
            for mm in _ACQUIRE.finditer(mbody)
        ]
        for start, arg in acquires:
            if not arg:
                continue
            base, lockfield = arg.group(1), arg.group(2)
            # next release of the same &base->lockfield
            rel_end = len(mbody)
            for rm in _RELEASE.finditer(mbody, arg.end()):
                ra = _LOCK_ARG.search(mbody, rm.end(), rm.end() + 80)
                if ra and ra.group(1) == base and ra.group(2) == lockfield:
                    rel_end = rm.start()
                    break
            regions.append(
                {
                    "function": fname,
                    "base": base,
                    "lock_field": lockfield,
                    "body": body[arg.end() : rel_end],
                }
            )
    return regions


def extract(src: str) -> dict[str, Any]:
    """The M1 concurrency IR for one subsystem source."""
    masked = _mask_comments(src)
    structs = lock_structs(masked)
    regions = critical_sections(masked, src)

    # protects(struct, lock_field) = fields of that struct accessed on the locked
    # base var inside a critical section. We attribute a region's lock_field to a
    # struct if that struct declares the field AND at least one accessed field.
    protects: dict[str, dict[str, set[str]]] = {}
    protected_field_set: set[str] = set()
    for reg in regions:
        macc = _mask_comments(reg["body"])
        touched = {
            fld
            for b, fld in _FIELD_ACCESS.findall(macc)
            if b == reg["base"]
        }
        for sname, sinfo in structs.items():
            if reg["lock_field"] in sinfo["locks"]:
                fields_here = touched & set(sinfo["fields"])
                if fields_here:
                    protects.setdefault(sname, {}).setdefault(reg["lock_field"], set())
                    protects[sname][reg["lock_field"]] |= fields_here
                    protected_field_set |= fields_here

    # unprotected accesses: a field of a lock-bearing struct touched somewhere but
    # never seen protected (candidate bug OR simply not covered by the sections we
    # found — surfaced, not judged).
    all_field_access = {fld for _, fld in _FIELD_ACCESS.findall(masked)}
    unprotected = []
    for sname, sinfo in structs.items():
        for fld in sinfo["fields"]:
            if fld in sinfo["locks"]:
                continue
            if fld in all_field_access and fld not in protected_field_set:
                unprotected.append({"struct": sname, "field": fld})

    return {
        "structs": structs,
        "regions": regions,
        "protects": {s: {lf: sorted(fs) for lf, fs in d.items()} for s, d in protects.items()},
        "unprotected_accesses": unprotected,
    }

"""A4 follow-up — wrapping arithmetic (realize.wrapify).

The verified bodies use bare `+ - *`, which PANIC on overflow when checks are
on; C wraps, and a panic in a freestanding kernel object is a hang (Kani found
the exposure in seqbuf_seek). wrapify pins wrapping semantics in the SOURCE.
Pins: precedence and associativity preserved; non-arithmetic operators
untouched; unary signs not mistaken for binary ops; unparseable input returned
UNCHANGED (conservative — the candidate stays flagged, never silently wrong).
Pure Python, always runs.
"""
import importlib.util
import os

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "realize_w", os.path.join(_HERE, "..", "realize", "realize.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def _norm(s):
    return "".join(s.split())


def test_basic_binary_ops_become_wrapping():
    assert _norm(R.wrapify("pos + a1")) == "(pos).wrapping_add(a1)"
    assert _norm(R.wrapify("a - 1")) == "(a).wrapping_sub(1)"
    assert _norm(R.wrapify("a * b")) == "(a).wrapping_mul(b)"


def test_left_associativity_and_precedence():
    # a + b - c  ==  (a + b) - c
    assert _norm(R.wrapify("a + b - c")) == "((a).wrapping_add(b)).wrapping_sub(c)"
    # a * b + c  ==  (a * b) + c   — * binds tighter
    assert _norm(R.wrapify("a * b + c")) == "((a).wrapping_mul(b)).wrapping_add(c)"
    # explicit parens respected
    assert _norm(R.wrapify("(a + b) * c")) == "(((a).wrapping_add(b))).wrapping_mul(c)"


def test_non_arithmetic_is_untouched():
    for e in ("a >= b", "a << 2", "x as i64", "(*f_x)", "x",
              "if a > b { 1 } else { 2 }", "a == b", "a & b", "a | b"):
        assert R.wrapify(e) == e, e


def test_unary_sign_is_not_a_binary_op():
    assert R.wrapify("-1") == "-1"
    # binary plus with a negative literal RHS still rewrites correctly
    assert _norm(R.wrapify("a + -1")) == "(a).wrapping_add(-1)"


def test_calls_and_nested_args_survive():
    assert _norm(R.wrapify("f(a, b) + g(c)")) == "(f(a,b)).wrapping_add(g(c))"
    # commas inside a call are not split points
    assert _norm(R.wrapify("field(A, b) - 1")) == "(field(A,b)).wrapping_sub(1)"


def test_constant_expression_receivers_are_left_alone():
    # A bare literal / literal-only expression is an ambiguous `{integer}` in
    # Rust and cannot take a method: `(166666 * 2).wrapping_add(1)` is E0689.
    # These are const-folded (literal overflow is a COMPILE error), so there is
    # no runtime hang to prevent — leave them unchanged. Regression: this broke
    # cx22700/sp887x_get_tune_settings in a full census before the guard.
    for e in ("166667 * 2", "(166666 * 2) + 1", "0x10 + 2", "1 + 2 * 3"):
        assert R.wrapify(e) == e, e
    # but a real receiver next to a literal still wraps
    assert "wrapping_add" in R.wrapify("x + 1")
    assert "wrapping_add" in R.wrapify("f(1) + 2")


def test_stmt_rewriter_only_touches_let_rhs():
    body = "let x = a + b;\nif a > b { }\nlet y: i64 = c * d;\nother(a + b);"
    out = R.wrapify_stmts(body)
    assert "wrapping_add" in out and "wrapping_mul" in out
    assert "if a > b { }" in out          # untouched
    assert "other(a + b);" in out          # non-let statement untouched (conservative)

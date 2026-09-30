"""The closed condition/arithmetic expression grammar."""

from __future__ import annotations

import re
from typing import Any

from pscx.common import as_number
from pscx.diagnostics import DIAGNOSTICS


# --------------------------------------------------------------------------
# Condition expressions
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(\d+\.?\d*(?:[eE][-+]?\d+)?|[A-Za-z_]\w*"
    r"|==|!=|>=|<=|&&|\|\||[<>()!+\-*/])"
)
_LITERALS = {"true": 1, "false": 0}


def _truth(value: Any) -> Any:
    """A value read where a LOGICAL operator asks for one.

    A comparison-less expression means "non-zero" -- ``#IF RSOff`` holds
    because RSOff is 1.0e8. That is a property of the CONTEXT, not of the
    expression: applying it at every parenthesis instead made
    ``(YD1*Lead)==2`` compare 1 against 2 and pick the wrong ``#CASE``
    arm.
    """
    if isinstance(value, bool):
        return value
    return bool(value) if isinstance(value, str) else value != 0


def _coerce_pair(left: Any, right: Any) -> tuple[Any, Any]:
    """Compare numerically when both sides look numeric, else as strings."""
    if not (isinstance(left, str) or isinstance(right, str)):
        return left, right
    ln, rn = as_number(left), as_number(right)
    if ln is not None and rn is not None:
        return ln, rn
    return str(left).strip().lower(), str(right).strip().lower()


class ConditionParser:
    """Recursive-descent evaluator for PSCAD port ``cond`` expressions.

    Grammar (no function calls appear anywhere in master.pslx)::

        or   := and ( '||' and )*
        and  := cmp ( '&&' cmp )*
        cmp  := add ( ('=='|'!='|'>='|'<='|'<'|'>') add )?
        add  := mul ( ('+'|'-') mul )*
        mul  := atom ( ('*'|'/') atom )*
        atom := '!' atom | '-' atom | '(' or ')' | number | identifier

    Identifiers are parameter names resolved **case-insensitively**: ``tpflt``
    declares ``View``/``Ctype`` on its form but its port conditions test
    ``VIEW``/``CType``. Getting this wrong silently selects the wrong port set.

    Every production returns the VALUE of what it read; a truth value is
    taken only where one is asked for (:func:`_truth`, at ``&&``/``||``,
    at ``!``, and by the caller of a whole condition). ``parse`` therefore
    answers 2 for ``(YD1*Lead)``, which is what makes ``(selector)==k``
    -- the guard a ``#CASE`` arm carries -- name the arm the selector's
    arithmetic names.
    """

    def __init__(self, text: str, env: dict[str, Any], arith: bool = False) -> None:
        self._tokens = _TOKEN.findall(text)
        self._pos = 0
        self._env = {(k or "").lower(): v for k, v in env.items()}
        #: arith=True raises on an unknown identifier instead of defaulting
        #: it to 0 -- a condition may name a parameter that does not exist
        #: for this instance, while a Computations/Branch value derived
        #: from a silent 0 is silently wrong.
        self._arith = arith

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str:
        self._pos += 1
        return self._tokens[self._pos - 1]

    def parse(self) -> Any:
        return self._or()

    def _or(self) -> Any:
        value = self._and()
        while self._peek() == "||":
            self._next()
            value = _truth(self._and()) or _truth(value)
        return value

    def _and(self) -> Any:
        value = self._compare()
        while self._peek() == "&&":
            self._next()
            value = _truth(self._compare()) and _truth(value)
        return value

    def _compare(self) -> Any:
        left = self._add()
        if self._peek() in ("==", "!=", ">=", "<=", "<", ">"):
            op = self._next()
            left, right = _coerce_pair(left, self._add())
            return {
                "==": left == right,
                "!=": left != right,
                ">=": left >= right,
                "<=": left <= right,
                "<": left < right,
                ">": left > right,
            }[op]
        #: A comparison-less expression keeps its VALUE here. Truth is
        #: applied by whatever asks for it -- `&&`/`||`/`!` above, and
        #: `bool()` at the caller for a whole condition -- because the
        #: same production also parses the operand of a comparison and of
        #: arithmetic, where the value is what is meant.
        return left

    def _add(self) -> Any:
        value = self._mul()
        while self._peek() in ("+", "-"):
            op = self._next()
            right = self._mul()
            if isinstance(value, str) or isinstance(right, str):
                return value
            value = value + right if op == "+" else value - right
        return value

    def _mul(self) -> Any:
        value = self._atom()
        while self._peek() in ("*", "/"):
            op = self._next()
            right = self._atom()
            if isinstance(value, str) or isinstance(right, str):
                return value
            value = value * right if op == "*" else (value / right if right else 0)
        return value

    def _atom(self) -> Any:
        token = self._next()
        if token == "!":
            return 0 if self._atom() else 1
        if token == "-":
            operand = self._atom()
            return operand if isinstance(operand, str) else -operand
        if token == "(":
            value = self._or()
            if self._peek() == ")":
                self._next()
            return 1 if value is True else (0 if value is False else value)

        numeric = as_number(token)
        if numeric is not None and re.fullmatch(r"[\d.].*", token):
            return numeric

        name = token.lower()
        if name in _LITERALS and name not in self._env:
            return _LITERALS[name]
        if name not in self._env and self._arith:
            # Arithmetic must be LOUD about unknown names -- the condition
            # default of 0 would silently corrupt every derived value.
            raise KeyError(name)
        raw = self._env.get(name, "0")
        numeric = as_number(raw)
        return numeric if numeric is not None else str(raw)


def condition_holds(expression: str | None, env: dict[str, Any]) -> bool:
    """Whether a conditional port exists for this parameter environment."""
    if not expression:
        return True
    normalised = expression.strip().lower()
    if normalised == "true":
        return True
    if normalised == "false":
        return False
    try:
        return bool(ConditionParser(expression, env).parse())
    except Exception:
        # Fail open so a parser bug cannot delete a terminal, but record it.
        DIAGNOSTICS.emit("cond_parse_failed", f"{expression[:40]}")
        return True


def _eval_arithmetic(expr: str, env: dict[str, Any]) -> float | None:
    """Numeric value of a closed arithmetic/conditional expression, or None.

    Reuses :class:`ConditionParser` (same grammar as port conds, which is
    also exactly the grammar Computations use). A function call, such as a
    cable helper's ``cos(...)``, fails loud here.
    """
    if re.search(r"\w\s*\(", expr):
        DIAGNOSTICS.emit("expr_function_call", f"{expr[:40]}")
        return None
    scope = dict(env)
    if not any((k or "").lower() == "pi" for k in scope):
        scope["pi"] = 3.141592653589793  # Computations use PI (``2*PI*F``)
    try:
        value = ConditionParser(expr, scope, arith=True).parse()
    except Exception:
        DIAGNOSTICS.emit("expr_parse_failed", f"{expr[:40]}")
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None

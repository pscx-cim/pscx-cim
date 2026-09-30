"""The Branch grammar and the script readers over it.

The ``#IF``/``#CASE``/``~`` structure itself lives in :mod:`pscx.guards`;
this module is the Branch declaration grammar and the two flat readers
(#OUTPUT writer directives, guard expressions) built on top of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pscx.diagnostics import DIAGNOSTICS
from pscx.guards import (  # noqa: F401  (re-exported: one import site)
    _DIM_MACRO,
    _DIRECTIVE_SPACING,
    _OUTPUT_DIRECTIVE,
    _guard_holds,
    assemble_splices,
    guarded_lines,
    parse_script,
)


# --------------------------------------------------------------------------
# Branch script segments (electrical primitives)
# --------------------------------------------------------------------------
#
# Grammar, enumerated over ALL Branch-carrying definitions in master
# (every line classified, none unaccounted):
#
#     line   := [label '='] node node body [comment]
#     node   := '$'NAME | '$'NAME'('index')' | number   (a numeric node -- in
#               practice always 0 -- is the ground reference)
#     index  := number | '$'NAME | '${'expr'}'
#     body   := value                       -- pure resistance (R)
#             | value value                 -- R L   (one occurrence: dc_mac_2w)
#             | value value value           -- R L C (3 values)
#             | 'BREAKER' value*            -- switched branch (0..3 values)
#             | 'AMMETER' value?            -- measuring branch
#             | 'SOURCE'  value*            -- source branch (1..3 values)
#     value  := number | '$'NAME
#     comment: everything from the first token starting with '/'
#
# The transformer families additionally use the ~/#CASE SPLICE grammar,
# fully decomposed by :func:`_assemble_splices` into the plain
# grammar above with an extra ``(expr)==k`` guard per #CASE arm; the
# "winding" kind survives only as a loud safety net (none occur after
# decomposition). Node tokens may also appear as bare ``NAME(index)``
# without ``$`` -- a shipped typo in intermediate.pslx, tracked exactly in
# :data:`BRANCH_BARE_NODES`.
# ``$``-names resolve case-insensitively against ports, form parameters and
# Computations results (``$Nx`` matches port ``NX``, ``$RTOFF``
# matches param ``RTOff``).

_BRANCH_KEYWORDS = frozenset({"BREAKER", "AMMETER", "SOURCE"})

_NUMERIC_TOKEN = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")

#: Bare (un-``$``-prefixed) node token, ``name`` or ``name(index)``.
_BARE_NODE_TOKEN = re.compile(r"[A-Za-z]\w*(?:\([^)]*\))?\Z")

#: Shipped-library defect registry (like mmc_FullCell's $RTOFF):
#: node tokens written WITHOUT the ``$`` prefix. intermediate.pslx
#: xfmr-3p4w2 writes ``{~N2(3) $G2 ~}`` in one #CASE arm of each BRS row --
#: a typo for ``$N2(3)``. Since the parser strips ``$`` anyway, the token
#: resolves identically; each occurrence is recorded here.
BRANCH_BARE_NODES: set[tuple[str, str | None, str]] = set()


@dataclass(frozen=True)
class BranchDecl:
    """One guarded line of a Branch segment (unevaluated)."""

    label: str | None
    node_a: str | None  # raw token, ``$`` stripped: "A", "N(3)", or "0"
    node_b: str | None
    kind: str  # "rlc" (1..3 positional R/L/C values) | keyword | "winding"
    values: tuple[str, ...]  # raw value tokens, ``$`` kept
    guards: tuple


def _parse_branch_segment(text: str, context: str) -> list[BranchDecl]:
    return _branch_decls(assemble_splices(parse_script(text), context),
                         context)


def _branch_decls(tree, context: str) -> list[BranchDecl]:
    """The Branch declarations a splice-assembled guard tree states."""
    decls: list[BranchDecl] = []
    for line, guards in guarded_lines(tree):
        s = line.strip()
        if not s or s.startswith("!") or s.startswith("#"):
            continue
        match = re.match(r"^(?:([A-Za-z]\w*)\s*=\s*)?(.*)$", s)
        label, rest = match.group(1), match.group(2).strip()
        tokens = rest.split()
        # trailing free-text comment: '/', '/C', '/ Pi-Section ...'
        for i, token in enumerate(tokens):
            if token.startswith("/"):
                tokens = tokens[:i]
                break
        if not tokens:
            continue
        if any(t.startswith("~") for t in tokens):
            decls.append(BranchDecl(label, None, None, "winding",
                                    tuple(tokens), guards))
            continue
        if len(tokens) < 3:
            DIAGNOSTICS.emit("branch_unparsed_line", f"{context}: {s[:40]}")
            continue
        node_a, node_b = tokens[0], tokens[1]
        body = tokens[2:]
        bad_node = False
        for t in (node_a, node_b):
            if t.startswith("$") or _NUMERIC_TOKEN.match(t):
                continue
            if _BARE_NODE_TOKEN.match(t):
                entry = (context, label, t)
                # once per DISTINCT typo, not once per parse: a library is
                # re-read for every case that references it, and counting
                # re-reads would make the pin a function of the case count
                # ...and only what was REPORTED is registered: a library
                # parsed inside DIAGNOSTICS.suppressed() would otherwise
                # register the typo without emitting it, and the registry
                # is permanent, so no later parse could report it again
                if entry not in BRANCH_BARE_NODES:
                    DIAGNOSTICS.emit("branch_bare_node", f"{context}: {t}")
                    if not DIAGNOSTICS.suppressing:
                        BRANCH_BARE_NODES.add(entry)
                continue
            bad_node = True
        if bad_node:
            DIAGNOSTICS.emit("branch_bad_nodes", f"{context}: {s[:40]}")
            continue
        if body[0].upper() in _BRANCH_KEYWORDS:
            kind, values = body[0].lower(), tuple(body[1:])
        elif len(body) <= 3:
            kind, values = "rlc", tuple(body)
        else:
            DIAGNOSTICS.emit("branch_unparsed_body", f"{context}: {s[:40]}")
            continue
        if any(not (v.startswith("$") or _NUMERIC_TOKEN.match(v))
               for v in values):
            DIAGNOSTICS.emit("branch_bad_value", f"{context}: {s[:40]}")
            continue
        decls.append(BranchDecl(
            label, node_a.lstrip("$"), node_b.lstrip("$"), kind, values, guards
        ))
    return decls


def preprocess_script(text: str) -> list[tuple[str, tuple]]:
    """``(line, guards)`` for every non-conditional line of a script segment.

    The deferred form of concrete expansion over the guard tree
    (:func:`pscx.guards.guarded_lines`): each surviving line carries its
    guard chain as a tuple of ``(expr, want)`` -- the line is active when
    every guard evaluates to ``want``. #ELSEIF/#ELSE arms contribute the
    *negations* of their earlier siblings. Lines that are neither
    conditional directives nor structure (including #OUTPUT, #LOCAL and
    #CASE) are yielded verbatim for the caller to interpret; the Branch
    reader is the one that asks for #CASE and ``~`` to be assembled first.
    """
    return guarded_lines(parse_script(text))


def _guard_expressions(text: str) -> list[str]:
    """Every #IF/#ELSEIF condition in a segment (brace bodies skipped),
    using the same brace-form rules as :func:`preprocess_script`."""
    exprs: list[str] = []
    depth = 0
    for line in text.splitlines():
        stripped = _DIRECTIVE_SPACING.sub(r"#\1", line.strip())
        if depth > 0:
            depth += line.count("{") - line.count("}")
            if depth < 0:
                depth = 0
            continue
        if stripped.startswith(("#IF", "#ELSEIF")):
            keyword = "#ELSEIF" if stripped.startswith("#ELSEIF") else "#IF"
            head = stripped.partition("{")[0]
            expr = head[len(keyword):].strip()
            if expr:
                exprs.append(expr)
            if "{" in stripped:
                depth = stripped.count("{") - stripped.count("}")
                if depth < 0:
                    depth = 0
    return exprs


def _writer_directives(text: str) -> list[tuple[str, int | None, tuple]]:
    """``(param, dim, guards)`` for every #OUTPUT in a script segment.
    ``dim`` is the declared signal dimension; None = inherited/unknown."""
    return _writer_directives_of(parse_script(text))


def _writer_directives_of(tree) -> list[tuple[str, int | None, tuple]]:
    found = []
    for line, guards in guarded_lines(tree):
        match = _OUTPUT_DIRECTIVE.match(line)
        if match:
            dim_token = match.group(2)
            dim = (None if dim_token is None or dim_token == "0"
                   else int(dim_token))
            if dim is None and dim_token is None:
                dim = 1  # no dim token = scalar
            found.append((match.group(1), dim, guards))
    return found

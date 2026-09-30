"""The guard tree: a script segment's ``#IF``/``#CASE``/``~`` structure.

PSCAD script segments are line-oriented text under a C-style preprocessor.
Read as a flat stream, every line carries the chain of conditions that
admits it and the shape of the source is gone. Read as a tree, three
things the flat form can only assert become properties of the types:

- an ``#IF``/``#ELSEIF``/``#ELSE`` chain is ONE node with n arms. The arms
  exclude each other because they are siblings, not because a reader
  recomputed negations from nesting depth.
- ``#CASE`` arms are TEXT. Nothing nests inside an arm, so two alternations
  can never lie on one path and their counts can only be summed. That makes
  "alternatives accumulate, never compound" a type rather than a loop
  invariant: intermediate.pslx's xfmr-3p2w BRS12 row has
  12 + 12 = 24 variants, and 12 * 12 is unsayable.
- one logical line is open at a time. A :class:`Splice` refuses to hold
  another in its body.

Two interpreters walk the same tree. :func:`guarded_lines` pairs every
line with the chain that admits it -- the deferred form every caller
needs, because a definition is parsed once and placed many times.
:func:`expand` takes an instance environment and returns the lines PSCAD
emits, descending only into the arms that hold. :func:`enumerate_arms`
takes no environment at all and names every arm in the tree, which is what
says which arms no case ever selects.

Brace one-line conditionals (``#IF expr { then } { else }``) are
substitution text, not structure: they emit no line of their own and open
no block, so the tree drops them exactly as the flat form did.
:func:`pscx.preproc._guard_expressions` remains the reader for their
conditions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from pscx.diagnostics import DIAGNOSTICS
from pscx.expr import ConditionParser

#: master indents nested conditionals by spacing the keyword off the hash
#: (``# IF``, ``#   ENDIF``); PSCAD reads those as directives, so the
#: spelling is normalised before the parser sees a line. The forms occur in
#: Model-Data, Computations and Fortran-family segments of master.
_DIRECTIVE_SPACING = re.compile(r"^#\s+(IF|ELSEIF|ELSE|ENDIF)\b")

#: ``#OUTPUT TYPE Param [dim] {payload}...``: the optional dim is a
#: literal integer, never a $var.
#: dim 0 means "inherited from the measured object" (ammeter Name 0), same
#: convention as a Port's dim attribute; absent means scalar.
_OUTPUT_DIRECTIVE = re.compile(r"^\s*#OUTPUT\s+\w+\s+(\w+)(?:\s+(\d+))?")

#: master closes some of its conditional blocks with ``#END`` rather than
#: ``#ENDIF``, in the same indented spelling ``_DIRECTIVE_SPACING``
#: normalises. Every occurrence has an ``#IF`` open
#: at that point. The word boundary matters: ``#ENDBEGIN`` closes a
#: ``#BEGIN`` and must not close a conditional.
_END_DIRECTIVE = re.compile(r"^#\s*END\b")

_CASE_DIRECTIVE = re.compile(r"^#CASE\s+(.+?)\s*((?:\{[^}]*\}\s*)+)$")
_CASE_ARM = re.compile(r"\{([^}]*)\}")

#: One link of a guard chain: the condition and the truth value it must
#: take. ``(expr, False)`` is what an #ELSEIF arm contributes for each
#: earlier arm of its chain.
Guard = tuple[str, bool]
Guards = tuple[Guard, ...]


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """One line of the segment, verbatim. Leading and trailing whitespace
    is kept: the Branch and Model-Data readers strip their own, and a
    Fortran body's indentation is content."""

    line: str


@dataclass(frozen=True)
class Arm:
    """One arm of a conditional chain. ``condition`` is None for ``#ELSE``,
    which is what makes "the arm with no condition of its own" a value
    rather than a sentinel string."""

    condition: str | None
    body: tuple[Node, ...]


@dataclass(frozen=True)
class Conditional:
    """A whole ``#IF``/``#ELSEIF``.../``#ELSE`` chain: n arms in source
    order, at most one of which is taken."""

    arms: tuple[Arm, ...]

    def guard_of(self, index: int) -> Guards:
        """What admits arm ``index``: the negation of every earlier arm's
        condition, then its own if it has one."""
        chain: list[Guard] = []
        for arm in self.arms[:index]:
            if arm.condition is not None:
                chain.append((arm.condition, False))
        own = self.arms[index].condition
        if own is not None:
            chain.append((own, True))
        return tuple(chain)


@dataclass(frozen=True)
class Case:
    """``#CASE expr {arm} {arm} ...`` -- n alternatives selected by the
    value of ``expr``.

    ``arms`` are STRINGS. That is the load-bearing part of the design: an
    arm cannot contain another Case, so no path through any tree carries
    two alternations and there is no shape in which arm counts multiply.
    ``glued`` records, per arm, whether the source wrote ``{~...~}`` -- the
    form that continues an open logical line rather than stating a whole
    one.
    """

    selector: str
    arms: tuple[str, ...]
    glued: tuple[bool, ...]

    def guard_of(self, index: int) -> Guards:
        return ((f"({self.selector})=={index}", True),)


@dataclass(frozen=True)
class Splice:
    """One logical line spread over several physical ones: ``head ~`` opens
    it, ``~ tail`` closes it, and the ``#CASE`` lines in ``body`` each
    contribute one middle per arm.

    ``body`` is a subtree because the contributing ``#CASE`` lines may sit
    in mutually exclusive arms of a conditional -- BRS12's two do -- so the
    middles carry a guard chain of their own. It may not contain another
    Splice: one logical line is open at a time, and two would let their
    middles pair up.
    """

    head: str
    body: tuple[Node, ...]
    tail: str

    def __post_init__(self) -> None:
        if any(isinstance(node, Splice) for node in _walk(self.body)):
            raise ValueError(
                "a splice cannot be opened inside an open one: one "
                "logical line is open at a time")

    def alternatives(self) -> tuple[Alternative, ...]:
        """Every middle this line can take, in source order.

        The count is the SUM of the body's ``#CASE`` arm counts -- one
        alternative per arm, never a pairing between two ``#CASE`` lines.
        """
        found: list[Alternative] = []
        for case, guards in _cases(self.body, ()):
            for index, arm in enumerate(case.arms):
                found.append(Alternative(arm, guards + case.guard_of(index)))
        return tuple(found)


@dataclass(frozen=True)
class Alternative:
    """One middle contributed to an open logical line, with the chain that
    admits it -- relative to the splice, which is where the chain starts."""

    text: str
    guards: Guards


Node = Text | Conditional | Case | Splice


def _walk(nodes: Sequence[Node]) -> Iterator[Node]:
    """Every node in the subtree, parents before children."""
    for node in nodes:
        yield node
        if isinstance(node, Conditional):
            for arm in node.arms:
                yield from _walk(arm.body)
        elif isinstance(node, Splice):
            yield from _walk(node.body)


def _cases(nodes: Sequence[Node],
           guards: Guards) -> Iterator[tuple[Case, Guards]]:
    """Every ``#CASE`` in the subtree with the chain admitting it."""
    for node in nodes:
        if isinstance(node, Case):
            yield node, guards
        elif isinstance(node, Conditional):
            for index, arm in enumerate(node.arms):
                yield from _cases(arm.body, guards + node.guard_of(index))


# --------------------------------------------------------------------------
# Parsing: text -> conditional tree
# --------------------------------------------------------------------------


def parse_script(text: str) -> tuple[Node, ...]:
    """The ``#IF`` structure of a script segment, as Text and Conditional.

    ``#CASE`` lines and ``~`` glue come out as ordinary Text here;
    :func:`assemble_splices` folds them into Case and Splice nodes for the
    segments whose grammar uses them.

    BRACE form: ``#IF expr { then }`` optionally followed by
    ``{ else }`` on the same line is SELF-CONTAINED -- it does not open a
    block, and its brace bodies are inline substitution text (the ``{~``
    variants splice into the surrounding emitted line). A brace body may
    continue over following lines until braces balance (Ztrans_fcnZ).
    Brace bodies never contain an #OUTPUT (asserted loudly below).
    """
    root: list[Node] = []
    # (arms closed so far, condition of the open arm, its body); the last
    # entry is the innermost open chain
    stack: list[tuple[list[Arm], str | None, list[Node]]] = []
    brace_depth = 0

    def body() -> list[Node]:
        return stack[-1][2] if stack else root

    def open_arm(condition: str | None) -> None:
        arms, current, current_body = stack[-1]
        arms.append(Arm(current, tuple(current_body)))
        stack[-1] = (arms, condition, [])

    for line in text.splitlines():
        stripped = _DIRECTIVE_SPACING.sub(r"#\1", line.strip())
        if brace_depth > 0:
            # inside a brace body opened on a previous line
            if _OUTPUT_DIRECTIVE.match(line):
                DIAGNOSTICS.emit("script_output_inside_brace_body")
            brace_depth += line.count("{") - line.count("}")
            if brace_depth < 0:
                DIAGNOSTICS.emit("script_brace_underflow")
                brace_depth = 0
            continue
        if stripped.startswith(("#IF", "#ELSEIF")) and "{" in stripped:
            _head, _, brace_body = stripped.partition("{")
            if stripped.startswith("#ELSEIF"):
                DIAGNOSTICS.emit("script_brace_elseif")  # never seen; loud
            if "#OUTPUT" in brace_body:
                DIAGNOSTICS.emit("script_output_inside_brace_body")
            brace_depth = stripped.count("{") - stripped.count("}")
            if brace_depth < 0:
                DIAGNOSTICS.emit("script_brace_underflow")
                brace_depth = 0
            continue
        if stripped.startswith("#IF"):
            stack.append(([], stripped[3:].strip(), []))
        elif stripped.startswith("#ELSEIF"):
            if stack:
                open_arm(stripped[7:].strip())
            else:
                DIAGNOSTICS.emit("script_unbalanced_elseif")
        elif stripped.startswith("#ELSE"):
            if stack:
                open_arm(None)
            else:
                DIAGNOSTICS.emit("script_unbalanced_else")
        elif stripped.startswith("#ENDIF") or _END_DIRECTIVE.match(stripped):
            if stack:
                _close_chain(stack, root)
            else:
                DIAGNOSTICS.emit("script_unbalanced_endif")
        else:
            body().append(Text(line))
    # An #IF left open at end of segment guards everything after it, which
    # is what the flat form did; closing it here says the same thing.
    while stack:
        _close_chain(stack, root)
    return tuple(root)


def _close_chain(stack: list[tuple[list[Arm], str | None, list[Node]]],
                 root: list[Node]) -> None:
    arms, current, current_body = stack.pop()
    arms.append(Arm(current, tuple(current_body)))
    target = stack[-1][2] if stack else root
    target.append(Conditional(tuple(arms)))


# --------------------------------------------------------------------------
# Splice assembly: Text -> Case and Splice
# --------------------------------------------------------------------------


def _is_case(line: str) -> bool:
    return line.strip().startswith("#CASE")


def _opens_line(line: str) -> bool:
    s = line.strip()
    return s.endswith("~") and not s.startswith(("!", "#"))


def _closes_line(line: str) -> bool:
    return line.strip().startswith("~")


def _parse_case(line: str, context: str, glued_expected: bool) -> Case | None:
    s = line.strip()
    match = _CASE_DIRECTIVE.match(s)
    if match is None:
        DIAGNOSTICS.emit("branch_bad_case", f"{context}: {s[:40]}")
        return None
    selector, arm_blob = match.group(1), match.group(2)
    arms: list[str] = []
    glued: list[bool] = []
    for arm in _CASE_ARM.findall(arm_blob):
        inner = arm.strip()
        is_glued = inner.startswith("~")
        arms.append(inner.strip("~").strip())
        glued.append(is_glued)
        if glued_expected and not is_glued:
            DIAGNOSTICS.emit("branch_case_arm_unglued",
                             f"{context}: {arms[-1][:30]}")
        elif not glued_expected and is_glued:
            DIAGNOSTICS.emit("branch_case_dangling_glue",
                             f"{context}: {arms[-1][:30]}")
    return Case(selector, tuple(arms), tuple(glued))


def assemble_splices(nodes: Sequence[Node], context: str) -> tuple[Node, ...]:
    """Fold ``#CASE`` lines and ``~`` glue into Case and Splice nodes.

    Every Branch splice in master opens and closes
    at ONE conditional nesting level, so a run from ``head ~`` to
    ``~ tail`` is a stretch of siblings; the conditionals BETWEEN them, and
    the ``#CASE`` lines inside those, become the splice's body. A run that
    reached the end of its sibling list without closing is reported --
    that would be a logical line escaping its own block.
    """
    return _assemble(nodes, context, open_line=False)


def _assemble(nodes: Sequence[Node], context: str,
              open_line: bool) -> tuple[Node, ...]:
    out: list[Node] = []
    head: str | None = None
    body: list[Node] = []

    def close(tail: str) -> None:
        nonlocal head, body
        out.append(Splice(head or "", tuple(body), tail))
        head, body = None, []

    for node in nodes:
        target = body if head is not None else out
        inside = open_line or head is not None
        if isinstance(node, Conditional):
            target.append(Conditional(tuple(
                Arm(arm.condition, _assemble(arm.body, context, inside))
                for arm in node.arms)))
            continue
        if not isinstance(node, Text):
            target.append(node)
            continue
        if _is_case(node.line):
            case = _parse_case(node.line, context, glued_expected=inside)
            if case is not None:
                target.append(case)
            continue
        if head is not None:
            if _closes_line(node.line):
                tail = node.line.strip()[1:].strip()
                if tail.endswith("~"):
                    DIAGNOSTICS.emit("branch_splice_reopen",
                                     f"{context}: {node.line.strip()[:40]}")
                    tail = tail.rstrip("~").strip()
                close(tail)
                continue
            DIAGNOSTICS.emit("branch_splice_broken",
                             f"{context}: {node.line.strip()[:40]}")
            close("")
            # fall through: the current line is handled as an ordinary one
        elif open_line:
            # a nested conditional inside an ENCLOSING open line: only
            # #CASE arms continue a logical line, so any other text here
            # is a declaration that would be lost inside the splice
            DIAGNOSTICS.emit("branch_splice_broken",
                             f"{context}: {node.line.strip()[:40]}")
            continue
        if _opens_line(node.line):
            head, body = node.line.strip().rstrip("~").strip(), []
            continue
        out.append(node)
    if head is not None:
        DIAGNOSTICS.emit("branch_splice_unterminated", f"{context}")
        close("")
    return tuple(out)


# --------------------------------------------------------------------------
# Interpreter 1 -- concrete expansion, deferred and immediate
# --------------------------------------------------------------------------


def guarded_lines(nodes: Sequence[Node]) -> list[tuple[str, Guards]]:
    """Every line the segment can emit, with the chain that admits it.

    The deferred form of concrete expansion: a definition is parsed once
    and placed many times, so the guards travel with the line and are
    evaluated per instance. A Splice contributes one line per alternative
    (head + middle + tail) and, when its body holds no ``#CASE`` at all,
    exactly one line of head + tail.
    """
    found: list[tuple[str, Guards]] = []
    _emit(nodes, (), found)
    return found


def _emit(nodes: Sequence[Node], guards: Guards,
          found: list[tuple[str, Guards]]) -> None:
    for node in nodes:
        if isinstance(node, Text):
            found.append((node.line, guards))
        elif isinstance(node, Conditional):
            for index, arm in enumerate(node.arms):
                _emit(arm.body, guards + node.guard_of(index), found)
        elif isinstance(node, Case):
            for index, arm in enumerate(node.arms):
                found.append((arm, guards + node.guard_of(index)))
        elif isinstance(node, Splice):
            alternatives = node.alternatives()
            if not alternatives:
                found.append((f"{node.head} {node.tail}", guards))
                continue
            for alternative in alternatives:
                found.append((f"{node.head} {alternative.text} {node.tail}",
                              guards + alternative.guards))


def expand(nodes: Sequence[Node], env: dict[str, Any],
           dim_resolver: Callable[[str], int | None] | None = None
           ) -> list[str]:
    """CONCRETE expansion: the lines this segment emits for one instance.

    A separate walk from :func:`guarded_lines`, not a filter over it: it
    evaluates each arm's condition at the node and never descends into an
    arm that does not hold. The two agreeing on every guard evaluation is
    what says the tree means one thing.
    """
    lines: list[str] = []
    _expand(nodes, env, dim_resolver, lines)
    return lines


def _expand(nodes: Sequence[Node], env: dict[str, Any],
            dim_resolver: Callable[[str], int | None] | None,
            lines: list[str]) -> None:
    for node in nodes:
        if isinstance(node, Text):
            lines.append(node.line)
        elif isinstance(node, Conditional):
            for index, arm in enumerate(node.arms):
                if _holds(node.guard_of(index), env, dim_resolver):
                    _expand(arm.body, env, dim_resolver, lines)
        elif isinstance(node, Case):
            for index, arm in enumerate(node.arms):
                if _holds(node.guard_of(index), env, dim_resolver):
                    lines.append(arm)
        elif isinstance(node, Splice):
            alternatives = node.alternatives()
            if not alternatives:
                lines.append(f"{node.head} {node.tail}")
                continue
            for alternative in alternatives:
                if _holds(alternative.guards, env, dim_resolver):
                    lines.append(
                        f"{node.head} {alternative.text} {node.tail}")


def _holds(guards: Guards, env: dict[str, Any],
           dim_resolver: Callable[[str], int | None] | None) -> bool:
    return all(_guard_holds(expr, env, want, dim_resolver)
               for expr, want in guards)


# --------------------------------------------------------------------------
# Interpreter 2 -- symbolic enumeration, with no environment at all
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmRef:
    """One arm of one alternation, named so two runs can agree on it.

    ``path`` is the position of the alternation in the tree and ``index``
    the arm within it, so the identity survives re-parsing the same text
    and says nothing about any environment. ``condition`` is the arm's own
    -- None for an ``#ELSE``, which is the arm that holds when no other
    does.
    """

    kind: str  # "conditional" | "case"
    path: tuple[int, ...]
    index: int
    condition: str | None
    #: What admits the ALTERNATION this arm belongs to. Held apart from the
    #: arm's own contribution because "no placement takes this arm" and "no
    #: placement ever gets here" are different findings.
    prefix: Guards
    own: Guards

    @property
    def guards(self) -> Guards:
        return self.prefix + self.own

    @property
    def key(self) -> tuple[str, tuple[int, ...], int]:
        return (self.kind, self.path, self.index)

    @property
    def alternation(self) -> tuple[str, tuple[int, ...]]:
        return (self.kind, self.path)


def enumerate_arms(nodes: Sequence[Node]) -> list[ArmRef]:
    """Every arm of every alternation in the tree, without an environment.

    What concrete expansion structurally cannot report: it only ever asks
    about the arms it already chose, so an arm no environment selects is
    invisible to it -- whether because the library feature is unused or
    because its guard is mis-evaluated.
    """
    found: list[ArmRef] = []
    _arms(nodes, (), (), found)
    return found


def _arms(nodes: Sequence[Node], path: tuple[int, ...], guards: Guards,
          found: list[ArmRef]) -> None:
    for position, node in enumerate(nodes):
        here = path + (position,)
        if isinstance(node, Conditional):
            for index, arm in enumerate(node.arms):
                own = node.guard_of(index)
                found.append(ArmRef("conditional", here, index,
                                    arm.condition, guards, own))
                _arms(arm.body, here + (index,), guards + own, found)
        elif isinstance(node, Case):
            for index in range(len(node.arms)):
                own = node.guard_of(index)
                found.append(ArmRef("case", here, index, own[0][0],
                                    guards, own))
        elif isinstance(node, Splice):
            _arms(node.body, here, guards, found)


#: A ``#CASE`` arm's guard, as :meth:`Case.guard_of` writes it.
_SELECTOR_GUARD = re.compile(r"^\((.*)\)==(\d+)$")


def contradiction(guards: Guards) -> str | None:
    """The expression a guard chain requires to be two things at once.

    Purely symbolic, and the strongest thing this side can say: an arm
    whose chain demands one condition both true and false is unreachable
    for EVERY environment, so no amount of concrete expansion can find
    it: expansion only ever asks about arms it already chose. Two
    ``#CASE`` selector guards on one chain are the same finding in the
    alternatives grammar: they would mean two alternations on one path,
    which is exactly the compounding the node types forbid.

    None when the chain is satisfiable as far as this can tell -- it
    compares expressions by their text and proves nothing about two
    conditions that contradict each other for reasons only arithmetic
    knows.
    """
    wanted: dict[str, bool] = {}
    selected: dict[str, str] = {}
    for expr, want in guards:
        if wanted.get(expr, want) != want:
            return expr
        wanted[expr] = want
        match = _SELECTOR_GUARD.match(expr)
        if match is not None and want:
            selector, arm = match.group(1), match.group(2)
            if selected.get(selector, arm) != arm:
                return selector
            selected[selector] = arm
    return None


def selected_arms(nodes: Sequence[Node], env: dict[str, Any],
                  dim_resolver: Callable[[str], int | None] | None = None
                  ) -> set[tuple[str, tuple[int, ...], int]]:
    """The arms one environment selects, keyed like :func:`enumerate_arms`.

    An arm is selected when its chain holds AND its alternation is
    reachable, so an arm inside a dead branch is not counted as exercised.
    """
    chosen: set[tuple[str, tuple[int, ...], int]] = set()
    for arm in enumerate_arms(nodes):
        if _holds(arm.guards, env, dim_resolver):
            chosen.add(arm.key)
    return chosen


# --------------------------------------------------------------------------
# Guard evaluation
# --------------------------------------------------------------------------

_DIM_MACRO = re.compile(r"\$#DIM\(\s*(\w+)\s*\)", re.IGNORECASE)


def _guard_holds(expr: str, env: dict[str, Any], want: bool,
                 dim_resolver=None) -> bool:
    """One #IF-chain guard.

    ``$#DIM(port)`` is the ONLY ``$``-macro occurring in any guard in
    master (``$#Component``/``$#M[i]`` are body-text
    substitutions). With a ``dim_resolver`` (port name -> int dim or None)
    the macro is substituted and the guard evaluates STRICTLY. A macro that
    cannot be resolved -- no resolver, unknown port, or a non-DIM ``$`` form
    -- fails OPEN in both branches (a duplicated writer param is harmless,
    a dropped one loses a driver) and is counted loudly.
    """
    if "$" in expr:
        if dim_resolver is not None:
            def _sub(m: re.Match) -> str:
                d = dim_resolver(m.group(1))
                return str(d) if d is not None else m.group(0)
            expr = _DIM_MACRO.sub(_sub, expr)
        if "$" in expr:
            DIAGNOSTICS.emit("script_guard_macro_fail_open", f"{expr[:40]}")
            return True
    try:
        value = bool(ConditionParser(expr, env).parse())
    except Exception:  # noqa: BLE001 -- any failure to read a guard fails
        # OPEN, in both branches: admitting both arms duplicates content,
        # dropping one loses it. Counted loudly.
        DIAGNOSTICS.emit("script_guard_parse_failed", f"{expr[:40]}")
        return True
    return value == want

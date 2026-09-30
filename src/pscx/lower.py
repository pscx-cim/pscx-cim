"""Branch evaluation, numeric/unit resolution, port dimensions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

from pscx.common import as_number
from pscx.diagnostics import DIAGNOSTICS
from pscx.preproc import BranchDecl, _NUMERIC_TOKEN, _guard_holds, preprocess_script
from pscx.expr import _eval_arithmetic
from pscx.units import value_in_declared_unit

# ``Component``/``ComponentDef``/``Port``/``Instance`` appear in
# annotations only (lazy under ``from __future__ import annotations``);
# importing them at runtime would close an import cycle with
# pscx.model (Component.dim_of_port calls port_dim).
if TYPE_CHECKING:
    from pscx.elaborate import Instance
    from pscx.model import Component, ComponentDef, Port


# --------------------------------------------------------------------------
# Parameter evaluation (Computations + hierarchical value resolution)
# --------------------------------------------------------------------------

#: Shipped-library defect registry, the value-side twin of
#: :data:`pscx.preproc.BRANCH_BARE_NODES`: ``(definition, name)`` for every
#: Branch value naming a parameter its definition does not declare.
#: master.pslx ships exactly one, mmc_FullCell's ``$RTOFF``.
BRANCH_DANGLING_VALUES: set[tuple[str, str]] = set()

#: A parameter value that is a plain number with an optional unit tail
#: (``"100.0 [ohm]"``, ``"1.0E6"``). Anything else is treated as an
#: expression over the enclosing namespace. ``as_number`` alone must NOT be
#: used to detect this -- it would read ``2*x`` as 2.
_NUMBER_WITH_UNIT = re.compile(
    r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\s*(?:\[[^\]]*\])?\Z"
)

_COMPUTATION_LINE = re.compile(
    r"^(INTEGER|REAL|LOGICAL)\s+(\w+)\s*=\s*(.*?)\s*$", re.I
)

_IDENT = re.compile(r"[A-Za-z_]\w*")


def computations_env(definition: ComponentDef, env: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a definition's Computations segment against ``env``.

    Returns ``{lowercased name: numeric value}``. Lines are #IF-guarded like
    every other script segment. Failures are loud but do not
    abort remaining lines. Non-assignment lines (``!`` comments, ``#CASE``,
    ``~`` continuations -- cable geometry only) are counted, not parsed.
    """
    scope: dict[str, Any] = {}
    for key, value in env.items():
        if not key:
            continue
        converted = value_in_declared_unit(
            str(value), definition.units.get(key.lower()))
        scope[key.lower()] = converted if converted is not None else value
    out: dict[str, Any] = {}
    for line, guards in preprocess_script(definition.computations_text):
        s = line.strip()
        if not s or s.startswith("!"):
            continue
        if not all(_guard_holds(g, {**scope, **out}, want) for g, want in guards):
            continue
        match = _COMPUTATION_LINE.match(s)
        if match is None:
            # bare reassignment (``RRHOC = RRHOC*(...)``, 3 in master)
            match = re.match(r"^(\w+)\s*=\s*(.*?)\s*$", s)
            if match and (match.group(1).lower() in scope
                          or match.group(1).lower() in out):
                name, expr = match.group(1), match.group(2)
            else:
                DIAGNOSTICS.emit("computation_unparsed",
                                 f"{definition.name}: {s[:40]}")
                continue
        else:
            name, expr = match.group(2), match.group(3)
        value = _eval_arithmetic(expr, {**scope, **out})
        if value is None:
            DIAGNOSTICS.emit("computation_failed",
                             f"{definition.name}: {s[:40]}")
            continue
        out[name.lower()] = value
    return out


def resolve_numeric(inst: "Instance | None", expr: str,
                    declared_unit: str | None = None,
                    _seen: frozenset = frozenset()) -> float | None:
    """Numeric value of a parameter expression in ``inst``'s canvas namespace.

    The namespace of a value written on canvas C is C's own parameter set --
    i.e. the env of the *instance* of C. A bare identifier is looked up
    there (case-insensitively) and its stored value is then an
    expression in the PARENT instance's namespace, recursively up the tree
    (``PIwithFreeze.Kp = 'Kpd'`` -> VSCControl2 instance env ``Kpd`` -> ...).
    ``$(...)`` globals were already substituted at parse time.
    ``declared_unit`` is the target unit of the parameter this expression is
    the value of; literal values written in another unit are converted at
    every hop (each hop's target comes from the hop's OWN declaration).
    Returns None (loud) when the chain dead-ends.
    """
    expr = (expr or "").strip()
    if not expr:
        return None
    if _NUMBER_WITH_UNIT.match(expr):
        return value_in_declared_unit(expr, declared_unit)
    if inst is None:
        DIAGNOSTICS.emit("param_unresolved_at_root", f"{expr[:30]}")
        return None
    env_ci = {k.lower(): v for k, v in inst.env.items() if k}
    own_units = inst.netlist.own_def.units if inst.netlist.own_def else {}
    idents = set(_IDENT.findall(expr))
    resolved: dict[str, Any] = {}
    for name in idents:
        key = name.lower()
        if key in ("true", "false"):
            continue
        if key not in env_ci:
            DIAGNOSTICS.emit("param_name_missing", f"{inst.canvas}.{name}")
            return None
        if (id(inst), key) in _seen:
            DIAGNOSTICS.emit("param_cycle", f"{inst.canvas}.{name}")
            return None
        value = resolve_numeric(inst.parent, str(env_ci[key]),
                                own_units.get(key),
                                _seen | {(id(inst), key)})
        if value is None:
            return None
        resolved[key] = value
    return _eval_arithmetic(expr, resolved)


@dataclass
class EvaluatedBranch:
    """One Branch line evaluated for a specific component instance."""

    component: Component
    decl: BranchDecl
    #: ``(port_name, index)`` per endpoint; ``(None, None)`` = ground (a
    #: literal 0 node). ``index`` is None for scalar ports.
    node_a: tuple
    node_b: tuple
    #: Numeric values, positionally per kind: rlc -> (R, L, C). A value is
    #: None when its ``$var`` resolves to nothing (master's one dangling
    #: reference, mmc_FullCell.$RTOFF).
    values: tuple

    @property
    def unresolved(self) -> bool:
        return any(v is None for v in self.values)


def _resolve_branch_node(comp: Component, token: str,
                         scope: dict[str, Any]) -> tuple | None:
    """``(port_name, index|None)`` for a node token, or None on failure."""
    if _NUMERIC_TOKEN.match(token):
        return (None, None)  # literal node: ground reference
    match = re.match(r"^(\w+)(?:\((.*)\))?$", token)
    if match is None:
        DIAGNOSTICS.emit("branch_node_syntax", f"{token[:30]}")
        return None
    name, index_expr = match.group(1), match.group(2)
    port_names = { (p.name or "").split(":")[0].lower() for p in
                   (comp.definition.ports if comp.definition else ()) }
    if name.lower() not in port_names:
        DIAGNOSTICS.emit("branch_node_unknown_port", f"{comp.kind}.{name}")
        return None
    index = None
    if index_expr is not None:
        inner = index_expr.strip()
        if inner.startswith("${") and inner.endswith("}"):
            inner = inner[2:-1]
        inner = inner.lstrip("$")
        index = _eval_arithmetic(inner, scope)
        if index is None:
            return None
        index = int(index)
    return (name, index)


def evaluate_branches(inst: "Instance", comp: Component) -> list[EvaluatedBranch]:
    """Evaluate every active Branch line of ``comp`` for instance context
    ``inst`` (the instance of the canvas the component sits on)."""
    if comp.definition is None or not comp.definition.branch_decls:
        return []
    env_ci = {k.lower(): v for k, v in comp.params.items() if k}
    # Guards, node indices AND values may all reference Computations results
    # (pi_section2 gates whole arms on computed ``Cm>0``), so the computed
    # env is built up front and merged into every evaluation scope.
    computed = computations_env(comp.definition, comp.params)
    guard_env = {**env_ci, **computed}
    out: list[EvaluatedBranch] = []

    def value_of(token: str) -> float | None:
        if not token.startswith("$"):
            return as_number(token)
        name = token[1:].lower()
        if name in env_ci:
            return resolve_numeric(inst, str(env_ci[name]),
                                   comp.definition.units.get(name))
        if name in computed:
            return computed[name]
        # A dangling $var. master.pslx itself ships one: mmc_FullCell's
        # DTBP==0 arm says ``BRx = $a $b BREAKER $RTOFF`` but the definition
        # declares Roff/RIoff, no RTOFF (mmc_HalfCell has RTOFF; the FullCell
        # line was evidently copied without renaming). Kept as a None value
        # so the branch is counted rather than lost invisibly.
        #
        # Reported once per DISTINCT dangling name: a defect is a property
        # of the shipped library, and more than one caller evaluates a
        # case's branches, so a per-call count would measure the caller
        # rather than the library.
        entry = (comp.definition.name, name)
        # ...and only what was REPORTED is registered: the registry is
        # permanent for the process, so an entry added while the bus was
        # suppressed would make the finding unreachable rather than quiet
        if entry not in BRANCH_DANGLING_VALUES:
            DIAGNOSTICS.emit("branch_dangling_value",
                             f"{comp.definition.name}.{token[1:]}")
            if not DIAGNOSTICS.suppressing:
                BRANCH_DANGLING_VALUES.add(entry)
        return None

    for decl in comp.definition.branch_decls:
        if not all(_guard_holds(g, guard_env, want) for g, want in decl.guards):
            continue
        if decl.kind == "winding":
            out.append(EvaluatedBranch(comp, decl, (None, None), (None, None), ()))
            continue
        node_a = _resolve_branch_node(comp, decl.node_a, guard_env)
        node_b = _resolve_branch_node(comp, decl.node_b, guard_env)
        if node_a is None or node_b is None:
            continue
        values = tuple(value_of(t) for t in decl.values)
        out.append(EvaluatedBranch(comp, decl, node_a, node_b, values))
    return out


def port_dim(comp: Component, port: Port, inst: "Instance | None" = None,
             _computed_cache: dict | None = None) -> int | None:
    """The dimension (signal width / phase count) of a placed port.

    Numeric ``dim`` attr > 0 wins; else a ``dim_name`` suffix resolves
    case-insensitively against the instance's parameters (hierarchically,
    when ``inst`` is given) or the definition's Computations (datamerge's
    accumulated ``Dim``). None = inherited from the connected signal
    (``dim`` attr 0 with no suffix).
    """
    d = str(port.dim)
    if d.isdigit() and int(d) > 0:
        return int(d)
    if not port.dim_name:
        return None
    key = port.dim_name.lower()
    env_ci = {k.lower(): v for k, v in comp.params.items() if k}
    if key in env_ci:
        declared = (comp.definition.units.get(key)
                    if comp.definition else None)
        value = resolve_numeric(inst, str(env_ci[key]), declared)
        return int(value) if value is not None else None
    if comp.definition is not None:
        if _computed_cache is not None and id(comp) in _computed_cache:
            computed = _computed_cache[id(comp)]
        else:
            computed = computations_env(comp.definition, comp.params)
            if _computed_cache is not None:
                _computed_cache[id(comp)] = computed
        if key in computed:
            value = computed[key]
            return int(value) if isinstance(value, (int, float)) else None
    DIAGNOSTICS.emit("port_dim_unresolved",
                     f"{comp.kind}.{port.name}:{port.dim_name}")
    return None

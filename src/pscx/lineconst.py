"""Line constants: RowCanvas tower geometry -> sequence R/X/B.

A TLine's right-of-way lives on a ``RowCanvas`` definition as ordinary
placed components (``Line_Tower_*``, ``Line_Ground``, ``Line_ManualYZ``,
model-option components). Each carries a ``Model-Data`` script segment
that states its whole data record in ``key = value`` form with
``$param``/``${expr}`` operands under ordinary ``#IF`` guards -- the same
preprocessor and arithmetic the Branch grammar uses. That segment, not a
reading of the tower drawing, is the authority for conductor positions.

From the geometry this module computes the power-frequency series
impedance and shunt admittance matrices (earth return by the
Deri-Semlyen complex-depth formula -- the method the cases themselves
select, ``Line_Ground.EarthForm2 == 0``), eliminates the ground wires by
Kron reduction, and projects onto symmetrical components. What it does
NOT model: conductor skin effect (series R is the DC value), long-line
correction, and any frequency dependence -- these are nominal-pi
constants at one frequency, which is what CIM's ACLineSegment holds.
"""

from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pscx.common import as_number
from pscx.diagnostics import DIAGNOSTICS
from pscx.expr import _eval_arithmetic
from pscx.model import Component, ComponentDef
from pscx.preproc import _guard_holds, preprocess_script
from pscx.units import unit_factor, value_in_declared_unit

#: Vacuum permeability [H/m] and permittivity [F/m].
MU0 = 4.0e-7 * math.pi
EPS0 = 8.8541878128e-12


# --------------------------------------------------------------------------
# Model-Data records
# --------------------------------------------------------------------------

#: One operand: a ``${expr}`` group (never contains whitespace in any
#: master Line Constants segment) or a whitespace-delimited token.
_OPERAND = re.compile(r"\$\{[^}]*\}|\S+")

#: The ``${expr}`` operand form alone, for masking before a brace scan.
_OPERAND_GROUP = re.compile(r"\$\{[^}]*\}")

_IDENT = re.compile(r"[A-Za-z_]\w*")


@dataclass
class Record:
    """One ``key = value...`` line of a Model-Data segment, plus the brace
    block that follows it (``Circuit = 1 { ... }``)."""

    key: str
    values: tuple[str, ...] = ()
    children: list[Record] = field(default_factory=list)

    def child(self, key: str) -> Record | None:
        for record in self.children:
            if record.key.lower() == key.lower():
                return record
        return None


def model_data_records(definition: ComponentDef,
                       params: dict[str, str]) -> list[Record]:
    """Parse a definition's Model-Data segment for one instance's params.

    ``#IF`` guards are evaluated against ``params``; operand tokens are
    left RAW (``$RadiusC``, ``${X-XC}``) for :func:`resolve_operands`,
    which needs each referenced parameter's declared unit.
    """
    env = {k.lower(): v for k, v in params.items() if k}
    root = Record("")
    stack: list[Record] = [root]
    pending: Record | None = None
    for line, guards in preprocess_script(definition.model_data_text):
        text = line.strip()
        if not text or text.startswith("!"):
            continue
        if not all(_guard_holds(expr, env, want) for expr, want in guards):
            continue
        if text in ("{", "}"):
            if text == "{":
                if pending is None:
                    DIAGNOSTICS.emit("modeldata_block_unanchored",
                                     f"{definition.name}")
                    pending = Record("")
                    stack[-1].children.append(pending)
                stack.append(pending)
            elif len(stack) > 1:
                stack.pop()
            else:
                DIAGNOSTICS.emit("modeldata_brace_underflow",
                                 f"{definition.name}")
            pending = None
            continue
        # ``${expr}`` operands carry braces of their own; only structural
        # ones (a block opener sharing a line with data) are a problem.
        if "{" in _OPERAND_GROUP.sub("", text) or "}" in _OPERAND_GROUP.sub(
                "", text):
            DIAGNOSTICS.emit("modeldata_inline_brace",
                             f"{definition.name}: {text[:30]}")
            continue
        if text.endswith(":"):
            pending = Record(text[:-1].strip())
        elif "=" in text:
            key, _, rest = text.partition("=")
            pending = Record(key.strip(), tuple(_OPERAND.findall(rest)))
        else:
            DIAGNOSTICS.emit("modeldata_unparsed",
                             f"{definition.name}: {text[:30]}")
            continue
        stack[-1].children.append(pending)
    if len(stack) != 1:
        DIAGNOSTICS.emit("modeldata_brace_unbalanced", f"{definition.name}")
    return root.children


def _param_unit(definition: ComponentDef, name: str) -> str:
    return definition.units.get(name.lower(), "")


def resolve_operands(record: Record, comp: Component,
                     unit: str | None = None) -> list[float] | None:
    """Numeric value of every operand of ``record``, in ``unit``.

    An operand names form parameters whose values are in their DECLARED
    units; ``unit`` is the unit the caller's physics wants, and
    every referenced parameter must be declared in a unit convertible to
    it -- a mismatch is loud, never a silent scale error. Returns None if
    any operand fails to resolve.
    """
    definition = comp.definition
    if definition is None:
        return None
    env = {k.lower(): v for k, v in comp.params.items() if k}
    out: list[float] = []
    for token in record.values:
        expr = token
        if expr.startswith("${") and expr.endswith("}"):
            expr = expr[2:-1]
        expr = expr.replace("$", "")
        literal = as_number(expr)
        if literal is not None and not _IDENT.search(expr):
            out.append(literal)
            continue
        scope: dict[str, Any] = {}
        factor = 1.0
        for name in set(_IDENT.findall(expr)):
            key = name.lower()
            if key not in env:
                DIAGNOSTICS.emit("modeldata_operand_missing",
                                 f"{comp.kind}.{name}")
                return None
            declared = _param_unit(definition, key)
            value = value_in_declared_unit(str(env[key]), declared)
            if value is None:
                DIAGNOSTICS.emit("modeldata_operand_nonnumeric",
                                 f"{comp.kind}.{name}")
                return None
            if unit is not None and declared != (unit or ""):
                converted = unit_factor(declared or unit, unit)
                if converted is None:
                    DIAGNOSTICS.emit(
                        "modeldata_operand_unit",
                        f"{comp.kind}.{name}: [{declared}] -> [{unit}]")
                    return None
                factor = converted
            scope[key] = value
        value = _eval_arithmetic(expr, scope)
        if value is None:
            DIAGNOSTICS.emit("modeldata_operand_expr",
                             f"{comp.kind}: {token[:30]}")
            return None
        out.append(value * factor)
    return out


# --------------------------------------------------------------------------
# The evaluated record: the engine-facing view of a record-bearing placement
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatedRecord:
    """One Model-Data row with its operands evaluated.

    The dual of :class:`Record`: same key, same nesting, but each
    parameterised operand is resolved to a number in its DECLARED unit
    (never converted), each literal and each of the manual form's format
    words is kept verbatim, and the unit those operands are declared in is
    stated per row. This is what an engine's line-constants solver consumes
    and what the interchange document states; the verbatim ``$param`` text
    stays on the source side.
    """

    key: str
    values: tuple[str, ...]
    unit: str
    children: tuple[EvaluatedRecord, ...]


def _evaluated_operand(token: str, definition: ComponentDef, env: dict,
                       computed: dict, units: set,
                       comp: Component) -> str:
    """One operand's evaluated text.

    A token without ``$`` is already a value -- a literal number or a
    format word -- and is kept verbatim. A ``$`` token resolves through
    the placement's form parameters in their declared units, falling back
    to the definition's Computations environment for computed variables
    (``RR3``, ``Y3`` -- the cable grammar's radii and depth). A token
    that is one bare parameter reference takes the parameter's own TEXT
    where the value is not a number -- the source's ``$``-substitution,
    which is how a tower's ``Name`` row states a name -- while an
    arithmetic operand must evaluate, and one that does not is counted
    and carried verbatim, so the record is loud about it rather than
    silently short one value.
    """
    if "$" not in token:
        return token
    expr = token
    if expr.startswith("${") and expr.endswith("}"):
        expr = expr[2:-1]
    expr = expr.replace("$", "")
    bare = _IDENT.fullmatch(expr) is not None
    scope: dict[str, Any] = {}
    for name in set(_IDENT.findall(expr)):
        key = name.lower()
        if key in env:
            declared = _param_unit(definition, key)
            value = value_in_declared_unit(str(env[key]), declared)
            if value is None:
                if bare:
                    return str(env[key])
                break
            if declared:
                units.add(declared)
            scope[key] = value
        elif key in computed:
            scope[key] = computed[key]
        else:
            break
    else:
        value = _eval_arithmetic(expr, scope)
        if value is not None:
            return repr(float(value))
    DIAGNOSTICS.emit("modeldata_operand_unevaluated",
                     f"{comp.kind}: {token[:30]}")
    return token


def evaluated_records(comp: Component) -> list[EvaluatedRecord]:
    """The evaluated record of one record-bearing placement.

    Parses the placement's Model-Data with its own parameters (guards
    included), then evaluates every operand. The unit stated per row is
    the declared unit of the form parameters the row's operands
    reference, stated only where they agree on exactly one. A row whose
    operands are literals, words or computed variables states none, and
    the key's meaning under the master grammar governs, exactly as it
    does inside PSCAD.
    """
    from pscx.lower import computations_env

    definition = comp.definition
    if definition is None or not definition.model_data_text:
        return []
    env = {k.lower(): v for k, v in comp.params.items() if k}
    computed = (computations_env(definition, comp.params)
                if definition.computations_text else {})

    def evaluate(record: Record) -> EvaluatedRecord:
        units: set[str] = set()
        values = tuple(
            _evaluated_operand(token, definition, env, computed, units, comp)
            for token in record.values)
        return EvaluatedRecord(
            key=record.key, values=values,
            unit=units.pop() if len(units) == 1 else "",
            children=tuple(evaluate(child) for child in record.children))

    return [evaluate(record)
            for record in model_data_records(definition, comp.params)]


def declared_length(device) -> tuple[float, str] | None:
    """The wire's stated length as ``(value, declared unit)``.

    The value is the number the wire writes, untouched; the unit is the
    one it states, or the kilometres its form declares where it states
    none (the counted assumption of :func:`_length_m`). None where no
    length is stated at all -- absence is the caller's to count, never to
    default.
    """
    raw = str(device.params.get("Length") or "").strip()
    if not raw:
        return None
    if "[" in raw:
        number, _, unit = raw.partition("[")
        unit = unit.rstrip("]").strip()
    else:
        number, unit = raw, "km"
    value = as_number(number.strip())
    if value is None or not unit:
        return None
    return value, unit


# --------------------------------------------------------------------------
# Right-of-way geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Conductor:
    """One conductor (or ground wire) of a right-of-way. Positions and
    radii in metres, DC resistance in ohm/m, shunt conductance in S/m."""

    x: float
    y: float
    sag: float
    radius: float
    r_dc: float
    mu_r: float
    #: Sub-conductors per bundle and their spacing on the bundle circle.
    sub: int = 1
    spacing: float = 0.0
    #: Phase number from ``Conductor Phase Information``; None on a
    #: ground wire (which is eliminated, never a phase).
    phase: int | None = None
    shunt_g: float = 0.0

    @property
    def height(self) -> float:
        """Average height: a catenary spends two thirds of its span below
        the tower attachment point."""
        return self.y - 2.0 * self.sag / 3.0


@dataclass
class RightOfWay:
    """Every conductor sharing one RowCanvas, plus the earth beneath."""

    conductors: list[Conductor]
    ground_wires: list[Conductor]
    #: Conductor indices per circuit, in the order the tower declares them.
    circuits: list[tuple[int, ...]]
    earth_resistivity: float
    earth_mu_r: float


#: Model-Data branches this module does not implement. Each names a data
#: entry mode; a case that selects one is counted, and its
#: line keeps the placeholder impedance rather than a value derived from
#: the wrong defaults.
_UNSUPPORTED = {
    "Library": "conductor library lookup",
    "Inner Radius": "hollow conductor",
    "Strand Radius": "stranded conductor",
}


def _conductor_from(block: Record, comp: Component, positions: Record,
                    index: int, *, phase: int | None,
                    sag: Record | None) -> Conductor | None:
    for key, why in _UNSUPPORTED.items():
        if block.child(key) is not None:
            DIAGNOSTICS.emit("lineconst_unsupported", f"{why}")
            return None
    radius = _one(block.child("Radius"), comp, "m")
    r_dc_km = _one(block.child("DCResistance"), comp, "ohm/km")
    mu_r = _one(block.child("Conductor Relative Permeability"), comp, "")
    if mu_r is None:
        mu_r = _one(block.child("Ground Wire Relative Permeability"), comp, "")
    coords = resolve_operands(positions, comp, "m")
    sag_value = _one(sag, comp, "m") if sag is not None else 0.0
    shunt = _one(block.child("ShuntConductance"), comp, "mho/m")
    if radius is None or r_dc_km is None or coords is None or len(coords) != 2:
        return None
    bundle = _one(block.child("Sub-ConductorsPerBundle"), comp, "")
    spacing = 0.0
    if bundle is not None and bundle >= 2:
        inner = block.child("Sub-ConductorsPerBundle")
        spacing_record = inner.child("BundleSpacing") if inner else None
        if spacing_record is None:
            DIAGNOSTICS.emit("lineconst_unsupported", "asymmetric bundle")
            return None
        spacing = _one(spacing_record, comp, "m") or 0.0
    return Conductor(
        x=coords[0], y=coords[1],
        sag=sag_value if sag_value is not None else 0.0,
        radius=radius,
        # the form declares DC resistance per KILOmetre
        r_dc=r_dc_km * 1e-3,
        mu_r=mu_r if mu_r is not None else 1.0,
        sub=int(bundle) if bundle else 1,
        spacing=spacing,
        phase=phase,
        shunt_g=shunt or 0.0,
    )


def _one(record: Record | None, comp: Component,
         unit: str | None) -> float | None:
    if record is None:
        return None
    values = resolve_operands(record, comp, unit)
    return values[0] if values and len(values) == 1 else None


def right_of_way(netlist) -> RightOfWay | None:
    """Read a RowCanvas netlist's tower/ground components into geometry.

    Returns None when the canvas states no tower geometry (a manual-Y/Z
    right-of-way, or an unsupported data entry mode -- both counted).
    """
    conductors: list[Conductor] = []
    ground_wires: list[Conductor] = []
    circuits: list[tuple[int, ...]] = []
    resistivity = mu_earth = None
    for comp in netlist.components:
        kind = comp.master_kind or comp.kind
        definition = comp.definition
        if definition is None or not definition.model_data_text:
            continue
        for record in model_data_records(definition, comp.params):
            if record.key == "Line Constants Ground Data":
                if _one(record.child("Ground Resistivity Type"), comp, "") != 0:
                    DIAGNOSTICS.emit("lineconst_unsupported",
                                     "frequency-dependent ground")
                    return None
                resistivity = _one(record.child("GroundResistivity"),
                                   comp, "ohm*m")
                mu_earth = _one(record.child("GroundPermeability"), comp, "")
                if _one(record.child("EarthImpedanceFormula"), comp, "") != 0:
                    # only the Deri-Semlyen analytical approximation is
                    # implemented; numerical integration would give
                    # different (and here, unvalidated) values
                    DIAGNOSTICS.emit("lineconst_unsupported", "earth formula")
                    return None
            elif record.key == "Line Constants Tower":
                if not _read_tower(record, comp, conductors, ground_wires,
                                   circuits):
                    return None
            elif kind.startswith("Cable_"):
                DIAGNOSTICS.emit("lineconst_unsupported", "cable cross-section")
                return None
    if not conductors:
        return None
    if resistivity is None:
        DIAGNOSTICS.emit("lineconst_missing_ground_data")
        return None
    return RightOfWay(conductors, ground_wires, circuits,
                      resistivity, mu_earth if mu_earth else 1.0)


def _read_tower(tower: Record, comp: Component, conductors: list[Conductor],
                ground_wires: list[Conductor],
                circuits: list[tuple[int, ...]]) -> bool:
    for block in tower.children:
        if block.key == "Circuit":
            phases = resolve_operands(
                block.child("Conductor Phase Information"), comp, "")
            count = _one(block.child("Conductors"), comp, "")
            sag = block.child("Sag")
            if phases is None or count is None or len(phases) != int(count):
                DIAGNOSTICS.emit("lineconst_tower_phases", f"{comp.kind}")
                return False
            start = len(conductors)
            for i in range(int(count)):
                positions = block.child(f"P{i + 1}")
                if positions is None:
                    DIAGNOSTICS.emit("lineconst_tower_position", f"{comp.kind}")
                    return False
                conductor = _conductor_from(block, comp, positions, i,
                                            phase=int(phases[i]), sag=sag)
                if conductor is None:
                    return False
                conductors.append(conductor)
            circuits.append(tuple(range(start, len(conductors))))
        elif block.key == "GroundWires":
            count = _one(block, comp, "")
            if not count:
                continue
            if _one(block.child("Eliminate Ground Wires"), comp, "") != 1:
                # a retained ground wire is a phase of the line; the
                # emission has no conductor to attach it to
                DIAGNOSTICS.emit("lineconst_unsupported", "retained ground wire")
                return False
            sag = block.child("Sag")
            for i in range(int(count)):
                positions = block.child(f"P{i + 1}")
                if positions is None:
                    DIAGNOSTICS.emit("lineconst_gw_position", f"{comp.kind}")
                    return False
                wire = _conductor_from(block, comp, positions, i,
                                       phase=None, sag=sag)
                if wire is None:
                    return False
                ground_wires.append(wire)
    return True


# --------------------------------------------------------------------------
# Physics
# --------------------------------------------------------------------------


def bundle_gmr(gmr_sub: float, count: int, spacing: float) -> float:
    """Geometric mean radius of a symmetric bundle of ``count``
    sub-conductors evenly spaced on a circle, with ``spacing`` between
    adjacent ones: the geometric mean of all count^2 distances, taking a
    sub-conductor's distance to itself as its own GMR."""
    if count <= 1:
        return gmr_sub
    radius = spacing / (2.0 * math.sin(math.pi / count))
    points = [(radius * math.cos(2 * math.pi * k / count),
               radius * math.sin(2 * math.pi * k / count))
              for k in range(count)]
    total = 0.0
    for i, (xi, yi) in enumerate(points):
        for j, (xj, yj) in enumerate(points):
            total += math.log(gmr_sub if i == j else math.hypot(xi - xj,
                                                                yi - yj))
    return math.exp(total / (count * count))


def bundle_radius(radius_sub: float, count: int, spacing: float) -> float:
    """Equivalent radius of a bundle for potential coefficients -- the same
    geometric mean with the physical radius in the self terms."""
    return bundle_gmr(radius_sub, count, spacing)


def complex_depth(resistivity: float, omega: float, mu_r: float) -> complex:
    """Deri-Semlyen complex penetration depth: the earth return path
    behaves as an image conductor buried ``p`` below the surface."""
    return cmath.sqrt(resistivity / (1j * omega * MU0 * mu_r))


def self_impedance(height: float, gmr: float, r_dc: float, depth: complex,
                   omega: float) -> complex:
    """Series self impedance per metre with earth return."""
    return r_dc + 1j * omega * MU0 / (2 * math.pi) * cmath.log(
        2.0 * (height + depth) / gmr)


def mutual_impedance(hi: float, hj: float, dx: float, depth: complex,
                     omega: float) -> complex:
    """Series mutual impedance per metre between two conductors."""
    direct = math.hypot(hi - hj, dx)
    image = cmath.sqrt((hi + hj + 2.0 * depth) ** 2 + dx ** 2)
    return 1j * omega * MU0 / (2 * math.pi) * cmath.log(image / direct)


def _matrices(row: RightOfWay, omega: float) -> tuple[list[list], list[list]]:
    """Series impedance [ohm/m] and potential coefficient [m/F] matrices
    over conductors then ground wires, before any reduction."""
    members = list(row.conductors) + list(row.ground_wires)
    depth = complex_depth(row.earth_resistivity, omega, row.earth_mu_r)
    gmrs = [bundle_gmr(c.radius * math.exp(-c.mu_r / 4.0), c.sub, c.spacing)
            for c in members]
    radii = [bundle_radius(c.radius, c.sub, c.spacing) for c in members]
    n = len(members)
    z = [[0j] * n for _ in range(n)]
    p = [[0j] * n for _ in range(n)]
    for i in range(n):
        ci = members[i]
        z[i][i] = self_impedance(ci.height, gmrs[i], ci.r_dc / ci.sub,
                                 depth, omega)
        p[i][i] = math.log(2.0 * ci.height / radii[i]) / (2 * math.pi * EPS0)
        for j in range(i + 1, n):
            cj = members[j]
            dx = ci.x - cj.x
            z[i][j] = z[j][i] = mutual_impedance(ci.height, cj.height, dx,
                                                 depth, omega)
            direct = math.hypot(ci.height - cj.height, dx)
            image = math.hypot(ci.height + cj.height, dx)
            p[i][j] = p[j][i] = math.log(image / direct) / (2 * math.pi * EPS0)
    return z, p


def kron_reduce(matrix: list[list], keep: int) -> list[list]:
    """Eliminate the trailing rows/columns: ``A - B * D^-1 * C`` for the
    partition ``[[A, B], [C, D]]``. ``keep`` == len(matrix) is a no-op."""
    n = len(matrix)
    if keep >= n:
        return [row[:] for row in matrix]
    d_inv = invert([[matrix[i][j] for j in range(keep, n)]
                    for i in range(keep, n)])
    out = []
    for i in range(keep):
        row = []
        for j in range(keep):
            correction = 0j
            for a in range(n - keep):
                for b in range(n - keep):
                    correction += (matrix[i][keep + a] * d_inv[a][b]
                                   * matrix[keep + b][j])
            row.append(matrix[i][j] - correction)
        out.append(row)
    return out


def invert(matrix: list[list]) -> list[list]:
    """Gauss-Jordan inverse of a small dense complex matrix."""
    n = len(matrix)
    work = [[complex(matrix[i][j]) for j in range(n)]
            + [1j * 0 + (1.0 if i == j else 0.0) for j in range(n)]
            for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) == 0.0:
            raise ZeroDivisionError("singular line-constants matrix")
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [v / scale for v in work[col]]
        for r in range(n):
            if r == col:
                continue
            factor = work[r][col]
            if factor:
                work[r] = [v - factor * w for v, w in zip(work[r], work[col])]
    return [row[n:] for row in work]


def sequence_component(matrix: list[list], order: int) -> complex:
    """The ``order``-th symmetrical component of a square matrix:
    ``(1/n) v^H M v`` with ``v[m] = exp(-j 2 pi order m / n)``. For n=3
    this is the textbook Z1 = Zs - Zm (order 1) and Z0 = Zs + 2Zm
    (order 0); it generalises to the differential mode of a bipole (n=2)
    and to a single conductor (n=1)."""
    n = len(matrix)
    v = [cmath.exp(-2j * math.pi * order * m / n) for m in range(n)]
    total = 0j
    for i in range(n):
        for j in range(n):
            total += v[i].conjugate() * matrix[i][j] * v[j]
    return total / n


@dataclass(frozen=True)
class SequenceConstants:
    """Per-METRE positive- and zero-sequence line constants (SI).

    The zero sequence is None where the source states none: a tower
    right-of-way always yields one (it comes out of the same Kron
    reduction as the positive sequence), while a manual entry may leave
    PSCAD to estimate it, and an estimate we cannot reproduce is not a
    number to emit.
    """

    r1: float
    x1: float
    b1: float
    r0: float | None
    x0: float | None
    b0: float | None
    #: Shunt CONDUCTANCE, the real part of the same sequence admittance
    #: b1/b0 are the imaginary part of. It is what CGMES calls gch/g0ch,
    #: and g0ch is mandatory in the ShortCircuit profile, so it has to be
    #: derived rather than left out.
    g1: float = 0.0
    g0: float | None = None


def sequence_constants(row: RightOfWay,
                       freq_hz: float) -> dict[int, SequenceConstants]:
    """Sequence constants per PHASE NUMBER, one set per circuit.

    Ground wires are Kron-eliminated from both the impedance and the
    potential coefficient matrix (they are at earth potential), the
    remaining phase matrix is inverted to capacitance, and each circuit's
    own sub-block is projected onto symmetrical components. Ideal
    transposition needs no separate step: the projection already takes
    the balanced-mode value.
    """
    omega = 2.0 * math.pi * freq_hz
    z_full, p_full = _matrices(row, omega)
    phases = len(row.conductors)
    z = kron_reduce(z_full, phases)
    p = kron_reduce(p_full, phases)
    capacitance = invert(p)
    admittance = [[1j * omega * capacitance[i][j] for j in range(phases)]
                  for i in range(phases)]
    for i in range(phases):
        admittance[i][i] += row.conductors[i].shunt_g
    out: dict[int, SequenceConstants] = {}
    for circuit in row.circuits:
        z_sub = [[z[i][j] for j in circuit] for i in circuit]
        y_sub = [[admittance[i][j] for j in circuit] for i in circuit]
        z1, z0 = sequence_component(z_sub, 1), sequence_component(z_sub, 0)
        y1, y0 = sequence_component(y_sub, 1), sequence_component(y_sub, 0)
        constants = SequenceConstants(
            r1=z1.real, x1=z1.imag, b1=y1.imag,
            r0=z0.real, x0=z0.imag, b0=y0.imag,
            g1=y1.real, g0=y0.real,
        )
        for index in circuit:
            phase = row.conductors[index].phase
            if phase is not None:
                out[phase] = constants
    return out




# --------------------------------------------------------------------------
# Manual sequence-data entry (Line_ManualYZ)
# --------------------------------------------------------------------------

#: ``Data Entry Format`` -> the operand keys that state the positive
#: sequence in that format. All six formats say the same physics in
#: different units; the two file-based ones state it somewhere else
#: entirely and are counted, never guessed.
_MANUAL_FORMATS = {
    "p.u./m": ("pu", "Capacitive Reactance"),
    "ohms/m": ("ohm", "Capacitive Reactance"),
    "RXB p.u./m": ("pu", "Capacitive Susceptance"),
    "Surge Impedance and Travel Time": ("surge", None),
}


def _manual_sequence(manual: Record, comp: Component, freq_hz: float,
                     basis: str, shunt_key: str | None,
                     prefix: str) -> tuple[float, float, float] | None:
    """``(r, x, b)`` per metre for ONE sequence of a manual entry.

    ``prefix`` selects the sequence: the form names its groups
    ``+ve Sequence ...`` and ``0 Sequence ...``, in the same format and
    the same units, so both sequences are read by one function rather
    than by a positive-sequence reader and a zero-sequence copy of it.
    """
    r = _one(manual.child(f"{prefix} Resistance"), comp, None)
    if basis == "surge":
        # a lossless travelling-wave line: L' = Zc*tau, C' = tau/Zc
        surge = _one(manual.child(f"{prefix} Surge Impedance"), comp, "ohm")
        travel = _one(manual.child(f"{prefix} Travel Time"), comp, "s/m")
        if r is None or not surge or travel is None:
            return None
        omega = 2.0 * math.pi * freq_hz
        return r, omega * surge * travel, omega * travel / surge
    x = _one(manual.child(f"{prefix} Inductive Reactance"), comp, None)
    shunt = _one(manual.child(f"{prefix} {shunt_key}"), comp, None)
    if r is None or x is None or shunt is None:
        return None
    if basis == "pu":
        kv = _one(manual.child("Voltage Rating (kV L-L RMS)"), comp, "kV")
        mva = _one(manual.child("Total MVA Rating"), comp, "MVA")
        if not kv or not mva:
            DIAGNOSTICS.emit("lineconst_manual_no_base")
            return None
        z_base = kv * kv / mva  # (kV)^2 / MVA is ohm
        r, x = r * z_base, x * z_base
        # the reactance form gives Xc [pu*m], the susceptance form B [pu/m]
        b = (1.0 / (shunt * z_base) if shunt_key.endswith("Reactance")
             else shunt / z_base)
    else:
        # the ohm form declares its capacitive reactance in Mohm*m
        b = 1.0 / (shunt * 1e6)
    return r, x, b


def manual_constants(records: list[Record], comp: Component,
                     freq_hz: float) -> SequenceConstants | None:
    """Sequence constants from a ``Line_ManualYZ`` record.

    Both sequences are read where the form states both. The
    zero-sequence group is guarded by ``(NCond>=2)&&(Estim==0)``: a
    single conductor or ``Estim`` set hides the group and the form then
    states no zero sequence at all, so the zero sequence is left ABSENT
    and counted rather than filled with the positive one -- x0 exceeds
    x1 in every real line, so copying it would be a fabrication a
    consumer could not detect.
    ``Long Line Corrected`` is not acted on -- it says whether the
    entered values already fold in the hyperbolic correction, and either
    way they multiply by length.
    """
    manual = next((r for r in records if r.key == "Manual Data Entry"), None)
    if manual is None:
        return None
    fmt_record = manual.child("Data Entry Format")
    fmt = " ".join(fmt_record.values) if fmt_record is not None else ""
    if fmt not in _MANUAL_FORMATS:
        DIAGNOSTICS.emit("lineconst_unsupported",
                         f"manual data format {fmt[:24]}")
        return None
    basis, shunt_key = _MANUAL_FORMATS[fmt]
    positive = _manual_sequence(manual, comp, freq_hz, basis, shunt_key,
                                "+ve Sequence")
    if positive is None:
        DIAGNOSTICS.emit("lineconst_manual_incomplete")
        return None
    zero = _manual_sequence(manual, comp, freq_hz, basis, shunt_key,
                            "0 Sequence")
    if zero is None:
        DIAGNOSTICS.emit("lineconst_manual_no_zero_sequence")
    r1, x1, b1 = positive
    r0, x0, b0 = zero if zero is not None else (None, None, None)
    # None of the four manual formats has a conductance field, so the
    # entered line has no shunt conductance: 0.0 is what the form says,
    # not a stand-in for something it left blank.
    return SequenceConstants(r1=r1, x1=x1, b1=b1, r0=r0, x0=x0, b0=b0,
                             g1=0.0, g0=None if zero is None else 0.0)


# --------------------------------------------------------------------------
# One TLine device -> per-phase impedances
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LineImpedance:
    """Sequence impedances of one whole line segment (SI): series ohms and
    total shunt susceptance, ready for cim:ACLineSegment.

    ``r``/``x``/``b``/``g`` are the positive sequence, which is what the
    EQ profile holds. ``r0``/``x0``/``b0``/``g0`` are the zero sequence,
    which lives in the ShortCircuit profile, and are None where the
    source states none.
    """

    r: float
    x: float
    b: float
    g: float = 0.0
    r0: float | None = None
    x0: float | None = None
    b0: float | None = None
    g0: float | None = None


def line_impedances(flat, inst, device,
                    fallback_freq: float | None = None
                    ) -> dict[int, LineImpedance] | None:
    """Positive-sequence impedance per PHASE for one TLine device.

    The right-of-way is the RowCanvas the device's ``defn`` names; the
    length and solution frequency come from the device's own paramlist.
    Returns None -- always with a counted diagnostic -- when any of that
    is missing or states a mode this module does not implement.
    """
    netlist = flat.right_of_way(device.defn or "")
    if netlist is None:
        DIAGNOSTICS.emit("lineconst_rowdefn_missing", f"{device.defn}")
        return None
    length = _length_m(device)
    if not length:
        DIAGNOSTICS.emit("lineconst_no_length", f"{device.defn}")
        return None
    freq = _frequency_hz(inst, device)
    if freq == 0.0:
        # a right-of-way solved at 0 Hz is a DC line: it has no
        # power-frequency positive sequence for ACLineSegment to hold
        DIAGNOSTICS.emit("lineconst_dc_line", f"{device.defn}")
        return None
    if freq is None:
        freq = fallback_freq
    if not freq:
        DIAGNOSTICS.emit("lineconst_no_frequency", f"{device.defn}")
        return None

    per_metre: dict[int, SequenceConstants] = {}
    for comp in netlist.components:
        definition = comp.definition
        if definition is None or not definition.model_data_text:
            continue
        records = model_data_records(definition, comp.params)
        constants = manual_constants(records, comp, freq)
        if constants is not None:
            # manual entry states one set of constants for the whole
            # right-of-way, at the line's own solution frequency
            per_metre = {phase: constants
                         for phase in range(1, _conductors(device) + 1)}
            break
    else:
        row = right_of_way(netlist)
        if row is None:
            return None
        per_metre = sequence_constants(row, freq)
        # a tower states each conductor's DCResistance, so the sequence
        # resistance this path derives is the DC one, below the
        # power-frequency value of a stranded conductor, since skin effect
        # is not modeled. Counted per phase, like every other thing the
        # derivation knows it leaves out.
        DIAGNOSTICS.emit("lineconst_resistance_dc_only",
                         count=len(per_metre))
    if not per_metre:
        return None
    def scaled(value: float | None) -> float | None:
        return None if value is None else value * length

    return {
        phase: LineImpedance(r=c.r1 * length, x=c.x1 * length,
                             b=c.b1 * length, g=c.g1 * length,
                             r0=scaled(c.r0), x0=scaled(c.x0),
                             b0=scaled(c.b0), g0=scaled(c.g0))
        for phase, c in per_metre.items()
    }


def _conductors(device) -> int:
    value = as_number(str(device.dim or "1"))
    return int(value) if value and value > 0 else 1


def _length_m(device) -> float | None:
    raw = str(device.params.get("Length") or "").strip()
    if not raw:
        return None
    if "[" not in raw:
        # PSCAD's line form declares Length in kilometres; a value written
        # without its unit is counted so the assumption stays visible
        DIAGNOSTICS.emit("lineconst_length_unitless")
        raw = f"{raw} [km]"
    return value_in_declared_unit(raw, "m")


def _frequency_hz(inst, device) -> float | None:
    """The line's own solution frequency, in Hz.

    The value may be an expression in the hosting canvas's namespace
    (``f``), which resolves hierarchically like any other parameter.
    None leaves the caller to fall back on the case's system frequency.
    """
    from pscx.lower import resolve_numeric

    raw = str(device.params.get("Freq") or "").strip()
    if not raw:
        return None
    return resolve_numeric(inst, raw, "Hz")

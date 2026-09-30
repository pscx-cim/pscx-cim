"""Dataclasses: definitions, placed components, nodes, nets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pscx.common import (
    ELECTRICAL_MODES,
    ELECTYPE_GROUND,
    NAME_BRIDGES,
    SIGNAL_MODES,
    _SIGNAL_NAME,
)
from pscx.preproc import BranchDecl, _guard_holds
from pscx.geometry import DisjointSet, Point
from pscx.lower import port_dim


# --------------------------------------------------------------------------
# Library definitions
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PortDef:
    """A port as declared in a component definition (library-local coords).

    A declared name ``B:Dim`` means: the port is called ``B`` and its
    dimension is the value named ``Dim``: a form parameter (most suffixed
    master ports) or a Computations result (the rest, e.g. datamerge's
    accumulated ``Dim``). Suffixed ports carry ``dim`` attr 0;
    the only exceptions (conjugate IN/OUT) put the *name* in the dim attr
    instead, which is normalised here too. Verified over every master port:
    0 suffixes fail to resolve.
    """

    name: str
    x: int
    y: int
    condition: str
    mode: str
    electype: str
    dim: str
    internal: bool
    #: Name of the parameter/computed value holding this port's dimension
    #: (from a ``base:DimName`` port name or a non-numeric dim attr).
    dim_name: str | None = None
    #: Control-signal Fortran type: 1 = Logical, 2 = Integer,
    #: 3 = Real, 4 = Complex; 0/absent = none (electrical/pass-through).
    #: Enum named by the manual ("Control Signal Type ... LOGICAL, INTEGER,
    #: REAL or COMPLEX") and pinned numerically by datamerge/datatap, whose
    #: form choice ``Type`` (0=Logical..3=Complex) selects the port variant
    #: with datatype = Type+1 in lockstep across every conditional port.
    datatype: str = "0"


@dataclass(frozen=True)
class FormParameterDef:
    """One declared form parameter as the LIVENESS machinery needs it: its
    name and type, the unit it is quoted in, and the two condition levels
    that gate it. Enable, Visible and untyped conds are all honoured
    identically.

    A gated-off field is not data: with ``IorMVA == 0`` a machine form's
    ``MVA`` entry is inactive and holds whatever the dialog last had, so
    whether a parameter is LIVE for one placement is decided by evaluating
    ``category_condition`` and ``condition`` against that placement's own
    environment, never by the field merely holding a value.
    """

    name: str
    #: The form's declared type ("Real", "Integer", "Choice", "Text", ...).
    type: str | None
    #: Declared unit, verbatim from the form; None where it states none.
    unit: str | None
    #: The parameter's own ``<cond>``; empty/None gates nothing.
    condition: str | None
    #: The enclosing category's ``<cond>``.
    category_condition: str | None


@dataclass(frozen=True)
class ComponentDef:
    """A component definition plus its form-parameter defaults."""

    namespace: str
    name: str
    ports: tuple[PortDef, ...]
    defaults: dict[str, str] = field(compare=False)
    #: ``(param, guards)`` from #OUTPUT script directives; the instance
    #: *value* of an active param is a signal name the component writes.
    writer_directives: tuple[tuple[str, int | None, tuple], ...] = ()
    #: Parsed Branch script lines (electrical primitives).
    branch_decls: tuple[BranchDecl, ...] = ()
    #: Raw Computations segment text (evaluated lazily per instance env;
    #: cable geometry helpers use functions/#CASE forms that only matter if
    #: something actually references their results).
    computations_text: str = ""
    #: Raw ``Model-Data`` segment text. The Line Constants components
    #: (towers, ground, manual Y/Z) emit their whole data record from this
    #: segment in the ``key = value`` form the line-constants solver reads,
    #: with ``$param``/``${expr}`` operands and ordinary #IF guards -- it is
    #: the authoritative statement of a tower's conductor geometry.
    model_data_text: str = ""
    #: Declared unit per form parameter (lowercased name -> unit string).
    #: Instance values written in other units are converted to these.
    units: dict = field(default_factory=dict, compare=False)
    #: One guard tree per script segment, in document order, as
    #: ``(segment_name, tree)``. A name may repeat (Computations and
    #: Model-Data are each written in several segments), so this is a
    #: sequence and not a mapping. Branch trees have their ``#CASE``/``~``
    #: splices assembled; the rest keep those lines as text,
    #: which is what their own grammars state.
    guard_trees: tuple = field(default=(), compare=False)
    #: Every declared form parameter with its type, unit and condition
    #: gates, in the form's own order (:class:`FormParameterDef`). What
    #: ``defaults``/``units`` flatten away is kept here: which fields a
    #: given placement's selectors actually ENABLE.
    form_parameters: tuple = field(default=(), compare=False)


# --------------------------------------------------------------------------
# Canvas model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Port:
    """A port of a placed component, in absolute canvas coordinates."""

    name: str
    x: int
    y: int
    mode: str
    electype: str
    dim: str
    internal: bool
    local: Point
    #: See PortDef.dim_name: parameter/computed name holding the dimension.
    dim_name: str | None = None
    #: See PortDef.datatype: 1=Logical 2=Integer 3=Real 4=Complex.
    datatype: str = "0"

    @property
    def point(self) -> Point:
        return (self.x, self.y)

    @property
    def is_electrical(self) -> bool:
        return self.mode in ELECTRICAL_MODES and not self.internal

    @property
    def is_signal(self) -> bool:
        return self.mode in SIGNAL_MODES and not self.internal

    @property
    def is_signal_source(self) -> bool:
        """mode 2 = output pin: drives its net."""
        return self.mode == "2" and not self.internal

    @property
    def is_ground(self) -> bool:
        return self.electype == ELECTYPE_GROUND


@dataclass
class Component:
    defn: str
    x: int
    y: int
    orient: int
    ports: list[Port]
    params: dict[str, str]
    element_id: str | None = None
    definition: "ComponentDef | None" = None
    #: Lowercased placed-port name -> dim of the electrical NODE the port
    #: sits on (filled by extract() after node building, with dim 0
    #: inheriting the width of what the port connects to). Used to
    #: evaluate ``$#DIM(port)`` guards strictly, because every guard macro
    #: argument in master names an electrical port.
    node_dims: dict = field(default_factory=dict)

    def dim_of_port(self, name: str) -> int | None:
        """``$#DIM`` resolver: declared numeric dim > 0 wins, else the dim
        of the node the placed port sits on, else None."""
        key = (name or "").lower()
        for port in self.ports:
            if port.name.lower() == key:
                if str(port.dim).isdigit() and int(port.dim) > 0:
                    return int(port.dim)
                if port.dim_name:
                    resolved = port_dim(self, port, None)
                    if resolved is not None:
                        return resolved
                break
        return self.node_dims.get(key)

    @property
    def kind(self) -> str:
        """Bare definition name, without namespace."""
        return self.defn.partition(":")[2] or self.defn

    @property
    def electrical_ports(self) -> list[Port]:
        return [p for p in self.ports if p.is_electrical]

    @property
    def signal_ports(self) -> list[Port]:
        return [p for p in self.ports if p.is_signal]

    @property
    def master_kind(self) -> str:
        """``kind`` when the resolved definition is master's, else ``""``.

        Special-component semantics (NAME_BRIDGES, xnode, radiolink,
        datatap...) key on this so a project-local definition named like a
        master special cannot be misclassified (assumption b closed).
        """
        if self.definition is not None and self.definition.namespace == "master":
            return self.kind
        return ""

    @property
    def is_name_bridge(self) -> bool:
        return self.master_kind in NAME_BRIDGES

    @property
    def written_signals(self) -> list[tuple[str, str]]:
        """``(param, signal_name)`` for each *active*, validly named #OUTPUT
        parameter, guard chains evaluated against this instance's params."""
        if self.definition is None:
            return []
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for param, _dim, guards in self.definition.writer_directives:
            value = (self.params.get(param) or "").strip()
            if not value or not _SIGNAL_NAME.match(value):
                continue
            if not all(_guard_holds(expr, self.params, want, self.dim_of_port)
                       for expr, want in guards):
                continue
            if (param, value) not in seen:
                seen.add((param, value))
                out.append((param, value))
        return out

    @property
    def suppressed_writer_values(self) -> list[tuple[str, str]]:
        """Non-empty #OUTPUT parameter values that are NOT valid signal names
        (e.g. ``fft.Fout = '\"'``). Exposed so nothing is filtered
        invisibly."""
        if self.definition is None:
            return []
        bad = []
        for param, _dim, _guards in self.definition.writer_directives:
            value = (self.params.get(param) or "").strip()
            if value and not _SIGNAL_NAME.match(value):
                bad.append((param, value))
        return sorted(set(bad))


@dataclass
class Device:
    """A two-terminal element hosted by a TLine/Cable/WireBranch wire."""

    kind: str
    defn: str | None
    name: str
    dim: str
    terminal_a: Any
    terminal_b: Any
    vertex_a: Point
    vertex_b: Point
    #: The hosted ``<User>``'s whole paramlist. ``Name``/``Dim`` are lifted
    #: to fields above; the rest carries the line's own data -- ``Length``
    #: and ``Freq`` are what turn per-metre line constants into an
    #: impedance.
    params: dict = field(default_factory=dict)


@dataclass
class Node:
    """One electrical node. ``dim`` > 1 means it carries that many phases."""

    key: Any
    ports: list[tuple[Component, Port]]
    dim: int
    is_ground: bool
    #: Names/BaseKVs of the Bus wires belonging to this node.
    bus_names: tuple = ()
    bus_kvs: tuple = ()

    @property
    def base_kv(self) -> float | None:
        """The node's single base voltage; None if unset or conflicting."""
        kvs = set(self.bus_kvs)
        return kvs.pop() if len(kvs) == 1 else None


@dataclass
class SignalNet:
    """One data-signal net: wire nets joined with canvas-scoped names.

    ``drivers``/``readers`` entries are tagged tuples:

    - ``("port", component, port)`` -- a mode-2 / mode-1 pin of an ordinary
      component (name bridges excluded; they join, they don't source or sink),
    - ``("writer", component, param, name)`` -- a non-empty ``#OUTPUT``
      parameter (drivers only),
    - ``("boundary_in"|"boundary_out", port_name)`` -- a mode-1 / mode-2
      graphics Port of the canvas's *own* definition matched by name,
    - ``("form_param", name)`` -- a form parameter of the canvas's own
      definition matched by name (drivers only; a constant source).
    """

    key: Any
    ports: list[tuple["Component", Port]]
    bridges: list[tuple["Component", Port]]
    names: set[str]
    drivers: list[tuple]
    readers: list[tuple]


@dataclass
class Netlist:
    case: str
    canvas: str
    components: list[Component]
    nodes: list[Node]
    devices: list[Device]
    segments: list[tuple[Point, Point]]
    stubs: list[tuple[tuple[Point, Point], Any]] = field(default_factory=list)
    #: Union-find over coordinates and terminal sentinels. Needed to map a
    #: device terminal (which may carry no component port) onto its node.
    dsu: "DisjointSet | None" = None
    dim_conflicts: int = 0
    #: The data-signal graph -- entirely separate from the electrical nodes.
    signal_nets: list[SignalNet] = field(default_factory=list)
    signal_segments: list[tuple[Point, Point]] = field(default_factory=list)
    signal_dsu: "DisjointSet | None" = None
    #: The canvas's own definition; its graphics Ports and form parameters
    #: are part of the canvas's signal namespace.
    own_def: "ComponentDef | None" = None
    #: schematic classid: "UserCanvas" (module page) or "RowCanvas" (TLine/
    #: Cable right-of-way geometry -- tower data, not circuit topology).
    classid: str | None = None
    #: Raw ``(name, base_kv, anchor_point)`` per Bus wire on this canvas;
    #: names/kvs are also attached to the owning Node objects.
    buses: list = field(default_factory=list)
    #: Project namespace this canvas was read from. With ``canvas``, the
    #: page key: two projects can each have a ``Main``.
    namespace: str = ""

    def node_of(self, key: Any) -> "Node | None":
        """Node containing an arbitrary key (coordinate or terminal sentinel)."""
        if self.dsu is None:
            return None
        root = self.dsu.find(key)
        for node in self.nodes:
            if self.dsu.find(node.key) == root:
                return node
        return None

    def same_node(self, a: Any, b: Any) -> bool:
        return self.dsu is not None and self.dsu.find(a) == self.dsu.find(b)

    @property
    def electrical_ports(self) -> list[tuple[Component, Port]]:
        return [(c, p) for c in self.components for p in c.electrical_ports]

    @property
    def phase_expanded_node_count(self) -> int:
        return sum(n.dim for n in self.nodes)

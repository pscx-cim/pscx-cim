"""The DC subsystem: which drawn nodes are DC, and what stands on them.

CGMES models a DC network with its own node space -- ``DCNode`` and
``DCTopologicalNode`` rather than ``ConnectivityNode`` and
``TopologicalNode``, ``DCTerminal`` and ``ACDCConverterDCTerminal``
rather than ``Terminal``, and a ``DCEquipmentContainer``
(``DCConverterUnit`` or ``DCLine``) where AC equipment has a
``VoltageLevel``. So a converter mapping cannot begin with the converter:
its two DC terminals have to have somewhere to attach, and deciding
WHICH drawn nodes those are is the whole of this module.

**The rule is the audit's, not a second one.** :func:`pscx.audit.dc_map`
already answers "what does an AC source reach", and the complement of
that is the DC side. Its stated property is that the complement is an
OVER-estimate -- an unmapped series element truncates reachability
exactly as a converter does -- so it bounds the answer rather than being
it. What this module adds is the other bound, grown from the converter's
own DC ports outward, and the two are asserted to sandwich:

    poles-outward region  ⊆  what no AC source reaches

The lower bound is a statement the CASE makes: a ``g6p200_2`` names its
DC poles ``DP`` and ``DN`` on its own port list, so a node one of them
sits on is DC because the component says so, not because a sweep failed
to reach it. The upper bound is the audit's. A region that escaped the
upper bound would mean an AC source reaches a node a converter calls a
DC pole, and that is a finding rather than a number to widen.

**Growth stops where a DC class would have to be invented.** The region
is grown only through equipment that has a DC counterpart -- passive
R/L/C to ``DCSeriesDevice``, zero-impedance conduction to
``DCDisconnector`` -- and stops at ground, at another converter, and at
any component this repo cannot name. Where something else stands on a
region node, the whole converter group is REFUSED with the reason, so a
half-DC island is never emitted: a drawn node cannot be a DCNode for one
of its elements and a ConnectivityNode for another, and pretending
otherwise would state a DC network that is disconnected from its own
smoothing reactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple


class ConverterForm(NamedTuple):
    """Where one converter kind states the things CIM asks a converter for.

    ``dc_ports`` maps the form's own port names to the
    ``ACDCConverterDCTerminal.polarity`` each one carries -- PSCAD names
    the poles and CIM asks for the polarity, and the two say the same
    thing in different words. ``valves`` is ``ACDCConverter``'s
    ``numberOfValves``, which for a bridge is a property of the topology
    rather than of the instance.

    ``firing`` names the signal ports that carry the firing control. They
    are deliberately not attributes: CGMES has no vocabulary for a firing
    angle as a SIGNAL (``CsConverter`` states operating-point alpha and
    gamma, which is a different quantity), so they belong to the
    phase-resolution layer and are recorded here only so the mapping can
    say what it is leaving behind.
    """

    cim_class: str
    dc_ports: dict[str, str]
    valves: int
    firing: tuple[str, ...]


#: The converter kinds this repo can name a CIM class for. Only the
#: six-pulse bridge so far: the MMC cells and `peswitch` are single
#: valves and submodules, which CIM has no class for at all because it
#: models the CONVERTER and never its switches.
CONVERTER_FORMS: dict[str, ConverterForm] = {
    "g6p200_2": ConverterForm(
        cim_class="CsConverter",
        dc_ports={"DP": "positive", "DN": "negative"},
        valves=6,
        firing=("AO", "AM", "GM", "KB"),
    ),
}

#: Master kinds whose two-terminal element has a DC counterpart, and the
#: class it would take. A resistor and an inductor in the DC circuit are
#: the smoothing reactor and its damping; a capacitor across the poles is
#: the DC filter. All three are `DCSeriesDevice` when they stand between
#: two live DC nodes.
DC_SERIES_KINDS = frozenset({"resistor", "inductor", "capacitor"})


@dataclass(frozen=True)
class Converter:
    """One converter placement, read through its own port names."""

    instance: Any
    component: Any
    kind: str
    form: ConverterForm
    #: drawn node -> conductors, for the AC side. One entry under
    #: `View=1`, because a dim-3 port is ONE node, which is exactly the
    #: single AC terminal `ACDCConverter` asks for.
    ac: tuple[tuple[Any, tuple[int, ...]], ...]
    #: polarity -> drawn node, for the two DC poles.
    poles: dict[str, Any]


@dataclass(frozen=True)
class DcGroup:
    """One DC island and the converters that feed it."""

    converters: tuple[Converter, ...]
    nodes: frozenset
    #: ``(instance index, component id)`` of every element inside.
    series: frozenset
    #: Drawn nodes of the island that are the case's ground symbol. A DC
    #: pole on the earth is what a monopolar link's return looks like and
    #: `cim:DCGround` is the class for it.
    grounded: frozenset
    #: None when the group is emittable, else why it is not.
    refusal: str | None


@dataclass(frozen=True)
class DcSubsystem:
    """Every DC island in one case, and what could not be read."""

    groups: tuple[DcGroup, ...]
    #: Converter placements whose ports did not resolve at all.
    unresolved: int

    @property
    def emittable(self) -> tuple[DcGroup, ...]:
        return tuple(g for g in self.groups if g.refusal is None)

    @property
    def nodes(self) -> frozenset:
        """Drawn nodes that would become DCNodes."""
        out: set = set()
        for group in self.emittable:
            out |= group.nodes
        return frozenset(out)


def converter_placements(flat, widths) -> tuple[list[Converter], int]:
    """Every converter in a flattened case, with its ports read by NAME.

    The port name is the statement, not the conductor count. A width-3
    endpoint is the AC terminal under `View=1`, but that is a property of
    the drawing, and the form is what says which port is a pole. `View=0`
    draws the same bridge with three scalar AC ports named A/B/C and the
    same two named DP/DN.
    """
    found: list[Converter] = []
    unresolved = 0
    for inst in flat.instances:
        for comp in inst.netlist.components:
            kind = comp.master_kind or comp.kind
            form = CONVERTER_FORMS.get(kind)
            if form is None:
                continue
            ac: dict[Any, set] = {}
            poles: dict[str, Any] = {}
            for node in inst.netlist.nodes:
                for placed, port in node.ports:
                    if placed is not comp or port.mode not in ("3", "4"):
                        continue
                    name = (port.name or "").upper()
                    key = flat.flat_node_key(inst, node.key)
                    if name in form.dc_ports:
                        poles[form.dc_ports[name]] = key
                    elif name in ("N", "A", "B", "C"):
                        ac.setdefault(key, set()).update(
                            range(1, widths.get(key, 1) + 1))
            if len(poles) != len(form.dc_ports) or not ac:
                unresolved += 1
                continue
            found.append(Converter(
                instance=inst, component=comp, kind=kind, form=form,
                ac=tuple((key, tuple(sorted(ac[key])))
                         for key in sorted(ac, key=repr)),
                poles=poles,
            ))
    return found, unresolved


def dc_subsystem(flat, graph, widths, *, ground_keys=None) -> DcSubsystem:
    """The DC islands of one case, each with its verdict.

    ``graph`` is the per-phase LIR; ``widths`` the emission widths.
    ``ground_keys`` are the drawn nodes the case's ground symbol makes,
    which bound the region: PSCAD's earth is ONE flat node for the whole
    case, so growing through it would union the DC return with
    every AC shunt's far end and call the result a DC island.
    """
    from pscx.rules import CONDUCTION_KINDS, MASTER_KIND_TO_CIM

    ground_keys = frozenset(ground_keys or ())
    converters, unresolved = converter_placements(flat, widths)
    if not converters:
        return DcSubsystem(groups=(), unresolved=unresolved)

    converter_ids = {(c.instance.index, id(c.component)) for c in converters}
    pole_keys = {key for c in converters for key in c.poles.values()}

    #: drawn node -> the placements standing on it, with whether each one
    #: has a DC counterpart. Read off the LIR's own edges so a component
    #: with no Branch row cannot be missed by looking at ports alone.
    standing: dict[Any, set] = {}
    adjacency: dict[Any, set] = {}
    classable: dict[tuple, bool] = {}
    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        inst = data.get("instance")
        if comp is None or inst is None:
            continue
        placement = (inst.index, id(comp))
        if placement in converter_ids:
            continue
        kind = comp.master_kind or comp.kind
        is_dc_classable = (
            (kind in DC_SERIES_KINDS
             and MASTER_KIND_TO_CIM.get(kind) == "EquivalentBranch")
            or kind in CONDUCTION_KINDS)
        classable[placement] = is_dc_classable
        key_u, key_v = u[0], v[0]
        for key in (key_u, key_v):
            standing.setdefault(key, set()).add(placement)
        if is_dc_classable and key_u not in ground_keys \
                and key_v not in ground_keys:
            adjacency.setdefault(key_u, set()).add(key_v)
            adjacency.setdefault(key_v, set()).add(key_u)

    # grow each pole outward, then merge the converters that land in one
    # island -- a 12-pulse stack is two bridges in series on the DC side
    # and they share a midpoint node, so they are ONE DCConverterUnit
    region_of: dict[Any, frozenset] = {}
    for start in pole_keys:
        if start in region_of:
            continue
        seen, queue = {start}, [start]
        while queue:
            key = queue.pop()
            if key in ground_keys:
                continue
            for other in adjacency.get(key, ()):
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        frozen = frozenset(seen)
        for key in seen:
            region_of[key] = frozen

    islands: dict[frozenset, list[Converter]] = {}
    for converter in converters:
        merged: set = set()
        for key in converter.poles.values():
            merged |= region_of.get(key, {key})
        islands.setdefault(frozenset(merged), []).append(converter)

    # merge islands that overlap: two poles of one converter may have
    # grown into separate sets that a third converter joins
    changed = True
    while changed:
        changed = False
        for a in list(islands):
            for b in list(islands):
                if a is b or a not in islands or b not in islands or a == b:
                    continue
                if a & b:
                    islands[frozenset(a | b)] = (islands.pop(a)
                                                 + islands.pop(b))
                    changed = True
                    break
            if changed:
                break

    groups = []
    for nodes, members in islands.items():
        # What stands on the island is asked of its LIVE nodes only. The
        # earth is one flat node for the whole case, so every grounded
        # element in it -- a transformer neutral, an AC shunt -- stands on
        # the node a monopolar link's return also lands on, and counting
        # those as island members would refuse a group for equipment
        # that is not on the DC side at all.
        inside = {p for key in nodes - (nodes & ground_keys)
                  for p in standing.get(key, ())}
        foreign = sorted({p for p in inside if not classable.get(p, False)})
        refusal = None
        if foreign:
            kinds = sorted({_kind_of(flat, p) for p in foreign})
            refusal = (
                "an element with no DC class stands on this island: "
                + ", ".join(kinds))
        groups.append(DcGroup(
            converters=tuple(members),
            nodes=frozenset(nodes),
            series=frozenset(p for p in inside if classable.get(p, False)),
            grounded=frozenset(nodes & ground_keys),
            refusal=refusal,
        ))
    return DcSubsystem(groups=tuple(groups), unresolved=unresolved)


def _kind_of(flat, placement) -> str:
    index, comp_id = placement
    for inst in flat.instances:
        if inst.index != index:
            continue
        for comp in inst.netlist.components:
            if id(comp) == comp_id:
                return comp.master_kind or comp.kind
    return "?"

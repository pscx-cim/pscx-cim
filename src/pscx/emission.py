"""The projection from the per-phase LIR onto the granularity PSCAD drew,
and its inverse.

A dim-3 port IS three electrical nodes drawn as one symbol. The
per-phase split the LIR performs is an elaboration device introduced for
one internal reason -- a Branch declaration indexes phases as ``$N(3)``,
so endpoint placement needs per-phase nodes -- and not a
property of the source. PSCAD says which granularity a case draws in the
component form itself: ``breaker3``, ``xfmr-3p2w``, ``source_3`` and the
rest declare a ``View`` parameter selecting between one dim-3 port and
three dim-1 ports named A/B/C, and ``dim`` is that choice made
observable. So the emission rule is not "collapse where it is safe", it
is EMIT AT THE GRANULARITY PSCAD DREW: one drawn node is one
ConnectivityNode however many phases it carries, and three per-phase
wires are three ConnectivityNodes because that is what the case draws.

The inverse lives here too, in the same file, because losslessness is a
property of the pair: :func:`reconstruct` rebuilds the per-phase network
from emitted documents alone, and the oracle that composes it with the
forward projection is what turns "the projection loses nothing" into a
test rather than a claim.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from pscx.diagnostics import DIAGNOSTICS
from pscx.rules import IMPLICIT_GROUND, PHASE_LETTERS, TWO_TERMINAL


def project(lir_node: tuple) -> Any:
    """The emission node a per-phase LIR node belongs to.

    This is the whole projection: the phase is dropped, because the phase
    was never in the source -- it is the index the LIR introduced.
    """
    return lir_node[0]


def emission_widths(graph) -> dict[Any, int]:
    """Emission node -> how many electrical nodes the one symbol draws.

    Read from the LIR rather than from ``FlatNode.dim`` so that
    device-internal nodes, which have no FlatNode, are covered
    by the same rule.
    """
    widths: dict[Any, int] = {}
    for key, phase in graph.nodes:
        widths[key] = max(widths.get(key, 0), phase)
    return widths


def paired_phases(width_a: int, width_b: int) -> list[tuple[int, int]]:
    """The per-phase conductor pairing of one two-terminal element drawn
    between emission nodes of these widths.

    Equal widths pair conductor-to-conductor; a width-1 end broadcasts,
    which is how a three-phase shunt bank drawn as one symbol reaches its
    single star point or ground. This is the ONE statement of the rule:
    :func:`pscx.lir._phase_pairs` expands whole-port branch endpoints
    through it, and :func:`reconstruct` inverts it.
    """
    if width_a == width_b:
        return [(k, k) for k in range(1, width_a + 1)]
    if width_a == 1:
        return [(1, k) for k in range(1, width_b + 1)]
    if width_b == 1:
        return [(k, 1) for k in range(1, width_a + 1)]
    return []


def component_ends(graph) -> dict[tuple, set]:
    """Placement -> the per-phase LIR endpoints its Branch rows touch.

    Keyed by ``(instance index, component identity)`` and NOT by component
    alone: instances of one canvas share their ``Netlist`` and therefore
    their ``Component`` objects, so ``id(comp)`` pools four placements'
    endpoints into one. MMC_HalfBridge_Bipole places its converter
    transformer module four times.
    """
    ends: dict[tuple, set] = {}
    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        if comp is None:
            continue
        ends.setdefault((data["instance"].index, id(comp)), set()).update(
            (u, v))
    return ends


def placement_ends(flat, graph) -> dict[tuple, list]:
    """Placement -> its per-phase LIR connection points, sorted.

    A component's Branch rows say where it meets the network; one that
    declares none (an interface or a purely graphical placement) falls
    back to the nodes its own electrical ports sit on, every conductor of
    them -- the same fallback the meter anchoring uses. Keyed by
    ``(instance index, component identity)``, because instances of one
    canvas share their Component objects.

    This is ONE definition, called by both the equipment document (which
    gives each of these nodes a ConnectivityNode) and the add-on profile
    (which puts an ``emt:ModelTerminal`` on each of them). Two definitions
    that agreed by inspection would let the two documents describe
    different sets, and "nothing is dropped" would then mean nothing --
    the same reason ``unmapped_components`` is one definition.
    """
    ends = component_ends(graph)
    from_branches = set(ends)
    widths = emission_widths(graph)
    for inst in flat.instances:
        for node in inst.netlist.nodes:
            for comp, _port in node.ports:
                if (inst.index, id(comp)) in from_branches:
                    continue
                key = flat.flat_node_key(inst, node.key)
                ends.setdefault((inst.index, id(comp)), set()).update(
                    (key, phase) for phase in range(1, widths.get(key, 1) + 1))
    return {k: sorted(v, key=repr) for k, v in ends.items()}


def device_ends(flat, inst, device, widths: dict) -> list[tuple]:
    """A hosted device's per-phase connection points, sorted.

    A device is not shaped like a component and its ends are not ports:
    a TLine/Cable/WireBranch body meets the network at the two SYNTHETIC
    terminal keys its hosting wire carries, which is what keeps a
    collapsed body from shorting the line through a shared vertex. So the
    port fallback :func:`placement_ends` uses does not apply here, and
    neither does a Branch row -- a device declares none.

    Both keys always exist: ``Device.terminal_a``/``.terminal_b`` are set
    from the hosting wire's own id when the device is read, and
    ``flat_node_key`` is total, so a device end cannot fail to resolve.
    """
    ends: set = set()
    for terminal in (device.terminal_a, device.terminal_b):
        key = flat.flat_node_key(inst, terminal)
        ends.update((key, phase)
                    for phase in range(1, widths.get(key, 1) + 1))
    return sorted(ends, key=repr)


def phase_code(width: int, phases: tuple[int, ...]) -> str | None:
    """cim:PhaseCode for a terminal touching ``phases`` of a ``width``-wide
    drawn node, or None when standard CIM cannot state it.

    Only a drawn THREE-phase bundle has phase letters: index k of it is
    conductor k of an A/B/C set, which is what PSCAD's own per-phase view
    of the same component spells out. A node of any other width is a
    number of conductors CIM's PhaseCode has no member for -- one
    unnamed conductor, a bipole, a six-conductor double circuit -- and
    the width is carried by the extension profile instead, which is the
    one thing CGMES has no vocabulary for.

    Note also that stating a code where CIM cannot is not merely
    unhelpful, it is invalid: 301's Terminal.phases consistency rules
    forbid a two-terminal element whose ends carry different codes, so
    labelling a broadcast branch's scalar end at all would make the
    three-phase end unstateable.
    """
    if width != 3:
        return None
    letters = [PHASE_LETTERS[p] for p in sorted(phases) if p in PHASE_LETTERS]
    if len(letters) != len(phases):
        return None
    return "".join(letters) or None


@dataclass(frozen=True)
class DrawnElement:
    """One two-terminal element at the granularity the case draws it.

    ``node_a``/``node_b`` are emission nodes, ``phases`` the conductor
    pairs the one symbol summarises in ascending order, and ``edges`` the
    per-phase LIR edges parallel to them. ``label`` is the Branch row's
    own label, which is what tells two rows of one component apart when
    the case draws them separately.
    """

    component: Any
    instance: Any
    label: str
    node_a: Any
    node_b: Any
    phases: tuple[tuple[int, int], ...]
    edges: tuple[dict, ...]

    @property
    def phases_a(self) -> tuple[int, ...]:
        return tuple(a for a, _b in self.phases)

    @property
    def phases_b(self) -> tuple[int, ...]:
        return tuple(b for _a, b in self.phases)


def drawn_elements(edges, widths: dict[Any, int], *,
                   diagnostics=None) -> list[DrawnElement]:
    """Group per-phase LIR edges into the elements the case draws.

    ``edges`` is an iterable of ``(u, v, data)`` LIR edges that all map to
    the same CIM class. One drawn element is one component's one Branch
    row between one pair of emission nodes: a ``breaker3`` in single-line
    view declares ONE row across a dim-3 port pair and is one Breaker,
    while the same component in per-phase view declares three labelled
    rows across three scalar node pairs and is three Breakers. Neither is
    a choice this function makes -- it reads what the case drew.

    Two things it refuses to do quietly, both counted: collapse an
    element whose two ends land on ONE emission node (a two-terminal CIM
    object on one node states nothing), and collapse a group that does
    not cover the full conductor pairing of its two nodes (an element
    wired to some conductors of a bundle and not others, which standard
    CIM could only state through per-terminal phase subsets).

    ``diagnostics`` is the sink those three findings go to. They are
    statements about ONE emitted model, so build_cim hands its own sink
    and the findings land where a per-model assertion can read them;
    ``emit_files`` then extends that sink onto the process-global bus, so
    each reaches the global exactly once. The default is the global bus,
    which is where a caller with no model to attribute them to needs them.
    """
    diagnostics = DIAGNOSTICS if diagnostics is None else diagnostics
    groups: dict[tuple, list] = {}
    for u, v, data in edges:
        component = data["component"]
        label = data["branch"].decl.label or "BR"
        # Canonical orientation by the emission key's repr: the LIR is a
        # networkx MultiGraph, whose iteration order decides which
        # endpoint comes back first, and terminal sequence numbers must
        # not depend on that.
        a, b = ((u, v) if repr(u[0]) <= repr(v[0]) else (v, u))
        # The PLACEMENT is part of the identity, not just the component:
        # instances of one canvas share their Component objects, so a
        # module drawn four times would otherwise collapse into one
        # element with four conductors on the same pair.
        groups.setdefault((data["instance"].index, id(component), label,
                           repr(a[0]), repr(b[0])), []).append((a, b, data))

    out: list[DrawnElement] = []
    for members in groups.values():
        a0, b0, data0 = members[0]
        node_a, node_b = a0[0], b0[0]
        if node_a == node_b:
            # both ends of one drawn element on one drawn node: the
            # per-phase edge was between two conductors of the same
            # bundle (a delta element drawn on a single dim-3 port), and
            # a two-terminal CIM object cannot say that.
            diagnostics.emit("cim_element_self_loop")
            continue
        phases = tuple(sorted((a[1], b[1]) for a, b, _d in members))
        if len(set(phases)) != len(phases):
            # two per-phase edges of one group on the same conductor pair
            # are parallel elements, not phases of one element
            diagnostics.emit("cim_element_phase_overlap")
            continue
        expected = paired_phases(widths.get(node_a, 1), widths.get(node_b, 1))
        if phases != tuple(sorted(expected)):
            diagnostics.emit("cim_element_partial_phases")
        by_phase = {(a[1], b[1]): d for a, b, d in members}
        out.append(DrawnElement(
            component=data0["component"], instance=data0["instance"],
            label=data0["branch"].decl.label or "BR",
            node_a=node_a, node_b=node_b, phases=phases,
            edges=tuple(by_phase[p] for p in phases),
        ))
    return out


def drawn_sides(phase_nodes: list[tuple]) -> list[Any]:
    """The emission nodes a list of per-phase endpoints lands on, in first
    -appearance order and without repetition.

    A transformer winding side is stated as one per-phase endpoint per
    phase. In single-line view all of them index one dim-3 port
    and the side is ONE drawn node; in per-phase view they are three
    scalar nodes and the side is three.
    """
    out: list[Any] = []
    for key, _phase in phase_nodes:
        if key not in out:
            out.append(key)
    return out


# --------------------------------------------------------------------------
# The inverse: emitted documents -> the per-phase network
# --------------------------------------------------------------------------

CIM_NS = "http://iec.ch/TC57/CIM100#"



@dataclass(frozen=True)
class PerPhaseNetwork:
    """The per-phase electrical network a set of documents states.

    ``nodes`` is every ``(ConnectivityNode mRID, conductor)`` pair;
    ``edges`` a MULTISET of ``(class, end, end)`` per-phase connections in
    terminal-sequence order, because two identical elements in parallel
    are two connections and a set would silently merge them;
    ``model_ends`` a multiset of the extension profile's per-phase
    connection points.
    """

    nodes: frozenset
    edges: Counter
    model_ends: Counter


def _mrid(iri) -> str:
    return str(iri).removeprefix("urn:uuid:")


def reconstruct(graphs) -> PerPhaseNetwork:
    """Rebuild the per-phase network from emitted documents ALONE.

    Nothing here reads the LIR, the flattened project or the emitter:
    the conductor count of a drawn node comes from
    ``emt:ConnectivityNode.phaseCount``, the conductor a proprietary
    model's terminal touches from ``emt:ModelTerminal.phase``, and the
    per-phase expansion of a two-terminal element from
    :func:`paired_phases` -- the same one statement of the rule the
    forward projection collapses.

    A ``ModelTerminal`` with no phase yields a connection point on
    conductor ``None``, which no per-phase network contains: the reader
    reports what the document says rather than repairing it, so dropping
    a phase makes the reconstruction fail instead of silently succeeding.
    """
    import rdflib
    from rdflib import RDF

    from pscx.emt import EMT_NS

    merged = rdflib.Graph()
    for graph in graphs:
        for triple in graph:
            merged.add(triple)

    cim = rdflib.Namespace(CIM_NS)
    emt = rdflib.Namespace(EMT_NS)

    widths: dict[str, int] = {}
    for subject, count in merged.subject_objects(
            emt["ConnectivityNode.phaseCount"]):
        widths[_mrid(subject)] = int(str(count))

    nodes = frozenset(
        (node, phase)
        for node, width in widths.items()
        for phase in range(1, width + 1)
    )

    #: terminal -> (equipment, connectivity node, sequence number)
    terminals: dict[Any, dict] = {}
    for subject in set(merged.subjects(cim["Terminal.ConductingEquipment"],
                                       None)):
        equipment = merged.value(subject, cim["Terminal.ConductingEquipment"])
        node = merged.value(subject, cim["Terminal.ConnectivityNode"])
        sequence = merged.value(subject, cim["ACDCTerminal.sequenceNumber"])
        terminals[subject] = {
            "equipment": _mrid(equipment) if equipment else None,
            "node": _mrid(node) if node else None,
            "sequence": int(str(sequence)) if sequence is not None else None,
        }

    by_equipment: dict[str, list] = {}
    for entry in terminals.values():
        if entry["equipment"]:
            by_equipment.setdefault(entry["equipment"], []).append(entry)

    classes: dict[str, str] = {}
    grounds: set = set()
    for subject, kind in merged.subject_objects(RDF.type):
        name = str(kind).rsplit("#", 1)[-1]
        if name in TWO_TERMINAL or name in IMPLICIT_GROUND:
            classes[_mrid(subject)] = name
        elif name == "Ground":
            grounds.add(_mrid(subject))

    # The earth reference, read off the documents like everything else
    # here. Exactly one is required before an implicit ground end is
    # rebuilt from it: with none, or with two, the absent terminal names no
    # particular node and the reader reports what the document says rather
    # than choosing.
    ground_nodes = {entry["node"] for entry in terminals.values()
                    if entry["equipment"] in grounds and entry["node"]}
    ground_node = ground_nodes.pop() if len(ground_nodes) == 1 else None

    edges: Counter = Counter()
    for equipment, name in classes.items():
        own = sorted(by_equipment.get(equipment, ()),
                     key=lambda e: (e["sequence"] is None, e["sequence"]))
        if name in IMPLICIT_GROUND:
            if len(own) != 1 or ground_node is None:
                continue
            node_a, node_b = own[0]["node"], ground_node
        elif len(own) != 2:
            continue
        else:
            node_a, node_b = own[0]["node"], own[1]["node"]
        for phase_a, phase_b in paired_phases(widths.get(node_a, 1),
                                              widths.get(node_b, 1)):
            edges[(name, (node_a, phase_a), (node_b, phase_b))] += 1

    model_ends: Counter = Counter()
    for subject in set(merged.subjects(emt["ModelTerminal.ConnectivityNode"],
                                       None)):
        node = merged.value(subject, emt["ModelTerminal.ConnectivityNode"])
        phase = merged.value(subject, emt["ModelTerminal.phase"])
        model_ends[(_mrid(node),
                    None if phase is None else int(str(phase)))] += 1

    return PerPhaseNetwork(nodes=nodes, edges=edges, model_ends=model_ends)

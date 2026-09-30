"""LIR: the flattened electrical graph on per-phase nodes, in SI units.

Nodes are ``(flat_key, phase)`` with ``phase`` 1-based (matching Branch
``$N(k)`` indexing): a FlatNode of dim d contributes exactly d nodes
(a dim-3 port is three electrical nodes drawn as one symbol). Edges
come from ``branch_endpoint_records()``; rlc edges carry SI quantities
converted from the script's declared units (R [ohm], L [H], C [uF]).
networkx is the carrier only -- geometric unification stays with the
validated DisjointSet path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from pscx.diagnostics import DIAGNOSTICS
from pscx.emission import paired_phases
from pscx.geometry import DisjointSet
from pscx.lower import port_dim
from pscx.units import si_factor

if TYPE_CHECKING:
    from pscx.elaborate import FlatProject

#: Declared units of the positional rlc Branch values (``$A $B $R L C``,
#: master's capacitor C is declared [uF]). Public because it
#: is the ONE statement of what those three numbers are in: the add-on
#: profile writes each mapped passive's values with these units beside
#: them, and a second copy of the triple is how a value and its stated
#: unit would eventually disagree.
RLC_DECLARED_UNITS = ("ohm", "H", "uF")


def _phase_pairs(end_a, end_b, dims):
    """Per-phase node pairs for one branch line.

    A ``(key, k)`` endpoint is the single phase k; ``(key, None)`` spans
    the node's full width. Equal widths pair phase-to-phase; a width-1
    end broadcasts (e.g. a literal-0 ground end under a 3-phase port).
    """

    def expand(end):
        key, idx = end
        if idx is not None:
            return [(key, idx)]
        return [(key, phase) for phase in range(1, dims[key] + 1)]

    a_nodes, b_nodes = expand(end_a), expand(end_b)
    # One statement of the pairing rule, shared with the emission
    # projection that collapses it and the reconstruction that inverts it.
    pairs = paired_phases(len(a_nodes), len(b_nodes))
    if not pairs:
        DIAGNOSTICS.emit("lir_phase_mismatch", f"{len(a_nodes)}x{len(b_nodes)}")
        return []
    return [(a_nodes[i - 1], b_nodes[j - 1]) for i, j in pairs]


def galvanic_islands(graph: nx.MultiGraph) -> DisjointSet:
    """Union of every LIR node reachable without leaving a voltage level.

    A transformer winding, a source's internals and ground each END an
    island: they are where one nominal voltage stops and another begins.
    Ground has to break propagation for a reason measured rather than
    assumed -- including it leaks a voltage across islands through the
    shared flat ground node.

    A CONVERTER ends one too. An `ACDCConverter` stands between an AC nominal voltage and
    a DC one, so its own Branch rows -- which join the AC port to each
    pole through a valve -- pool the two sides into one island and let an
    AC voltage propagate onto a DC node. Without the cut a DC node
    carries an AC nominal voltage, and one island in
    `Cigre_BM` carries TWO of them (a DC island, seen from the emitted
    documents rather than from the reachability sweep). The converter
    does not have to be EMITTED for this to be true of it, which is why
    the classification consults `CONVERTER_FORMS` beside the emission
    map: what a component is and whether this repo writes it down are
    different questions, and only the first one decides an island.

    The equipment classification is deliberately the CIM one rather than
    a list of master kinds: whether a component separates two voltage
    levels is the same question as which CIM class it maps to, and two
    lists that must agree would eventually not.
    """
    from pscx.dc import CONVERTER_FORMS
    from pscx.rules import MASTER_KIND_TO_CIM

    dsu = DisjointSet()
    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        kind = (comp.master_kind or comp.kind) if comp else None
        if MASTER_KIND_TO_CIM.get(kind) in ("PowerTransformer",
                                            "ExternalNetworkInjection"):
            continue
        if kind in CONVERTER_FORMS:
            continue
        if graph.nodes[u]["is_ground"] or graph.nodes[v]["is_ground"]:
            continue
        dsu.union(u, v)
    return dsu


def switch_pairs(graph: nx.MultiGraph) -> list[tuple]:
    """Per-phase node pairs joined by a switch, in emission order.

    A SWITCH DOES NOT CHANGE NOMINAL VOLTAGE, and that is not an
    inference about the network -- it is a requirement of the profile we
    emit into. 452's ``Switch:connection`` says a switch's two
    ConnectivityNodes shall sit in VoltageLevels of the same nominal
    voltage, at Violation severity, whatever the switch's state. So these
    pairs are equalities the OUTPUT has to satisfy, not evidence to be
    weighed against other evidence.

    They are strictly finer than :func:`galvanic_islands`, which is why
    they say something it does not: an island spanning two declared
    voltages resolves nothing for the nodes inside it, while a switch
    inside that island still equates its own two ends.

    The classification is the CIM one, for the same reason it is there:
    which components are switches is the same question as which map to a
    switch class, and two lists that must agree would eventually not.
    """
    from pscx.rules import CONDUCTION_KINDS, MASTER_KIND_TO_CIM

    pairs = []
    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        if comp is None:
            continue
        kind = comp.master_kind or comp.kind
        is_breaker = (data["kind"] == "breaker"
                      and MASTER_KIND_TO_CIM.get(kind) == "Breaker")
        is_conduction = (data["kind"] == "ammeter"
                         and kind in CONDUCTION_KINDS)
        if (is_breaker or is_conduction) and u[0] != v[0]:
            pairs.append((u, v))
    return pairs


def build_lir(flat: FlatProject) -> nx.MultiGraph:
    """The per-phase electrical multigraph of one flattened project.

    Its three findings -- ``lir_phase_mismatch``,
    ``lir_internal_collision``, ``lir_dangling_endpoint`` -- go to the
    process-global bus and stay there, deliberately. They are raised while
    the graph is being BUILT, which is before any CimModel exists and
    upstream of the question a CimModel answers: the same LIR feeds the
    equipment document, the add-on profile and the audit, so attributing
    one of them to a document would name a consumer rather than the thing
    that produced it. Nothing asserts them off a model sink, which is the
    property that would make such an assertion vacuous.
    """
    graph = nx.MultiGraph(case=flat.case, project=flat.project)
    dims: dict = {}
    for node in flat.nodes:
        dims[node.key] = node.dim
        for phase in range(1, node.dim + 1):
            graph.add_node(
                (node.key, phase),
                flat_node=node,
                internal=False,
                is_ground=node.is_ground,
                bus_names=node.bus_names,
                base_kv=node.base_kv,
            )

    records, _stats = flat.branch_endpoint_records()

    # Device-internal electrical nodes (internal ports never connect
    # externally). Branch endpoints on internal ports are real
    # nodes of the device's internal network, and a key that coincided
    # with a flat node would silently merge internal with external, hence
    # the loud counter. Width:
    # resolved port dim, stretched by any indexed endpoint actually used.
    internal_width: dict = {}
    for record in records:
        comp, inst = record["component"], record["instance"]
        ports = {(p.name or "").lower(): p for p in comp.ports}
        for end, token in (
            (record["end_a"], record["branch"].node_a),
            (record["end_b"], record["branch"].node_b),
        ):
            port = ports.get((token[0] or "").lower()) if token[0] else None
            if port is None or not port.internal:
                continue
            key, idx = end
            if key in dims:
                DIAGNOSTICS.emit("lir_internal_collision")
                continue
            width = max(port_dim(comp, port, inst) or 1, idx or 1)
            internal_width[key] = max(width, internal_width.get(key, 1))
    for key, width in internal_width.items():
        dims[key] = width
        for phase in range(1, width + 1):
            graph.add_node(
                (key, phase),
                flat_node=None,
                internal=True,
                is_ground=False,
                bus_names=(),
                base_kv=None,
            )

    for record in records:
        branch = record["branch"]
        ends = (record["end_a"], record["end_b"])
        if any(end[0] not in dims for end in ends):
            # add_edge would silently CREATE the missing node; refuse loud
            DIAGNOSTICS.emit("lir_dangling_endpoint")
            continue
        attrs = {
            "kind": branch.decl.kind,
            "instance": record["instance"],
            "component": record["component"],
            "branch": branch,
        }
        if branch.decl.kind == "rlc" and not branch.unresolved:
            for name, declared, value in zip(
                ("r_ohm", "l_h", "c_f"), RLC_DECLARED_UNITS, branch.values
            ):
                factor, _si = si_factor(declared)
                attrs[name] = value * factor
        for node_a, node_b in _phase_pairs(*ends, dims):
            graph.add_edge(node_a, node_b, **attrs)
    return graph

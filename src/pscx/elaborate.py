"""Cross-page flattening: instance tree, flat nodes/nets, radiolinks."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pscx.common import GROUND_KEY, SIGNAL_MODES
from pscx.diagnostics import DIAGNOSTICS
from pscx.preproc import _guard_holds
from pscx.geometry import DisjointSet
from pscx.hir import HirProject
from pscx.model import Component, Netlist
from pscx.io import _is_disabled, _layer_states, project_namespace
from pscx.nets import (
    _bridge_name,
    _canon_name,
    library_user_canvas,
    netlists_of,
    prepare,
)
from pscx.lower import evaluate_branches, port_dim, resolve_numeric


# --------------------------------------------------------------------------
# Cross-page connectivity (instance tree + boundary linking + radiolink)
# --------------------------------------------------------------------------
#
# How pages connect:
#
# - Every project has ONE StationCanvas whose WireBranch hosts a <User> for
#   the root module (usually ``Main``, with or without a paramlist).
#   The WireBranch's recv/send/back/I/J/K attributes carry no
#   connectivity.
# - A module instance is a <User> whose resolved definition has a UserCanvas.
#   The definition's namespace is the page key, so a sibling library page
#   the case places is an instance. A workspace filepath is opened the
#   same way. The page is built from that project at the moment of the
#   placement; the project's other canvases are not. RowCanvas
#   definitions (TLine/Cable right-of-way data, referenced by hosting wires)
#   are tower geometry, not circuit topology.
# - SIGNAL boundary: the instance's placed mode-1/2 ports (placed from the
#   child definition's graphics Ports with conds evaluated on the INSTANCE's
#   params) join the parent wire net at the port coordinate; the child-side
#   net is the one carrying the same name (the ``("SIG", name)`` sentinel).
# - ELECTRICAL boundary: ``xnode`` (master, single mode-3 port ``N`` +
#   ``Name`` param) is the electrical name bridge. xnode Names and the
#   definition's mode-3 boundary Port names match 1:1 (no duplicates, no
#   strays).
# - ``radiolink``: within-project links have ``rccon=0``; RX (Mode==0)
#   pairs with TX (Mode==1) by case-insensitive Name, and RX's ``Source``
#   names the TX's canvas (case-insensitive). ``rccon=1`` links are
#   cross-PROJECT
#   co-simulation endpoints (Source is ``project:canvas``-qualified) and
#   cannot pair inside one case.


@dataclass
class Instance:
    """One node of the instance tree: a module canvas in a specific context."""

    index: int
    path: str
    canvas: str
    netlist: Netlist
    #: The instancing component on the PARENT canvas (None for the root).
    component: Component | None
    parent: "Instance | None"
    #: Parameter environment: child defaults overlaid with the instance's
    #: paramlist (``$()`` globals already substituted).
    env: dict[str, str] = field(default_factory=dict)
    children: list = field(default_factory=list)

    @property
    def module_component_ids(self) -> set:
        """``id()`` of the Component objects on THIS canvas that are module
        instances: their ports are joins into child canvases, not real pins.

        OBJECT ADDRESSES, not element ids: a hosted device is a ``<User>``
        on a wire and is identified by the WIRE's id rather than by its own,
        so ``Component.element_id`` is ``str | None`` and there is
        no stable key available everywhere. Putting ``comp.element_id`` on
        either side of a membership test here returns False without
        raising, and the placement is then treated as equipment.

        Derived from the children on every read rather than stored, so an
        address never outlives the objects it names: a copied or unpickled
        project answers with its own components' addresses.
        ``tests/id_inventory.py`` classifies every such address in the
        package.
        """
        return {id(child.component) for child in self.children}


@dataclass
class FlatNode:
    """A flattened electrical node: per-canvas nodes joined across pages."""

    key: Any
    #: ``(instance, component, port)`` members; module-instance ports and
    #: xnode pins are joins and excluded.
    ports: list
    dim: int
    is_ground: bool
    #: Bus names / BaseKVs merged from all member canvases.
    bus_names: tuple = ()
    bus_kvs: tuple = ()

    @property
    def base_kv(self) -> float | None:
        """The node's single base voltage; None if unset or conflicting."""
        kvs = set(self.bus_kvs)
        return kvs.pop() if len(kvs) == 1 else None


@dataclass
class FlatSignalNet:
    """A flattened signal net. Same tagged-tuple style as SignalNet, with an
    Instance prepended: ``("port", inst, comp, port)``,
    ``("writer", inst, comp, param, name)``, ``("form_param", inst, name)``,
    ``("boundary_in"|"boundary_out", inst, name)`` (root instance only)."""

    key: Any
    ports: list
    names: set
    drivers: list
    readers: list
    #: Widths parallel to ``drivers``/``readers`` (int, or None = inherited/
    #: unknown). A datatap's A-pin is a SLICE reader: its width entry is
    #: None and the slice lives in ``taps`` instead.
    driver_widths: list = field(default_factory=list)
    reader_widths: list = field(default_factory=list)
    #: ``(inst, comp, offset, width)`` for each datatap reading elements
    #: ``offset .. offset+width-1`` (1-based) of THIS net.
    taps: list = field(default_factory=list)
    #: The single width every width-known endpoint agrees on (None if no
    #: endpoint knows); ``dim_conflict`` when they disagree.
    dim: int | None = None
    dim_conflict: bool = False


@dataclass
class RadioLinkEnd:
    instance: Instance
    component: Component
    name: str
    source: str
    mode: str  # "1" = TX (reads In), else RX (drives Out)
    #: Cross-project (rccon=1) multi-task slot: "0" = plain link;
    #: k >= 1 on a co-simulation MASTER's end addresses the k-th launched
    #: task of the slave project (MatchSatMaster carries V[1..6] TX and
    #: Iexc_simulated[1..6] RX; the slave's ends are rank 0). Within one
    #: project (canvas, name, mode, rank, source) is unique.
    rank: str = "0"


@dataclass
class FlatProject:
    case: str
    netlists: dict[str, Netlist]
    roots: list[Instance]
    instances: list[Instance]
    nodes: list[FlatNode]
    signal_nets: list[FlatSignalNet]
    #: (tx_end, rx_end) pairs of within-project (rccon=0) radiolinks.
    radio_pairs: list
    #: Diagnostic counters.
    stats: Counter
    #: UserCanvas definitions with content that no instance tree reaches
    #: (dead pages).
    orphan_canvases: list
    #: The project's ``name`` attribute -- the namespace cross-project
    #: radiolink Sources refer to.
    project: str = ""
    #: Cross-project (rccon=1) RadioLinkEnds. They cannot pair inside one
    #: case.
    cross_radio_ends: list = field(default_factory=list)
    #: Flat union-finds over ``(instance_index, local_root)`` keys, for
    #: cross-page short/split checks.
    dsu: DisjointSet | None = None
    signal_dsu: DisjointSet | None = None
    #: Every canvas built for this flatten, keyed by ``(namespace, name)``.
    #: The case's canvases are all of them. A library canvas is here when
    #: a placement resolved to its UserCanvas.
    pages: dict[tuple[str, str], Netlist] = field(default_factory=dict)

    def page(self, defn: str) -> "Netlist | None":
        """The loaded canvas ``defn`` names.

        ``namespace:name``, or a bare name in this project's namespace.
        """
        namespace, sep, name = (defn or "").partition(":")
        if not sep:
            namespace, name = self.project, namespace
        return self.pages.get((namespace, name))

    def right_of_way(self, defn: str) -> "Netlist | None":
        """The RowCanvas a hosted device's ``defn`` names, or None.

        A right-of-way is tower geometry drawn in the case that draws
        the line, so the NAMESPACE a hosting wire qualifies it with is
        advisory and the bare name is what resolves. Saving a project
        under a new name leaves the old qualifier on every wire:
        ieee_39_bus_v5 states ``ieee_39_bus:TLine_1`` through
        ``TLine_34``, and PLL_Example_3 states ``PLL3:TLine``, while
        each declares the bare name itself. The qualified page is tried
        first so a library that ever does supply one still wins.

        A name the case does not declare is a dangling reference and
        stays unresolved, which is the LightningCoord shape and what
        ``lineconst_rowdefn_missing`` counts.

        The RowCanvas test is here rather than at the call sites because
        a hosted device's ``defn`` naming a UserCanvas is not a
        right-of-way at all, and a caller that read tower rows off one
        would be reading a schematic as geometry.
        """
        found = self.page(defn)
        if found is None:
            name = (defn or "").partition(":")[2]
            found = self.pages.get((self.project, name)) if name else None
        if found is not None and found.classid != "RowCanvas":
            return None
        return found

    def flat_node_key(self, inst: "Instance", point_or_key: Any) -> Any:
        return self.dsu.find((inst.index, inst.netlist.dsu.find(point_or_key)))

    def rlc_branches(self) -> list[dict]:
        """One record per two-terminal R/L/C instance in the flattened
        project: flat node keys plus numeric (R, L, C). Every master
        resistor/inductor/capacitor carries exactly one unguarded rlc
        Branch line (``$A $B $R 0 0`` etc.), so a component that
        yields anything but exactly one record is loud."""
        out = []
        for inst in self.instances:
            for comp in inst.netlist.components:
                if comp.kind not in ("resistor", "inductor", "capacitor"):
                    continue
                if not comp.defn.startswith("master:"):
                    DIAGNOSTICS.emit("rlc_nonmaster", f"{comp.defn}")
                    continue
                branches = evaluate_branches(inst, comp)
                if len(branches) != 1:
                    DIAGNOSTICS.emit("rlc_branch_count",
                                     f"{comp.kind}={len(branches)}")
                    continue
                branch = branches[0]
                if branch.unresolved:
                    DIAGNOSTICS.emit("rlc_value_unresolved", f"{comp.kind}")
                    continue
                ports = {(p.name or "").lower(): p for p in comp.ports}
                pa = ports.get((branch.node_a[0] or "").lower())
                pb = ports.get((branch.node_b[0] or "").lower())
                if pa is None or pb is None:
                    DIAGNOSTICS.emit("rlc_port_missing", f"{comp.kind}")
                    continue
                r, l, c = branch.values
                out.append(dict(
                    instance=inst, component=comp,
                    node_a=self.flat_node_key(inst, pa.point),
                    node_b=self.flat_node_key(inst, pb.point),
                    R=r, L=l, C=c,
                ))
        return out

    def branch_endpoint_records(self) -> tuple[list[dict], Counter]:
        """Every active Branch line in the flat project with PER-PHASE
        endpoints -- the last pre-CIM piece.

        Each record's ``end_a``/``end_b`` is ``(flat_node_key, phase)``:
        ``phase`` is the evaluated 1-based array index for ``$N(3)``-style
        endpoints, or None for whole-port (scalar or full-width) endpoints;
        a literal-0 node maps to the instance's ground key. The companion
        stats make the containment oracle: every index lies in
        ``[1, port_dim]``, and ``branch_end_index_oob`` counts the ones
        that do not.
        """
        records: list[dict] = []
        st: Counter = Counter()
        for inst in self.instances:
            for comp in inst.netlist.components:
                if comp.definition is None or not comp.definition.branch_decls:
                    continue
                ports_ci = {(p.name or "").lower(): p for p in comp.ports}
                for br in evaluate_branches(inst, comp):
                    if br.decl.kind == "winding":
                        continue
                    ends = []
                    for pname, idx in (br.node_a, br.node_b):
                        if pname is None:  # literal 0 = ground
                            st["branch_end_ground"] += 1
                            ends.append(
                                (self.flat_node_key(inst, GROUND_KEY), None))
                            continue
                        port = ports_ci.get(pname.lower())
                        if port is None:
                            st["branch_end_port_unplaced"] += 1
                            ends.append(None)
                            continue
                        if idx is not None:
                            st["branch_end_indexed"] += 1
                            dim = port_dim(comp, port, inst)
                            if dim is None:
                                dim = comp.node_dims.get(pname.lower())
                            if dim is None:
                                st["branch_end_dim_unknown"] += 1
                            elif not 1 <= idx <= dim:
                                st["branch_end_index_oob"] += 1
                        ends.append(
                            (self.flat_node_key(inst, port.point), idx))
                    if None in ends:
                        continue
                    st["branch_end_records"] += 1
                    records.append(dict(
                        instance=inst, component=comp, branch=br,
                        end_a=ends[0], end_b=ends[1],
                    ))
        return records, st


def _station_roots(project: HirProject, namespace: str) -> list[str]:
    """Module names hosted by the project's StationCanvas WireBranches."""
    roots: list[str] = []
    layer_states = _layer_states(project)
    for definition in project.definitions:
        canvas = definition.canvas
        if canvas is None or canvas.classid != "StationCanvas":
            continue
        for wire in canvas.wires:
            for user in wire.hosted:
                if _is_disabled(user, layer_states):
                    continue
                nsp, colon, name = (user.defn or "").partition(":")
                if not colon:  # unqualified defn names a local module
                    nsp, name = namespace, nsp
                if nsp == namespace:
                    roots.append(name)
                else:
                    DIAGNOSTICS.emit("station_foreign_root", f"{user.defn}")
    return roots


def pair_radio_ends(rx_ends, tx_ends, stats: Counter) -> list:
    """Pair project-local (rccon=0) radiolink ends, RX by RX.

    The pairing rule is two statements, not one: a name, and the CANVAS the
    RX's ``Source`` names. The name alone finds *a* transmitter; where two
    transmitters share a name the source is the only thing that says which
    -- so it selects here rather than being checked afterwards on whatever
    the name lookup happened to return first.

    ``radio_source_mismatch`` therefore means "the RX names a canvas that
    hosts no transmitter of this name", which is a real failure to pair
    rather than a note appended to a pairing already made.
    """
    by_name: dict[str, list] = defaultdict(list)
    for tx in tx_ends:
        by_name[tx.name.lower()].append(tx)
    matched_tx: set[int] = set()
    pairs = []
    for rx in rx_ends:
        cands = by_name.get(rx.name.lower(), [])
        if not cands:
            stats["radio_rx_no_tx"] += 1
            continue
        if rx.source:
            named = [tx for tx in cands
                     if tx.instance.canvas.lower() == rx.source.lower()]
            if not named:
                stats["radio_source_mismatch"] += 1
                continue
            if len(named) > 1:
                stats["radio_rx_multi_tx"] += 1
            tx = named[0]
        else:
            # an RX that names no source can only be paired by name, and
            # an ambiguity there is unresolvable rather than resolved
            if len(cands) > 1:
                stats["radio_rx_multi_tx"] += 1
            tx = cands[0]
        matched_tx.add(id(tx))
        pairs.append((tx, rx))
    stats["radio_tx_no_rx"] += sum(
        1 for tx in tx_ends if id(tx) not in matched_tx)
    stats["radio_pairs"] = len(pairs)
    return pairs


def boundary_xnode(xnodes: dict, port_name: str):
    """The xnode a placed boundary port joins to: the SAME-NAMED one.

    The boundary-port rule, as one lookup, so a test can redirect it to a
    different but real xnode and watch the identity oracle fail. A
    linking oracle that counts that a port found *an* xnode is satisfied
    exactly by a wrong-but-real one.
    """
    return xnodes.get(port_name)


def flatten(case_path: str,
            project: HirProject | None = None,
            workspaces: Sequence[str] = ()) -> FlatProject:
    """Whole-project view: instance tree + cross-page net linking.

    A caller that already read the case hands the project in and no
    second read happens; ``emit_files`` takes the same parameter, so one
    read serves the whole conversion. The result never retains what it
    was handed: the ``FlatProject`` reaches no HIR object either way.
    """
    stats: Counter = Counter()

    # One read of the case, handed in by the caller when they have it.
    # Sibling libraries come back with the registry so a page can be built
    # from the project before it is dropped. Nothing in the result holds
    # either project.
    prepared = prepare(case_path, project, workspaces)
    if prepared is None:
        return FlatProject(case_path, {}, [], [], [], [], [], stats, [])
    project, registry, libraries = prepared
    namespace = project_namespace(project)
    case_netlists = netlists_of(project, registry, case_path)
    netlists = {nl.canvas: nl for nl in case_netlists}
    pages: dict[tuple[str, str], Netlist] = {
        (namespace, nl.canvas): nl for nl in case_netlists
    }
    by_library = {
        project_namespace(lib): (path, lib) for path, lib in libraries
    }

    def library_page(ns: str, name: str) -> Netlist | None:
        key = (ns, name)
        found = pages.get(key)
        if found is not None:
            return found
        located = by_library.get(ns)
        if located is None:
            return None
        path, lib = located
        built = library_user_canvas(lib, registry, path, name)
        if built is None:
            return None
        pages[key] = built
        return built

    instances: list[Instance] = []

    def build(ns: str, canvas: str, component: Component | None,
              parent: Instance | None, path: str,
              ancestry: frozenset) -> Instance:
        netlist = pages[(ns, canvas)]
        env = dict(component.params) if component is not None else dict(
            netlist.own_def.defaults if netlist.own_def else {}
        )
        inst = Instance(
            index=len(instances), path=path, canvas=canvas, netlist=netlist,
            component=component, parent=parent, env=env,
        )
        instances.append(inst)
        for comp in netlist.components:
            defn = comp.definition
            if defn is None:
                continue
            key = (defn.namespace, defn.name)
            page = pages.get(key)
            if page is None:
                page = library_page(defn.namespace, defn.name)
            if page is None or page.classid != "UserCanvas":
                continue
            if key in ancestry:
                DIAGNOSTICS.emit("recursive_module", f"{defn.name}")
                continue
            label = (comp.params.get("Name") or "").strip() or (
                f"{defn.name}#{comp.element_id}"
            )
            child = build(defn.namespace, defn.name, comp, inst,
                          f"{path}/{label}", ancestry | {key})
            inst.children.append(child)
        return inst

    roots = []
    for root_name in _station_roots(project, namespace):
        if (namespace, root_name) in pages:
            roots.append(build(namespace, root_name, None, None, root_name,
                               frozenset({(namespace, root_name)})))
        else:
            DIAGNOSTICS.emit("station_root_missing", f"{root_name}")
    stats["instances"] = len(instances)

    reached = {(inst.netlist.namespace, inst.canvas) for inst in instances}
    orphan_canvases = [
        name for (ns, name), nl in pages.items()
        if ns == namespace and nl.classid == "UserCanvas"
        and (ns, name) not in reached
    ]
    stats["orphan_canvases"] = len(orphan_canvases)

    # ---------------- electrical linking (xnode) ---------------------------
    fdsu = DisjointSet()
    FLAT_GROUND = ("FLATGND",)
    for inst in instances:
        if inst.netlist.dsu is not None:
            fdsu.union((inst.index, inst.netlist.dsu.find(GROUND_KEY)),
                       FLAT_GROUND)

    def xnodes_of(netlist: Netlist) -> dict[str, Component]:
        table: dict[str, Component] = {}
        for comp in netlist.components:
            if comp.master_kind == "xnode":
                name = (_bridge_name(comp) or "").strip().lower()
                if name in table:
                    stats["xnode_dup_name"] += 1
                table[name] = comp
        return table

    for inst in instances:
        if inst.component is None:
            continue
        parent, comp = inst.parent, inst.component
        xnodes = xnodes_of(inst.netlist)
        linked_names: set[str] = set()
        seen_port_names: set[str] = set()
        for port in comp.ports:
            if not port.is_electrical or port.is_ground:
                continue
            pname = (port.name or "").strip().lower()
            if pname in seen_port_names:
                stats["elec_boundary_dup_port"] += 1
            seen_port_names.add(pname)
            stats["elec_boundary_ports"] += 1
            xn = boundary_xnode(xnodes, pname)
            if xn is None:
                stats["elec_boundary_no_xnode"] += 1
                continue
            linked_names.add(pname)
            stats["elec_boundary_linked"] += 1
            fdsu.union(
                (parent.index, parent.netlist.dsu.find(port.point)),
                (inst.index, inst.netlist.dsu.find(xn.ports[0].point)),
            )
        # symmetric direction: an xnode this instance never links is a
        # boundary port whose cond is false for these params (or unplaced).
        stats["xnode_unlinked_instance"] += len(set(xnodes) - linked_names)

    # flat electrical nodes
    grouped: dict[Any, list] = defaultdict(list)
    flat_bus_names: dict[Any, list] = defaultdict(list)
    flat_bus_kvs: dict[Any, list] = defaultdict(list)
    ground_root = fdsu.find(FLAT_GROUND)
    for inst in instances:
        modules = inst.module_component_ids
        if inst.netlist.dsu is None:
            continue
        for node in inst.netlist.nodes:
            fkey = fdsu.find((inst.index, inst.netlist.dsu.find(node.key)))
            for name in node.bus_names:
                if name not in flat_bus_names[fkey]:
                    flat_bus_names[fkey].append(name)
            for kv in node.bus_kvs:
                if kv not in flat_bus_kvs[fkey]:
                    flat_bus_kvs[fkey].append(kv)
            for comp, port in node.ports:
                if id(comp) in modules or comp.master_kind == "xnode":
                    continue  # joins, not pins
                grouped[fkey].append((inst, comp, port))
    nodes = []
    for fkey, members in grouped.items():
        # same dim-inheritance rule as the per-canvas builder,
        # with suffix dims resolved in the INSTANCE env when available.
        dims = set()
        for _i, _c, p in members:
            if str(p.dim).isdigit():
                dims.add(int(p.dim))
            if getattr(p, "dim_name", None):
                resolved = port_dim(_c, p, _i)
                if resolved is not None:
                    dims.add(resolved)
        dims.discard(0)
        if len(dims) > 1:
            stats["flat_dim_conflicts"] += 1
        nodes.append(FlatNode(
            key=fkey, ports=members, dim=max(dims) if dims else 1,
            is_ground=(fkey == ground_root),
            bus_names=tuple(flat_bus_names.get(fkey, ())),
            bus_kvs=tuple(flat_bus_kvs.get(fkey, ())),
        ))

    # ---------------- signal linking (boundary names) ----------------------
    fsd = DisjointSet()
    for inst in instances:
        if inst.component is None:
            continue
        parent, comp = inst.parent, inst.component
        seen_names: set[str] = set()
        for port in comp.signal_ports:
            canon = _canon_name(port.name)
            if canon in seen_names:
                # two placed boundary ports with one name would make the
                # link ambiguous; conds are meant to be disjoint.
                stats["sig_boundary_dup_port"] += 1
            seen_names.add(canon)
            stats["sig_boundary_ports"] += 1
            child_key = (inst.index,
                         inst.netlist.signal_dsu.find(("SIG", canon)))
            # positive evidence that the child side actually attaches:
            child_attached = any(
                inst.netlist.signal_dsu.find(net.key) == child_key[1]
                and (net.ports or net.bridges or net.drivers or net.readers)
                for net in inst.netlist.signal_nets
            )
            if child_attached:
                stats["sig_boundary_linked"] += 1
            else:
                stats["sig_boundary_child_dangling"] += 1
            fsd.union(child_key,
                      (parent.index, parent.netlist.signal_dsu.find(port.point)))

    sig_grouped: dict[Any, FlatSignalNet] = {}

    def flat_net(inst: Instance, local_key: Any) -> FlatSignalNet:
        fkey = fsd.find((inst.index, inst.netlist.signal_dsu.find(local_key)))
        if fkey not in sig_grouped:
            sig_grouped[fkey] = FlatSignalNet(
                key=fkey, ports=[], names=set(), drivers=[], readers=[])
        return sig_grouped[fkey]

    root_indices = {r.index for r in roots}
    for inst in instances:
        modules = inst.module_component_ids
        for net in inst.netlist.signal_nets:
            fnet = flat_net(inst, net.key)
            for name in net.names:
                fnet.names.add((inst.index, name))
            for entry in net.ports:
                comp, port = entry
                if id(comp) in modules:
                    continue  # join into a child canvas
                fnet.ports.append((inst, comp, port))
                if port.is_signal_source:
                    fnet.drivers.append(("port", inst, comp, port))
                else:
                    fnet.readers.append(("port", inst, comp, port))
            for entry in net.drivers:
                if entry[0] == "writer":
                    _tag, comp, param, name = entry
                    fnet.drivers.append(("writer", inst, comp, param, name))
                elif entry[0] == "boundary_in" and inst.index in root_indices:
                    # the root has no parent to replace its boundary sources
                    fnet.drivers.append(("boundary_in", inst, entry[1]))
            for entry in net.readers:
                if entry[0] == "boundary_out" and inst.index in root_indices:
                    fnet.readers.append(("boundary_out", inst, entry[1]))

    # form-parameter fallback, per INSTANCE: the value comes from this
    # instance's env, so two instances of one canvas get their own constants.
    for fnet in sig_grouped.values():
        if fnet.drivers:
            continue
        for inst_index, display in sorted(fnet.names):
            inst = instances[inst_index]
            own = inst.netlist.own_def
            if own is None:
                continue
            canon = _canon_name(display)
            boundary = {_canon_name(p.name) for p in own.ports
                        if p.mode in SIGNAL_MODES and not p.internal}
            params = {_canon_name(k): k for k in own.defaults if k}
            if canon in params and canon not in boundary:
                fnet.drivers.append(("form_param", inst, params[canon]))

    # ---------------- dimension annotation ---------------------------------
    # Element-level array flow: every endpoint contributes its width (from
    # dim attrs, :suffix names, or #OUTPUT dims); a datatap's A-pin instead
    # reads the 1-based slice (Index .. Index+Dim-1) of its net.
    computed_cache: dict = {}
    node_dim_cache: dict = {}

    def _electrical_dim(e_inst: Instance, e_comp: Component) -> int | None:
        """Dim of the electrical node a meter measures: an inherit-dim
        (#OUTPUT dim 0) writer like multimeter.CurI emits one value per
        phase of the wire it sits on."""
        if id(e_inst.netlist) not in node_dim_cache:
            table = {}
            for node in e_inst.netlist.nodes:
                table[e_inst.netlist.dsu.find(node.key)] = node.dim
            node_dim_cache[id(e_inst.netlist)] = table
        table = node_dim_cache[id(e_inst.netlist)]
        dims = {table.get(e_inst.netlist.dsu.find(p.point))
                for p in e_comp.electrical_ports}
        dims.discard(None)
        return max(dims) if dims else None

    def _entry_width(entry) -> int | None:
        if entry[0] == "port":
            _tag, e_inst, e_comp, e_port = entry
            return port_dim(e_comp, e_port, e_inst, computed_cache)
        if entry[0] == "writer":
            _tag, e_inst, e_comp, e_param, _name = entry
            if e_comp.definition is not None:
                # Multiple #OUTPUT arms may declare the same param with
                # different dims under $#DIM guards (arrester Curr 3/2/1);
                # pick the arm whose guard chain holds. Fall
                # back to the first arm when none does.
                first: tuple | None = None
                chosen: tuple | None = None
                for w_param, w_dim, w_guards in e_comp.definition.writer_directives:
                    if w_param != e_param:
                        continue
                    if first is None:
                        first = (w_dim,)
                    if all(_guard_holds(g, e_comp.params, want,
                                        e_comp.dim_of_port)
                           for g, want in w_guards):
                        chosen = (w_dim,)
                        break
                if chosen is None:
                    chosen = first
                if chosen is not None:
                    if chosen[0] is None:  # inherited from measured object
                        return _electrical_dim(e_inst, e_comp)
                    return chosen[0]
            return None
        if entry[0] == "form_param":
            return 1  # always scalar Real
        if entry[0] in ("boundary_in", "boundary_out"):
            _tag, e_inst, e_name = entry
            own = e_inst.netlist.own_def
            if own is not None:
                for pdef in own.ports:
                    if _canon_name(pdef.name) == _canon_name(e_name):
                        if str(pdef.dim).isdigit() and int(pdef.dim) > 0:
                            return int(pdef.dim)
                        return None
            return None
        return None

    for fnet in sig_grouped.values():
        widths: set[int] = set()
        for entry in fnet.drivers:
            w = _entry_width(entry)
            fnet.driver_widths.append(w)
            if w:
                widths.add(w)
        for entry in fnet.readers:
            if (entry[0] == "port" and entry[2].master_kind == "datatap"
                    and (entry[3].name or "").upper() == "A"):
                fnet.reader_widths.append(None)
                e_inst, e_comp = entry[1], entry[2]
                env_ci = {k.lower(): v for k, v in e_comp.params.items() if k}
                offset = resolve_numeric(e_inst, str(env_ci.get("index", "1")))
                width = resolve_numeric(e_inst, str(env_ci.get("dim", "1")))
                fnet.taps.append((
                    e_inst, e_comp,
                    int(offset) if offset is not None else None,
                    int(width) if width is not None else None,
                ))
                continue
            w = _entry_width(entry)
            fnet.reader_widths.append(w)
            if w:
                widths.add(w)
        if len(widths) == 1:
            fnet.dim = widths.pop()
        elif len(widths) > 1:
            fnet.dim_conflict = True
            fnet.dim = max(widths)
        stats["sig_dim_conflicts"] += int(fnet.dim_conflict)
        # Backfill SIGNAL-port dims: ``$#DIM(port)`` means "dimension of
        # whatever the port is connected to"; for signal ports
        # that is the resolved net width. Electrical ports were filled from
        # node dims at extract() time; together dim_of_port() becomes the
        # complete $#DIM resolver.
        if fnet.dim is not None:
            for _pinst, pcomp, pport in fnet.ports:
                pcomp.node_dims.setdefault(pport.name.lower(), fnet.dim)

    # ---------------- radiolink pairing ------------------------------------
    tx_ends: list[RadioLinkEnd] = []
    rx_ends: list[RadioLinkEnd] = []
    cross_radio_ends: list[RadioLinkEnd] = []
    for inst in instances:
        for comp in inst.netlist.components:
            if comp.master_kind != "radiolink":
                continue
            p = comp.params
            end = RadioLinkEnd(
                instance=inst, component=comp,
                name=(p.get("Name") or "").strip(),
                source=(p.get("Source") or "").strip(),
                mode=(p.get("Mode") or "0").strip(),
                rank=(p.get("rank") or "0").strip(),
            )
            if (p.get("rccon") or "0").strip() == "1":
                stats["radio_cross_project"] += 1
                cross_radio_ends.append(end)
                continue
            (tx_ends if end.mode == "1" else rx_ends).append(end)

    radio_pairs = pair_radio_ends(rx_ends, tx_ends, stats)

    return FlatProject(
        case=case_path, netlists=netlists, roots=roots, instances=instances,
        nodes=nodes, signal_nets=list(sig_grouped.values()),
        radio_pairs=radio_pairs, stats=stats, orphan_canvases=orphan_canvases,
        pages=pages,
        project=namespace, cross_radio_ends=cross_radio_ends,
        dsu=fdsu, signal_dsu=fsd,
    )

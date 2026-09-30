"""Per-canvas electrical and signal graph construction."""

from __future__ import annotations

import glob
import os
import re
from collections import defaultdict
from typing import Any, Iterator, Sequence

from lxml import etree as ET

from pscx.common import (
    GROUND_KEY,
    HOSTING_VERTEX_COUNT,
    HOSTING_WIRES,
    SIGNAL_MODES,
    as_number,
)
from pscx.diagnostics import DIAGNOSTICS, Span
from pscx.expr import condition_holds
from pscx.geometry import DisjointSet, Point, point_on_segment, rotate_port
from pscx.hir import HirComponent, HirProject, HirWire
from pscx.model import Component, ComponentDef, Device, Netlist, Node, Port, SignalNet
from pscx.io import (
    _is_disabled,
    _layer_states,
    _vertices,
    load_definitions,
    load_master,
    project_namespace,
    read_project,
    register_definitions,
    resolve,
)
from pscx.lower import port_dim


#: Global-substitution names PSCAD supplies itself at build time, so no
#: ``<Sub>`` list ever declares one. master.pslx is the witness and the
#: reason this is a fixed set rather than a measured one: it declares
#: zero ``<Sub>`` elements and still places components whose ``caption``
#: reads ``$(Name) [$(Rank)]``, which a shipped library could not do if
#: those names came from a user project's globals.
PSCAD_INTRINSIC_SUBSTITUTIONS = frozenset({"Name", "Rank"})

#: What a ``$(x)`` becomes when nothing resolves it. A string, because
#: substitution happens before any expression is parsed, and the numeric
#: fields it lands in are read by ``as_number`` afterwards.
SUBSTITUTION_FALLBACK = "0"


def _collect_wires(
    wires: Sequence[HirWire],
    layer_states: dict[str, str] | None = None,
    substitute=None,
) -> tuple[
    list[tuple[Point, Point]],
    list[tuple[tuple[Point, Point], Any]],
    list[Device],
    list[tuple[Point, Point]],
    list[tuple[str, float | None, Point]],
]:
    """Split a canvas's wires into conductive segments, device stubs, devices.

    Also returns the subset of segments eligible to carry *data signals*
    (``WireOrthogonal`` serves both graphs, ``Bus`` is electrical-only) and
    the canvas's Bus identities: ``(name, base_kv, anchor_point)`` per Bus
    wire -- the anchor is the wire's first vertex, which the node
    builder unions with the rest of the wire, so ``dsu.find(anchor)`` names
    the node the Bus belongs to. ``base_kv`` is None when unset (PSCAD
    writes ``0.0`` for unset -- normalised here, a real 0 kV bus base is
    meaningless).
    """
    segments: list[tuple[Point, Point]] = []
    signal_segments: list[tuple[Point, Point]] = []
    stubs: list[tuple[tuple[Point, Point], Any]] = []
    devices: list[Device] = []
    buses: list[tuple[str, float | None, Point]] = []

    for wire in wires:
        if _is_disabled(wire, layer_states):
            continue
        points = _vertices(wire)
        if not points:
            continue
        classid = wire.classid

        if classid in HOSTING_WIRES and len(points) == HOSTING_VERTEX_COUNT:
            # Synthetic terminal keys, NOT coordinates. A collapsed body
            # (v1 == v2, e.g. Relay_Cases/case3_Mutual_midpoint) would
            # otherwise let the two stubs merge through a shared vertex and
            # short the line.
            wid = wire.id
            key_a, key_b = ("terminal", wid, "A"), ("terminal", wid, "B")
            # A stub tuple leads with its OUTER end -- the path endpoint a
            # meeting wire must join. The 4-vertex path runs
            # outerA -> innerA -> innerB -> outerB, so B's outer end is
            # points[3], the same vertex Device.vertex_b records.
            stubs.append(((points[0], points[1]), key_a))
            stubs.append(((points[3], points[2]), key_b))

            hosted_params: dict[str, str] = {}
            if wire.hosted:
                for plist in wire.hosted[0].params:
                    for name, value in plist.values.items():
                        hosted_params[name] = (
                            substitute(value, plist.span_for(name))
                            if substitute else value)
            devices.append(
                Device(
                    kind=classid,
                    defn=wire.defn,
                    name=hosted_params.get("Name", ""),
                    dim=hosted_params.get("Dim", "1"),
                    terminal_a=key_a,
                    terminal_b=key_b,
                    vertex_a=points[0],
                    vertex_b=points[3],
                    params=hosted_params,
                )
            )
        else:
            pairs = list(zip(points, points[1:]))
            segments.extend(pairs)
            if classid == "WireOrthogonal":
                signal_segments.extend(pairs)
            elif classid == "Bus":
                params = wire.params[0].values if wire.params else {}
                kv = as_number(params.get("BaseKV"))
                buses.append((
                    (params.get("Name") or "").strip(),
                    kv if kv else None,  # 0.0 = unset
                    points[0],
                ))

    return segments, stubs, devices, signal_segments, buses


def _place_components(
    placements: Sequence[HirComponent],
    registry: dict[tuple[str, str], ComponentDef],
    substitute,
    layer_states: dict[str, str] | None = None,
) -> list[Component]:
    components: list[Component] = []
    for user in placements:
        if _is_disabled(user, layer_states):
            continue
        definition = resolve(registry, user.defn or "")
        if definition is None:
            DIAGNOSTICS.emit("unresolved_definition", f"{user.defn}",
                             span=user.span)
            continue
        try:
            ox, oy = int(user.x), int(user.y)
            # a stated ``orient`` is used as stated, even where it does not
            # parse; only an ABSENT one defaults to unrotated
            orient = int(user.orient if user.orient is not None else "0")
        except (TypeError, ValueError):
            DIAGNOSTICS.emit("component_bad_coords", span=user.span)
            continue

        env = dict(definition.defaults)
        for plist in user.params:
            for name, value in plist.values.items():
                env[name] = substitute(value, plist.span_for(name))
        # ``memberof`` is a SYSTEM-written paramlist entry, not a form
        # parameter: no master definition declares it, yet
        # instances of every kind (datalabel, pgb, ground, annotation...)
        # carry it, always empty. The only component-membership
        # concept in the PSCAD 5 docs is the canvas "Grouping Components"
        # feature (runtime-object grouping uses the declared ``Group`` param
        # instead), so a non-empty value most plausibly names a canvas
        # group. That reading is INFERRED, so one is warned about loudly.
        if (env.get("memberof") or "").strip():
            DIAGNOSTICS.emit("memberof_nonempty",
                             f"{user.defn}="
                             f"{str(env.get('memberof'))[:30]}",
                             span=user.span)

        ports = []
        for port_def in definition.ports:
            if not condition_holds(port_def.condition, env):
                continue
            dx, dy = rotate_port(port_def.x, port_def.y, orient)
            ports.append(
                Port(
                    name=port_def.name,
                    x=ox + dx,
                    y=oy + dy,
                    mode=port_def.mode,
                    electype=port_def.electype,
                    dim=port_def.dim,
                    internal=port_def.internal,
                    local=(port_def.x, port_def.y),
                    dim_name=port_def.dim_name,
                    datatype=port_def.datatype,
                )
            )
        # Port-LESS components are kept: a module instance whose definition
        # declares no graphics Ports is still live (Series_AF/Shunt_AF
        # instantiate their radio-connected CtrlSystem page that way), and a
        # portless component can still write signals via #OUTPUT params.
        components.append(
            Component(
                defn=user.defn or "",
                x=ox,
                y=oy,
                orient=orient,
                ports=ports,
                params=env,
                element_id=user.id,
                definition=definition,
            )
        )
    return components


def _build_nodes(
    components: Sequence[Component],
    segments: Sequence[tuple[Point, Point]],
    stubs: Sequence[tuple[tuple[Point, Point], Any]],
) -> tuple[list[Node], int, DisjointSet]:
    dsu = DisjointSet()

    # 1. each conductive wire is internally one node
    for start, end in segments:
        dsu.union(start, end)

    # 2. a hosting wire binds only its OUTER stub end to that terminal; inner
    #    ends stay free so a collapsed body cannot couple the two terminals
    for (outer, _inner), key in stubs:
        dsu.union(outer, key)

    # 3. wire-to-wire T-junctions: an endpoint landing mid-segment of another
    #    wire must merge with it -- shared vertices alone miss these.
    endpoints = {p for segment in segments for p in segment}
    endpoints.update(outer for (outer, _inner), _key in stubs)
    for point in endpoints:
        for start, end in segments:
            if point in (start, end):
                continue
            if point_on_segment(point, start, end):
                dsu.union(point, start)
                break

    # 4. attach component ports to whatever they touch
    for component in components:
        for port in component.electrical_ports:
            if port.is_ground:
                dsu.union(port.point, GROUND_KEY)
            attached = False
            for start, end in segments:
                if point_on_segment(port.point, start, end):
                    dsu.union(port.point, start)
                    attached = True
                    break
            if attached:
                continue
            # A port at the point where two hosting wires meet joins BOTH
            # terminals -- the point is one electrical junction, and taking
            # only the first stub in wire order would make the join depend
            # on element order, which a written case does not preserve. One
            # stub per wire still: both stubs of one collapsed-body wire
            # can contain the same point, and uniting them would short the
            # line the synthetic terminal keys exist to keep apart.
            joined_wires: set[str] = set()
            for (start, end), key in stubs:
                if key[1] in joined_wires:
                    continue
                if point_on_segment(port.point, start, end):
                    dsu.union(port.point, key)
                    joined_wires.add(key[1])

    # 4b. mode-4 ports are pass-through JUNCTIONS: ``pin`` placed where two
    #    wires CROSS (no shared endpoint) connects them. Ordinary ports
    #    keep the attach-first behaviour above.
    for component in components:
        for port in component.ports:
            if port.mode == "4" and not port.internal:
                for start, end in segments:
                    if point_on_segment(port.point, start, end):
                        dsu.union(port.point, start)

    # 5. group ports into nodes and assign a phase count
    grouped: dict[Any, list[tuple[Component, Port]]] = defaultdict(list)
    for component in components:
        for port in component.electrical_ports:
            grouped[dsu.find(port.point)].append((component, port))

    nodes: list[Node] = []
    conflicts = 0
    ground_root = dsu.find(GROUND_KEY)
    for key, members in grouped.items():
        # dim 0 = "assume the dimension of whatever it is connected to"
        # (PSCAD 5 manual, Port Connections): a dim-0 port
        # inherits the node's dim from the other members, so the node dim
        # is the max of the RESOLVED member dims. A ``B:Name`` suffix dim
        # counts once resolved. A node where EVERY member
        # inherits is a scalar node by construction -- nothing declares a
        # width, so the wire is dim 1.
        dims = set()
        for _c, p in members:
            if str(p.dim).isdigit():
                dims.add(int(p.dim))
            if getattr(p, "dim_name", None):
                resolved = port_dim(_c, p, None)
                if resolved is not None:
                    dims.add(resolved)
        dims.discard(0)
        if len(dims) > 1:
            conflicts += 1
            DIAGNOSTICS.emit("dim_conflict_on_node")
        nodes.append(
            Node(
                key=key,
                ports=members,
                dim=max(dims) if dims else 1,
                is_ground=(dsu.find(key) == ground_root),
            )
        )
    return nodes, conflicts, dsu


#: Signal names match case-INSENSITIVELY, like parameter names.
#: Folding case fixes genuine references (``Dblk`` vs ``DBlk``,
#: ``Vdcmin`` vs ``VdcMin``).
#: Consistent with EMTDC signal names becoming case-insensitive Fortran
#: identifiers.
SIGNAL_NAME_CASEFOLD = True


def _canon_name(name: str) -> str:
    name = (name or "").strip()
    return name.lower() if SIGNAL_NAME_CASEFOLD else name


def _bridge_name(component: Component) -> str:
    """The (already $()-substituted) ``Name`` parameter of a name bridge."""
    if "Name" in component.params:
        return (component.params["Name"] or "").strip()
    for key, value in component.params.items():
        if key and key.lower() == "name":
            return (value or "").strip()
    return ""


def _build_signal_nets(
    components: Sequence[Component],
    signal_segments: Sequence[tuple[Point, Point]],
    own_def: ComponentDef | None,
) -> tuple[list[SignalNet], DisjointSet]:
    """Union wire geometry with canvas-scoped names into signal nets.

    Same machinery as the electrical graph (shared-vertex union, T-junction
    merge, port attachment by coincidence) over ``WireOrthogonal`` segments
    only, plus a name layer: bridges union their pin onto a ``("SIG", name)``
    sentinel, #OUTPUT writers drive that sentinel, and the canvas's own
    definition contributes boundary ports and form parameters by name.
    """
    dsu = DisjointSet()

    for start, end in signal_segments:
        dsu.union(start, end)

    # wire-to-wire T-junctions, same rule as the electrical graph
    endpoints = {p for segment in signal_segments for p in segment}
    for point in endpoints:
        for start, end in signal_segments:
            if point in (start, end):
                continue
            if point_on_segment(point, start, end):
                dsu.union(point, start)
                break

    port_entries: list[tuple[Component, Port]] = []
    bridge_entries: list[tuple[Component, Port]] = []
    names_by_key: dict[Any, str] = {}

    for component in components:
        for port in component.signal_ports:
            for start, end in signal_segments:
                if point_on_segment(port.point, start, end):
                    dsu.union(port.point, start)
                    break
            if component.is_name_bridge:
                name = _bridge_name(component)
                if name:
                    key = ("SIG", _canon_name(name))
                    names_by_key.setdefault(key, name)
                    dsu.union(port.point, key)
                else:
                    DIAGNOSTICS.emit("bridge_empty_name")
                bridge_entries.append((component, port))
            else:
                port_entries.append((component, port))

    # mode-4 pass-through junctions (``pin``) join data wires too, by the
    # same rule the electrical pass applies: a pin lands on a segment it is
    # not an endpoint of and joins it, resolving e.g. the ONOFFDelays net
    # where compare.O drives two pudot.in pins through a wire crossing. They
    # are junctions, not signal pins: never members, drivers or readers.
    for component in components:
        for port in component.ports:
            if port.mode == "4" and not port.internal:
                for start, end in signal_segments:
                    if point_on_segment(port.point, start, end):
                        dsu.union(port.point, start)

    writer_entries: list[tuple[Any, tuple]] = []
    for component in components:
        for param, name in component.written_signals:
            key = ("SIG", _canon_name(name))
            names_by_key.setdefault(key, name)
            writer_entries.append((key, ("writer", component, param, name)))

    nets: dict[Any, SignalNet] = {}

    def net_for(key: Any) -> SignalNet:
        root = dsu.find(key)
        if root not in nets:
            nets[root] = SignalNet(
                key=root, ports=[], bridges=[], names=set(), drivers=[], readers=[]
            )
        return nets[root]

    for component, port in port_entries:
        net = net_for(port.point)
        net.ports.append((component, port))
        if port.is_signal_source:
            net.drivers.append(("port", component, port))
        else:
            net.readers.append(("port", component, port))
    for component, port in bridge_entries:
        net_for(port.point).bridges.append((component, port))
    for key, display in names_by_key.items():
        net_for(key).names.add(display)
    for key, entry in writer_entries:
        net_for(key).drivers.append(entry)

    # The canvas's own definition is part of the namespace. Boundary-port
    # conds are evaluated against the definition's DEFAULTS -- the canvas is
    # shared by every instance, so instance-specific conds are approximated.
    boundary_in: dict[str, str] = {}
    boundary_out: dict[str, str] = {}
    form_params: dict[str, str] = {}
    if own_def is not None:
        for pdef in own_def.ports:
            if pdef.internal or pdef.mode not in SIGNAL_MODES:
                continue
            if not condition_holds(pdef.condition, own_def.defaults):
                continue
            target = boundary_in if pdef.mode == "1" else boundary_out
            target[_canon_name(pdef.name)] = pdef.name or ""
        form_params = {_canon_name(k): k for k in own_def.defaults if k}

    for net in nets.values():
        for display in sorted(net.names):
            canon = _canon_name(display)
            if canon in boundary_in:
                net.drivers.append(("boundary_in", boundary_in[canon]))
            if canon in boundary_out:
                net.readers.append(("boundary_out", boundary_out[canon]))
        # A form parameter is a FALLBACK constant source: it drives a net
        # only when nothing else does. INFERRED from nets such as
        # PLL_Example_1 FreqMeas and Motor_Drive_SVM x_theta / x_I_dis, where
        # a label matching a form parameter is also wire- or writer-driven;
        # counting the parameter there would double-drive the net, while
        # e.g. every import on PIwithFreeze requires the parameter source.
        if not net.drivers:
            for display in sorted(net.names):
                canon = _canon_name(display)
                if canon in form_params and canon not in boundary_in:
                    net.drivers.append(("form_param", form_params[canon]))

    return list(nets.values()), dsu


def workspace_paths(path: str) -> list[str]:
    """Project files a ``.pswx`` lists, beside the workspace file.

    ``filepath`` is relative to the workspace and may use Windows
    separators. A workspace that will not parse is reported as a library
    that did not load: every project it names is then unresolvable, which
    is that diagnostic's consequence. An empty list is that report.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.XMLSyntaxError as exc:
        DIAGNOSTICS.emit("unparseable_library",
                         f"{os.path.basename(path)}",
                         span=Span(os.path.basename(path), exc.lineno))
        return []
    except Exception:
        DIAGNOSTICS.emit("unparseable_library",
                         f"{os.path.basename(path)}",
                         span=Span(os.path.basename(path)))
        return []
    directory = os.path.dirname(os.path.abspath(path))
    paths = []
    for project in root.iter("project"):
        filepath = project.get("filepath")
        if not filepath:
            continue
        relative = filepath.replace("\\", "/")
        paths.append(os.path.normpath(os.path.join(directory, relative)))
    return paths


def prepare(case_path: str,
            project: HirProject | None = None,
            workspaces: Sequence[str] = (),
            ) -> tuple[HirProject, dict, list[tuple[str, HirProject]]] | None:
    """The case, the definition registry, and each opened project.

    ``project`` is the case already read. A caller holding one hands it
    over rather than have the same bytes parsed a second time. None when
    the case did not parse: the diagnostic is already filed.

    Sibling ``.pslx`` files and each ``filepath`` of ``workspaces`` are
    opened the same way. The case file is not opened again when a
    workspace lists it. Each entry is ``(path, project)``. The project is
    returned so a page can be built from it, and the caller drops it
    afterwards. A netlist does not keep the project it was built from.
    """
    if project is None:
        try:
            project = read_project(case_path)
        except ET.XMLSyntaxError as exc:
            DIAGNOSTICS.emit("unparseable_case",
                             f"{os.path.basename(case_path)}",
                             span=Span(os.path.basename(case_path),
                                       exc.lineno))
            return None
        except Exception:
            DIAGNOSTICS.emit("unparseable_case",
                             f"{os.path.basename(case_path)}",
                             span=Span(os.path.basename(case_path)))
            return None

    registry = dict(load_master())
    register_definitions(project, registry)
    libraries: list[tuple[str, HirProject]] = []
    opened = {os.path.realpath(case_path)}
    directory = os.path.dirname(case_path)
    candidates = list(glob.glob(os.path.join(directory, "*.pslx")))
    for workspace in workspaces:
        candidates.extend(workspace_paths(workspace))
    for library in candidates:
        real = os.path.realpath(library)
        if real in opened:
            continue
        opened.add(real)
        loaded = load_definitions(library, registry)
        if loaded is not None:
            libraries.append((library, loaded))
    return project, registry, libraries


def netlists_of(project: HirProject,
                registry: dict,
                source_path: str,
                only: set[str] | None = None) -> list[Netlist]:
    """One :class:`Netlist` per schematic canvas in ``project``.

    ``only`` restricts the canvases by definition name. A library page is
    built when a placement resolves to it, and the library's other
    canvases are not: a category page the case never instances files
    nothing.
    """
    # Namespace under which register_definitions registered this project's
    # definitions; needed to look up each canvas's own ComponentDef.
    namespace = project_namespace(project)

    # ``HirProject.globals`` is the same table a walk of ``<Sub>`` anywhere
    # under the root builds.
    globals_map = project.globals
    layer_states = _layer_states(project)
    built: list[Netlist] = []

    def substitute(value: str | None, span: Span | None = None) -> str:
        def resolve_one(match: re.Match) -> str:
            name = match.group(1)
            if name in globals_map:
                return globals_map[name]
            DIAGNOSTICS.emit(
                "substitution_intrinsic" if name in PSCAD_INTRINSIC_SUBSTITUTIONS
                else "substitution_unresolved", name, span=span)
            return SUBSTITUTION_FALLBACK

        return re.sub(r"\$\((\w+)\)", resolve_one, value or "")

    for definition in project.definitions:
        if only is not None and definition.name not in only:
            continue
        canvas = definition.canvas
        if canvas is None:
            continue
        # A canvas counts as present if it holds any enabled component, even if
        # none of them declare ports (annotation-only pages yield empty netlists).
        if not any(not _is_disabled(c, layer_states)
                   for c in canvas.components):
            continue
        components = _place_components(canvas.components, registry, substitute,
                                       layer_states)
        segments, stubs, devices, signal_segments, buses = _collect_wires(
            canvas.wires, layer_states, substitute)
        nodes, conflicts, dsu = _build_nodes(components, segments, stubs)
        # Attach each placed electrical port's NODE dim so ``$#DIM`` guards
        # evaluate strictly; must precede signal-net building,
        # which consults written_signals.
        for node in nodes:
            for comp, port in node.ports:
                comp.node_dims[port.name.lower()] = node.dim
        # Attach Bus identities: a Bus wire's anchor point names
        # its node; a Bus whose node carries no component port has no Node
        # object and is skipped here.
        node_by_root = {dsu.find(n.key): n for n in nodes}
        for bus_name, bus_kv, anchor in buses:
            node = node_by_root.get(dsu.find(anchor))
            if node is None:
                continue
            if bus_name and bus_name not in node.bus_names:
                node.bus_names = node.bus_names + (bus_name,)
            if bus_kv is not None and bus_kv not in node.bus_kvs:
                node.bus_kvs = node.bus_kvs + (bus_kv,)
        own_def = registry.get((namespace, definition.name))
        signal_nets, signal_dsu = _build_signal_nets(
            components, signal_segments, own_def
        )
        built.append(Netlist(
            case=source_path,
            canvas=definition.name,
            components=components,
            nodes=nodes,
            devices=devices,
            segments=segments,
            stubs=stubs,
            dsu=dsu,
            dim_conflicts=conflicts,
            signal_nets=signal_nets,
            signal_segments=signal_segments,
            signal_dsu=signal_dsu,
            own_def=own_def,
            classid=canvas.classid,
            buses=buses,
            namespace=namespace,
        ))
    return built


def library_user_canvas(project: HirProject,
                        registry: dict,
                        source_path: str,
                        name: str) -> Netlist | None:
    """The named UserCanvas of ``project``, built now, or None.

    None when the definition has no UserCanvas, so a component type that
    lives in the same library is not a page and files nothing of its own.
    """
    definition = next((item for item in project.definitions
                       if item.name == name), None)
    canvas = definition.canvas if definition is not None else None
    if canvas is None or canvas.classid != "UserCanvas":
        return None
    built = netlists_of(project, registry, source_path, only={name})
    return built[0] if built else None


def extract(case_path: str,
            project: HirProject | None = None,
            workspaces: Sequence[str] = ()) -> Iterator[Netlist]:
    """Yield one :class:`Netlist` per schematic canvas in a ``.pscx``.

    ``project`` is the case already read. A caller holding one hands it
    over rather than have the same bytes parsed a second time; a caller
    with none is read for, and the unreadable-file diagnostic belongs to
    whichever of the two did the reading.
    """
    prepared = prepare(case_path, project, workspaces)
    if prepared is None:
        return
    project, registry, _libraries = prepared
    yield from netlists_of(project, registry, case_path)

"""The DiagramLayout profile: the drawing, as standard CGMES DL.

Every other document this package writes describes the ELECTRICAL model
and stops at the semantic layer. This one carries what the case was drawn
as: where each `<User>` and `<Wire>` sits on its page, how far it is
turned, the polyline a wire runs along, and the port geometry of a
definition's symbol. `cim:Diagram`, `cim:DiagramObject` and
`cim:DiagramObjectPoint` are standard classes and DL is a standard
profile, so nothing here costs a published identifier.

**A diagram is a DEFINITION's page, and every other document is per
INSTANCE.** That is the one structural fact this module is organised
around and it decides the shape of everything else. A `<Definition>` is
drawn once and placed any number of times, sometimes none; `build_cim`
and `build_emt` mint one subject per placement per instance path. So a
drawn component on a reused page corresponds to as many pieces of
equipment as the page has instances, and
`cim:DiagramObject.IdentifiedObject` is `0..1`. It is
therefore stated NOWHERE: naming the object of one arbitrary instance
would be false for the others, and a page nothing instantiates has no
object to name at all. 301 makes the association optional precisely so a
drawn thing with no domain object can still be drawn.

For the same reason the document's header declares no `DependentOn`. It
references no subject in any other document, and asserting a dependency
that does not exist is what `emt:SimulationCase` already refuses to do.
600-1's PROF10 permits a DL header to depend on EQ; it does not require
it, and we have nothing to join.

Writing triples directly rather than routing pycgmes resources through
``profile_graph`` is forced rather than chosen: ``cim:DiagramObjectPoint``
is not an ``IdentifiedObject``, carries no mRID, and cannot live in a
``CimModel`` keyed by one. It is the same shape as ``cim:ParameterValue``
in :mod:`pscx.emt`, and it is handled the same way.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import rdflib
from rdflib import RDF, Literal, URIRef

from pscx.diagnostics import DIAGNOSTICS, Diagnostics, Provenance
from pscx.mrid import MridCollision, mrid

if TYPE_CHECKING:
    from pscx.elaborate import FlatProject
    from pscx.hir import HirProject

CIM_NS = "http://iec.ch/TC57/CIM100#"
CIM = rdflib.Namespace(CIM_NS)

#: The two drawings a `<Definition>` can state, and the value each one's
#: `cim:Diagram` carries as its description. A `<schematic>` is the page
#: the definition contains; the `<graphics>` `<Port>`s are the symbol it
#: is placed AS, and the two are different coordinate spaces -- a port's
#: x/y is library-local and gets rotated onto a canvas by
#: :func:`pscx.geometry.rotate_port`. One Diagram each, so a reader never
#: has to guess which space a point is in.
SCHEMATIC = "schematic"
SYMBOL = "symbol"

#: `cim:OrientationKind.negative` is x rightward and y DOWNWARD, which is
#: the coordinate system a PSCAD canvas states: coordinates are
#: non-negative integers measured from the top left. `positive`
#: would put the origin at the bottom left and mirror every page.
ORIENTATION = "OrientationKind.negative"

#: The element kinds a page draws, as the tag half of a DiagramObject's
#: name. `<User>` and `<Wire>` ids are ambiguous BY NUMBER, because one
#: case can state an id that belongs to both, so no seed and no name here
#: is built from an id alone.
USER = "User"
WIRE = "Wire"
PORT = "Port"


@dataclass
class DlModel:
    """The diagram-layout graph of one case, its metrics and its findings.

    ``minted`` is the same ledger :class:`pscx.emt.EmtModel` keeps and for
    the same reason: four seed spaces write into this graph and they stay
    apart only because their seed strings differ. Two objects handed one
    IRI merge into a subject carrying both their statements, and the
    document still parses and still conforms.
    """

    project: str
    graph: rdflib.Graph
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    metrics: Counter = field(default_factory=Counter)
    minted: dict[str, str] = field(default_factory=dict)

    def mint(self, mrid_value: str, class_name: str) -> URIRef:
        existing = self.minted.get(mrid_value)
        if existing is not None:
            raise MridCollision(
                f"two objects were given mRID {mrid_value}: a "
                f"{existing} and a {class_name}. An mRID is uuid5 over one "
                f"(project, kind, id) seed -- see pscx/mrid.py -- so this "
                f"is one seed used twice, and the fix is a seed that tells "
                f"the two objects apart.")
        self.minted[mrid_value] = class_name
        subject = _uri(mrid_value)
        self.graph.add((subject, RDF.type, CIM[class_name]))
        return subject

    def named(self, mrid_value: str, class_name: str, name: str) -> URIRef:
        """Mint an IdentifiedObject and state the mRID and name it carries.

        Both are mandatory on every DL class that is one: 600-2 puts
        `IdentifiedObject.mRID` and `.name` at 1..1 on Diagram,
        DiagramObject and VisibilityLayer alike. DiagramObjectPoint is NOT
        an IdentifiedObject and mints directly.
        """
        subject = self.mint(mrid_value, class_name)
        self.graph.add((subject, CIM["IdentifiedObject.mRID"],
                        Literal(mrid_value)))
        self.graph.add((subject, CIM["IdentifiedObject.name"], Literal(name)))
        return subject


def _uri(mrid_value: str) -> URIRef:
    return URIRef(f"urn:uuid:{mrid_value}")


def _graph() -> rdflib.Graph:
    graph = rdflib.Graph()
    graph.bind("cim", CIM_NS)
    return graph


def _literal(value: Any) -> Literal:
    """A PLAIN literal, like every other document this repo writes: a
    typed literal breaks PowSyBl's plain-string SPARQL comparisons, and
    datatypes are the validator's job."""
    return Literal(str(value))


def _seed(*parts: Any) -> str:
    """A seed over several parts, each percent-quoted before joining.

    The quoting is what makes the encoding injective, the same reason
    :func:`pscx.mrid.mrid` quotes its three: a naive join would let a
    definition named ``a`` holding a ``b/c`` meet a definition named
    ``a/b`` holding a ``c``.
    """
    return "/".join(quote(str(part), safe="") for part in parts)


def _coordinate(value: str | None) -> int | None:
    """One stated coordinate as an integer, or None.

    PSCAD coordinates are always integral (port geometry rests on
    exact integer collinearity), so a value that is not is a statement
    this module cannot carry rather than one to round.
    """
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def rotation_degrees(orient: str | None) -> float | None:
    """`cim:DiagramObject.rotation` for a stated PSCAD ``orient``, or None.

    PSCAD packs two things into one integer 0-7: a quarter turn in the low
    two bits and a mirror in x above 4 (:func:`pscx.geometry.rotate_port`).
    CIM's rotation is an angle -- "zero degrees is pointing to the top of
    the diagram, rotation is clockwise" -- and an angle has no mirror. On
    a page whose y grows downward the quarter turn ``orient`` applies
    reads as clockwise, so the angle is ``90 * (orient % 4)``.

    **The mirror does not survive.** DL has no property for a reflected
    symbol and this profile declares no term of its own, so the bit is
    dropped rather than folded into an angle that would say something
    false. It is counted, not silently lost.
    """
    if orient is None:
        return None
    try:
        value = int(orient)
    except ValueError:
        return None
    return float(90 * (value % 4))


# --------------------------------------------------------------------------
# Forward: the HIR -> one DL graph
# --------------------------------------------------------------------------


def build_dl(flat: FlatProject, project: HirProject | None = None) -> DlModel:
    """The diagram-layout graph of one case.

    ``project`` is the case already read, for a caller that has one; with
    nothing handed over the case is read here, exactly as
    :func:`pscx.nets.extract` takes its own. The project is not retained:
    an ``HirProject`` keeps the elements it kept opaque and an lxml
    element keeps its document, so every value this builder wants is
    copied out as a string or an int and the tree is let go.
    """
    from pscx.io import read_project

    name = flat.project or flat.case
    hir = read_project(flat.case) if project is None else project
    dl = DlModel(project=name, graph=_graph())
    layered: dict[str, list[URIRef]] = {}

    for definition in hir.definitions:
        defn = definition.name
        if not defn:
            # every DL class that is an IdentifiedObject needs a name, and
            # a definition with none names no page to put its drawing on
            dl.diagnostics.emit("dl_definition_unnamed",
                                provenance=_where(name, definition.id))
            continue
        if definition.canvas is not None:
            _canvas(dl, defn, definition.canvas, layered)
        if definition.ports:
            _symbol(dl, defn, definition.ports, layered)

    _visibility_layers(dl, hir.layers, layered)
    return dl


def _where(project: str, element: Any) -> Provenance:
    return Provenance(case=project, element=None if element is None
                      else str(element))


def _diagram(dl: DlModel, defn: str, role: str) -> URIRef:
    """One page of one definition.

    `Diagram.orientation` is the one attribute 600-2 makes mandatory here,
    and the description says WHICH drawing of the definition this is --
    the two are different coordinate spaces and a reader that could not
    tell them apart would read a symbol-local port offset as a canvas
    position.
    """
    subject = dl.named(mrid(dl.project, "Diagram", _seed(defn, role)),
                       "Diagram", defn)
    dl.graph.add((subject, CIM["IdentifiedObject.description"],
                  Literal(role)))
    dl.graph.add((subject, CIM["Diagram.orientation"], CIM[ORIENTATION]))
    dl.metrics[f"dl_diagram: {role}"] += 1
    return subject


def _canvas(dl: DlModel, defn: str, canvas, layered: dict) -> None:
    diagram = _diagram(dl, defn, SCHEMATIC)
    for component in canvas.components:
        _placement(dl, diagram, defn, component, layered)
    for index, wire in enumerate(canvas.wires):
        # A `<Wire>` need not state an id. An element that states no
        # identity has none to seed from, so the ordinal within its canvas
        # is used and the substitution is counted rather than hidden. An
        # ordinal shifts when the page is reordered, which is exactly what
        # an id exists to stop.
        ident = wire.id
        if ident is None:
            ident = f"#{index}"
            dl.diagnostics.emit("dl_element_without_id", WIRE,
                                provenance=_where(dl.project, defn))
        _drawn(dl, diagram, defn, WIRE, ident, wire.orient,
               _wire_points(dl, defn, wire), wire.layer, layered)
        # A hosted device is a `<User>` INSIDE a `<Wire>`, and
        # it is drawn on the same page as everything else, from the id,
        # position and orient it states. Its
        # containment by a wire is an electrical relation, not a
        # graphical one, so nothing about it belongs in the drawing.
        for hosted in wire.hosted:
            _placement(dl, diagram, defn, hosted, layered)


def _placement(dl: DlModel, diagram: URIRef, defn: str, component,
               layered: dict) -> None:
    _drawn(dl, diagram, defn, USER, component.id, component.orient,
           _point_of(dl, defn, component.x, component.y, USER),
           component.layer, layered)


def _symbol(dl: DlModel, defn: str, ports, layered: dict) -> None:
    diagram = _diagram(dl, defn, SYMBOL)
    for index, port in enumerate(ports):
        ident = port.id
        if ident is None:
            ident = f"#{index}"
            dl.diagnostics.emit("dl_element_without_id", PORT,
                                provenance=_where(dl.project, defn))
        # a `<Port>` states no orient: the symbol is drawn upright and the
        # PLACEMENT of the definition is what turns it
        _drawn(dl, diagram, defn, PORT, ident, None,
               _point_of(dl, defn, port.x, port.y, PORT), None, layered)


def _point_of(dl: DlModel, defn: str, x, y, kind: str) -> list:
    px, py = _coordinate(x), _coordinate(y)
    if px is None or py is None:
        dl.diagnostics.emit("dl_bad_coordinates", kind,
                            provenance=_where(dl.project, defn))
        return []
    return [(px, py)]


def _wire_points(dl: DlModel, defn: str, wire) -> list:
    """A wire's polyline in CANVAS coordinates: its origin plus each offset
    the `<vertex>` list states, which is what
    :func:`pscx.io._vertices` reads for the netlist.

    The wire's own x/y come back from the FIRST point, which is exact
    when a wire's first `<vertex>` is (0,0). The format does not require
    that, so a wire whose first vertex is an offset is counted here rather
    than assumed away:
    the polyline it draws is still stated exactly, and only the split
    between origin and offsets would come back differently.
    """
    ox, oy = _coordinate(wire.x), _coordinate(wire.y)
    if ox is None or oy is None:
        dl.diagnostics.emit("dl_bad_coordinates", WIRE,
                            provenance=_where(dl.project, defn))
        return []
    points = []
    for x, y in wire.vertices:
        dx, dy = _coordinate(x), _coordinate(y)
        if dx is None or dy is None:
            dl.diagnostics.emit("dl_bad_coordinates", "vertex",
                                provenance=_where(dl.project, defn))
            return []
        points.append((ox + dx, oy + dy))
    if points and points[0] != (ox, oy):
        dl.diagnostics.emit("dl_wire_origin_offset",
                            provenance=_where(dl.project, defn))
    return points


def _drawn(dl: DlModel, diagram: URIRef, defn: str, kind: str, ident: Any,
           orient: str | None, points: list, layer: str | None,
           layered: dict) -> None:
    """One drawn element: a DiagramObject on a page, and its points.

    The NAME carries the element's identity within its page, because
    nothing else can: an mRID here is uuid5 over a seed and is one-way, so
    a reader with only the document would otherwise know where a thing is
    drawn and never which thing it is. The tag is part of it rather than
    left implicit, because a reference can be ambiguous by element
    number alone.
    """
    if not points:
        return
    seed = _seed(defn, kind, ident)
    subject = dl.named(mrid(dl.project, "DiagramObject", seed),
                       "DiagramObject", f"{kind} {ident}")
    dl.graph.add((subject, CIM["DiagramObject.Diagram"], diagram))
    rotation = rotation_degrees(orient)
    if rotation is not None:
        dl.graph.add((subject, CIM["DiagramObject.rotation"],
                      _literal(rotation)))
    turned = _coordinate(orient)
    if turned is not None and turned >= 4:
        dl.diagnostics.emit("dl_mirror_dropped", kind,
                            provenance=_where(dl.project, defn))
    for sequence, (x, y) in enumerate(points, start=1):
        # 301 puts sequenceNumber above zero STRICTLY, so the numbering
        # starts at 1: a zero-based sequence is a Violation, not a style
        point = dl.mint(
            mrid(dl.project, "DiagramObjectPoint", _seed(seed, sequence)),
            "DiagramObjectPoint")
        dl.graph.add((point, CIM["DiagramObjectPoint.DiagramObject"], subject))
        dl.graph.add((point, CIM["DiagramObjectPoint.sequenceNumber"],
                      _literal(sequence)))
        dl.graph.add((point, CIM["DiagramObjectPoint.xPosition"], _literal(x)))
        dl.graph.add((point, CIM["DiagramObjectPoint.yPosition"], _literal(y)))
    dl.metrics[f"dl_drawn: {kind}"] += 1
    dl.metrics["dl_points"] += len(points)
    if layer:
        layered.setdefault(layer, []).append(subject)


def _visibility_layers(dl: DlModel, layers, layered: dict) -> None:
    """One `cim:VisibilityLayer` per declared layer that something is ON.

    600-2 puts `VisibilityLayer.VisibleObjects` at 1..n, so a layer no
    drawn element names cannot be written down: an empty layer is a
    Violation and not merely uninformative.

    `HirLayer.state` does not travel either. DL has no property for
    whether a layer is enabled, and this profile declares no term of its
    own.
    """
    declared = {layer.name for layer in layers if layer.name}
    for name in sorted(set(layered) - declared):
        dl.diagnostics.emit("dl_layer_undeclared", name)
    for layer in layers:
        members = layered.get(layer.name or "")
        if not layer.name or not members:
            dl.diagnostics.emit("dl_layer_unused", layer.name or "")
            continue
        subject = dl.named(mrid(dl.project, "VisibilityLayer", layer.name),
                           "VisibilityLayer", layer.name)
        for member in members:
            dl.graph.add((subject, CIM["VisibilityLayer.VisibleObjects"],
                          member))
        dl.metrics["dl_visibility_layer"] += 1


def emit_dl_file(flat: FlatProject, out_dir, *, scenario_time: str,
                 modeling_authority_set: str | None = None,
                 version: str = "1",
                 project: HirProject | None = None) -> Path:
    """Write the diagram-layout document for one case.

    ``project`` is the case already read, passed straight through to
    :func:`build_dl`.
    """
    from pycgmes.utils.profile import Profile

    from pscx.cimxml import (
        DEFAULT_MODELING_AUTHORITY_SET,
        full_model_header,
        serialize_graph,
    )

    if modeling_authority_set is None:
        modeling_authority_set = DEFAULT_MODELING_AUTHORITY_SET
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dl = build_dl(flat, project)
    DIAGNOSTICS.extend(dl.diagnostics)
    model_id = mrid(dl.project, "FullModel", f"{Profile.DL.name}/{version}")
    # no DependentOn: this document references no subject in any other
    # one, and a dependency that does not exist is one a consumer would
    # resolve against nothing
    full_model_header(dl.graph, model_id, Profile.DL.uris[0], scenario_time,
                      version,
                      modeling_authority_set=modeling_authority_set)
    path = out / f"{dl.project}_{Profile.DL.name}.xml"
    path.write_bytes(serialize_graph(dl.graph))
    return path


# --------------------------------------------------------------------------
# The inverse: an emitted DL document -> the drawing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DrawnObject:
    """One drawn element, as either side of the round trip states it.

    ``vertices`` is the OFFSET list a `<Wire>` states, so the pair
    ``(x, y), vertices`` is exactly what the HIR holds; a `<User>` and a
    `<Port>` draw one point and state none.
    """

    kind: str
    ident: str
    x: int
    y: int
    rotation: float | None
    vertices: tuple[tuple[int, int], ...] = ()


def drawing_of(project: HirProject) -> dict:
    """What the HIR states, in the shape :func:`reconstruct_drawing` returns.

    ``(definition, role) -> {(kind, ident): DrawnObject}``, keyed by
    identity rather than ordered, because a DiagramObject carries no
    ordinal and the document therefore states no order among the elements
    of a page. Neither does the HIR across kinds -- `HirCanvas` keeps
    components and wires in two lists -- so the order within one kind is
    the only thing either side could have compared, and it is not what
    this oracle is about.
    """
    out: dict = {}
    for definition in project.definitions:
        defn = definition.name
        if not defn:
            continue
        if definition.canvas is not None:
            page: dict = {}
            for component in definition.canvas.components:
                _record(page, USER, component.id, component.x, component.y,
                        component.orient, ())
            for index, wire in enumerate(definition.canvas.wires):
                ident = wire.id if wire.id is not None else f"#{index}"
                _record(page, WIRE, ident, wire.x, wire.y, wire.orient,
                        tuple((_coordinate(x), _coordinate(y))
                              for x, y in wire.vertices))
                for hosted in wire.hosted:
                    _record(page, USER, hosted.id, hosted.x, hosted.y,
                            hosted.orient, ())
            out[(defn, SCHEMATIC)] = page
        if definition.ports:
            page = {}
            for index, port in enumerate(definition.ports):
                ident = port.id if port.id is not None else f"#{index}"
                _record(page, PORT, ident, port.x, port.y, None, ())
            out[(defn, SYMBOL)] = page
    return out


def _record(page: dict, kind: str, ident: Any, x, y, orient, vertices) -> None:
    px, py = _coordinate(x), _coordinate(y)
    if px is None or py is None or any(
            v is None for pair in vertices for v in pair):
        return
    page[(kind, str(ident))] = DrawnObject(
        kind=kind, ident=str(ident), x=px, y=py,
        rotation=rotation_degrees(orient), vertices=tuple(vertices))


def reconstruct_drawing(graphs) -> dict:
    """Rebuild the drawing from emitted documents ALONE.

    Nothing here reads the HIR, the flattened project or the builder. A
    page is a `cim:Diagram` plus its description; a drawn element is a
    `cim:DiagramObject` whose name states its kind and its id; its
    geometry is the `cim:DiagramObjectPoint`s in sequence order, and the
    wire's own origin is the first of them.

    A point with no sequence number sorts last and keeps document order
    among its equals, which is what makes dropping one a DIFFERENT
    drawing rather than a silently repaired one.
    """
    merged = rdflib.Graph()
    for graph in graphs:
        for triple in graph:
            merged.add(triple)

    pages: dict[URIRef, tuple[str, str]] = {}
    for subject in merged.subjects(RDF.type, CIM["Diagram"]):
        name = merged.value(subject, CIM["IdentifiedObject.name"])
        role = merged.value(subject, CIM["IdentifiedObject.description"])
        if name is None or role is None:
            continue
        pages[subject] = (str(name), str(role))

    points: dict[URIRef, list] = {}
    for order, point in enumerate(sorted(
            merged.subjects(RDF.type, CIM["DiagramObjectPoint"]))):
        owner = merged.value(point, CIM["DiagramObjectPoint.DiagramObject"])
        x = merged.value(point, CIM["DiagramObjectPoint.xPosition"])
        y = merged.value(point, CIM["DiagramObjectPoint.yPosition"])
        if owner is None or x is None or y is None:
            continue
        sequence = merged.value(point, CIM["DiagramObjectPoint.sequenceNumber"])
        key = (float("inf") if sequence is None else float(str(sequence)),
               order)
        points.setdefault(owner, []).append(
            (key, (int(float(str(x))), int(float(str(y))))))

    # every page the document states, including one nothing is drawn on:
    # a definition with an empty `<schematic>` still HAS a page, and a
    # reader that inferred pages from their contents would lose it
    out: dict = {page: {} for page in pages.values()}
    for subject in merged.subjects(RDF.type, CIM["DiagramObject"]):
        page = pages.get(merged.value(subject, CIM["DiagramObject.Diagram"]))
        name = merged.value(subject, CIM["IdentifiedObject.name"])
        own = sorted(points.get(subject, ()))
        if page is None or name is None or not own:
            continue
        kind, _, ident = str(name).partition(" ")
        rotation = merged.value(subject, CIM["DiagramObject.rotation"])
        coordinates = [value for _key, value in own]
        (x, y), rest = coordinates[0], coordinates[1:]
        out.setdefault(page, {})[(kind, ident)] = DrawnObject(
            kind=kind, ident=ident, x=x, y=y,
            rotation=None if rotation is None else float(str(rotation)),
            vertices=tuple([(0, 0)] + [(px - x, py - y) for px, py in rest])
            if kind == WIRE else ())
    return out

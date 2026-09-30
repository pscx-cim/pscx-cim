"""A faithful, unevaluated model of a .pscx / .pslx project.

The extractor reads what it needs and lets the rest fall on the floor. It
has to: it answers "what is connected to what", and a drawing's zoom level
is not part of that answer. But a thing that falls on the floor silently
cannot be counted, so nobody can say what the loader drops: `memberof`,
`layer` and the dead WireBranch attributes are each invisible to a
loader that keeps only what it reads.

This loader keeps everything, in three buckets that make different claims:

  MODELED       a typed field, because the meaning is established -- by the
                 PSCAD manual or by the extractor's use of it.
  OPAQUE         a subtree deliberately not interpreted (drawing primitives,
                 help text, form layout). The element itself is retained, so
                 a writer can put it back unchanged.
  UNRECOGNIZED   we cannot say what it means. Banked with its span and the
                 xpath of the element that carried it, which is the same
                 shape as a GAP diagnostic: something is missing from our
                 understanding and what reaches a consumer is incomplete --
                 but an attribute's value survives verbatim, addressed, and
                 a writer can put it back where it was.

Nothing here evaluates anything. Parameter values are the expressions the
file states, guards are trees, units are unconverted -- the elaboration
that turns those into a network is the extractor's job and stays there.

Spans come from the element (:func:`pscx.io.span_of`), never from a caller:
an HIR node built during loading gets file and line for free, and a node
built any other way gets None rather than a plausible-looking wrong line.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from lxml import etree as ET

from pscx.diagnostics import DIAGNOSTICS, Span
from pscx.guards import Node, assemble_splices, parse_script
from pscx.io import _XML_PARSER, span_of

# --------------------------------------------------------------------------
# The channel
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Unrecognized:
    """Something the loader read and cannot give a meaning to."""

    #: "attribute" or "element"
    kind: str
    #: tag of the element carrying it -- an attribute name alone does not
    #: identify it, since `name` means something different on every element
    owner: str
    name: str
    value: str | None
    span: Span | None
    #: XPath of the element that carried it, from lxml's ``getpath``:
    #: tag-positional steps from the root, total over any document. A
    #: plain string, so it still names the element after the tree is
    #: dropped -- an element reference would BE the tree. One string per
    #: element, shared by every item banked from it; the span cannot
    #: serve, because a source line carries several elements.
    xpath: str | None = None

    @property
    def key(self) -> str:
        return f"{self.owner}@{self.name}" if self.kind == "attribute" \
            else f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class Opaque:
    """A subtree kept verbatim and not interpreted. The element is retained
    rather than copied, so it round-trips exactly and costs nothing."""

    tag: str
    element: Any
    span: Span | None


class _Reader:
    """Reads elements while banking whatever it was not told to expect."""

    def __init__(self) -> None:
        self.unrecognized: list[Unrecognized] = []

    def attributes(self, element: ET.Element,
                   *recognized: str) -> dict[str, str]:
        """The recognized attributes, with everything else banked."""
        known = set(recognized)
        span = span_of(element)
        xpath = None
        for name, value in element.attrib.items():
            if name not in known:
                if xpath is None:
                    xpath = element.getroottree().getpath(element)
                self.unrecognized.append(Unrecognized(
                    "attribute", element.tag, name, value, span, xpath))
        return {name: element.get(name) for name in recognized
                if element.get(name) is not None}

    def children(self, element: ET.Element, *recognized: str) -> None:
        """Bank every child element the caller did not name."""
        known = set(recognized)
        for child in element:
            if isinstance(child.tag, str) and child.tag not in known:
                self.unrecognized.append(Unrecognized(
                    "element", element.tag, child.tag, None, span_of(child),
                    child.getroottree().getpath(child)))


#: Subtrees the HIR deliberately does not interpret. Drawing primitives and
#: help text describe how a component LOOKS and reads, which is a different
#: model from what it is connected to; form layout likewise. They are
#: retained whole rather than banked, because "we chose not to model this"
#: and "we do not know what this is" are different statements.
OPAQUE_TAGS = frozenset({
    "Gfx", "svg", "help", "vis", "regex", "error_msg", "choice", "column",
    "row", "references", "grouping", "bookmarks", "output", "List",
    "constants", "sections", "Frame", "Instrument", "FileCmp", "Line",
    "Sticky", "call", "path", "channel", "Curve",
})

GLOBAL_SUBSTITUTION_TAGS = ("GlobalSubstitutions", "GlobalSubsitutions")


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


@dataclass
class HirParams:
    """A ``<paramlist>``: raw name -> value expressions, in document order.
    Values are the text the file states; nothing is substituted, resolved
    or unit-converted here."""

    name: str | None
    values: dict[str, str] = field(default_factory=dict)
    span: Span | None = None
    #: Source line per value, kept as a bare line number rather than as a
    #: ``Span`` because the file is the paramlist's and a large case
    #: states a great many of these. What it buys is :meth:`span_for`:
    #: a value is an expression a reader may have to be sent to, and the
    #: line the ``<paramlist>`` opens on is not that line.
    lines: dict[str, int] = field(default_factory=dict)
    #: The ``<row>`` texts a matrix parameter states under its
    #: ``<param>``, verbatim and in row order, keyed like ``values``.
    #: The rows ARE the matrix -- the ``value`` attribute states only a
    #: dimension where the source declares one -- and the source tool
    #: reads a matrix stated without them as that coupling disabled: a
    #: different network, invisible to elaboration because matrix
    #: contents never reach the netlist.
    rows: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def span_for(self, name: str) -> Span | None:
        """Where one value was stated: its own ``<param>``'s line.

        The paramlist's span where the line is unknown, and None where
        the paramlist has none -- a tree built in memory has no document
        to name, and a plausible-looking wrong line is worse than an
        honest absence.
        """
        if self.span is None:
            return None
        line = self.lines.get(name)
        return self.span if line is None else Span(self.span.file, line)


@dataclass
class HirSubstitution:
    """A ``<Sub>``: one global substitution, as the file groups it.

    The name and the value are two ``<param>`` entries of the one
    ``<paramlist>`` rather than fields here. That is the shape the file
    states and the shape the extractor reads, and promoting them would
    make the model claim a paramlist holds exactly those two keys, which
    nothing establishes.
    """

    id: str | None
    classid: str | None
    params: list[HirParams] = field(default_factory=list)
    span: Span | None = None

    @property
    def declared(self) -> tuple[str, str] | None:
        """``(name, value)`` if this Sub states both, else None."""
        for params in self.params:
            name = params.values.get("name")
            if name is not None:
                return name, params.values.get("value", "")
        return None


@dataclass
class HirSubstitutionList:
    """A ``<List>`` inside the substitution container: the grouping layer.

    Modeled rather than left to the OPAQUE bucket because what it holds
    is not opaque -- the ``<Sub>`` elements under it are the substitution
    table itself. Every container states two, ``classid="Sub"`` holding
    the substitutions and ``classid="ValueSet"`` empty in master, and
    both are kept: an empty list is a thing the
    file states, and writing back only the populated one emits a document
    the file did not write.
    """

    classid: str | None
    name: str | None = None
    subs: list[HirSubstitution] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirSubstitutions:
    """A ``<GlobalSubstitutions>`` container, in the three levels the file
    states it in: container, ``<List>``, ``<Sub>``.

    Flattening the three into one list of paramlists is what made the
    container's own paramlist and a Sub's indistinguishable, and a writer
    given that list can only emit the innermost level at the outermost
    depth. The nesting is the value here, not decoration: ``pscx.nets``
    builds its substitution table from ``root.iter("Sub")``, so a document
    with no ``<Sub>`` in it answers no ``$(NAME)`` at all.
    """

    #: Which of :data:`GLOBAL_SUBSTITUTION_TAGS` this container spelt,
    #: kept so a writer puts back the spelling it read. Correcting the
    #: misspelling on the way out would be the writer inventing, and the
    #: misspelling is the shipped state of some PSCAD files.
    tag: str
    name: str | None = None
    #: The container's OWN paramlists, distinct from the Sub-level ones.
    params: list[HirParams] = field(default_factory=list)
    lists: list[HirSubstitutionList] = field(default_factory=list)
    span: Span | None = None

    @property
    def subs(self) -> list[HirSubstitution]:
        return [sub for group in self.lists for sub in group.subs]


@dataclass
class HirPort:
    """A ``<Port>`` of a definition's graphics: where a connection may be
    made, in definition-local coordinates."""

    id: str | None
    classid: str | None
    x: str | None
    y: str | None
    params: HirParams | None = None
    span: Span | None = None


@dataclass
class HirComponent:
    """A placed ``<User>``: an instance of a definition on a canvas."""

    classid: str | None
    id: str | None
    defn: str | None
    x: str | None
    y: str | None
    orient: str | None
    name: str | None = None
    disable: str | None = None
    layer: str | None = None
    params: list[HirParams] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirWire:
    """A ``<Wire>``: geometry, and possibly a hosted device."""

    classid: str | None
    id: str | None
    name: str | None
    x: str | None
    y: str | None
    orient: str | None
    disable: str | None = None
    layer: str | None = None
    defn: str | None = None
    vertices: list[tuple[str | None, str | None]] = field(default_factory=list)
    params: list[HirParams] = field(default_factory=list)
    hosted: list[HirComponent] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirCanvas:
    """A definition's ``<schematic>``: the page it draws."""

    classid: str | None
    params: list[HirParams] = field(default_factory=list)
    components: list[HirComponent] = field(default_factory=list)
    wires: list[HirWire] = field(default_factory=list)
    opaque: list[Opaque] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirFormParameter:
    """One declared form parameter: its name, its type and unit, its
    default VALUE as written, and the condition that enables it."""

    name: str | None
    type: str | None = None
    desc: str | None = None
    group: str | None = None
    unit: str | None = None
    intent: str | None = None
    dim: str | None = None
    content_type: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    value: str | None = None
    condition: str | None = None
    #: What the condition gates: "Enable", "Visible", or None when the
    #: form does not say. The extractor honours all three identically as
    #: write-activity gates; the distinction is recorded here
    #: because the form makes it, not because anything acts on it yet.
    condition_type: str | None = None
    opaque: list[Opaque] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirFormCategory:
    """A group of form parameters, with the condition enabling the group.
    The two condition levels both gate an ``intent="Output"`` writer,
    so neither can be folded into the other."""

    name: str | None
    visible: str | None = None
    condition: str | None = None
    condition_type: str | None = None
    parameters: list[HirFormParameter] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirSegment:
    """One ``<segment>`` of a definition's script, as text AND as the guard
    tree that text parses to. Both are kept: the text is
    what round-trips, the tree is what anything reasons over."""

    name: str | None
    id: str | None
    text: str
    tree: tuple[Node, ...]
    span: Span | None = None


@dataclass
class HirDefinition:
    """A ``<Definition>``: a component's form, ports, script and page."""

    classid: str | None
    name: str | None
    id: str | None
    group: str | None = None
    params: list[HirParams] = field(default_factory=list)
    #: The ``<form>`` element's own ``name``: the component's title as a
    #: reader sees it ("6 Pulse Bridge"), which is the only place a
    #: definition states in English what it IS. ``name`` above is the
    #: identifier scripts and instances resolve against.
    form_name: str | None = None
    form: list[HirFormCategory] = field(default_factory=list)
    ports: list[HirPort] = field(default_factory=list)
    segments: list[HirSegment] = field(default_factory=list)
    canvas: HirCanvas | None = None
    opaque: list[Opaque] = field(default_factory=list)
    span: Span | None = None


@dataclass
class HirLayer:
    """A drawing layer and its state."""

    name: str | None
    state: str | None
    id: str | None = None
    span: Span | None = None


@dataclass
class HirProject:
    """One .pscx case or .pslx library, whole and unevaluated."""

    path: str
    name: str | None = None
    version: str | None = None
    target: str | None = None
    schema: str | None = None
    params: list[HirParams] = field(default_factory=list)
    definitions: list[HirDefinition] = field(default_factory=list)
    layers: list[HirLayer] = field(default_factory=list)
    #: The ``<GlobalSubstitutions>`` containers, whole. A list rather than
    #: a scalar because the loader finds them by iterating: master states
    #: at most one, and a model that holds one cannot say so about a file
    #: that states two.
    substitutions: list[HirSubstitutions] = field(default_factory=list)
    settings: list[HirParams] = field(default_factory=list)
    opaque: list[Opaque] = field(default_factory=list)
    unrecognized: list[Unrecognized] = field(default_factory=list)

    @property
    def canvases(self) -> list[HirCanvas]:
        return [d.canvas for d in self.definitions if d.canvas is not None]

    @property
    def globals(self) -> dict[str, str]:
        """``$(Name)`` -> value, over every ``<Sub>`` the file states.

        The same table ``pscx.nets`` builds from ``root.iter("Sub")``, so
        an extractor reading the HIR instead of the tree asks one question
        rather than walking three levels. Later Subs win, which is what
        iterating the document does.
        """
        declared = (sub.declared for container in self.substitutions
                    for sub in container.subs)
        return dict(pair for pair in declared if pair is not None)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _params(reader: _Reader, element: ET.Element) -> HirParams:
    attrs = reader.attributes(element, "name")
    reader.children(element, "param")
    values: dict[str, str] = {}
    lines: dict[str, int] = {}
    rows: dict[str, tuple[str, ...]] = {}
    for param in element.findall("param"):
        got = reader.attributes(param, "name", "value")
        reader.children(param, "row")
        if got.get("name") is not None:
            values[got["name"]] = got.get("value", "")
            stated = param.findall("row")
            if stated:
                for row in stated:
                    # a row is text-only; an attribute or child element
                    # would be a statement this model has no field for,
                    # so it lands on the channel rather than vanishing
                    reader.attributes(row)
                    reader.children(row)
                rows[got["name"]] = tuple(row.text or "" for row in stated)
            if param.sourceline is not None:
                lines[got["name"]] = param.sourceline
    return HirParams(attrs.get("name"), values, span_of(element), lines,
                     rows)


def _paramlists(reader: _Reader, element: ET.Element) -> list[HirParams]:
    return [_params(reader, p) for p in element.findall("paramlist")]


def _opaque(reader: _Reader, element: ET.Element,
            *recognized: str) -> list[Opaque]:
    """Retain the deliberately-uninterpreted children; bank the rest."""
    reader.children(element, *recognized, *OPAQUE_TAGS)
    return [Opaque(c.tag, c, span_of(c)) for c in element
            if isinstance(c.tag, str) and c.tag in OPAQUE_TAGS]


def _component(reader: _Reader, element: ET.Element) -> HirComponent:
    attrs = reader.attributes(
        element, "classid", "id", "defn", "x", "y", "orient", "name",
        "disable", "layer")
    reader.children(element, "paramlist")
    return HirComponent(
        classid=attrs.get("classid"), id=attrs.get("id"),
        defn=attrs.get("defn"), x=attrs.get("x"), y=attrs.get("y"),
        orient=attrs.get("orient"), name=attrs.get("name"),
        disable=attrs.get("disable"), layer=attrs.get("layer"),
        params=_paramlists(reader, element), span=span_of(element))


def _wire(reader: _Reader, element: ET.Element) -> HirWire:
    attrs = reader.attributes(
        element, "classid", "id", "name", "x", "y", "orient", "disable",
        "layer", "defn")
    reader.children(element, "vertex", "User", "paramlist")
    vertices = []
    for vertex in element.findall("vertex"):
        got = reader.attributes(vertex, "x", "y")
        reader.children(vertex)
        vertices.append((got.get("x"), got.get("y")))
    return HirWire(
        classid=attrs.get("classid"), id=attrs.get("id"),
        name=attrs.get("name"), x=attrs.get("x"), y=attrs.get("y"),
        orient=attrs.get("orient"), disable=attrs.get("disable"),
        layer=attrs.get("layer"), defn=attrs.get("defn"),
        vertices=vertices, params=_paramlists(reader, element),
        hosted=[_component(reader, u) for u in element.findall("User")],
        span=span_of(element))


def _canvas(reader: _Reader, element: ET.Element) -> HirCanvas:
    attrs = reader.attributes(element, "classid")
    opaque = _opaque(reader, element, "paramlist", "User", "Wire")
    return HirCanvas(
        classid=attrs.get("classid"),
        params=_paramlists(reader, element),
        components=[_component(reader, u) for u in element.findall("User")],
        wires=[_wire(reader, w) for w in element.findall("Wire")],
        opaque=opaque, span=span_of(element))


def _condition(reader: _Reader,
               element: ET.Element) -> tuple[str | None, str | None]:
    """``(text, type)`` of the first ``<cond>``, or ``(None, None)``."""
    cond = element.find("cond")
    if cond is None:
        return None, None
    got = reader.attributes(cond, "type")
    return (cond.text or "").strip(), got.get("type")


def _form(reader: _Reader,
          element: ET.Element) -> tuple[str | None, list[HirFormCategory]]:
    form_name = reader.attributes(element, "name").get("name")
    reader.children(element, "category")
    categories = []
    for category in element.findall("category"):
        attrs = reader.attributes(category, "name", "visible")
        reader.children(category, "cond", "parameter")
        parameters = []
        for parameter in category.findall("parameter"):
            got = reader.attributes(
                parameter, "name", "type", "desc", "group", "unit", "intent",
                "dim", "content_type", "min", "max")
            value = parameter.find("value")
            if value is not None:
                reader.attributes(value)
            condition, condition_type = _condition(reader, parameter)
            parameters.append(HirFormParameter(
                name=got.get("name"), type=got.get("type"),
                desc=got.get("desc"), group=got.get("group"),
                unit=got.get("unit"), intent=got.get("intent"),
                dim=got.get("dim"), content_type=got.get("content_type"),
                minimum=got.get("min"), maximum=got.get("max"),
                value=(value.text or "").strip() if value is not None else None,
                condition=condition, condition_type=condition_type,
                opaque=_opaque(reader, parameter, "value", "cond"),
                span=span_of(parameter)))
        condition, condition_type = _condition(reader, category)
        categories.append(HirFormCategory(
            name=attrs.get("name"), visible=attrs.get("visible"),
            condition=condition, condition_type=condition_type,
            parameters=parameters, span=span_of(category)))
    return form_name, categories


def _definition(reader: _Reader, element: ET.Element) -> HirDefinition:
    attrs = reader.attributes(element, "classid", "name", "id", "group")
    opaque = _opaque(reader, element,
                     "paramlist", "schematic", "form", "script", "graphics")
    ports: list[HirPort] = []
    graphics = element.find("graphics")
    if graphics is not None:
        reader.attributes(graphics)
        opaque += _opaque(reader, graphics, "paramlist", "Port", "cond")
        for port in graphics.findall("Port"):
            got = reader.attributes(port, "id", "classid", "x", "y")
            reader.children(port, "paramlist")
            plists = _paramlists(reader, port)
            ports.append(HirPort(
                id=got.get("id"), classid=got.get("classid"),
                x=got.get("x"), y=got.get("y"),
                params=plists[0] if plists else None, span=span_of(port)))
    segments: list[HirSegment] = []
    script = element.find("script")
    if script is not None:
        reader.attributes(script)
        reader.children(script, "segment")
        for segment in script.findall("segment"):
            got = reader.attributes(segment, "name", "id", "classid")
            text = segment.text or ""
            tree = parse_script(text)
            if got.get("name") == "Branch":
                tree = assemble_splices(tree, attrs.get("name") or "?")
            segments.append(HirSegment(
                name=got.get("name"), id=got.get("id"), text=text, tree=tree,
                span=span_of(segment)))
    form = element.find("form")
    schematic = element.find("schematic")
    form_name, categories = (_form(reader, form) if form is not None
                             else (None, []))
    return HirDefinition(
        classid=attrs.get("classid"), name=attrs.get("name"),
        id=attrs.get("id"), group=attrs.get("group"),
        params=_paramlists(reader, element),
        form_name=form_name, form=categories,
        ports=ports, segments=segments,
        canvas=_canvas(reader, schematic) if schematic is not None else None,
        opaque=opaque, span=span_of(element))


def load_project(path: str, bus=None) -> HirProject:
    """Read a whole .pscx or .pslx, keeping everything it states.

    Raises whatever lxml raises on an unreadable file: the HIR is not a
    diagnostic pass, and a caller that wants the extractor's tolerance of
    a broken file should use :func:`pscx.nets.extract`.

    ``bus`` is where :func:`_report` files the unrecognized channel, and
    it is a parameter rather than the module global because a caller that
    wants the channel silenced has to be able to name the bus it is
    silencing. Suppressing a global two modules share works only while
    they share it; :func:`pscx.io.load_definitions` does not.
    """
    root = ET.parse(path, _XML_PARSER).getroot()
    reader = _Reader()
    attrs = reader.attributes(root, "name", "version", "Target", "schema")
    project = HirProject(
        path=os.path.basename(path), name=attrs.get("name"),
        version=attrs.get("version"), target=attrs.get("Target"),
        schema=attrs.get("schema"))
    reader.children(root, "paramlist", "definitions", "Layers", "hierarchy",
                    *GLOBAL_SUBSTITUTION_TAGS, *OPAQUE_TAGS)
    project.params = _paramlists(reader, root)
    project.opaque = [Opaque(c.tag, c, span_of(c)) for c in root
                      if isinstance(c.tag, str) and c.tag in OPAQUE_TAGS]

    for layers in root.iter("Layers"):
        reader.attributes(layers)
        reader.children(layers, "Layer")
        for layer in layers.findall("Layer"):
            got = reader.attributes(layer, "name", "state", "id", "classid")
            reader.children(layer, "paramlist")
            _paramlists(reader, layer)
            project.layers.append(HirLayer(
                got.get("name"), got.get("state"), got.get("id"),
                span_of(layer)))

    for element in root.iter(*GLOBAL_SUBSTITUTION_TAGS):
        reader.children(element, "paramlist", "List")
        container = HirSubstitutions(
            tag=element.tag,
            name=reader.attributes(element, "name").get("name"),
            params=_paramlists(reader, element), span=span_of(element))
        # `findall`, not `iter`: a <Sub> anywhere else under the container
        # is banked as unrecognized rather than hoisted to a depth it was
        # not read from, which is the failure the nesting exists to stop.
        for node in element.findall("List"):
            got = reader.attributes(node, "classid", "name")
            reader.children(node, "Sub")
            group = HirSubstitutionList(
                classid=got.get("classid"), name=got.get("name"),
                span=span_of(node))
            for sub in node.findall("Sub"):
                got = reader.attributes(sub, "id", "classid")
                reader.children(sub, "paramlist")
                group.subs.append(HirSubstitution(
                    id=got.get("id"), classid=got.get("classid"),
                    params=_paramlists(reader, sub), span=span_of(sub)))
            container.lists.append(group)
        project.substitutions.append(container)

    for settings in root.iter("Settings"):
        reader.attributes(settings, "classid", "id", "link")
        reader.children(settings, "paramlist", "path")
        project.settings.extend(_paramlists(reader, settings))

    for definitions in root.iter("definitions"):
        reader.attributes(definitions)
        reader.children(definitions, "Definition")
    for definition in root.findall(".//Definition"):
        project.definitions.append(_definition(reader, definition))

    project.unrecognized = reader.unrecognized
    _report(project, bus)
    return project


def _report(project: HirProject, bus=None) -> None:
    """One finding per DISTINCT unrecognized thing, not per occurrence.

    A file can state one unrecognized attribute thousands of times, and
    that is one thing we do not understand, not thousands. This is the same
    deduplication the shipped-library defects needed. The count
    rides along, and the span is the first occurrence, so a reader has
    somewhere to look.
    """
    bus = DIAGNOSTICS if bus is None else bus
    first: dict[str, Unrecognized] = {}
    counts: Counter = Counter()
    for item in project.unrecognized:
        first.setdefault(item.key, item)
        counts[item.key] += 1
    for key, item in first.items():
        bus.emit("hir_unrecognized", key, span=item.span,
                 count=counts[key])

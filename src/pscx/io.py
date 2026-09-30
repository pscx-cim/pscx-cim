"""master.pslx / .pscx definition loading and resolution."""

from __future__ import annotations

import functools
import getpass
import hashlib
import os
import pickle
import tempfile
from typing import TYPE_CHECKING

from lxml import etree as ET

from pscx.common import MASTER_PSLX, _WRITER_SEGMENTS
from pscx.diagnostics import DIAGNOSTICS, Span
from pscx.preproc import (
    BranchDecl,
    _branch_decls,
    _writer_directives_of,
)
from pscx.geometry import Point
from pscx.model import ComponentDef, FormParameterDef, PortDef

if TYPE_CHECKING:
    # pscx.hir reads _XML_PARSER and span_of from here, so the import that
    # goes the other way is made where it is used rather than at module
    # scope. These names are for annotations only and cost nothing.
    from pscx.hir import HirDefinition, HirPort, HirProject, HirWire

#: lxml parser configured to reproduce ``xml.etree`` semantics exactly: the
#: stdlib parser discards comments and processing instructions, lxml keeps
#: them by default -- and a kept comment node would surface in bare child
#: iteration (``_params``) and tagless ``findall``. lxml is used for its
#: ``sourceline``, which :func:`span_of` turns into a diagnostic's span.
_XML_PARSER = ET.XMLParser(remove_comments=True, remove_pis=True)


def span_of(element: ET.Element) -> Span | None:
    """Where an element was read from.

    Both halves come from the element itself -- ``sourceline`` and the
    tree's own ``docinfo.URL`` -- so no loader has to thread a path
    alongside every node it walks. None when the tree was built in memory
    rather than parsed, which has no line to report.
    """
    line = element.sourceline
    url = element.getroottree().docinfo.URL
    if line is None or url is None:
        # a tree built in memory still carries a sourceline (1, for the
        # string it was parsed from) and no document at all, so reporting
        # it would name a line of a file that does not exist -- the same
        # fabrication a diagnostic past the loaders refuses
        return None
    return Span(os.path.basename(url), line)


def _port_def(definition_name: str | None, port: HirPort) -> PortDef | None:
    """One ``PortDef`` from a ``HirPort``, or None where the coordinates do
    not parse -- the one thing a port can state that has no lowering, and
    the reason this is reported rather than skipped."""
    attrs = port.params.values if port.params is not None else {}
    try:
        px, py = int(port.x), int(port.y)
    except (TypeError, ValueError):
        DIAGNOSTICS.emit(
            "port_bad_coords",
            f"{definition_name}.{attrs.get('name') or '?'}", span=port.span)
        return None
    base_name, _, suffix = (attrs.get("name") or "").partition(":")
    raw_dim = attrs.get("dim", "1")
    dim_name = suffix.strip() or None
    if dim_name is None and raw_dim and not raw_dim.lstrip("-").isdigit():
        dim_name, raw_dim = raw_dim, "0"  # conjugate-style dim attr
    return PortDef(
        name=base_name,
        x=px,
        y=py,
        condition=attrs.get("cond", "true"),
        mode=attrs.get("mode", "0"),
        electype=attrs.get("electype", "0"),
        dim=raw_dim,
        internal=(attrs.get("internal", "false") == "true"),
        dim_name=dim_name,
        datatype=attrs.get("datatype", "0"),
    )


def component_def(namespace: str, definition: HirDefinition) -> ComponentDef:
    """The ``ComponentDef`` a ``HirDefinition`` lowers to.

    A lowering and not a second front end: every field here is read off
    the HIR, which keeps strictly more of a ``<Definition>`` than a
    ``ComponentDef`` holds. Nothing is re-parsed -- a segment arrives with
    its guard tree already built and its Branch splices already assembled,
    so the tree the extractor walks is the tree the HIR made.
    """
    ports = [port for port in (_port_def(definition.name, p)
                               for p in definition.ports) if port is not None]

    defaults: dict[str, str] = {}
    units: dict[str, str] = {}
    form_parameters: list[FormParameterDef] = []
    for category in definition.form:
        for parameter in category.parameters:
            # HirFormParameter.value is None where the form states no
            # <value> element at all; a ComponentDef default is "".
            defaults[parameter.name] = (
                parameter.value if parameter.value is not None else "")
            unit = (parameter.unit or "").strip()
            if unit:
                units[(parameter.name or "").lower()] = unit
            if parameter.name:
                form_parameters.append(FormParameterDef(
                    name=parameter.name,
                    type=parameter.type,
                    unit=unit or None,
                    condition=parameter.condition,
                    category_condition=category.condition,
                ))

    writer_directives: list[tuple[str, int | None, tuple]] = []
    branch_decls: list[BranchDecl] = []
    computations_text = ""
    model_data_text = ""
    guard_trees: list[tuple[str, tuple]] = []
    for segment in definition.segments:
        seg_name = segment.name or "?"
        guard_trees.append((seg_name, segment.tree))
        if seg_name in _WRITER_SEGMENTS:
            writer_directives.extend(_writer_directives_of(segment.tree))
        elif seg_name == "Branch":
            branch_decls.extend(
                _branch_decls(segment.tree, definition.name or "?"))
        elif seg_name == "Computations":
            computations_text += segment.text + "\n"
        elif seg_name == "Model-Data":
            model_data_text += segment.text + "\n"

    # Second writer mechanism: form parameters declared intent="Output"
    # (always Real/content_type="Variable", and never also named by an
    # #OUTPUT directive). The script references them as ``$Param`` output
    # arguments directly -- e.g. intermediate.pslx duality transformers
    # writing FLUX_*/IMAG_* monitoring signals. Their form enable-conds
    # (category and parameter level) gate whether the write is active.
    # That equivalence is inferred rather than documented: honouring the
    # conds leaves no phantom writer.
    for category in definition.form:
        for parameter in category.parameters:
            if parameter.intent != "Output":
                continue
            guards = [(cond, True) for cond in
                      (category.condition, parameter.condition) if cond]
            writer_directives.append((parameter.name, 1, tuple(guards)))

    return ComponentDef(
        namespace=namespace,
        name=definition.name,
        ports=tuple(ports),
        defaults=defaults,
        writer_directives=tuple(writer_directives),
        branch_decls=tuple(branch_decls),
        computations_text=computations_text,
        model_data_text=model_data_text,
        units=units,
        guard_trees=tuple(guard_trees),
        form_parameters=tuple(form_parameters),
    )


def read_project(path: str) -> HirProject:
    """One .pscx or .pslx as the HIR: the package's only reader of either.

    Raises whatever :func:`pscx.hir.load_project` raises, because what an
    unreadable file *is* differs by caller -- a library that will not read
    and a case that will not read are different diagnostics, and neither
    belongs to the reader.

    *The unrecognized channel is silenced.* ``load_project`` banks every
    element and attribute it cannot give a meaning to and files one GAP
    per distinct kind. That channel describes a WHOLE document -- its
    canvases, its substitutions, its drawing primitives -- and the callers
    here read a document for its definitions and its drawing and claim
    nothing about the rest. Letting it through would file master.pslx's
    own findings, unchanged from run to run and about a library the case
    did not write, against every case extracted; and a case's own banked
    attributes against every extraction of it.
    ``pscx.hir.load_project`` and ``tools/unrecognized.py`` are how that
    question gets asked, per file and on purpose.

    The result is deliberately NOT retained by anything it is handed to.
    An ``HirProject`` keeps the elements it kept opaque and an lxml
    element keeps its document, so holding one holds the whole tree,
    which over many files is a large cost against a test run whose worker
    count is already a memory bound. Every caller copies out what
    it needs and lets the project go.
    """
    from pscx.hir import load_project  # pscx.hir reads spans from this module

    with DIAGNOSTICS.suppressed():
        return load_project(path, bus=DIAGNOSTICS)


def project_namespace(project: HirProject) -> str:
    """The namespace ``project``'s definitions are keyed under.

    The project's declared name, or the filename stem when it states none.
    One rule, because a canvas and the definition it belongs to have to
    land on the same key.
    """
    return project.name or os.path.splitext(project.path)[0]


def register_definitions(
    project: HirProject, registry: dict[tuple[str, str], ComponentDef]
) -> None:
    """Lower every ``<Definition>`` ``project`` states into ``registry``.

    Keyed by ``(namespace, name)`` so a project-local ``resistor`` cannot
    shadow ``master:resistor``.
    """
    if not project.definitions:
        # A library that parses and declares nothing collapses the netlist
        # exactly as an unparseable one does, and reaches that state with
        # no parse error to report it. A PSCAD library declares at least
        # one definition, so this fires only on a file that is not the
        # library it is configured to be.
        DIAGNOSTICS.emit("empty_library", f"{project.path}",
                         span=Span(project.path))
        return
    namespace = project_namespace(project)
    for definition in project.definitions:
        registry[(namespace, definition.name)] = component_def(
            namespace, definition)


def load_definitions(
    path: str, registry: dict[tuple[str, str], ComponentDef]
) -> HirProject | None:
    """Load every ``<Definition>`` from a .pslx/.pscx into ``registry``.

    Returns the project when it declared definitions, so a caller can
    build a page from it before dropping it. None when the load was
    reported: unreadable, or a document that declares nothing.

    :func:`read_project` raises on a file lxml cannot parse, because the
    HIR is not a diagnostic pass. This is a diagnostic pass. A library
    that will not read has to be named rather than thrown, so the raise
    is caught here and reported as this function's own parse failure.
    """
    try:
        project = read_project(path)
    except ET.XMLSyntaxError as exc:
        # the parser knows where it gave up; "master.pslx is broken" and
        # "master.pslx is broken at line 40195" are different diagnostics
        DIAGNOSTICS.emit("unparseable_library", f"{os.path.basename(path)}",
                         span=Span(os.path.basename(path), exc.lineno))
        return None
    except Exception:
        DIAGNOSTICS.emit("unparseable_library", f"{os.path.basename(path)}",
                         span=Span(os.path.basename(path)))
        return None

    if not project.definitions:
        register_definitions(project, registry)
        return None
    register_definitions(project, registry)
    return project


def _master_cache_path() -> str | None:
    """Where this master.pslx's parsed registry is cached, or None.

    Parsing master.pslx costs the better part of a second and a transient
    lxml tree several times the size of the file, in EVERY interpreter
    that wants it -- which a parallel test run pays once per worker and
    again in every subprocess a test spawns, and the CLI pays per
    invocation. Unpickling the finished registry costs neither: it holds
    no elements, which is also why it survives the library's tree being
    freed. The key covers the library file and the newest pscx
    source alike, so a change to what a ComponentDef holds can never
    resurrect a registry that a different loader built. None where the
    library is unreadable: that path emits a diagnostic and must keep
    reaching the parser to emit it.
    """
    try:
        library = os.stat(MASTER_PSLX)
    except OSError:
        return None
    package = os.path.dirname(os.path.abspath(__file__))
    loader = max(
        os.stat(os.path.join(package, name)).st_mtime_ns
        for name in os.listdir(package)
        if name.endswith(".py")
    )
    key = hashlib.sha256(
        f"{MASTER_PSLX}|{library.st_mtime_ns}|{library.st_size}|{loader}".encode()
    ).hexdigest()[:16]
    return os.path.join(
        os.environ.get(
            "PSCX_CACHE",
            os.path.join(tempfile.gettempdir(), f"pscx-cache-{getpass.getuser()}"),
        ),
        f"master-{key}.pickle",
    )


def _cache_master(path: str, registry: dict[tuple[str, str], ComponentDef]) -> None:
    """Write REGISTRY to PATH, and drop the entries other keys wrote.

    Written to a sibling temp name and renamed, because a parallel run
    starts many workers that each miss the cache and each write it: a
    reader must never see a half-written pickle. Superseded entries are
    pruned because the key includes a source mtime, so an editing session
    would otherwise leave one 2 MB file per edit behind.
    """
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            handle.write(pickle.dumps(registry, protocol=pickle.HIGHEST_PROTOCOL))
            staged = handle.name
        os.replace(staged, path)
        for name in os.listdir(directory):
            if name.startswith("master-") and name != os.path.basename(path):
                os.unlink(os.path.join(directory, name))
    except OSError:
        # a cache that cannot be written is a cache miss next time, never
        # a failed load -- the registry in hand is already correct
        pass


def master_project() -> HirProject:
    """master.pslx, whole and unevaluated.

    The HIR view of the library, for a caller that wants what a
    ``ComponentDef`` drops -- the form's English title, the group, the
    declared descriptions. It is the same read :func:`load_definitions`
    does, under the same policy on the unrecognized channel, which is why
    it lives here rather than beside its one consumer: master.pslx has
    exactly one reader and this module is where that is decided.

    NOT cached here, and that is measured rather than assumed. An
    ``HirProject`` retains the elements it kept opaque, an lxml element
    retains its document, and holding master's project therefore holds
    master's whole tree -- a second load into a process still holding the
    first pays its cost again instead of nothing. Under ``-n 8``, which is
    a memory bound rather than a preference, that is a bill eight workers
    each pay. So whether to hold it is the caller's decision to make and
    to justify: the extraction path does not, and wants the pickled
    registry, which holds no elements at all.
    """
    return read_project(MASTER_PSLX)


@functools.cache
def load_master() -> dict[tuple[str, str], ComponentDef]:
    cached = _master_cache_path()
    if cached:
        try:
            with open(cached, "rb") as handle:
                return pickle.load(handle)
        except (OSError, EOFError, pickle.UnpicklingError, AttributeError):
            pass
    registry: dict[tuple[str, str], ComponentDef] = {}
    if not MASTER_PSLX:
        DIAGNOSTICS.emit("unparseable_library", "PSCAD_MASTER is not set")
        return registry
    load_definitions(MASTER_PSLX, registry)
    # an empty registry is a library that did not parse, and caching it
    # would make one bad read permanent
    if cached and registry:
        _cache_master(cached, registry)
    return registry


def __getattr__(name: str):
    # ``MASTER`` is lazy: master.pslx is read on first access, not at
    # import, so pure-unit consumers (expr/units/preproc) never pay for a
    # master.pslx they do not use. load_master() is cached, so every
    # accessor sees the same registry object.
    if name == "MASTER":
        return load_master()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def resolve(
    registry: dict[tuple[str, str], ComponentDef], defn: str
) -> ComponentDef | None:
    """Resolve a ``namespace:name`` reference, falling back to a bare name."""
    namespace, _, name = defn.partition(":")
    if not name:
        namespace, name = "", namespace
    exact = registry.get((namespace, name))
    if exact is not None:
        return exact
    for (_, candidate), definition in registry.items():
        if candidate == name:
            return definition
    return None


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


#: What a layer state has to read for its members to be drawn but dead.
#: ``invisible`` is the SAME exclusion as ``disabled`` plus hiding, per the
#: PSCAD 5 manual (Layers_Pane.htm).
_DEAD_LAYER_STATES = ("disabled", "invisible")


def _layer_states(project: HirProject) -> dict[str, str]:
    """Project drawing layers: casefolded name -> casefolded state.

    ``<Layers><Layer name=".." state=".."/></Layers>`` at project level.
    States per the PSCAD 5 manual (Layers_Pane.htm): ``enabled``
    (normal), ``disabled`` (excluded from compilation and simulation),
    ``invisible`` (SAME as disabled, plus hidden), or a custom-configuration
    name (Advanced Layering -- per-component overrides we cannot evaluate;
    warned on sight). Members of disabled/invisible layers are dropped.
    """
    return {name: (layer.state or "enabled").strip().lower()
            for layer in project.layers
            for name in [(layer.name or "").strip().lower()] if name}


def _disabled(disable: str | None, layer: str | None,
              layer_states: dict[str, str] | None, span: Span | None) -> bool:
    """The out-of-service rule, over the two attributes that state it.

    Separate from its callers because a ``<User>`` and a ``<Wire>`` state
    it identically and the rule must not fork: one is a placement and the
    other is geometry, and a component silently kept alive on a dead layer
    is a netlist nothing downstream can tell apart from a correct one.
    """
    if (disable or "false").lower() == "true":
        return True
    # A component on a disabled/invisible layer is out of service.
    layer = (layer or "").strip()
    if layer:
        state = (layer_states or {}).get(layer.lower())
        if state is None:
            DIAGNOSTICS.emit("layer_undeclared", f"{layer[:30]}", span=span)
        elif state in _DEAD_LAYER_STATES:
            return True
        elif state != "enabled":
            DIAGNOSTICS.emit("layer_custom_state",
                             f"{layer[:20]}={state[:20]}", span=span)
    return False


def _is_disabled(node, layer_states: dict[str, str] | None = None) -> bool:
    """Whether a placed ``HirComponent`` or ``HirWire`` is out of service.

    Duck-typed over the two, which carry ``disable``, ``layer`` and
    ``span`` under the same names because the file states them under the
    same names.
    """
    return _disabled(node.disable, node.layer, layer_states, node.span)


def _vertices(wire: HirWire) -> list[Point]:
    """A wire's vertices in canvas coordinates: its own origin plus each
    offset the ``<vertex>`` list states.

    Both diagnostics here point at the WIRE. A ``<vertex>`` states nothing
    but two coordinates, and the model holds them as the pair rather than
    as a node with a line of its own -- so the honest span is the element
    that owns the geometry, and the code, not the line, is what separates
    a bad origin from a bad offset.
    """
    try:
        wx, wy = int(wire.x), int(wire.y)
    except (TypeError, ValueError):
        DIAGNOSTICS.emit("wire_bad_coords", span=wire.span)
        return []
    points = []
    for x, y in wire.vertices:
        try:
            points.append((wx + int(x), wy + int(y)))
        except (TypeError, ValueError):
            DIAGNOSTICS.emit("vertex_bad_coords", span=wire.span)
    return points

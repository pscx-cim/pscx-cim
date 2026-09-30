"""The source record: the design tree the source document states.

The stated-parameter surface (:mod:`pscx.surface`) carries what every
drawn element SAYS; this module carries what the source file IS built
from: the project root, each definition's own record -- its source
class, its group, its declared ports, its parameter form, its own
paramlists -- and the containment that hangs the drawn elements off
their pages. Everything here lands in the SOURCE document beside the
surface, self-contained under the same rule: no subject outside the
document is referenced, and the join to the DL document's pages is the
definition NAME, which both documents state.

**The port record is whole-copy on purpose.** The interchange document
states the declared interface of the types the equipment document
contains, because an ``emt:SignalConnection`` resolves against it. Some
port-declaring definitions are placed by nothing in their own case, so
the interchange statement can never cover the record. Here every
definition's ports are stated, each port's raw seven-key paramlist
verbatim. ``emt:ModelPort.DetailedModelTypeDynamics`` is named for
exactly this seam, one association serving a port wherever its type
lives.

**A paramlist with no form behind it gets its own row shape.** A
placement's parameters travel as ``cim:ParameterValue`` against
descriptors because a form arbitrates what they are; a project root, a
definition and a page state paramlists with no form and no type, so
they travel as ``emt:SourceParameterList`` / ``emt:SourceParameter``
rows -- names, values and order verbatim, blanks included, a stated
empty list name distinct from an absent one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import rdflib
from rdflib import RDF

from pscx.cimxml import FORM_ATTRIBUTES, payload_text
from pscx.diagnostics import Diagnostics
from pscx.dl import USER, WIRE
from pscx.mrid import mrid
from pscx.surface import DEFINITION, TYPE, _elements_of, _seed

if TYPE_CHECKING:
    from pscx.emt import EmtModel
    from pscx.hir import HirDefinition, HirProject

#: The seven keys of a ``<Port>`` paramlist, paired with the
#: ``emt:ModelPort`` attribute each is stated as. Dict order is not
#: meaning, so the record states them in this canonical order, the
#: same way a placement's paramlist comes back in the form's order.
PORT_KEYS = (
    ("name", "portName"),
    ("mode", "mode"),
    ("dim", "dimension"),
    ("datatype", "dataType"),
    ("electype", "electricalType"),
    ("internal", "internal"),
    ("cond", "condition"),
)

#: The three statement sites of an ``emt:SourceParameterList``.
PROJECT_SCOPE = "project"
DEFINITION_SCOPE = "definition"
CANVAS_SCOPE = "canvas"


def _mirror(orient) -> bool:
    """Whether one stated ``orient`` packs the x-mirror: values 4-7,
    beside the quarter turn DL carries as its rotation
    (:func:`pscx.dl.rotation_degrees`). A non-integer orient states no
    mirror, exactly as it states no rotation there."""
    try:
        return int(orient) >= 4
    except (TypeError, ValueError):
        return False


def _first_ordinal(element) -> Any:
    """The ordinal of one drawn element's ELEMENT-LEVEL subject: the
    subject that carries its stated name, its stated definition
    reference and its hosting join -- ``0`` where the element states
    paramlists, the ``"-"`` sentinel where it states none."""
    return 0 if element.lists else "-"


def _drawn_mrid(project: str, element, ordinal) -> str:
    identity = _seed(element.page, element.kind, element.ident)
    return mrid(project, "DetailedModelDynamics",
                f"drawn/{identity}/{ordinal}")


def add_source_record(emt: EmtModel, hir: HirProject,
                      descriptors: dict) -> None:
    """Write one case's source record into the source graph.

    ``descriptors`` is the surface's ``(type key, spelling) ->
    descriptor`` map, handed over so the form record lands on the very
    subjects the surface minted rather than on a parallel set. Nothing
    of ``hir`` is retained; every value is copied out as a string.
    """
    from pscx.emt import EMT, _literal, _named, _uri

    project = hir.name or hir.path

    subject = _named(emt, emt.source, mrid(project, "ModelProject",
                                           "record/project"),
                     EMT, "ModelProject", project)
    if hir.version is not None:
        emt.source.add((subject, EMT["ModelProject.sourceFormatVersion"],
                        _literal(hir.version)))
    if hir.target is not None:
        emt.source.add((subject, EMT["ModelProject.targetPlatform"],
                        _literal(hir.target)))
    emt.metrics["record: project"] += 1
    project_subject = subject

    definitions: dict[str, rdflib.URIRef] = {}
    for definition in hir.definitions:
        name = definition.name
        if not name or name in definitions:
            # the surface already diagnosed the unnamed page; a second
            # definition of one name is unreachable by that name. Both are
            # counted, never half-stated
            emt.metrics["record: definition_unstated"] += 1
            continue
        subject = _named(emt, emt.source,
                         mrid(project, "ModelDefinition",
                              f"record/{_seed(name)}"),
                         EMT, "ModelDefinition", name)
        definitions[name] = subject
        emt.source.add((subject, EMT["ModelDefinition.definitionName"],
                        _literal(name)))
        emt.source.add((subject, EMT["ModelDefinition.ModelProject"],
                        project_subject))
        if definition.classid is not None:
            emt.source.add((subject, EMT["IdentifiedObject.sourceClass"],
                            _literal(definition.classid)))
        if definition.group is not None:
            emt.source.add((subject, EMT["ModelDefinition.group"],
                            _literal(definition.group)))
        emt.metrics["record: definition"] += 1
        _add_scripts(emt, project, definition, subject)
        _add_ports(emt, project, definition, subject)
        _add_form_record(emt, project, definition, subject, descriptors)
        _add_paramlists(emt, project, DEFINITION_SCOPE, name,
                        definition.params, subject)
        if definition.canvas is not None:
            _add_paramlists(emt, project, CANVAS_SCOPE, name,
                            definition.canvas.params, subject)

    _add_paramlists(emt, project, PROJECT_SCOPE, project, hir.params,
                    project_subject)
    _add_payloads(emt, hir, project, project_subject, definitions,
                  descriptors)
    _add_substitutions(emt, hir, project, project_subject)
    _add_containment(emt, hir, project, definitions, _uri)


def _add_scripts(emt, project: str, definition: HirDefinition,
                 subject) -> None:
    """One ``emt:ModelScript`` per declared segment, the text verbatim.

    Verbatim includes empty: a segment can be declared with no text,
    and a definition that declares an empty segment is not one
    that declares none -- ``source`` is stated on every one, ``""``
    included, which is why the shape puts no minLength on it. The walk
    is over ``hir.definitions`` of the case file itself, so master's
    definitions travel as names on model types and never as bodies.
    """
    from pscx.emt import EMT, _literal, _named

    for sequence, segment in enumerate(definition.segments, start=1):
        if segment.name is None:
            # a nameless segment states no place in the definition's
            # script, and is counted
            emt.metrics["record: segment_unnamed"] += 1
            continue
        script = _named(
            emt, emt.source,
            mrid(project, "ModelScript",
                 f"record/{_seed(definition.name)}/segment/{sequence}"),
            EMT, "ModelScript", segment.name)
        emt.source.add((script, EMT["ModelScript.ModelDefinition"], subject))
        emt.source.add((script, EMT["ModelScript.segmentName"],
                        _literal(segment.name)))
        emt.source.add((script, EMT["ModelScript.sequenceNumber"],
                        _literal(sequence)))
        emt.source.add((script, EMT["ModelScript.source"],
                        _literal(segment.text)))
        emt.metrics["record: script"] += 1


def _add_ports(emt, project: str, definition: HirDefinition,
               subject) -> None:
    """One ``emt:ModelPort`` per declared port, the raw paramlist
    verbatim. A port with no stated name cannot be resolved against and
    is counted rather than emitted nameless.

    ``sourceIdentifier`` is the port's own element id, the join to the
    DL DiagramObject that carries the port's geometry under the same
    id. Ids are draw-order accidents, not sequence, so without this
    term the two halves of a multi-port declaration pair only by
    accident; a port that states no id is identified by its ordinal in
    both documents and states none here either."""
    from pscx.emt import EMT, _literal, _named

    for sequence, port in enumerate(definition.ports, start=1):
        values = port.params.values if port.params is not None else {}
        if port.params is not None and port.params.rows:
            raise ValueError(
                f"a port paramlist of {definition.name!r} states matrix "
                f"rows this record cannot carry")
        if not values.get("name"):
            emt.metrics["record: port_unnamed"] += 1
            continue
        port_subject = _named(
            emt, emt.source,
            mrid(project, "ModelPort",
                 f"record/{_seed(definition.name)}/port/{sequence}"),
            EMT, "ModelPort", values["name"])
        emt.source.add((port_subject,
                        EMT["ModelPort.DetailedModelTypeDynamics"], subject))
        emt.source.add((port_subject, EMT["ModelPort.sequenceNumber"],
                        _literal(sequence)))
        if port.id is not None:
            emt.source.add((port_subject, EMT["ModelPort.sourceIdentifier"],
                            _literal(port.id)))
        for key, attribute in PORT_KEYS:
            if key in values:
                emt.source.add((port_subject, EMT[f"ModelPort.{attribute}"],
                                _literal(values[key])))
        emt.metrics["record: port"] += 1


def _add_form_record(emt, project: str, definition: HirDefinition,
                     subject, descriptors: dict) -> None:
    """The form's own structure: one ``emt:ParameterCategory`` per
    declared category, and the eight attributes standard CIM has no
    place for on each declared descriptor, plus its category join.

    The descriptor of a spelling is the FIRST declaration's, exactly as
    :func:`pscx.surface._declared_form` resolves it.
    """
    from pscx.emt import EMT, _literal, _named

    type_key = (DEFINITION, definition.name)
    seen: set[str] = set()
    for sequence, category in enumerate(definition.form, start=1):
        if category.name is None:
            emt.metrics["record: category_unnamed"] += 1
            continue
        category_subject = _named(
            emt, emt.source,
            mrid(project, "ParameterCategory",
                 f"record/{_seed(definition.name)}/category/{sequence}"),
            EMT, "ParameterCategory", category.name)
        emt.source.add((category_subject,
                        EMT["ParameterCategory.ModelDefinition"], subject))
        emt.source.add((category_subject,
                        EMT["ParameterCategory.sequenceNumber"],
                        _literal(sequence)))
        if category.visible is not None:
            emt.source.add((category_subject,
                            EMT["ParameterCategory.visible"],
                            _literal(category.visible)))
        if category.condition is not None:
            emt.source.add((category_subject,
                            EMT["ParameterCategory.condition"],
                            _literal(category.condition)))
        emt.metrics["record: category"] += 1
        for parameter in category.parameters:
            spelling = parameter.name
            if spelling is None or spelling in seen:
                continue
            seen.add(spelling)
            descriptor = descriptors[(type_key, spelling)]
            emt.source.add((descriptor,
                            EMT["ParameterDescriptor.ParameterCategory"],
                            category_subject))
            for field, attribute in FORM_ATTRIBUTES:
                value = getattr(parameter, field)
                if value is not None:
                    emt.source.add(
                        (descriptor, EMT[f"ParameterDescriptor.{attribute}"],
                         _literal(value)))
            emt.metrics["record: form_parameter"] += 1


def _add_paramlists(emt, project: str, scope: str, owner_label: str,
                    paramlists, owner) -> None:
    """One ``emt:SourceParameterList`` per stated paramlist of one owner
    and scope, entries verbatim in the order the source wrote them."""
    from pscx.emt import EMT, _literal, _named

    for sequence, params in enumerate(paramlists, start=1):
        if params.rows:
            # matrix rows travel as emt:MatrixRow beside the stated
            # surface's ParameterValue; no project, definition or canvas
            # paramlist states any, and one that did would be silently
            # dropped here -- the defect the surface term closed
            raise ValueError(
                f"a {scope} paramlist of {owner_label!r} states matrix "
                f"rows this record cannot carry: {sorted(params.rows)}")
        seed = f"record/plist/{scope}/{_seed(owner_label)}/{sequence}"
        subject = _named(emt, emt.source,
                         mrid(project, "SourceParameterList", seed),
                         EMT, "SourceParameterList",
                         f"plist/{scope}/{sequence}")
        emt.source.add((subject, EMT["SourceParameterList.IdentifiedObject"],
                        owner))
        emt.source.add((subject, EMT["SourceParameterList.scope"],
                        _literal(scope)))
        emt.source.add((subject, EMT["SourceParameterList.sequenceNumber"],
                        _literal(sequence)))
        if params.name is not None:
            emt.source.add((subject,
                            EMT["SourceParameterList.parameterSetName"],
                            _literal(params.name)))
        emt.metrics["record: paramlist"] += 1
        for position, (key, value) in enumerate(params.values.items(),
                                                start=1):
            entry = emt.mint(emt.source,
                             mrid(project, "SourceParameter",
                                  f"{seed}/{_seed(key)}"),
                             EMT, "SourceParameter")
            emt.source.add((entry, EMT["SourceParameter.SourceParameterList"],
                            subject))
            emt.source.add((entry, EMT["SourceParameter.parameterName"],
                            _literal(key)))
            emt.source.add((entry, EMT["SourceParameter.value"],
                            _literal(value)))
            emt.source.add((entry, EMT["SourceParameter.sequenceNumber"],
                            _literal(position)))
            emt.metrics["record: parameter"] += 1


def _add_payloads(emt, hir: HirProject, project: str, project_subject,
                  definitions: dict, descriptors: dict) -> None:
    """One ``emt:ToolPayload`` per OPAQUE subtree, content verbatim.

    The owner is the record subject the fragment belongs back under: the
    ModelProject for a root fragment, the ModelDefinition for a
    definition's or its page's (the payloadKind decides which parent tag
    a writer reinserts under, one parent per kind),
    the surface's declared descriptor for a form parameter's.
    ``sequenceNumber`` is the fragment's 1-based position among ALL its
    parent element's children, because that is the index a writer
    reinserts at, modeled siblings included.
    """
    counters: dict[Any, int] = {}

    def emit(item, owner, seed_base: str) -> None:
        from pscx.emt import EMT, _literal, _named

        parent = item.element.getparent()
        if parent is None:
            emt.metrics["record: payload_unparented"] += 1
            return
        ordinal = counters.get(seed_base, 0) + 1
        counters[seed_base] = ordinal
        subject = _named(emt, emt.source,
                         mrid(project, "ToolPayload",
                              f"{seed_base}/{ordinal}"),
                         EMT, "ToolPayload", item.tag)
        emt.source.add((subject, EMT["ToolPayload.IdentifiedObject"],
                        owner))
        emt.source.add((subject, EMT["ToolPayload.payloadKind"],
                        _literal(item.tag)))
        emt.source.add((subject, EMT["ToolPayload.sequenceNumber"],
                        _literal(parent.index(item.element) + 1)))
        emt.source.add((subject, EMT["ToolPayload.content"],
                        _literal(payload_text(item.element))))
        emt.metrics["record: payload"] += 1

    for item in hir.opaque:
        emit(item, project_subject, "record/payload/project")
    for definition in hir.definitions:
        owner = definitions.get(definition.name)
        if owner is None:
            emt.metrics["record: payload_unowned"] += len(definition.opaque)
            continue
        base = f"record/payload/{_seed(definition.name)}"
        for item in definition.opaque:
            emit(item, owner, base)
        if definition.canvas is not None:
            for item in definition.canvas.opaque:
                emit(item, owner, base)
        type_key = (DEFINITION, definition.name)
        seen: set[str] = set()
        for category in definition.form:
            for parameter in category.parameters:
                spelling = parameter.name
                if spelling is None or spelling in seen:
                    # a nameless or shadowed declaration has no
                    # descriptor to belong under, and is counted
                    emt.metrics["record: payload_unowned"] += len(
                        parameter.opaque)
                    continue
                seen.add(spelling)
                if not parameter.opaque:
                    continue
                descriptor = descriptors[(type_key, spelling)]
                parameter_base = f"{base}/{_seed(spelling)}"
                for item in parameter.opaque:
                    emit(item, descriptor, parameter_base)


def _add_substitutions(emt, hir: HirProject, project: str,
                       project_subject) -> None:
    """One ``emt:Substitution`` per ``<Sub>``, in table order.

    The Sub's paramlist states name, value and, nearly always, a
    ``group`` and a ``cat``. Name, value and group are data and travel
    verbatim; ``cat`` is the empty string on every Sub that states it,
    restated by the pinned rule rather than carried, exactly like the
    container itself: a 5.0-era project states one
    ``GlobalSubstitutions`` container of fixed shape and a 4.5-era one
    states none, a rule the carried sourceFormatVersion resolves.
    """
    from pscx.emt import EMT, _literal, _named

    ordinal = 0
    for container in hir.substitutions:
        for sub in container.subs:
            declared = sub.declared
            if declared is None:
                # a Sub with no name declares nothing $() can reference,
                # and is counted
                emt.metrics["record: substitution_unstated"] += 1
                continue
            name, value = declared
            ordinal += 1
            subject = _named(emt, emt.source,
                             mrid(project, "Substitution",
                                  f"record/sub/{ordinal}"),
                             EMT, "Substitution", name)
            emt.source.add((subject, EMT["Substitution.ModelProject"],
                            project_subject))
            emt.source.add((subject, EMT["Substitution.value"],
                            _literal(value)))
            emt.source.add((subject, EMT["Substitution.sequenceNumber"],
                            _literal(ordinal)))
            group = next((params.values["group"] for params in sub.params
                          if "group" in params.values), None)
            if group is not None:
                emt.source.add((subject, EMT["Substitution.group"],
                                _literal(group)))
            emt.metrics["record: substitution"] += 1


def _add_containment(emt, hir: HirProject, project: str,
                     definitions: dict, _uri) -> None:
    """The design tree's downward joins, on the surface's own subjects.

    ``ContainingDefinition`` on every drawn subject -- the structural
    form of the page the name grammar already states, the anchor the
    surface module promised. On the element-level subject alone: the
    verbatim ``defn`` reference wherever the source states one, and the
    hosting join from a hosted device to the wire it sits inside.
    """
    from pscx.emt import EMT, _literal

    elements = _elements_of(hir, Diagnostics())
    subjects: dict[tuple, rdflib.URIRef] = {}
    for element in elements:
        container = definitions.get(element.page)
        if container is None:
            continue
        ordinals = list(range(len(element.lists))) if element.lists \
            else ["-"]
        for ordinal in ordinals:
            emt.source.add(
                (_uri(_drawn_mrid(project, element, ordinal)),
                 EMT["DetailedModelDynamics.ContainingDefinition"],
                 container))
        key = (element.page, element.kind, element.ident)
        subjects[key] = _uri(_drawn_mrid(project, element,
                                         _first_ordinal(element)))
        if element.defn is not None:
            emt.source.add(
                (subjects[key],
                 EMT["DetailedModelDynamics.definitionReference"],
                 _literal(element.defn)))
            emt.metrics["record: definition_reference"] += 1

    for definition in hir.definitions:
        if not definition.name or definition.canvas is None:
            continue
        for drawn in list(definition.canvas.components) + [
                hosted for wire in definition.canvas.wires
                for hosted in wire.hosted]:
            if not _mirror(drawn.orient):
                continue
            subject = subjects.get((definition.name, USER, str(drawn.id)))
            if subject is None:
                emt.metrics["record: mirror_uncarried"] += 1
                continue
            emt.source.add((subject, EMT["DetailedModelDynamics.mirrored"],
                            _literal(True)))
            emt.metrics["record: mirrored"] += 1
        for index, wire in enumerate(definition.canvas.wires):
            if _mirror(wire.orient):
                # a mirrored wire the carry
                # rule omitted would have nowhere to state the bit and
                # is counted rather than silently unreflected
                wire_key = (definition.name, WIRE,
                            str(wire.id if wire.id is not None
                                else f"#{index}"))
                subject = subjects.get(wire_key)
                if subject is None:
                    emt.metrics["record: mirror_uncarried"] += 1
                else:
                    emt.source.add(
                        (subject, EMT["DetailedModelDynamics.mirrored"],
                         _literal(True)))
                    emt.metrics["record: mirrored"] += 1
            if not wire.hosted:
                continue
            ident = wire.id if wire.id is not None else f"#{index}"
            host = subjects.get((definition.name, WIRE, str(ident)))
            if host is None:
                # a hosting wire is always carried by the surface: every
                # one states a name or a paramlist, and the carry rule
                # includes a stated defn. So this is a defect counter,
                # not a population
                emt.metrics["record: hosting_wire_uncarried"] += 1
                continue
            for hosted in wire.hosted:
                hosted_subject = subjects.get(
                    (definition.name, USER, str(hosted.id)))
                if hosted_subject is None:
                    emt.metrics["record: hosted_uncarried"] += 1
                    continue
                emt.source.add((hosted_subject,
                                EMT["DetailedModelDynamics.HostingWire"],
                                host))
                emt.metrics["record: hosted"] += 1


# --------------------------------------------------------------------------
# The inverse: the emitted source document -> the source record
# --------------------------------------------------------------------------


def stated_record(hir: HirProject) -> dict:
    """What the HIR states, in the shape :func:`reconstruct_record`
    returns."""
    project = {
        "name": hir.name or hir.path,
        "version": hir.version,
        "target": hir.target,
    }
    definitions: dict[str, dict] = {}
    for definition in hir.definitions:
        name = definition.name
        if not name or name in definitions:
            continue
        seen: set[str] = set()
        categories = []
        parameters = {}
        for category in definition.form:
            spellings = []
            for parameter in category.parameters:
                spelling = parameter.name
                if spelling is None or spelling in seen:
                    continue
                seen.add(spelling)
                spellings.append(spelling)
                parameters[spelling] = tuple(
                    getattr(parameter, field)
                    for field, _attribute in FORM_ATTRIBUTES)
            if category.name is None:
                continue
            categories.append((category.name, category.visible,
                               category.condition, tuple(spellings)))
        definitions[name] = {
            "sourceClass": definition.classid,
            "group": definition.group,
            "scripts": tuple((segment.name, segment.text)
                             for segment in definition.segments
                             if segment.name is not None),
            "ports": tuple(
                (port.id,
                 tuple((port.params.values if port.params is not None
                        else {}).get(key) for key, _a in PORT_KEYS))
                for port in definition.ports
                if port.params is not None
                and port.params.values.get("name")),
            "categories": tuple(categories),
            "parameters": parameters,
            "paramlists": _stated_lists(definition.params),
            "canvas_paramlists": _stated_lists(
                definition.canvas.params
                if definition.canvas is not None else []),
        }
    payloads: dict[tuple, list] = {}

    def state_payload(owner_key: tuple, item) -> None:
        from pscx.write import canonicalize

        parent = item.element.getparent()
        if parent is None:
            return
        payloads.setdefault(owner_key, []).append(
            (item.tag, parent.index(item.element) + 1,
             canonicalize(item.element)))

    for item in hir.opaque:
        state_payload(("project",), item)
    for definition in hir.definitions:
        name = definition.name
        if not name or name not in definitions:
            continue
        for item in definition.opaque:
            state_payload(("definition", name), item)
        if definition.canvas is not None:
            for item in definition.canvas.opaque:
                state_payload(("definition", name), item)
        seen_spellings: set[str] = set()
        for category in definition.form:
            for parameter in category.parameters:
                spelling = parameter.name
                if spelling is None or spelling in seen_spellings:
                    continue
                seen_spellings.add(spelling)
                for item in parameter.opaque:
                    state_payload(("parameter", name, spelling), item)

    substitutions = []
    for container in hir.substitutions:
        for sub in container.subs:
            declared = sub.declared
            if declared is None:
                continue
            group = next((params.values["group"] for params in sub.params
                          if "group" in params.values), None)
            substitutions.append((declared[0], declared[1], group))

    mirrored: dict[tuple, bool] = {}
    for definition in hir.definitions:
        if not definition.name or definition.name not in definitions \
                or definition.canvas is None:
            continue
        for drawn in list(definition.canvas.components) + [
                hosted for wire in definition.canvas.wires
                for hosted in wire.hosted]:
            if _mirror(drawn.orient):
                mirrored[(definition.name, USER, str(drawn.id))] = True
        for index, wire in enumerate(definition.canvas.wires):
            if _mirror(wire.orient) and (
                    wire.name or wire.params or wire.defn is not None):
                ident = wire.id if wire.id is not None else f"#{index}"
                mirrored[(definition.name, WIRE, str(ident))] = True

    out = {
        "project": project,
        "definitions": definitions,
        "project_paramlists": _stated_lists(hir.params),
        "substitutions": tuple(substitutions),
        "mirrored": mirrored,
        "payloads": {key: tuple(sorted(rows, key=lambda r: (r[1], r[0])))
                     for key, rows in payloads.items()},
        "containment": {},
        "references": {},
        "hosting": {},
    }
    for element in _elements_of(hir, Diagnostics()):
        if element.page not in definitions:
            continue
        key = (element.page, element.kind, element.ident)
        out["containment"][key] = element.page
        out["references"][key] = element.defn
    for definition in hir.definitions:
        if not definition.name or definition.canvas is None:
            continue
        for index, wire in enumerate(definition.canvas.wires):
            ident = wire.id if wire.id is not None else f"#{index}"
            for hosted in wire.hosted:
                out["hosting"][(definition.name, USER, str(hosted.id))] = \
                    (definition.name, WIRE, str(ident))
    return out


def _stated_lists(paramlists) -> tuple:
    return tuple((params.name, tuple(params.values.items()))
                 for params in paramlists)


def reconstruct_record(graph: rdflib.Graph) -> dict:
    """Rebuild the source record from an emitted document ALONE.

    Nothing here reads the HIR or the builder; joins resolve by IRI
    within the document and drawn subjects by the surface's reserved
    name grammar, and a subject that breaks either raises rather than
    being skipped.
    """
    from pscx.emt import CIM, EMT

    name_of = CIM["IdentifiedObject.name"]

    def text(subject, predicate) -> str | None:
        value = graph.value(subject, predicate)
        return None if value is None else str(value)

    projects = list(graph.subjects(RDF.type, EMT["ModelProject"]))
    if len(projects) > 1:
        raise ValueError(f"{len(projects)} ModelProject subjects in one "
                         f"document")
    project_subject = projects[0] if projects else None
    project = {
        "name": text(project_subject, name_of),
        "version": text(project_subject,
                        EMT["ModelProject.sourceFormatVersion"]),
        "target": text(project_subject, EMT["ModelProject.targetPlatform"]),
    }

    definitions: dict[str, dict] = {}
    of_subject: dict[Any, str] = {}
    for subject in graph.subjects(RDF.type, EMT["ModelDefinition"]):
        name = text(subject, EMT["ModelDefinition.definitionName"])
        if name is None or name in definitions:
            raise ValueError(f"a ModelDefinition states no usable name: "
                             f"{subject}")
        if graph.value(subject, EMT["ModelDefinition.ModelProject"]) \
                != project_subject:
            raise ValueError(f"definition {name!r} joins a project the "
                             f"document does not state")
        of_subject[subject] = name
        definitions[name] = {
            "sourceClass": text(subject, EMT["IdentifiedObject.sourceClass"]),
            "group": text(subject, EMT["ModelDefinition.group"]),
            "scripts": (), "ports": (), "categories": (), "parameters": {},
            "paramlists": (), "canvas_paramlists": (),
        }

    scripts: dict[str, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ModelScript"]):
        owner = graph.value(subject, EMT["ModelScript.ModelDefinition"])
        if owner not in of_subject:
            raise ValueError("a script joins a definition the document "
                             "does not state")
        scripts.setdefault(of_subject[owner], []).append((
            int(graph.value(subject, EMT["ModelScript.sequenceNumber"])),
            text(subject, EMT["ModelScript.segmentName"]),
            text(subject, EMT["ModelScript.source"]),
        ))
    for name, entries in scripts.items():
        definitions[name]["scripts"] = tuple(
            (segment, source) for _sequence, segment, source
            in sorted(entries))

    ports: dict[str, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ModelPort"]):
        owner = graph.value(subject,
                            EMT["ModelPort.DetailedModelTypeDynamics"])
        if owner not in of_subject:
            continue
        sequence = int(graph.value(subject, EMT["ModelPort.sequenceNumber"]))
        ports.setdefault(of_subject[owner], []).append(
            (sequence, text(subject, EMT["ModelPort.sourceIdentifier"]),
             tuple(text(subject, EMT[f"ModelPort.{attribute}"])
                   for _k, attribute in PORT_KEYS)))
    for name, entries in ports.items():
        definitions[name]["ports"] = tuple(
            (identifier, row)
            for _sequence, identifier, row in sorted(entries))

    categories: dict[Any, tuple] = {}
    grouped: dict[str, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ParameterCategory"]):
        owner = graph.value(subject, EMT["ParameterCategory.ModelDefinition"])
        if owner not in of_subject:
            continue
        sequence = int(graph.value(subject,
                                   EMT["ParameterCategory.sequenceNumber"]))
        entry = (text(subject, name_of),
                 text(subject, EMT["ParameterCategory.visible"]),
                 text(subject, EMT["ParameterCategory.condition"]))
        categories[subject] = (of_subject[owner], sequence)
        grouped.setdefault(of_subject[owner], []).append((sequence, subject,
                                                          entry))

    members: dict[Any, list] = {}
    for descriptor in graph.subjects(
            RDF.type, CIM["ParameterDescriptor"]):
        category = graph.value(descriptor,
                               EMT["ParameterDescriptor.ParameterCategory"])
        if category is None:
            continue
        if category not in categories:
            raise ValueError("a descriptor joins a category the document "
                             "does not state")
        owner_name, _sequence = categories[category]
        spelling = text(descriptor, name_of)
        position = graph.value(descriptor,
                               CIM["ParameterDescriptor.sequenceNumber"])
        members.setdefault(category, []).append(
            (int(str(position)) if position is not None else 0, spelling))
        definitions[owner_name]["parameters"][spelling] = tuple(
            text(descriptor, EMT[f"ParameterDescriptor.{attribute}"])
            for _f, attribute in FORM_ATTRIBUTES)
    for name, entries in grouped.items():
        definitions[name]["categories"] = tuple(
            entry + (tuple(spelling for _p, spelling
                           in sorted(members.get(subject, ()))),)
            for _sequence, subject, entry in sorted(
                entries, key=lambda item: item[0]))

    lists: dict[Any, dict] = {}
    for subject in graph.subjects(RDF.type, EMT["SourceParameterList"]):
        owner = graph.value(subject,
                            EMT["SourceParameterList.IdentifiedObject"])
        scope = text(subject, EMT["SourceParameterList.scope"])
        if owner == project_subject:
            if scope != PROJECT_SCOPE:
                raise ValueError(f"a {scope!r} list joins the project root")
            key = (PROJECT_SCOPE, None)
        elif owner in of_subject:
            key = (scope, of_subject[owner])
        else:
            raise ValueError("a paramlist joins an owner the document does "
                             "not state")
        lists.setdefault(key, {})[subject] = (
            int(graph.value(subject,
                            EMT["SourceParameterList.sequenceNumber"])),
            text(subject, EMT["SourceParameterList.parameterSetName"]),
        )
    entries: dict[Any, list] = {}
    for subject in graph.subjects(RDF.type, EMT["SourceParameter"]):
        owner = graph.value(subject,
                            EMT["SourceParameter.SourceParameterList"])
        entries.setdefault(owner, []).append((
            int(graph.value(subject, EMT["SourceParameter.sequenceNumber"])),
            text(subject, EMT["SourceParameter.parameterName"]),
            text(subject, EMT["SourceParameter.value"]),
        ))

    def rebuilt_lists(key) -> tuple:
        rows = sorted(((sequence, name, subject) for subject, (sequence, name)
                       in lists.get(key, {}).items()))
        return tuple(
            (name, tuple((k, v) for _s, k, v
                         in sorted(entries.get(subject, ()))))
            for _sequence, name, subject in rows)

    for name, definition in definitions.items():
        definition["paramlists"] = rebuilt_lists(
            (DEFINITION_SCOPE, name))
        definition["canvas_paramlists"] = rebuilt_lists(
            (CANVAS_SCOPE, name))

    from urllib.parse import unquote

    from lxml import etree as ET

    from pscx.write import canonicalize

    descriptor_key: dict[Any, tuple] = {}
    for descriptor in graph.subjects(RDF.type, CIM["ParameterDescriptor"]):
        owner_type = graph.value(
            descriptor,
            CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"])
        type_name = str(graph.value(owner_type, name_of) or "")
        if not type_name.startswith(f"{TYPE}/"):
            continue
        parts = type_name.split("/")
        if len(parts) != 3:
            raise ValueError(f"reserved name breaks the grammar: "
                             f"{type_name!r}")
        if unquote(parts[1]) != DEFINITION:
            continue
        descriptor_key[descriptor] = ("parameter", unquote(parts[2]),
                                      str(graph.value(descriptor, name_of)))

    payloads: dict[tuple, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ToolPayload"]):
        owner = graph.value(subject, EMT["ToolPayload.IdentifiedObject"])
        if owner == project_subject:
            key = ("project",)
        elif owner in of_subject:
            key = ("definition", of_subject[owner])
        elif owner in descriptor_key:
            key = descriptor_key[owner]
        else:
            raise ValueError("a payload joins an owner the document does "
                             "not state")
        kind = text(subject, EMT["ToolPayload.payloadKind"])
        sequence = int(graph.value(subject,
                                   EMT["ToolPayload.sequenceNumber"]))
        content = text(subject, EMT["ToolPayload.content"])
        payloads.setdefault(key, []).append(
            (kind, sequence, canonicalize(ET.fromstring(content))))

    containment: dict[tuple, str] = {}
    references: dict[tuple, str | None] = {}
    hosting: dict[tuple, tuple] = {}
    mirrored: dict[tuple, bool] = {}
    element_of: dict[Any, tuple] = {}
    for subject, target in graph.subject_objects(
            EMT["DetailedModelDynamics.ContainingDefinition"]):
        key = _parse_drawn(str(graph.value(subject, name_of) or ""))
        if target not in of_subject:
            raise ValueError(f"{key} is contained by a definition the "
                             f"document does not state")
        stated = containment.setdefault(key[:3], of_subject[target])
        if stated != of_subject[target]:
            raise ValueError(f"{key[:3]} is contained by two definitions")
        if key[3] in ("0", "-"):
            element_of[subject] = key[:3]
    for key3 in containment:
        references[key3] = None
    for subject, value in graph.subject_objects(
            EMT["DetailedModelDynamics.definitionReference"]):
        references[element_of[subject]] = str(value)
    for subject, target in graph.subject_objects(
            EMT["DetailedModelDynamics.HostingWire"]):
        hosting[element_of[subject]] = element_of[target]
    for subject, value in graph.subject_objects(
            EMT["DetailedModelDynamics.mirrored"]):
        if str(value) == "true":
            mirrored[element_of[subject]] = True

    substitutions = []
    for subject in graph.subjects(RDF.type, EMT["Substitution"]):
        if graph.value(subject, EMT["Substitution.ModelProject"]) \
                != project_subject:
            raise ValueError("a substitution joins a project the document "
                             "does not state")
        substitutions.append((
            int(graph.value(subject, EMT["Substitution.sequenceNumber"])),
            text(subject, name_of),
            text(subject, EMT["Substitution.value"]),
            text(subject, EMT["Substitution.group"]),
        ))

    return {
        "project": project,
        "definitions": definitions,
        "project_paramlists": rebuilt_lists((PROJECT_SCOPE, None)),
        "substitutions": tuple((name, value, group) for _s, name, value,
                               group in sorted(substitutions)),
        "mirrored": mirrored,
        "payloads": {key: tuple(sorted(rows, key=lambda r: (r[1], r[0])))
                     for key, rows in payloads.items()},
        "containment": containment,
        "references": references,
        "hosting": hosting,
    }


def _parse_drawn(name: str) -> tuple:
    """``drawn/<page>/<kind>/<ident>/<n>`` -> the unquoted four-tuple,
    raising on a name that breaks the grammar -- a defect, never a
    skip."""
    from urllib.parse import unquote

    parts = name.split("/")
    if len(parts) != 5 or parts[0] != "drawn":
        raise ValueError(f"not a drawn-subject name: {name!r}")
    page, kind, ident, n = (unquote(part) for part in parts[1:])
    if kind not in (USER, WIRE):
        raise ValueError(f"reserved name states no drawn kind: {name!r}")
    return (page, kind, ident, n)

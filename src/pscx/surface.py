"""The stated-parameter surface: what every drawn element says, verbatim.

Every ``<User>`` and every named or parametered ``<Wire>`` on every
definition page becomes a ``cim:DetailedModelDynamics`` in the SOURCE
document, one per paramlist, typed by an ``emt:LibraryModelType`` and
carrying one ``cim:ParameterValue`` per entry the file states -- blanks
included, because a paramlist that states ``Vmax=""`` and one that does
not mention ``Vmax`` are different files. What a parameter IS travels on
the type: a ``cim:ParameterDescriptor`` per spelling, and for a
definition the case itself authors, the form's own name, description,
unit and default ride the descriptor as ``cim:IdentifiedObject.name``,
``.description``, ``cim:ParameterDescriptor.engineeringUnit`` and
``.typicalValue``. Every term is standard CIM or already published by
the emt: vocabulary; nothing here declares anything.

**The surface travels in its own document.** The interchange documents
carry what an engine that cannot read ``.pscx`` needs; this one carries
the verbatim stated text of the source file, whole-copy. A stated value
that happens to equal an evaluated interchange literal is stated here
anyway: delta-encoding it away would make this document's content a
function of interchange internals, so every interchange change would
silently change which values are omitted and force the inverse to be
revalidated across case-folding, instancing and ``$()`` substitution.
The CONTENT is self-contained -- :func:`reconstruct_surface` reads this
one document and nothing else -- and the document makes exactly one
class of outward reference: ``emt:DetailedModelDynamics.IdentifiedObject``
joins each drawn statement to the interchange subject(s) it is exchanged
as, which is what lets a checker pair a stated text with the engine
values that restate it without guessing through stated names (stated
names collide).

**This surface is per DRAWN ELEMENT, not per instance.** The same
structural fact that shaped :mod:`pscx.dl` decides it: a definition is
drawn once and placed any number of times, sometimes none. Three facts
rule the per-instance alternative out, whether it attaches values to
the flattened placements that already carry detailed models or to
mapped equipment through the standard
``cim:DetailedModelDynamics.Equipment`` association:

- the extractor substitutes ``$(...)`` globals into placement
  parameters at parse time, so a flattened value is not the text the
  file states wherever a global is referenced;
- whole pages are drawn that ``flatten()`` never instantiates, so a
  per-instance surface cannot reach their elements at all;
- instancing multiplies the drawn placements severalfold, so
  per-instance subjects would state every reused page's parameters
  once per reuse and still cover less.

``DetailedModelDynamics.Equipment`` is therefore left unwritten: it is a
standard property and stays available, but equipment is per instance and
this surface is per drawing, so pointing an element's subject at one
arbitrary instance's equipment would be false for the others -- the same
argument that leaves ``cim:DiagramObject.IdentifiedObject`` stated
nowhere in the DL document. The per-instance join that IS stated is
``emt:DetailedModelDynamics.IdentifiedObject``, whose declared
multiplicity is 0..n precisely so that a drawn statement can name every
flat instance it is exchanged as -- checked against the core XMI, which
carries no such association at all (Equipment, DynamicsFunctionBlock,
ParameterValue and DetailedModelTypeDynamics are the only core ends on
the class), so the profile's own declaration governs and "many, not
one" is its stated semantics.

**Identity lives in the name, exactly as in DL.** An mRID is one-way, so
each subject's ``cim:IdentifiedObject.name`` is its identity string:
``drawn/<page>/<kind>/<ident>/<n>`` for the n-th paramlist of a drawn
element, ``type/...`` for a model type, every segment percent-quoted.
The two documents therefore name one drawn element identically, and the
join to the page becomes structural when the design hierarchy lands
(``emt:DetailedModelDynamics.ContainingDefinition`` has that anchor).
The prefixes are reserved: a detailed model is named after a component,
and a stated component name is user text that this grammar must not
collide with.

**The descriptor set is keyed by SPELLING here, not case-folded.** The
EMT document's hoist folds case because two spellings of one name are one
parameter to the solver; this surface is the file's own
text, where a paramlist can state a name in two cases as two
entries, so folding them would merge two stated
values onto one seed. The union rule itself is the hoist's, unchanged:
descriptors are hoisted onto the type, the set is the union over the
type's placements plus the form's own declarations, and a form position
is a property of the type -- declared descriptors carry their
``sequenceNumber``, extras carry none, which is also how the inverse
tells a form apart from what placements merely state.

The measured populations -- entries, subjects, descriptors, growth --
are pinned by ``tests/test_surface.py``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import unquote

import rdflib
from rdflib import RDF

from pscx.dl import USER, WIRE, _seed

if TYPE_CHECKING:
    from pscx.emt import EmtModel
    from pscx.hir import HirDefinition, HirProject

#: md:Model.profile of the source document. A sibling of the two
#: interchange profile URIs under the same authority (see
#: :data:`pscx.emt.EMT_PROFILE_URI`), same vocabulary namespace: only the
#: document is its own. Like the study document it declares no
#: DependentOn -- the surface references no subject outside itself, an
#: emptiness queried over the emitted graph rather than inherited from
#: the EQ-dependency default.
EMT_SOURCE_PROFILE_URI = "https://w3id.org/pscx-cim/ns/CIM/EMTSource/1.0"

#: The reserved first segment of every subject name this module mints.
#: DL's names are ``"<kind> <ident>"`` and a detailed model's name is the
#: component's own, so these two prefixes are what keeps the name spaces
#: apart. The surface has a document of its own.
DRAWN = "drawn"
TYPE = "type"

#: Tags of the three kinds of model type this surface mints. A
#: definition name, a library reference and a lone element are different
#: kinds of identifier, and only the tag keeps their seed spaces apart.
DEFINITION = "definition"
LIBRARY = "library"
ELEMENT = "element"


class _Element:
    """One drawn element and the raw paramlists it states."""

    def __init__(self, page: str, kind: str, ident: str, name: str | None,
                 defn: str | None, lists: list) -> None:
        self.page = page
        self.kind = kind
        self.ident = ident
        #: The element's own stated ``name`` attribute -- NOT the ``Name``
        #: parameter, which most placements state differently, so neither
        #: can stand in for the other.
        self.name = name
        self.defn = defn
        #: ``[{spelling: value}]`` per ``<paramlist>``, in document order,
        #: blanks kept: the list structure is part of what the file says
        #: (a plot page states xmin/xmax and ymin/ymax once per graph).
        self.lists = [dict(entry.values) for entry in lists]
        #: ``[{spelling: (row, ...)}]`` parallel to ``lists``: the matrix
        #: rows a stated parameter carries, verbatim and in row order.
        self.rows = [dict(entry.rows) for entry in lists]
        self.type_key: tuple = ()


def _resolve(defn: str | None, project: str | None,
             local: set) -> tuple[str, str] | None:
    """Which model type a stated ``defn`` names, or None for none stated.

    The same resolution order the reader uses: own file first, then
    master -- every case defines its own ``Station`` and ``Main`` and
    master states both, so a reader resolving master first would place
    the wrong one. Two spellings that resolve to one definition
    (``Proj:Sub1`` and ``Sub1``) are one type, which is what lets the
    union rule see all of a definition's placements.
    """
    if not defn:
        return None
    namespace, sep, name = defn.rpartition(":")
    if sep and namespace == "master":
        return (LIBRARY, f"master:{name}")
    if not sep:
        return (DEFINITION, name) if name in local \
            else (LIBRARY, f"master:{name}")
    if namespace == project or name in local:
        return (DEFINITION, name)
    return (LIBRARY, defn)


def _declared_form(definition: HirDefinition) -> list:
    """``(spelling, parameter)`` per named declared form parameter, in
    form order, first declaration winning where a spelling repeats."""
    seen: dict[str, object] = {}
    for category in definition.form:
        for parameter in category.parameters:
            if parameter.name is not None:
                seen.setdefault(parameter.name, parameter)
    return list(seen.items())


def _elements_of(hir: HirProject, diagnostics) -> list[_Element]:
    """Every drawn element this surface carries, in document order.

    Components always: each states at least a paramlist or a name worth
    keeping, and a uniform rule beats a threshold. A wire only where it
    states a non-empty name, any paramlist, or a ``defn`` -- nearly
    every wire states ``name=""`` and nothing else, and the inverse
    gives an absent wire exactly that, so omission states what emission
    would. The ``defn`` arm exists because a wire can state ``defn=""``
    and nothing else, and the element's own statement
    (:mod:`pscx.record`) carries the reference verbatim, which it
    cannot do for a wire with no subject.
    """
    from pscx.diagnostics import Provenance

    out: list[_Element] = []
    for definition in hir.definitions:
        page = definition.name
        if not page:
            # same rule as DL: a page named nothing cannot anchor an
            # identity, and DL already counted the definition
            diagnostics.emit("surface_definition_unnamed",
                             provenance=Provenance(
                                 case=hir.name or hir.path,
                                 element=str(definition.id)))
            continue
        if definition.canvas is None:
            continue
        for component in definition.canvas.components:
            out.append(_Element(page, USER, str(component.id),
                                component.name, component.defn,
                                component.params))
        for index, wire in enumerate(definition.canvas.wires):
            ident = wire.id if wire.id is not None else f"#{index}"
            if wire.name or wire.params or wire.defn is not None:
                if wire.id is None:
                    diagnostics.emit("surface_element_without_id", WIRE,
                                     provenance=Provenance(
                                         case=hir.name or hir.path,
                                         element=page))
                out.append(_Element(page, WIRE, str(ident),
                                    wire.name if wire.name else None,
                                    wire.defn, wire.params))
            for hosted in wire.hosted:
                out.append(_Element(page, USER, str(hosted.id),
                                    hosted.name, hosted.defn,
                                    hosted.params))
    return out


def _type_universe(hir: HirProject, elements: list[_Element]) -> dict:
    """``type key -> (definition | None, [spellings])`` for every model
    type this document states.

    A type exists for every own-file definition that states a form name
    or declares a named parameter -- placed or not, because the form is
    the definition's statement and a page nothing instantiates still
    makes it -- and for every key some drawn element resolves to. The
    spelling list is the union rule: the form's declarations in form
    order, then every spelling any placement states, extras sorted.
    """
    local = {d.name for d in hir.definitions if d.name}
    by_name = {}
    for definition in hir.definitions:
        if definition.name and definition.name not in by_name:
            by_name[definition.name] = definition

    for element in elements:
        key = _resolve(element.defn, hir.name, local)
        if key is None or element.kind == WIRE:
            # no ComponentDef arbitrates what a wire's parameters are,
            # so a wire is its own type -- and so is a
            # component that names no definition at all
            key = (ELEMENT, _seed(element.page, element.kind, element.ident))
        element.type_key = key

    universe: dict[tuple, tuple] = {}

    def order_of(declared: list, members: list) -> list:
        spellings = [spelling for spelling, _parameter in declared]
        taken = set(spellings)
        stated = sorted({spelling for element in members
                         for entry in element.lists for spelling in entry}
                        - taken)
        return spellings + stated

    grouped: dict[tuple, list] = {}
    for element in elements:
        grouped.setdefault(element.type_key, []).append(element)

    for definition in hir.definitions:
        name = definition.name
        if not name or by_name[name] is not definition:
            continue
        declared = _declared_form(definition)
        if (definition.form_name is None and not declared
                and (DEFINITION, name) not in grouped):
            continue
        key = (DEFINITION, name)
        universe[key] = (definition,
                         order_of(declared, grouped.get(key, ())))
    for key in sorted(grouped, key=repr):
        if key in universe:
            continue
        definition = by_name.get(key[1]) if key[0] == DEFINITION else None
        declared = _declared_form(definition) if definition else []
        universe[key] = (definition, order_of(declared, grouped[key]))
    return universe


def add_drawn_surface(emt: EmtModel, hir: HirProject,
                      restated: dict) -> tuple[dict, dict]:
    """Write one case's stated-parameter surface into the source graph.

    ``hir`` is the case already read; nothing of it is retained -- every
    value is copied out as a string before the tree is let go, the same
    contract :func:`pscx.dl.build_dl` keeps.

    ``restated`` maps a drawn identity ``(page, kind, ident)`` to the
    interchange ``cim:DetailedModelDynamics`` mRIDs its statement is
    exchanged as (:func:`pscx.emt._restated_of`). Every subject of that
    drawn element states the join -- each paramlist feeds the same flat
    placements, so each subject restates them all -- and a restated
    identity this surface does not state is a partition defect and
    raises, never a skip: the surface claims every drawn element, so a
    flat placement with no drawn statement means the two enumerations
    have drifted apart.

    Returns ``(type key -> subject, (type key, spelling) -> descriptor)``
    so the source record (:mod:`pscx.record`) states the form's own
    structure on the very descriptors this surface minted rather than on
    a parallel set.
    """
    from pscx.emt import CIM, EMT, _literal, _named, _uri
    from pscx.mrid import mrid

    project = emt.project
    elements = _elements_of(hir, emt.diagnostics)
    universe = _type_universe(hir, elements)

    types: dict[tuple, rdflib.URIRef] = {}
    descriptors: dict[tuple, rdflib.URIRef] = {}
    for key, (definition, spellings) in universe.items():
        digest = hashlib.sha256(
            "\n".join(spellings).encode()).hexdigest()[:16]
        seed = f"{TYPE}/{_seed(*key)}/{digest}"
        subject = _named(emt, emt.source,
                         mrid(project, "LibraryModelType", seed),
                         EMT, "LibraryModelType", f"{TYPE}/{_seed(*key)}")
        types[key] = subject
        emt.source.add((subject, EMT["LibraryModelType.modelingTool"],
                           _literal("PSCAD")))
        emt.source.add((subject, EMT["LibraryModelType.definitionName"],
                           _literal(key[1])))
        if hir.version:
            emt.source.add((subject, EMT["LibraryModelType.toolVersion"],
                               _literal(hir.version)))
        declared = dict(_declared_form(definition)) if definition else {}
        position = {spelling: i for i, spelling in enumerate(declared, 1)}
        if definition is not None and definition.form_name is not None:
            emt.source.add((subject, CIM["IdentifiedObject.description"],
                               _literal(definition.form_name)))
            emt.metrics["surface_form_names"] += 1
        for spelling in spellings:
            descriptor = _named(
                emt, emt.source,
                mrid(project, "ParameterDescriptor",
                     f"{seed}/{_seed(spelling)}"),
                CIM, "ParameterDescriptor", spelling)
            descriptors[(key, spelling)] = descriptor
            emt.source.add(
                (descriptor,
                 CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
                 subject))
            parameter = declared.get(spelling)
            if parameter is None:
                # stated by a placement, declared by nothing: it has no
                # form position, and carrying none is how the inverse
                # tells the form from the stated extras
                emt.metrics["surface_descriptors_extra"] += 1
                continue
            emt.metrics["surface_descriptors_declared"] += 1
            emt.source.add(
                (descriptor, CIM["ParameterDescriptor.sequenceNumber"],
                 _literal(position[spelling])))
            if parameter.desc is not None:
                emt.source.add(
                    (descriptor, CIM["IdentifiedObject.description"],
                     _literal(parameter.desc)))
            if parameter.unit is not None:
                emt.source.add(
                    (descriptor, CIM["ParameterDescriptor.engineeringUnit"],
                     _literal(parameter.unit)))
            if parameter.value is not None:
                # verbatim, blanks included: the form WROTE a default of
                # "" and the inverse must get that back, not an absence
                emt.source.add(
                    (descriptor, CIM["ParameterDescriptor.typicalValue"],
                     _literal(parameter.value)))

    for element in elements:
        emt.metrics[f"surface_drawn: {element.kind}"] += 1
        identity = _seed(element.page, element.kind, element.ident)
        # An element with no paramlist at all still needs a subject to
        # carry its stated name, and "one empty paramlist" is a different
        # file from "none" -- so the carrier-only subject takes the
        # sentinel ordinal "-" and the inverse rebuilds an empty LIST,
        # not a list of one empty dict.
        ordinals = list(enumerate(element.lists)) if element.lists \
            else [("-", None)]
        for n, entry in ordinals:
            seed = f"{DRAWN}/{identity}/{n}"
            subject = _named(emt, emt.source,
                             mrid(project, "DetailedModelDynamics", seed),
                             CIM, "DetailedModelDynamics", seed)
            for target in restated.get(
                    (element.page, element.kind, element.ident), ()):
                emt.source.add(
                    (subject,
                     EMT["DetailedModelDynamics.IdentifiedObject"],
                     _uri(target)))
                emt.metrics["surface_restates"] += 1
            emt.source.add(
                (subject, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"],
                 types[element.type_key]))
            emt.source.add((subject, CIM["DynamicsFunctionBlock.enabled"],
                               _literal(True)))
            if n in (0, "-") and element.name is not None:
                emt.source.add(
                    (subject, CIM["IdentifiedObject.description"],
                     _literal(element.name)))
            if entry is None:
                continue
            for spelling, stated in entry.items():
                value = emt.mint(
                    emt.source,
                    mrid(project, "ParameterValue",
                         f"{seed}/{_seed(spelling)}"),
                    CIM, "ParameterValue")
                emt.source.add(
                    (value, CIM["ParameterValue.DetailedModelDynamics"],
                     subject))
                emt.source.add(
                    (value, CIM["ParameterValue.ParameterDescriptor"],
                     descriptors[(element.type_key, spelling)]))
                emt.source.add((value, CIM["ParameterValue.value"],
                                   _literal(stated)))
                emt.metrics["surface_values"] += 1
                for position, text in enumerate(
                        element.rows[n].get(spelling, ()), start=1):
                    row = emt.mint(
                        emt.source,
                        mrid(project, "MatrixRow",
                             f"{seed}/{_seed(spelling)}/{position}"),
                        EMT, "MatrixRow")
                    emt.source.add(
                        (row, EMT["MatrixRow.ParameterValue"], value))
                    emt.source.add((row, EMT["MatrixRow.sequenceNumber"],
                                    _literal(position)))
                    emt.source.add((row, EMT["MatrixRow.value"],
                                    _literal(text)))
                    emt.metrics["surface_matrix_rows"] += 1
    unstated = set(restated) - {(e.page, e.kind, e.ident)
                                for e in elements}
    if unstated:
        raise ValueError(
            f"{len(unstated)} interchange placement(s) restate a drawn "
            f"element this surface does not state, first: "
            f"{min(unstated, key=repr)}. The surface claims every "
            f"drawn element, so the two enumerations have drifted apart.")
    return types, descriptors


# --------------------------------------------------------------------------
# The inverse: emitted documents -> the stated surface
# --------------------------------------------------------------------------


def stated_surface(hir: HirProject) -> dict:
    """What the HIR states, in the shape :func:`reconstruct_surface`
    returns.

    ``elements`` maps ``(page, kind, ident)`` to ``(name, lists)``;
    ``forms`` maps a definition name to ``(form_name, parameters)`` with
    each parameter's ``(name, desc, unit, value)`` in form order;
    ``rows`` maps ``(page, kind, ident, ordinal, spelling)`` to the
    matrix row texts that stated parameter carries, in row order. A wire
    the emission rule omits is omitted here by the same rule, and
    :func:`omitted_wires` is the negative space -- what the omission
    asserts about every wire it covers.
    """
    from pscx.diagnostics import Diagnostics

    elements = {}
    rows = {}
    for element in _elements_of(hir, Diagnostics()):
        elements[(element.page, element.kind, element.ident)] = (
            element.name, tuple(element.lists))
        for n, per_list in enumerate(element.rows):
            for spelling, texts in per_list.items():
                rows[(element.page, element.kind, element.ident, n,
                      spelling)] = tuple(texts)
    forms = {}
    for definition in hir.definitions:
        if not definition.name or definition.name in forms:
            continue
        declared = _declared_form(definition)
        if definition.form_name is None and not declared:
            continue
        forms[definition.name] = (
            definition.form_name,
            tuple((spelling, p.desc, p.unit, p.value)
                  for spelling, p in declared))
    return {"elements": elements, "forms": forms, "rows": rows}


def omitted_wires(hir: HirProject) -> tuple[int, int]:
    """``(omitted, violating)`` over the case's wires.

    The emission rule says an absent wire stated ``name=""``, no
    paramlist and no ``defn``; ``violating`` counts the wires for which
    that would be false, so a caller can assert the omission loses
    nothing.
    """
    omitted = violating = 0
    for definition in hir.definitions:
        if definition.canvas is None:
            continue
        for wire in definition.canvas.wires:
            if wire.name or wire.params or wire.defn is not None:
                continue
            omitted += 1
            violating += wire.name is None or wire.defn is not None
    return omitted, violating


def reconstruct_surface(graph: rdflib.Graph) -> dict:
    """Rebuild the stated surface from an emitted document ALONE.

    Nothing here reads the HIR or the builder. A subject belongs to this
    surface exactly when its name enters the reserved grammar; a name
    that starts a reserved prefix and then breaks the grammar is a
    defect and raises, never a skip.
    """
    from pscx.emt import CIM, EMT

    name_of = CIM["IdentifiedObject.name"]
    description_of = CIM["IdentifiedObject.description"]

    def parse(name: str, prefix: str, count: int) -> tuple | None:
        if not name.startswith(prefix + "/"):
            return None
        parts = name.split("/")[1:]
        if len(parts) != count:
            raise ValueError(f"reserved name breaks the grammar: {name!r}")
        return tuple(unquote(part) for part in parts)

    types = {}
    forms: dict[str, tuple] = {}
    for subject in graph.subjects(RDF.type, EMT["LibraryModelType"]):
        parsed = parse(str(graph.value(subject, name_of) or ""), TYPE, 2)
        if parsed is None:
            continue
        types[subject] = parsed

    declared: dict[rdflib.URIRef, list] = {}
    spelling_of = {}
    for subject in graph.subjects(RDF.type, CIM["ParameterDescriptor"]):
        owner = graph.value(
            subject, CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"])
        if owner not in types:
            continue
        spelling = str(graph.value(subject, name_of))
        spelling_of[subject] = spelling
        sequence = graph.value(
            subject, CIM["ParameterDescriptor.sequenceNumber"])
        if sequence is None:
            continue
        desc = graph.value(subject, description_of)
        unit = graph.value(subject, CIM["ParameterDescriptor.engineeringUnit"])
        value = graph.value(subject, CIM["ParameterDescriptor.typicalValue"])
        declared.setdefault(owner, []).append(
            (int(str(sequence)), spelling,
             None if desc is None else str(desc),
             None if unit is None else str(unit),
             None if value is None else str(value)))
    for subject, parsed in types.items():
        if parsed[0] != DEFINITION:
            continue
        form_name = graph.value(subject, description_of)
        parameters = tuple((s, d, u, v) for _n, s, d, u, v
                           in sorted(declared.get(subject, ())))
        if form_name is None and not parameters:
            continue
        forms[parsed[1]] = (
            None if form_name is None else str(form_name), parameters)

    lists: dict[tuple, dict[int, dict]] = {}
    names: dict[tuple, str | None] = {}
    dynamics = {}
    for subject in graph.subjects(RDF.type, CIM["DetailedModelDynamics"]):
        parsed = parse(str(graph.value(subject, name_of) or ""), DRAWN, 4)
        if parsed is None:
            continue
        page, kind, ident, n = parsed
        if kind not in (USER, WIRE):
            raise ValueError(f"reserved name states no drawn kind: {parsed}")
        key = (page, kind, ident)
        lists.setdefault(key, {})
        if n == "-":
            dynamics[subject] = (key, None)
        else:
            dynamics[subject] = (key, int(n))
            lists[key][int(n)] = {}
        if n in ("0", "-"):
            stated = graph.value(subject, description_of)
            names[key] = None if stated is None else str(stated)

    identity_of = {}
    for subject in graph.subjects(RDF.type, CIM["ParameterValue"]):
        owner = graph.value(subject, CIM["ParameterValue.DetailedModelDynamics"])
        if owner not in dynamics:
            continue
        key, n = dynamics[owner]
        if n is None:
            raise ValueError(f"a value on the carrier-only subject of {key}")
        descriptor = graph.value(subject,
                                 CIM["ParameterValue.ParameterDescriptor"])
        stated = graph.value(subject, CIM["ParameterValue.value"])
        lists[key][n][spelling_of[descriptor]] = str(stated)
        identity_of[subject] = key + (n, spelling_of[descriptor])

    gathered: dict[tuple, list] = {}
    for subject in graph.subjects(RDF.type, EMT["MatrixRow"]):
        owner = graph.value(subject, EMT["MatrixRow.ParameterValue"])
        if owner not in identity_of:
            raise ValueError("a matrix row joins a stated value the "
                             "document does not state")
        gathered.setdefault(identity_of[owner], []).append(
            (int(graph.value(subject, EMT["MatrixRow.sequenceNumber"])),
             str(graph.value(subject, EMT["MatrixRow.value"]))))
    rows = {identity: tuple(text for _position, text in sorted(entries))
            for identity, entries in gathered.items()}

    elements = {}
    for key, by_index in lists.items():
        if sorted(by_index) != list(range(len(by_index))):
            raise ValueError(f"paramlists of {key} are not dense: "
                             f"{sorted(by_index)}")
        elements[key] = (names.get(key),
                         tuple(by_index[n] for n in sorted(by_index)))
    return {"elements": elements, "forms": forms, "rows": rows}


def restated_join(graph: rdflib.Graph) -> dict:
    """The join, back from the source document alone:
    ``(page, kind, ident) -> tuple of interchange subject IRIs``.

    The inverse of the ``emt:DetailedModelDynamics.IdentifiedObject``
    statements :func:`add_drawn_surface` writes. Every subject of one
    drawn element states the same target set -- each paramlist feeds the
    same flat placements -- so the sets are read per subject, checked
    equal across the element's subjects, and returned once per element;
    two subjects of one element stating different sets is a defect and
    raises. An element with no join statement is absent from the result:
    nothing exchanges it (a drawing-only annotation, an uninstantiated
    page), and that absence is data.
    """
    from pscx.emt import CIM, EMT

    name_of = CIM["IdentifiedObject.name"]
    per_subject: dict[tuple, dict] = {}
    for subject, target in graph.subject_objects(
            EMT["DetailedModelDynamics.IdentifiedObject"]):
        name = str(graph.value(subject, name_of) or "")
        if not name.startswith(DRAWN + "/"):
            raise ValueError(
                f"a restating subject outside the drawn grammar: {name!r}")
        parts = name.split("/")[1:]
        if len(parts) != 4:
            raise ValueError(f"reserved name breaks the grammar: {name!r}")
        page, kind, ident, _n = (unquote(part) for part in parts)
        per_subject.setdefault((page, kind, ident), {}).setdefault(
            subject, set()).add(str(target))
    subjects_of: dict[tuple, int] = {}
    for subject in graph.subjects(RDF.type, CIM["DetailedModelDynamics"]):
        name = str(graph.value(subject, name_of) or "")
        if not name.startswith(DRAWN + "/"):
            continue
        page, kind, ident, _n = (unquote(part)
                                 for part in name.split("/")[1:])
        subjects_of[(page, kind, ident)] = \
            subjects_of.get((page, kind, ident), 0) + 1
    out: dict[tuple, tuple] = {}
    for key, by_subject in per_subject.items():
        sets = {frozenset(targets) for targets in by_subject.values()}
        if len(sets) != 1 or len(by_subject) != subjects_of.get(key, 0):
            raise ValueError(
                f"the subjects of {key} do not state one restatement "
                f"set; one drawn element is exchanged as one set of "
                f"placements, whichever paramlist is asked")
        out[key] = tuple(sorted(next(iter(sets))))
    return out

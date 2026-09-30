"""The emt: extension profile: EMT data standard CGMES has no class for.

Three documents, one vocabulary. Equipment-attached models declare
``md:Model.DependentOn`` the EQ model, because they reference its nodes by
IRI. ``emt:SimulationCase`` depends on nothing: it states how a study is
solved, not what is solved, so merging it into the equipment document
would assert a dependency on the network that does not exist and would
destroy its reuse across network variants. The stated-parameter surface
(:mod:`pscx.surface`) is the third: the verbatim stated text of the
source file, as opposed to the evaluated engine-facing view, in a
SOURCE document whose content reads back single-document and whose one
outward reference is the restatement join into the interchange document
(its header states that dependency). Source fidelity carried by the
interchange document would grow the equipment document severalfold, and
an interchange consumer needs nothing the source document states.
``emt:LibraryModelType`` appearing in two of the three is what "one
vocabulary" means: a term keeps its home however the documents are cut.

A component standard CIM has no class for is exchanged with the standard
classes that describe exactly that: ``cim:DetailedModelDynamics`` for the
placement, ``cim:ParameterDescriptor`` on its type for what a parameter
IS, ``cim:ParameterValue`` for what the placement sets it to. They sit
outside the CGMES subset rather than outside standard CIM, so they are
written here. What has no standard home stays ``emt:``:
the model TYPE, which the standard model leaves without a concrete
subclass, and the ports it declares; the per-conductor anchor, which
presumes no standard equipment; a node's conductor count; the number a
stated parameter evaluates to; and the flattened control-signal graph,
whose nets touch no ``cim:ACDCTerminal`` for ``cim:SignalDescriptor``
to presume.

Nothing here writes to a standard document. The join runs the other way --
add-on subjects reference EQ subjects by ``urn:uuid:`` IRI -- so
a consumer that does not know this profile drops the file and builds
exactly the network the standard documents describe.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rdflib
from rdflib import RDF, Literal, URIRef

from pscx.cimxml import (
    CIM,
    CIM_NS,
    DEFAULT_MODELING_AUTHORITY_SET,
    EMT,
    EMT_NS,
    _uri,
    full_model_header,
    serialize_graph,
)
from pscx.diagnostics import DIAGNOSTICS, Diagnostics
from pscx.lir import build_lir
from pscx.mrid import MridCollision, mrid
from pscx.rules import (
    ACTIVE_CIM_CLASSES,
    SETTINGS_SECONDS,
    SETTINGS_VERBATIM,
    modeled_cim_class,
)

if TYPE_CHECKING:
    from pscx.cim import CimModel
    from pscx.elaborate import FlatProject
    from pscx.hir import HirProject

#: md:Model.profile of each document. The equipment-attached models, the
#: study settings and the stated-parameter surface are separate profiles
#: of the one vocabulary, matching the CGMES convention that a profile
#: URI is its versionIRI. The third lives with its emitter,
#: :data:`pscx.surface.EMT_SOURCE_PROFILE_URI`.
EMT_PROFILE_URI = "https://w3id.org/pscx-cim/ns/CIM/EMT/1.0"
EMT_SIMULATION_PROFILE_URI = (
    "https://w3id.org/pscx-cim/ns/CIM/EMTSimulation/1.0"
)





@dataclass
class EmtModel:
    """The three add-on graphs of one case, its coverage metrics, and its
    diagnostics.

    `emt_electrical_model: kind` is a METRIC. It says what the detailed
    models absorbed, and on a converter-heavy case it is the largest number
    in the run. What a detailed model could NOT join to the equipment
    document is the diagnostic.
    """

    project: str
    #: Equipment-attached models; joins to EQ by IRI.
    equipment: rdflib.Graph
    #: Study settings; joins to nothing.
    study: rdflib.Graph
    #: The stated-parameter surface: the source file's verbatim text,
    #: self-contained, joining nothing (:mod:`pscx.surface`).
    source: rdflib.Graph
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    metrics: Counter = field(default_factory=Counter)
    #: Every subject this document mints, mRID -> class name. The ledger
    #: exists to be collided with; nothing reads it back.
    minted: dict[str, str] = field(default_factory=dict)

    def mint(self, graph: rdflib.Graph, mrid_value: str,
             namespace: rdflib.Namespace, class_name: str) -> URIRef:
        """Register one new subject, type it, and return its IRI.

        Six seed spaces write into these graphs and they stay apart only
        because their seed strings differ. Nothing in RDF enforces that:
        two objects handed one IRI merge into a subject carrying both
        their statements, and the document still parses and still
        conforms. So the seed is registered rather than trusted, which is
        what ``cim.CimModel.add`` does for the equipment document.

        The namespace is a parameter because this profile writes subjects
        of standard cim: classes as well as its own, and which is which is
        stated once, in the shapes generator's ADOPTED table.
        """
        existing = self.minted.get(mrid_value)
        if existing is not None:
            raise MridCollision(
                f"two objects were given mRID {mrid_value}: a "
                f"{existing} and a {class_name}. An mRID is uuid5 over one "
                f"(project, kind, id) seed -- see pscx/mrid.py -- so this "
                f"is one seed used twice, and the fix is a seed that tells "
                f"the two objects apart."
            )
        self.minted[mrid_value] = class_name
        subject = _uri(mrid_value)
        graph.add((subject, RDF.type, namespace[class_name]))
        return subject


class _Placement:
    """One absorbed placement, and the model TYPE it is a placement of.

    The type is the library DEFINITION where the source names one: every
    placement of it states the same form, so its parameters are described
    once. Where there is no definition behind the carrier -- a hosted
    device declares no ComponentDef at all -- nothing arbitrates
    what the parameters of its kind are, so the placement is its own type.

    ``signal`` marks the pure-signal population, and it changes the
    PARAMETER rule, not the machinery: an electrical detailed-model
    placement states everything it says verbatim (losslessness with respect
    to the case), while a signal block states its LIVE, NUMERIC form
    parameters evaluated. That is the driving components' rule, because a
    control form is selector-gated too and a gated-off field is not data. Its value
    is the evaluated number verbatim in the form's declared unit, and a live
    numeric parameter that does not evaluate to a number is absent and
    counted rather than invented.
    """

    def __init__(self, inst, carrier, kind: str, seed: str, name: str,
                 ends, where, *, signal: bool = False) -> None:
        self.inst = inst
        self.carrier = carrier
        self.kind = kind
        self.seed = seed
        self.name = name
        self.ends = ends
        self.where = where
        self.signal = signal
        self.definition = getattr(carrier, "definition", None)
        # tagged, so a definition name and a placement path cannot meet:
        # they are two key spaces and only the tag keeps them apart
        self.type_key = (("definition", carrier.defn)
                         if self.definition is not None
                         else ("placement", seed))
        if signal:
            from pscx.cim import live_numeric_parameters, live_text_parameters
            from pscx.rules import RECORDING_KINDS

            live, unresolved = live_numeric_parameters(inst, carrier)
            self.live = {entry["key"]: entry for entry in live}
            self.unresolved = tuple(unresolved)
            self.stated = [(entry["key"], entry["spelling"])
                           for entry in live]
            # a RECORDING block's text is what the study records its
            # results as -- channel title, group, unit, a recorder's
            # file and slot names -- so it is stated verbatim beside
            # the numerics; every other signal block's text is a label
            # or a clue the resolved graph already carries
            self.text = {}
            if kind in RECORDING_KINDS:
                for entry in live_text_parameters(carrier):
                    self.text[entry["key"]] = entry["value"]
                    self.stated.append((entry["key"], entry["spelling"]))
        else:
            self.live = None
            self.unresolved = ()
            self.text = {}
            self.stated = _stated_parameters(carrier)


def _graph() -> rdflib.Graph:
    graph = rdflib.Graph()
    graph.bind("cim", CIM_NS)
    graph.bind("emt", EMT_NS)
    return graph


def _named(emt: EmtModel, graph: rdflib.Graph, mrid_value: str,
           namespace: rdflib.Namespace, class_name: str,
           name: str) -> URIRef:
    """Mint a subject and add the mRID and name an IdentifiedObject
    carries. ``cim:ParameterValue`` is the one class here that is not one,
    so it mints directly."""
    subject = emt.mint(graph, mrid_value, namespace, class_name)
    graph.add((subject, CIM["IdentifiedObject.mRID"], Literal(mrid_value)))
    graph.add((subject, CIM["IdentifiedObject.name"], Literal(name)))
    return subject


def _literal(value: Any) -> Literal:
    """A PLAIN literal, like every other document this repo writes: a
    typed literal breaks PowSyBl's plain-string SPARQL comparisons,
    and datatypes are the validator's job."""
    if isinstance(value, bool):
        return Literal("true" if value else "false")
    return Literal(str(value))


def _component_name(comp, kind: str) -> str:
    return next(
        (v.strip() for k, v in comp.params.items()
         if k.lower() == "name" and v and v.strip()),
        f"{kind}_{comp.element_id}",
    )


def _parameter_order(comp) -> list[str]:
    """The form's own parameter order.

    A definition's defaults preserve the order the form declares, which is
    the order a reader of that form expects; anything the placement adds
    beyond the form follows, sorted, so the sequence is deterministic
    either way.

    A hosted device declares no form at all: its ``<User>``
    paramlist is the whole statement of what it is, so the order the file
    writes it in is the only order there is, and it is kept.
    """
    if not hasattr(comp, "definition"):
        return list(comp.params)
    declared = list(comp.definition.defaults) if comp.definition else []
    lowered = {name.lower() for name in declared}
    extra = sorted(name for name in comp.params if name.lower() not in lowered)
    stated = {name.lower(): name for name in comp.params}
    ordered = [stated[name.lower()] for name in declared
               if name.lower() in stated]
    return ordered + extra


def _stated_parameters(carrier) -> list[tuple[str, str]]:
    """``(match key, spelling)`` per parameter one placement STATES, in the
    form's order.

    A parameter the placement leaves blank states nothing and is omitted;
    everything else is carried verbatim, so the model is lossless with
    respect to what the case says.

    The key is case-folded here and nowhere else, because it is what a
    parameter IS to the rest of this module: the descriptor set is keyed
    by it and so is the mRID seed of every cim:ParameterValue. Two
    spellings of one name are one parameter, so folding them
    late -- after the values are enumerated -- would seed two values onto
    one IRI, and rdflib merges those into a subject carrying two
    cim:ParameterValue.value literals. Where a placement states both, the
    LAST in document order survives, which is the spelling
    ``pscx.cim._stated`` resolves the value from; a disagreement between
    them is counted as ``cim_param_case_collision``.
    """
    stated: dict[str, str] = {}
    for name in _parameter_order(carrier):
        value = carrier.params.get(name)
        if value is None or not str(value).strip():
            continue
        stated[name.lower()] = name
    return list(stated.items())


def _type_parameters(definition, stated: list[list[str]]) -> list[tuple[str,
                                                                       str]]:
    """``(match key, published spelling)`` per descriptor of one type.

    Two placements of one definition need not state the same parameters: a
    form parameter one placement fills another can leave blank, and a
    placement can state a name the form never declared. The descriptor set
    is therefore the UNION over the type's placements, so no placement can
    hold a value with nothing to describe it, and the sequence numbers are
    a property of the TYPE rather than of whichever placement was read.

    Order is the form's own -- the order a reader of that form expects --
    and a name the form does not declare follows, sorted, so the sequence
    is deterministic even where two placements introduce different extras.
    A type with no definition behind it is a single placement, so the
    order the source file writes its parameters in is both available and
    the only order there is, and it is kept.

    The match key is lowercased because a placement can spell a declared
    name in another case and that is one parameter, not two;
    what gets published is the form's own spelling wherever the form
    declares it.
    """
    seen: dict[str, str] = {}
    for names in stated:
        for key, spelling in names:
            seen.setdefault(key, spelling)
    declared = (list(definition.defaults) if definition is not None
                else [spelling for names in stated for _key, spelling in names])
    ordered, taken = [], set()
    for name in declared:
        key = name.lower()
        if key in seen and key not in taken:
            taken.add(key)
            ordered.append((key, name))
    return ordered + [(key, seen[key]) for key in sorted(seen)
                      if key not in taken]


def _type_seed(type_key: tuple[str, str],
               parameters: list[tuple[str, str]]) -> str:
    """The mRID seed of one model type.

    Two things have to be in it. The KEY SPACE, because a definition name
    and a placement path are different kinds of identifier and a seed that
    merged them would let one shadow the other. And a digest of the
    DESCRIPTOR SET, because that set is the union over the placements of
    ONE document: two documents of a single project that place a
    definition differently describe two different types, and a seed
    covering only the definition name would mint one subject carrying two
    parameter sets and two sequence numbers. Two case files can carry one
    project name, so that is reachable rather than hypothetical.
    """
    kind, name = type_key
    digest = hashlib.sha256(
        "\n".join(key for key, _spelling in parameters).encode()
    ).hexdigest()[:16]
    return f"{kind}/{name}/{digest}"


def _probe_numeric(inst, comp, name: str) -> float | None:
    """The value of one detailed-model parameter as a number, or None.

    A proprietary parameter is arbitrary by construction: a number, a
    signal name, an enumeration index, a blank. Failing to resolve one is
    the NORMAL outcome and says nothing about the case, so the probe
    reports nothing rather than a flood of false unresolved-parameter
    diagnostics.
    """
    from pscx.cim import _param_numeric

    with DIAGNOSTICS.suppressed():
        return _param_numeric(inst, comp, name)


def _project_model(flat: FlatProject,
                   project: HirProject | None = None) -> HirProject | None:
    """The case as the HIR, or None where it will not read.

    Two things this profile needs are project-level and therefore not in
    the flattened model: the PSCAD version the case was written against
    and the Settings paramlist that states how it is solved. Both are on
    the ``HirProject``, so a caller that already read the case hands it
    over rather than have the same bytes read a second time.

    A case that will not read yields None and no finding. Whoever read it
    first owns that diagnostic, and this profile states version and
    solver settings on top of a model built from the same file: there is
    nothing here to report that the flatten did not already report.
    """
    from pscx.io import read_project

    if project is not None:
        return project
    # What a file that will not read raises: it is missing or unreadable,
    # or it is not well-formed XML -- lxml's XMLSyntaxError is a
    # SyntaxError, so the pair is named without importing a parser here.
    try:
        return read_project(flat.case)
    except (OSError, SyntaxError):
        return None


def _settings(project: HirProject | None) -> dict[str, str]:
    """The project-level ``<paramlist name="Settings">``, as name -> value.

    The first one, because a project states one.
    """
    if project is None:
        return {}
    for plist in project.params:
        if plist.name == "Settings":
            return dict(plist.values)
    return {}


def _as_float(text: str | None) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _add_phase_resolution(emt: EmtModel, model: CimModel,
                          widths: dict) -> None:
    """State how many conductors each emitted ConnectivityNode carries.

    This is the one thing standard CGMES has no vocabulary for. A drawn
    node of three conductors is ONE ConnectivityNode -- that is what the
    case drew -- and cim:Terminal.phases can say ABC about it, but it has
    no member for a bipole's two conductors or a double circuit's six,
    and 301's own consistency rule forbids stating a code on one end of a
    two-terminal element without stating the same code on the other. So
    the conductor count is written here, on the standard node, by the
    ``ClassName.attribute`` idiom the eu:/entsoe:/nc: extensions use --
    and with it the per-phase network is recoverable from the emitted
    documents alone.
    """
    from pscx.cim import ConnectivityNode

    for resource in model.resources.values():
        if not isinstance(resource, ConnectivityNode):
            continue
        key = model.node_keys.get(resource.mRID)
        emt.equipment.add((_uri(resource.mRID),
                           EMT["ConnectivityNode.phaseCount"],
                           _literal(widths.get(key, 1))))


def build_emt(flat: FlatProject, model: CimModel,
              project: HirProject | None = None) -> EmtModel:
    """The add-on graphs for one case, joined to an already-built EQ.

    ``model`` is read, never written: the standard documents are byte-for
    -byte what they would be if this profile did not exist. It is consulted
    for three things -- which ConnectivityNodes the equipment document
    actually contains, since referencing one it does not would resolve to
    nothing and raise no error; the per-phase LIR it was projected
    from; and the mapped-passive record (``CimModel.passives``), whose
    equipment each R/L/C statement joins. Reading the graph off the model
    rather than rebuilding one is
    what makes "the two documents describe one network" structural
    instead of a coincidence of both being handed the same FlatProject.

    ``project`` is the case already read, for the version and the solver
    settings the flattened model does not carry; with nothing handed over
    the case is read here, exactly as :func:`pscx.dl.build_dl` takes its
    own. It is not retained: both values are copied out as strings.
    """
    from pscx.cim import _where, absorbed_components, unmapped_devices
    from pscx.emission import device_ends, emission_widths, placement_ends

    name = flat.project or flat.case
    if model.project != name:
        raise ValueError(
            f"the equipment document is {model.project!r} and this project "
            f"is {name!r}: the add-on references EQ subjects by IRI, so "
            f"joining two cases would produce references that resolve to "
            f"nothing and raise nothing")
    emt = EmtModel(project=name, equipment=_graph(), study=_graph(),
                   source=_graph())
    graph = model.graph if model.graph is not None else build_lir(flat)
    widths = emission_widths(graph)
    ends_of = placement_ends(flat, graph)
    hir = _project_model(flat, project)
    tool_version = hir.version if hir is not None else None
    _add_phase_resolution(emt, model, widths)

    placements = []
    for inst, comp, kind, role in absorbed_components(flat):
        if role == "bridge":
            # a name bridge is a wiring clue the flat nets already
            # express; giving it a body would state the same join twice
            emt.metrics[f"emt_signal_bridge: {kind}"] += 1
            continue
        if role == "drawing":
            # no pin the engine could reference: the surface states its
            # text, and a body nothing can join is not engine data
            emt.metrics[f"emt_drawing_only: {kind}"] += 1
            continue
        placements.append(_Placement(
            inst, comp, kind, f"{inst.path}/{comp.element_id}",
            _component_name(comp, kind),
            ends_of.get((inst.index, id(comp)), ()),
            _where(name, inst, comp), signal=(role == "signal")))
    # A hosted device standard CIM has no class for is absorbed the same
    # way, and only its SHAPE differs: it is identified by the hosting
    # wire rather than by an element id, it declares no Branch row and has
    # no ports, so its ends are the wire's two synthetic terminal keys,
    # and it has no ComponentDef to state a parameter order or a
    # unit. What it does have is a paramlist, which is what a detailed
    # model is for.
    placements += [
        _Placement(inst, dev, dev.kind,
                   f"{inst.path}/{dev.terminal_a[1]}",
                   dev.name or dev.defn or f"{dev.kind.lower()}_"
                                           f"{dev.terminal_a[1]}",
                   device_ends(flat, inst, dev, widths),
                   _where(name, inst, element=dev.terminal_a[1]))
        for inst, dev in unmapped_devices(flat)
    ]

    _add_passive_models(emt, model, name, tool_version)
    _add_active_models(emt, model, name, tool_version)
    types, descriptors = _add_model_types(emt, placements, name,
                                          tool_version)
    for placement in placements:
        counter = ("emt_control_block" if placement.signal
                   else "emt_electrical_model")
        emt.metrics[f"{counter}: {placement.kind}"] += 1
        subject = _named(
            emt, emt.equipment,
            mrid(name, "DetailedModelDynamics", placement.seed),
            CIM, "DetailedModelDynamics", placement.name,
        )
        emt.equipment.add(
            (subject, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"],
             types[placement.type_key]))
        emt.equipment.add((subject, CIM["DynamicsFunctionBlock.enabled"],
                           _literal(True)))
        _add_parameter_values(emt, placement, subject, descriptors, name)
        # a signal block has no electrical end by construction: its
        # anchor is the signal graph's IdentifiedObject join, never a
        # ModelTerminal, so there is no absent terminal to report
        if not placement.signal:
            _add_terminals(emt, model, subject, placement.seed, name,
                           placement.name, placement.ends, placement.where)

    # after the placement loop on purpose: a hosted wire's detailed model
    # must exist before the right-of-way join can be stated on it
    _add_line_records(emt, flat, model, name)
    _add_signal_graph(emt, flat, model, placements, name)

    # The stated-parameter surface: what every drawn element says,
    # verbatim, per definition page rather than per instance -- see
    # pscx.surface for the module and for why the per-instance
    # alternative loses text, pages and count all three. A case that
    # will not read has no drawing to state; whoever read it first owns
    # that diagnostic.
    if hir is not None:
        from pscx.record import add_source_record
        from pscx.surface import add_drawn_surface

        _types, descriptors = add_drawn_surface(
            emt, hir, _restated_of(flat, placements, model, name))
        # the source record rides the same document: the design tree,
        # the definition bodies and the project-level statements, on and
        # around the subjects the surface just minted (pscx.record)
        add_source_record(emt, hir, descriptors)

    _add_simulation_case(emt, _settings(hir), name)
    return emt


def _restated_of(flat: FlatProject, placements: list, model: CimModel,
                 project: str) -> dict[tuple, list[str]]:
    """Drawn identity -> the interchange ``cim:DetailedModelDynamics``
    mRIDs its statement is exchanged as.

    The forward half of the ``emt:DetailedModelDynamics.IdentifiedObject``
    join the surface states (:func:`pscx.surface.add_drawn_surface`).
    Every interchange model is a flat placement of some drawn element,
    and this enumerates the three populations that mint one (the
    unmapped placements, the mapped passives and the mapped actives),
    keyed by the identity the surface names: the instance's canvas is the drawn
    page, and the element id is the drawn ident. A hosted device carries
    no element id of its own -- it is identified by the hosting wire's
    id -- so its placements join the WIRE's drawn subject, the
    element that hosts the statement.

    Many mRIDs per identity is the join's declared 0..n: a page placed N
    times, a per-phase component split over its Branch rows, a source
    split per electrical end all restate one drawn statement severally.
    """
    from pscx.dl import USER, WIRE

    out: dict[tuple, list[str]] = {}

    def note(page: str, kind: str, ident, seed: str) -> None:
        out.setdefault((page, kind, str(ident)), []).append(
            mrid(project, "DetailedModelDynamics", seed))

    for placement in placements:
        element_id = getattr(placement.carrier, "element_id", None)
        if element_id is not None:
            note(placement.inst.canvas, USER, element_id, placement.seed)
        else:
            note(placement.inst.canvas, WIRE,
                 placement.carrier.terminal_a[1], placement.seed)
    for record in model.passives + model.actives:
        note(flat.instances[record["instance"]].canvas, USER,
             record["element"], record["seed"])
    return out


#: ``(match key, published spelling)`` of the three quantities a mapped
#: passive states, positionally the rlc Branch row's own ($A $B $R L C).
#: The units beside them are :data:`pscx.lir.RLC_DECLARED_UNITS`
#: -- ohm, H, uF -- and the values are written in those units verbatim,
#: never converted, so the number and its stated unit cannot drift apart.
PASSIVE_PARAMETERS = [("r", "R"), ("l", "L"), ("c", "C")]


def _add_passive_models(emt: EmtModel, model: CimModel, project: str,
                        tool_version) -> None:
    """Each mapped R/L/C passive's primitives, joined to its equipment.

    CGMES load-flow attributes are omega-baked -- a shunt states
    bPerSection, a branch states x -- and an EMT engine wants R, L and C
    themselves. Where the case determines no frequency the equipment
    document carries a counted 0.0 placeholder, and without this the SI
    values evaluated in the LIR reach no interchange document at all.
    So every mapped passive gets one ``cim:DetailedModelDynamics`` joined
    to its equipment by ``cim:DetailedModelDynamics.Equipment`` (a
    standard association; the equipment-less detailed-model placements
    stay legal because both ends are optional), one ``emt:LibraryModelType``
    per mapped kind, and one ``cim:ParameterValue`` per quantity.

    UNIFORM over every mapped passive, not only behind the placeholder:
    an engine always wants the primitive, the inverse
    (:func:`passive_quantities`) needs no conditional, and the
    derivable redundancy -- x AND L, where omega is known -- is accepted
    and stated here. Degeneracy is thereby INFERABLE rather than marked:
    a consumer reading ``bPerSection 0.0`` beside a stated L with x = 0.0
    can tell placeholder from real, and an explicit marker would need a
    new term this profile does not mint.

    Per flattened placement, not per drawn element -- see
    ``CimModel.passives`` for why the surface makes the dual choice.
    """
    from pscx.lir import RLC_DECLARED_UNITS

    types: dict[tuple, URIRef] = {}
    descriptors: dict[tuple, URIRef] = {}
    for record in model.passives:
        type_key = ("definition", record["defn"])
        if type_key not in types:
            seed = _type_seed(type_key, PASSIVE_PARAMETERS)
            subject = _named(emt, emt.equipment,
                             mrid(project, "LibraryModelType", seed),
                             EMT, "LibraryModelType", record["defn"])
            types[type_key] = subject
            emt.equipment.add((subject, EMT["LibraryModelType.modelingTool"],
                               _literal("PSCAD")))
            emt.equipment.add((subject,
                               EMT["LibraryModelType.definitionName"],
                               _literal(record["defn"])))
            if tool_version:
                emt.equipment.add((subject,
                                   EMT["LibraryModelType.toolVersion"],
                                   _literal(tool_version)))
            for sequence, ((key, spelling), unit) in enumerate(
                    zip(PASSIVE_PARAMETERS, RLC_DECLARED_UNITS), start=1):
                descriptor = _named(
                    emt, emt.equipment,
                    mrid(project, "ParameterDescriptor", f"{seed}/{key}"),
                    CIM, "ParameterDescriptor", spelling,
                )
                emt.equipment.add(
                    (descriptor,
                     CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
                     subject))
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.sequenceNumber"],
                     _literal(sequence)))
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.engineeringUnit"],
                     _literal(unit)))
                # no typicalValue: a form default belongs to a FORM
                # parameter, and these descriptors describe the Branch
                # row's positions, which no form defaults
                descriptors[(type_key, key)] = descriptor
            _add_model_ports(emt, subject, record["definition"], seed,
                             project)

        emt.metrics[f"emt_passive: {record['kind']}"] += 1
        subject = _named(
            emt, emt.equipment,
            mrid(project, "DetailedModelDynamics", record["seed"]),
            CIM, "DetailedModelDynamics", record["name"],
        )
        emt.equipment.add(
            (subject, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"],
             types[type_key]))
        emt.equipment.add((subject, CIM["DynamicsFunctionBlock.enabled"],
                           _literal(True)))
        emt.equipment.add((subject, CIM["DetailedModelDynamics.Equipment"],
                           _uri(record["mrid"])))
        for (key, _spelling), value in zip(PASSIVE_PARAMETERS,
                                           record["values"]):
            parameter = emt.mint(
                emt.equipment,
                mrid(project, "ParameterValue", f"{record['seed']}/{key}"),
                CIM, "ParameterValue")
            emt.equipment.add(
                (parameter, CIM["ParameterValue.DetailedModelDynamics"],
                 subject))
            emt.equipment.add(
                (parameter, CIM["ParameterValue.ParameterDescriptor"],
                 descriptors[(type_key, key)]))
            emt.equipment.add((parameter, CIM["ParameterValue.value"],
                               _literal(value)))
            emt.equipment.add((parameter, EMT["ParameterValue.numericValue"],
                               _literal(value)))


def _add_active_models(emt: EmtModel, model: CimModel, project: str,
                       tool_version) -> None:
    """Each mapped driving component's live form parameters, joined to
    its equipment.

    The passive statement's pattern applied to the components that DRIVE
    the network: what reaches EQ/SSH is the CGMES load-flow slice, and
    without this a source's frequency, phase and internal impedance, a
    machine's reactances, time constants and inertia, and a
    transformer's magnetizing branch reach no interchange document, and
    the frequency would exist in the exchange only omega-baked into
    other elements' reactances. Same three classes, zero new ``emt:`` terms:
    one ``cim:DetailedModelDynamics`` per emitted equipment joined by
    ``cim:DetailedModelDynamics.Equipment``, one ``emt:LibraryModelType``
    per kind, one ``cim:ParameterValue`` per live quantity.

    The descriptor set is the union over the type's placements of what each
    states. That is the unmapped kinds' rule, and it holds here because
    liveness is per instance: a machine with ``IorMVA == 0`` states ``Ibase`` where its neighbour states
    ``MVA``, and both parameters belong to the one form. Units and
    typicalValue come from the form verbatim: the
    machine's p.u. numbers are stated AS p.u., and the reader converts
    nothing.

    Derivable redundancy with EQ's omega-baked attributes is accepted
    and stated, as in the passive statement: a transformer's Xl
    here and its end's x there derive one another where the base is
    known.
    """
    groups: dict[str, list] = {}
    for record in model.actives:
        groups.setdefault(record["defn"], []).append(record)
    for defn, members in groups.items():
        union: dict[str, dict] = {}
        for record in members:
            for parameter in record["parameters"]:
                union.setdefault(parameter["key"], parameter)
        # the form's own order, which each record preserves and `index`
        # remembers -- so the union of differently-gated placements still
        # sequences descriptors the way a reader of the form expects
        ordered = sorted(union.values(), key=lambda p: p["index"])
        type_key = ("definition", defn)
        seed = _type_seed(type_key, [(p["key"], p["spelling"])
                                     for p in ordered])
        subject = _named(emt, emt.equipment,
                         mrid(project, "LibraryModelType", seed),
                         EMT, "LibraryModelType", defn)
        emt.equipment.add((subject, EMT["LibraryModelType.modelingTool"],
                           _literal("PSCAD")))
        emt.equipment.add((subject, EMT["LibraryModelType.definitionName"],
                           _literal(defn)))
        if tool_version:
            emt.equipment.add((subject, EMT["LibraryModelType.toolVersion"],
                               _literal(tool_version)))
        descriptors: dict[str, URIRef] = {}
        for sequence, parameter in enumerate(ordered, start=1):
            descriptor = _named(
                emt, emt.equipment,
                mrid(project, "ParameterDescriptor",
                     f"{seed}/{parameter['key']}"),
                CIM, "ParameterDescriptor", parameter["spelling"],
            )
            emt.equipment.add(
                (descriptor,
                 CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
                 subject))
            emt.equipment.add(
                (descriptor, CIM["ParameterDescriptor.sequenceNumber"],
                 _literal(sequence)))
            if parameter["unit"]:
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.engineeringUnit"],
                     _literal(parameter["unit"])))
            # the form's own default: these descriptors DO describe form
            # parameters, so unlike the passive Branch-row positions each
            # has one to state (the unmapped kinds' rule)
            if parameter["typical"]:
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.typicalValue"],
                     _literal(parameter["typical"])))
            descriptors[parameter["key"]] = descriptor
        _add_model_ports(emt, subject, members[0]["definition"], seed,
                         project)

        for record in members:
            emt.metrics[f"emt_active: {record['kind']}"] += 1
            dmd = _named(
                emt, emt.equipment,
                mrid(project, "DetailedModelDynamics", record["seed"]),
                CIM, "DetailedModelDynamics", record["name"],
            )
            emt.equipment.add(
                (dmd, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"],
                 subject))
            emt.equipment.add((dmd, CIM["DynamicsFunctionBlock.enabled"],
                               _literal(True)))
            emt.equipment.add((dmd, CIM["DetailedModelDynamics.Equipment"],
                               _uri(record["mrid"])))
            for parameter in record["parameters"]:
                value = emt.mint(
                    emt.equipment,
                    mrid(project, "ParameterValue",
                         f"{record['seed']}/{parameter['key']}"),
                    CIM, "ParameterValue")
                emt.equipment.add(
                    (value, CIM["ParameterValue.DetailedModelDynamics"],
                     dmd))
                emt.equipment.add(
                    (value, CIM["ParameterValue.ParameterDescriptor"],
                     descriptors[parameter["key"]]))
                emt.equipment.add((value, CIM["ParameterValue.value"],
                                   _literal(parameter["value"])))
                emt.equipment.add((value,
                                   EMT["ParameterValue.numericValue"],
                                   _literal(parameter["value"])))


def _modeled_class(equipment: rdflib.Graph, dmd) -> str | None:
    """The CIM class a model's own stated type maps to, off the document
    alone: the join's type states a ``definitionName``, and
    :func:`pscx.rules.modeled_cim_class` says what that kind becomes.
    This is what partitions the equipment-joined models into the passive
    read-back and the active one without either consulting EQ."""
    type_iri = equipment.value(
        dmd, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"])
    defn = equipment.value(type_iri, EMT["LibraryModelType.definitionName"])
    return modeled_cim_class(None if defn is None else str(defn))


def passive_quantities(equipment: rdflib.Graph) -> dict[str, dict[str, float]]:
    """Each modeled equipment's stated primitives in SI, read from the
    DOCUMENT alone: ``equipment IRI -> {quantity name: SI value}``.

    This is the inverse of :func:`_add_passive_models`, kept beside the
    choice it inverts (the rule ``pscx.rules`` states for TWO_TERMINAL).
    It reads only what the interchange document says -- the
    ``cim:DetailedModelDynamics.Equipment`` joins, each value's
    descriptor for the quantity's name and stated unit, and
    ``emt:ParameterValue.numericValue`` -- and converts by the STATED
    unit, so a document whose unit and number disagree reads back wrong
    rather than being repaired. An engine rebuilding every passive from
    the emitted documents never touches the source file.

    Only the joins whose model TYPE states a mapped R/L/C kind: the
    driving components join their equipment through the same
    association, and their statement -- form parameters in the form's
    own units, p.u. included -- is :func:`active_parameters`' to read.
    The partition is the document's own (:func:`_modeled_class`).
    """
    from pscx.units import si_factor

    out: dict[str, dict[str, float]] = {}
    for dmd, target in equipment.subject_objects(
            CIM["DetailedModelDynamics.Equipment"]):
        if _modeled_class(equipment, dmd) != "EquivalentBranch":
            continue
        quantities = out.setdefault(str(target), {})
        for value in equipment.subjects(
                CIM["ParameterValue.DetailedModelDynamics"], dmd):
            descriptor = equipment.value(
                value, CIM["ParameterValue.ParameterDescriptor"])
            numeric = equipment.value(value,
                                      EMT["ParameterValue.numericValue"])
            if descriptor is None or numeric is None:
                continue
            name = str(equipment.value(descriptor,
                                       CIM["IdentifiedObject.name"]))
            unit = str(equipment.value(
                descriptor, CIM["ParameterDescriptor.engineeringUnit"]) or "")
            factor, _si = si_factor(unit)
            quantities[name] = float(numeric) * factor
    return out


def active_parameters(
        equipment: rdflib.Graph) -> dict[str, dict[str, tuple]]:
    """Each driving equipment's stated form parameters, read from the
    DOCUMENT alone: ``equipment IRI -> {parameter: (value, unit)}``.

    The inverse of :func:`_add_active_models`, and deliberately
    conversion-free where the passive one converts to SI: an active
    parameter's meaning is the form's -- a reactance in p.u. on the
    machine's own base IS the engine-facing number -- so the value comes
    back verbatim in the unit the descriptor states (None where the form
    declares none), and the oracle compares both halves. A document
    whose unit and number disagree therefore reads back wrong rather
    than being repaired, exactly like the passive read-back.
    """
    out: dict[str, dict[str, tuple]] = {}
    for dmd, target in equipment.subject_objects(
            CIM["DetailedModelDynamics.Equipment"]):
        if _modeled_class(equipment, dmd) not in ACTIVE_CIM_CLASSES:
            continue
        parameters = out.setdefault(str(target), {})
        for value in equipment.subjects(
                CIM["ParameterValue.DetailedModelDynamics"], dmd):
            descriptor = equipment.value(
                value, CIM["ParameterValue.ParameterDescriptor"])
            numeric = equipment.value(value,
                                      EMT["ParameterValue.numericValue"])
            if descriptor is None or numeric is None:
                continue
            name = str(equipment.value(descriptor,
                                       CIM["IdentifiedObject.name"]))
            unit = equipment.value(
                descriptor, CIM["ParameterDescriptor.engineeringUnit"])
            parameters[name] = (float(numeric),
                                None if unit is None else str(unit))
    return out


def _add_model_types(emt: EmtModel, placements: list, project: str,
                     tool_version) -> dict:
    """One emt:LibraryModelType per type, with its parameter descriptors.

    What a parameter IS -- its name, its position in the form, the unit it
    is quoted in -- belongs to the type and is written once; what a
    placement SETS it to is a cim:ParameterValue. Returns
    ``(type key -> subject, (type key, match key) -> descriptor)`` so a
    placement can join its type and each value the descriptor that
    describes it, without either call site re-deriving an mRID.
    """
    groups: dict[tuple, list] = {}
    for placement in placements:
        groups.setdefault(placement.type_key, []).append(placement)
    types, descriptors = {}, {}
    for type_key, members in groups.items():
        definition = members[0].definition
        parameters = _type_parameters(definition, [m.stated for m in members])
        seed = _type_seed(type_key, parameters)
        # a carrier that names no definition has only its own name to be
        # identified by, and definitionName is what makes the parameter
        # list interpretable, so it is never left unstated
        defn = members[0].carrier.defn or members[0].name
        subject = _named(emt, emt.equipment,
                         mrid(project, "LibraryModelType", seed),
                         EMT, "LibraryModelType", defn)
        types[type_key] = subject
        emt.equipment.add((subject, EMT["LibraryModelType.modelingTool"],
                           _literal("PSCAD")))
        emt.equipment.add((subject, EMT["LibraryModelType.definitionName"],
                           _literal(defn)))
        if tool_version:
            emt.equipment.add((subject, EMT["LibraryModelType.toolVersion"],
                               _literal(tool_version)))
        for sequence, (key, spelling) in enumerate(parameters, start=1):
            # seeded on the TYPE's seed, so a descriptor inherits the
            # parameter-set digest and cannot be shared by two types that
            # describe different sets
            descriptor = _named(
                emt, emt.equipment,
                mrid(project, "ParameterDescriptor", f"{seed}/{key}"),
                CIM, "ParameterDescriptor", spelling,
            )
            emt.equipment.add(
                (descriptor,
                 CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
                 subject))
            emt.equipment.add(
                (descriptor, CIM["ParameterDescriptor.sequenceNumber"],
                 _literal(sequence)))
            unit = definition.units.get(key) if definition is not None else None
            if unit:
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.engineeringUnit"],
                     _literal(unit)))
            # the form's own default, which is what the parameter is when
            # no placement overrides it. Only a form can state one: a
            # carrier with no ComponentDef has no default to read
            # and neither has a name the form never declared, and a blank
            # default states nothing rather than the empty string.
            typical = definition.defaults.get(spelling) \
                if definition is not None else None
            if typical is not None and str(typical).strip():
                emt.equipment.add(
                    (descriptor, CIM["ParameterDescriptor.typicalValue"],
                     _literal(str(typical).strip())))
            descriptors[(type_key, key)] = descriptor
        _add_model_ports(emt, subject, definition, seed, project)
    return types, descriptors


def _add_model_ports(emt: EmtModel, model_type: URIRef, definition,
                     seed: str, project: str) -> None:
    """One emt:ModelPort per port the type's definition declares.

    The declared interface belongs to the TYPE, exactly as its parameter
    descriptors do: what a port IS -- its name, its position, what kind
    of connection it makes, its declared width and signal type, the
    predicate under which it exists -- is written once, and every
    placement's emt:SignalConnection resolves against it by portName.
    Everything is stated verbatim in the source tool's own coding
    (mode, dataType, electricalType are enumerations PSCAD defines),
    because interpreting a tool's coding
    is the type's consumer's business, not the exchange's.

    A type with no definition behind it declares no ports, and
    a declared port with no name cannot be resolved against and is
    counted rather than emitted nameless.
    """
    if definition is None:
        return
    for sequence, port in enumerate(definition.ports, start=1):
        if not port.name:
            emt.metrics["emt_signal: port_unnamed"] += 1
            continue
        subject = _named(
            emt, emt.equipment,
            mrid(project, "ModelPort", f"{seed}/port/{sequence}"),
            EMT, "ModelPort", port.name,
        )
        emt.equipment.add((subject,
                           EMT["ModelPort.DetailedModelTypeDynamics"],
                           model_type))
        emt.equipment.add((subject, EMT["ModelPort.portName"],
                           _literal(port.name)))
        emt.equipment.add((subject, EMT["ModelPort.sequenceNumber"],
                           _literal(sequence)))
        emt.equipment.add((subject, EMT["ModelPort.mode"],
                           _literal(port.mode)))
        # the declared width, verbatim: a number, or the name of the
        # parameter that holds it where the declaration defers
        emt.equipment.add((subject, EMT["ModelPort.dimension"],
                           _literal(port.dim_name or port.dim)))
        emt.equipment.add((subject, EMT["ModelPort.dataType"],
                           _literal(port.datatype)))
        emt.equipment.add((subject, EMT["ModelPort.electricalType"],
                           _literal(port.electype)))
        emt.equipment.add((subject, EMT["ModelPort.internal"],
                           _literal(port.internal)))
        emt.equipment.add((subject, EMT["ModelPort.condition"],
                           _literal(port.condition)))
        emt.metrics["emt_signal: model_port"] += 1


def _add_parameter_values(emt: EmtModel, placement, subject: URIRef,
                          descriptors: dict, project: str) -> None:
    """One cim:ParameterValue per parameter one placement states.

    ParameterValue is the one detailed-model class that is not an
    IdentifiedObject: it carries no mRID and no name because what the
    parameter is has already been said on the descriptor, and this says
    only what this placement sets it to.
    """
    for key, spelling in placement.stated:
        value = emt.mint(emt.equipment,
                         mrid(project, "ParameterValue",
                              f"{placement.seed}/{key}"),
                         CIM, "ParameterValue")
        emt.equipment.add((value, CIM["ParameterValue.DetailedModelDynamics"],
                           subject))
        emt.equipment.add((value, CIM["ParameterValue.ParameterDescriptor"],
                           descriptors[(placement.type_key, key)]))
        if placement.signal:
            if key in placement.text:
                # a recording block's text, verbatim: what the study
                # records its results as. Text carries no numericValue,
                # which is how the inverse tells the two apart.
                emt.equipment.add((value, CIM["ParameterValue.value"],
                                   _literal(placement.text[key])))
                continue
            # a signal block's statement is the driving components' rule:
            # the LIVE set already evaluated, the number verbatim in the
            # form's declared unit as both the value and the numericValue
            numeric = placement.live[key]["value"]
            emt.equipment.add((value, CIM["ParameterValue.value"],
                               _literal(numeric)))
            emt.equipment.add((value, EMT["ParameterValue.numericValue"],
                               _literal(numeric)))
            continue
        raw = placement.carrier.params.get(spelling)
        emt.equipment.add((value, CIM["ParameterValue.value"],
                           _literal(str(raw).strip())))
        numeric = _probe_numeric(placement.inst, placement.carrier, spelling)
        if numeric is None:
            emt.diagnostics.emit("emt_parameter_not_numeric", spelling,
                                 provenance=placement.where)
        else:
            # core ParameterValue carries only the stated String; the
            # number this placement evaluates to is per PLACEMENT, so it
            # cannot go on the descriptor
            emt.equipment.add((value, EMT["ParameterValue.numericValue"],
                               _literal(numeric)))
    if placement.signal:
        # a live numeric parameter an input signal or unresolvable name
        # drives is absent and counted, never invented -- and a block
        # whose live numeric set is empty (a logic gate) is a normal
        # outcome for this population, not a finding
        emt.metrics["emt_signal: parameter_not_numeric"] += len(
            placement.unresolved)
        if not placement.stated:
            emt.metrics["emt_signal: model_no_parameters"] += 1
    elif not placement.stated:
        emt.diagnostics.emit("emt_model_no_parameters",
                             provenance=placement.where)


def _add_terminals(emt: EmtModel, model: CimModel, subject: URIRef, seed: str,
                   project: str, name: str, ends, provenance) -> None:
    """One ModelTerminal per PER-PHASE connection point the EQ document
    knows.

    ModelTerminal.ConnectivityNode is mandatory, and a ConnectivityNode
    exists only where the equipment document put one: a node whose only
    members are components CIM cannot express has none. Emitting a
    terminal there would produce a reference that resolves to nothing and
    raises no error, so the end is dropped and counted instead.

    ``ModelTerminal.phase`` is what keeps the resolution the standard
    documents summarise. A drawn node is one ConnectivityNode however
    many conductors it carries, so a terminal that named only the node
    would not say which conductor a valve or an MMC cell is wired to,
    and for a converter-heavy case that IS the topology.
    """
    sequence = 0
    for key, phase in ends:
        node_mrid = mrid(project, "ConnectivityNode", repr(key))
        if node_mrid not in model.resources:
            emt.diagnostics.emit("emt_terminal_node_absent",
                                 provenance=provenance)
            continue
        sequence += 1
        terminal = _named(
            emt, emt.equipment,
            mrid(project, "ModelTerminal", f"{seed}/{sequence}"),
            EMT, "ModelTerminal", f"{name}:T{sequence}",
        )
        emt.equipment.add((terminal,
                           EMT["ModelTerminal.DetailedModelDynamics"],
                           subject))
        emt.equipment.add((terminal, EMT["ModelTerminal.sequenceNumber"],
                           _literal(sequence)))
        emt.equipment.add((terminal, EMT["ModelTerminal.ConnectivityNode"],
                           _uri(node_mrid)))
        emt.equipment.add((terminal, EMT["ModelTerminal.phase"],
                           _literal(phase)))
    if not sequence:
        # the model carries its parameters but joins to nothing in the
        # equipment document; a consumer can read it, not place it
        emt.diagnostics.emit("emt_model_unanchored", provenance=provenance)


def _add_row_records(emt: EmtModel, records, parent: URIRef, seed: str,
                     project: str) -> None:
    """One ``emt:RightOfWayRecord`` per evaluated row, nested as the
    source grammar nests. Seeded by the sibling-ordinal path under the
    canvas, which is the same identity the sequence numbers state."""
    for ordinal, record in enumerate(records, start=1):
        subject = emt.mint(
            emt.equipment,
            mrid(project, "RightOfWayRecord", f"{seed}/{ordinal}"),
            EMT, "RightOfWayRecord")
        emt.equipment.add((subject, EMT["RightOfWayRecord.ParentRecord"],
                           parent))
        emt.equipment.add((subject, EMT["RightOfWayRecord.key"],
                           _literal(record.key)))
        emt.equipment.add((subject, EMT["RightOfWayRecord.sequenceNumber"],
                           _literal(ordinal)))
        if record.values:
            emt.equipment.add((subject, EMT["RightOfWayRecord.values"],
                               _literal(" ".join(record.values))))
        if record.unit:
            emt.equipment.add((subject, EMT["RightOfWayRecord.unit"],
                               _literal(record.unit)))
        emt.metrics["emt_line: record"] += 1
        emt.metrics["emt_line: value"] += len(record.values)
        _add_row_records(emt, record.children, subject, f"{seed}/{ordinal}",
                         project)


def _add_line_records(emt: EmtModel, flat: FlatProject, model: CimModel,
                      project: str) -> None:
    """Every referenced right-of-way's evaluated record, and each hosting
    wire's own values, joined wire -> right-of-way.

    The engine-side line statement (the fifth fidelity statement, after
    the passives, the actives, the control blocks and the signal graph):
    what EQ carries for a line is the one-frequency solved projection,
    which cannot state the frequency-dependent models most live wires
    select, and no standard class carries conductor geometry at all. So
    the interchange states the evaluated record itself -- geometry or
    matrices, the earth, and the model options whose KIND is the model
    choice, as root rows -- once per referenced RowCanvas, since the
    record is a per-canvas constant; per wire it states only what is
    per-placement (length, frequency, conductor count) and the join.

    The subject is seeded by the canvas DEFINITION, the type space's
    precedent for definition-keyed identity; only a canvas some wire
    references is stated (an unreferenced one parameterises no equipment
    and belongs to the source's definition record); a wire whose ``defn``
    dangles states its own values and no join -- there is no record to
    invent. ``Line_Out_Disp`` is display furniture (output-file base
    quantities) and is counted out, like the drawing-only components.

    A wire with a standard segment carries the statement on its
    ``cim:Line`` by the phaseCount idiom; a hosted device with no
    standard class (the Cable) carries the join on its detailed model,
    whose parameter values already state the wire's paramlist verbatim.
    """
    from pscx.cim import _where
    from pscx.lineconst import (
        _conductors,
        _frequency_hz,
        declared_length,
        evaluated_records,
    )
    from pscx.rules import DEVICE_KIND_TO_CIM

    subjects: dict[str, URIRef] = {}

    def canvas_subject(canvas: str, netlist) -> URIRef:
        if canvas in subjects:
            return subjects[canvas]
        subject = _named(emt, emt.equipment,
                         mrid(project, "RightOfWay", f"definition/{canvas}"),
                         EMT, "RightOfWay", canvas)
        subjects[canvas] = subject
        emt.metrics["emt_line: right_of_way"] += 1
        ordinal = 0
        for comp in netlist.components:
            definition = comp.definition
            if definition is None or not definition.model_data_text:
                continue
            kind = comp.master_kind or comp.kind
            if kind == "Line_Out_Disp":
                emt.metrics["emt_line: display_only"] += 1
                continue
            ordinal += 1
            root = emt.mint(
                emt.equipment,
                mrid(project, "RightOfWayRecord",
                     f"definition/{canvas}/{ordinal}"),
                EMT, "RightOfWayRecord")
            emt.equipment.add((root, EMT["RightOfWayRecord.RightOfWay"],
                               subject))
            emt.equipment.add((root, EMT["RightOfWayRecord.key"],
                               _literal(kind)))
            emt.equipment.add((root,
                               EMT["RightOfWayRecord.sequenceNumber"],
                               _literal(ordinal)))
            emt.metrics[f"emt_line: component {kind}"] += 1
            _add_row_records(emt, evaluated_records(comp), root,
                             f"definition/{canvas}/{ordinal}", project)
        return subject

    for inst in flat.instances:
        for dev in inst.netlist.devices:
            canvas = (dev.defn or "").partition(":")[2] or (dev.defn or "")
            netlist = flat.right_of_way(dev.defn or "")
            wid = dev.terminal_a[1]
            if DEVICE_KIND_TO_CIM.get(dev.kind) is not None:
                line_mrid = mrid(project, "Line", f"{inst.path}/{wid}")
                if line_mrid not in model.resources:
                    # the projection minted no cim:Line for this wire, so
                    # there is no subject to state the values on
                    emt.metrics["emt_line: wire_without_equipment"] += 1
                    continue
                subject = _uri(line_mrid)
                where = _where(project, inst, element=str(wid))
                length = declared_length(dev)
                if length is None:
                    emt.diagnostics.emit("emt_line_value_unresolved",
                                         "Length", provenance=where)
                else:
                    value, unit = length
                    emt.equipment.add((subject, EMT["Line.length"],
                                       _literal(f"{value!r} [{unit}]")))
                frequency = _frequency_hz(inst, dev)
                if frequency is None:
                    emt.diagnostics.emit("emt_line_value_unresolved",
                                         "Freq", provenance=where)
                else:
                    emt.equipment.add((subject, EMT["Line.frequency"],
                                       _literal(f"{frequency!r} [Hz]")))
                emt.equipment.add((subject, EMT["Line.conductorCount"],
                                   _literal(_conductors(dev))))
                if netlist is not None:
                    emt.equipment.add((subject, EMT["Line.RightOfWay"],
                                       canvas_subject(canvas, netlist)))
                    emt.metrics["emt_line: wire_joined"] += 1
                else:
                    emt.metrics["emt_line: wire_dangling"] += 1
            elif netlist is not None:
                # the hosted device's detailed model is its statement, and
                # the placement seed is the wire's own
                dmd = mrid(project, "DetailedModelDynamics",
                           f"{inst.path}/{wid}")
                if dmd in emt.minted:
                    emt.equipment.add(
                        (_uri(dmd), EMT["DetailedModelDynamics.RightOfWay"],
                         canvas_subject(canvas, netlist)))
                    emt.metrics["emt_line: wire_joined"] += 1


def right_of_way_statements(equipment: rdflib.Graph) -> dict[str, dict]:
    """Every stated right-of-way's evaluated record, read from the
    DOCUMENT alone: ``subject IRI -> {name, records}``.

    The inverse of :func:`_add_line_records`' canvas half, in the shape
    the other read-backs established. Each record row is ``{key, values,
    unit, records}`` -- values and unit None where the row states none --
    with children ordered by their stated sequenceNumber, so a document
    that drops a row, blanks a unit or reorders two conductor positions
    reads back wrong rather than being repaired.
    """
    rows: dict[Any, dict] = {}
    children: dict[Any, list] = {}
    roots: dict[Any, list] = {}
    for subject in equipment.subjects(RDF.type, EMT["RightOfWayRecord"]):
        key = equipment.value(subject, EMT["RightOfWayRecord.key"])
        values = equipment.value(subject, EMT["RightOfWayRecord.values"])
        unit = equipment.value(subject, EMT["RightOfWayRecord.unit"])
        sequence = equipment.value(subject,
                                   EMT["RightOfWayRecord.sequenceNumber"])
        rows[subject] = {
            "key": None if key is None else str(key),
            "values": None if values is None else str(values),
            "unit": None if unit is None else str(unit),
            "sequence": 0 if sequence is None else int(sequence),
        }
        parent = equipment.value(subject,
                                 EMT["RightOfWayRecord.ParentRecord"])
        if parent is not None:
            children.setdefault(parent, []).append(subject)
        else:
            row = equipment.value(subject,
                                  EMT["RightOfWayRecord.RightOfWay"])
            roots.setdefault(row, []).append(subject)

    def tree(subject) -> dict:
        entry = rows[subject]
        return {
            "key": entry["key"], "values": entry["values"],
            "unit": entry["unit"],
            "records": [tree(child) for child in sorted(
                children.get(subject, []),
                key=lambda s: rows[s]["sequence"])],
        }

    out: dict[str, dict] = {}
    for subject in equipment.subjects(RDF.type, EMT["RightOfWay"]):
        name = equipment.value(subject, CIM["IdentifiedObject.name"])
        out[str(subject)] = {
            "name": None if name is None else str(name),
            "records": [tree(root) for root in sorted(
                roots.get(subject, []),
                key=lambda s: rows[s]["sequence"])],
        }
    return out


def wire_rights_of_way(equipment: rdflib.Graph) -> dict[str, dict]:
    """Each hosting wire's statement, read from the DOCUMENT alone:
    ``wire subject IRI -> {right_of_way, length, frequency,
    conductor_count}``.

    The inverse of :func:`_add_line_records`' wire half, covering both
    wire shapes the emitter writes: a ``cim:Line`` subject carrying the
    three value attributes and the join, and a hosted device's detailed
    model carrying the join alone (its values are its own parameter
    values, :func:`block_bodies`' to read). ``right_of_way`` is None on
    a wire the document joins to nothing -- the dangling population --
    so a caller can tell "no record" from "not stated" only by the
    wire being in this map at all, which is the point.
    """
    out: dict[str, dict] = {}

    def entry(subject) -> dict:
        return out.setdefault(str(subject), {
            "right_of_way": None, "length": None, "frequency": None,
            "conductor_count": None})

    for subject, value in equipment.subject_objects(EMT["Line.length"]):
        entry(subject)["length"] = str(value)
    for subject, value in equipment.subject_objects(EMT["Line.frequency"]):
        entry(subject)["frequency"] = str(value)
    for subject, value in equipment.subject_objects(
            EMT["Line.conductorCount"]):
        entry(subject)["conductor_count"] = int(value)
    for subject, target in equipment.subject_objects(EMT["Line.RightOfWay"]):
        entry(subject)["right_of_way"] = str(target)
    for subject, target in equipment.subject_objects(
            EMT["DetailedModelDynamics.RightOfWay"]):
        entry(subject)["right_of_way"] = str(target)
    return out


def _flat_signal_key(flat: FlatProject, inst, point) -> Any:
    """The flat signal-net key of one canvas point, through both DSUs."""
    return flat.signal_dsu.find(
        (inst.index, inst.netlist.signal_dsu.find(point)))


def _net_display_name(fnet) -> str:
    """A deterministic display name for one flat net: the first of its
    canvas-scoped names, or a placeholder for the drawn-wire-only nets
    that carry none. Display only -- identity is the mRID, and every
    name the net carries stays resolvable through the connections."""
    names = sorted((str(name).casefold(), str(name)) for _i, name in
                   fnet.names)
    return names[0][1] if names else "signal"


def _add_signal_graph(emt: EmtModel, flat: FlatProject, model: CimModel,
                      placements: list, project: str) -> None:
    """The flattened control-signal graph: the third boundary-1 statement.

    Per FLAT net and FLAT placement, never per drawn element -- the
    drawing states the graph through four kinds of clue (wires joined by
    coordinate, canvas-scoped name matches, ``#OUTPUT`` writer
    declarations, wireless joins), and exchanging the clues would force
    every consumer to reimplement PSCAD's resolution rules. The resolved
    graph is exchanged instead, exactly as ConnectivityNodes exchange
    flat electrical nodes and not drawn wires; the clues stay
    source-side for reconstruction. One ``emt:SignalNet`` per flat net,
    one ``emt:SignalConnection`` per endpoint, plus two joins the graph
    alone does not state:

    - a switch reads the net named by its own control parameter
      (``CimModel.switches``), stated as a ``switchControl`` connection
      so the driver RELATIONSHIP travels beside SSH's t=0 outcome;
    - within-case radiolink pairs become
      ``emt:SignalNet.TransmittingSignalNet`` on the receiving net.
      Cross-project (rccon=1) links pair across cases and
      cannot be stated by one case's documents; they are counted.

    Endpoints join the engine-facing subject wherever the documents mint
    one: equipment through the passive/active/switch ledgers, absorbed
    models (the unmapped electrical kinds and the signal blocks) through
    their placements, measurements through the Analog ledger. ``placementPath``
    stays on every endpoint, anchored or not: the path is the endpoint's
    identity in the drawn world and the IRI is its join in the engine's, and
    dropping the path where a join exists would make the source-side
    correspondence conditional on the engine-side one. What still carries a
    path alone is counted as unanchored: module form-parameter constants and
    module writer endpoints (pages, not equipment) and the signal pins of
    structural electrical kinds (their electrical role IS the graph).
    """
    subjects: dict[Any, URIRef] = {}
    for fnet in flat.signal_nets:
        subject = _named(emt, emt.equipment,
                         mrid(project, "SignalNet", repr(fnet.key)),
                         EMT, "SignalNet", _net_display_name(fnet))
        subjects[fnet.key] = subject
        if fnet.dim is not None:
            emt.equipment.add((subject, EMT["SignalNet.width"],
                               _literal(fnet.dim)))
        emt.equipment.add((subject, EMT["SignalNet.widthConflict"],
                           _literal(fnet.dim_conflict)))
        emt.metrics["emt_signal: net"] += 1

    targets: dict[tuple, list[str]] = {}
    for record in (model.passives + model.actives + model.switches
                   + model.measurements):
        targets.setdefault((record["instance"], record["element"]),
                           []).append(record["mrid"])
    for placement in placements:
        element_id = getattr(placement.carrier, "element_id", None)
        if element_id is not None:
            targets.setdefault((placement.inst.index, element_id), []).append(
                mrid(project, "DetailedModelDynamics", placement.seed))

    def connect(net_key: Any, seed: str, kind: str, writes: bool,
                port_name: str | None, path: str,
                anchor: tuple | None) -> URIRef:
        connection = emt.mint(emt.equipment,
                              mrid(project, "SignalConnection", seed),
                              EMT, "SignalConnection")
        emt.equipment.add((connection, EMT["SignalConnection.SignalNet"],
                           subjects[net_key]))
        emt.equipment.add((connection, EMT["SignalConnection.connectionKind"],
                           _literal(kind)))
        emt.equipment.add((connection, EMT["SignalConnection.writes"],
                           _literal(writes)))
        if port_name is not None:
            emt.equipment.add((connection, EMT["SignalConnection.portName"],
                               _literal(port_name)))
        emt.equipment.add((connection, EMT["SignalConnection.placementPath"],
                           _literal(path)))
        joined = targets.get(anchor, ()) if anchor is not None else ()
        for target in sorted(joined):
            emt.equipment.add((connection,
                               EMT["SignalConnection.IdentifiedObject"],
                               _uri(target)))
        emt.metrics[f"emt_signal: {kind}"] += 1
        emt.metrics["emt_signal: anchored" if joined
                    else "emt_signal: unanchored"] += 1
        return connection

    def endpoint(net_key: Any, seed: str, entry, writes: bool,
                 width, tap: tuple | None = None) -> None:
        if entry[0] == "port":
            _tag, inst, comp, port = entry
            kind = "tap" if tap is not None else "port"
            connection = connect(
                net_key, seed, kind, writes, port.name,
                f"{inst.path}/{comp.element_id}",
                (inst.index, comp.element_id))
            if tap is not None:
                _inst, _comp, offset, tap_width = tap
                if offset is not None:
                    emt.equipment.add(
                        (connection, EMT["SignalConnection.offset"],
                         _literal(offset)))
                width = tap_width
        elif entry[0] == "writer":
            _tag, inst, comp, param, _name = entry
            connection = connect(
                net_key, seed, "writer", writes, param,
                f"{inst.path}/{comp.element_id}",
                (inst.index, comp.element_id))
        elif entry[0] == "form_param":
            _tag, inst, name = entry
            connection = connect(net_key, seed, "formParameter", writes,
                                 name, inst.path, None)
            value = inst.env.get(name)
            if value is not None:
                emt.equipment.add((connection, EMT["SignalConnection.value"],
                                   _literal(value)))
        else:  # boundary_in / boundary_out: the flattened root's interface
            _tag, inst, name = entry
            kind = "boundaryIn" if entry[0] == "boundary_in" else "boundaryOut"
            connection = connect(net_key, seed, kind, writes, name,
                                 inst.path, None)
        if width is not None:
            emt.equipment.add((connection, EMT["SignalConnection.width"],
                               _literal(width)))

    for fnet in flat.signal_nets:
        net_seed = repr(fnet.key)
        ordinal = 0
        taps = iter(fnet.taps)
        for entry, width in zip(fnet.drivers, fnet.driver_widths):
            ordinal += 1
            endpoint(fnet.key, f"{net_seed}/{ordinal}", entry, True, width)
        for entry, width in zip(fnet.readers, fnet.reader_widths):
            ordinal += 1
            # a datatap's A pin is the slice reader whose offset and
            # width travelled in ``taps``, in reader order (elaborate)
            tap = None
            if (entry[0] == "port" and entry[2].master_kind == "datatap"
                    and (entry[3].name or "").upper() == "A"):
                tap = next(taps)
            endpoint(fnet.key, f"{net_seed}/{ordinal}", entry, False, width,
                     tap=tap)

    # ---- the switch-control join (by name, not by drawn pin) ------------
    nets_by_name = {}
    for fnet in flat.signal_nets:
        for inst_index, name in fnet.names:
            nets_by_name[(inst_index, str(name).casefold())] = fnet
    grouped: dict[tuple, list] = {}
    for record in model.switches:
        if not record["signal"]:
            emt.metrics["emt_signal: switch_unnamed"] += 1
            continue
        grouped.setdefault((record["instance"], record["element"],
                            record["signal"]), []).append(record)
    for inst_index, element, signal in grouped:
        fnet = nets_by_name.get((inst_index, signal.casefold()))
        if fnet is None:
            emt.metrics["emt_signal: switch_signal_unresolved"] += 1
            continue
        inst = flat.instances[inst_index]
        connect(fnet.key, f"{fnet.key!r}/switch/{element}",
                "switchControl", False, signal, f"{inst.path}/{element}",
                (inst_index, element))

    # ---- the recorder-channel join (by slot name, not by drawn pin) ------
    # A recorder2_0 reads the signals its slot parameters NAME, and the
    # form's own channel counts (NChA, NChD) say which slots are in use:
    # a slot beyond the count holds placeholder text and is not data --
    # the same selector-respect the live-parameter rule applies to conds.
    # Each live slot becomes a reader connection joining the recorder's
    # body to the named net, exactly as a switch joins the net its Name
    # parameter states; a live slot naming no net is counted.
    for placement in placements:
        if placement.kind != "recorder2_0":
            continue
        inst_index = placement.inst.index
        element = placement.carrier.element_id
        for prefix, count_key in (("NA", "ncha"), ("ND", "nchd")):
            entry = placement.live.get(count_key) if placement.live else None
            count = int(entry["value"]) if entry is not None else 0
            for slot in range(1, count + 1):
                spelling = f"{prefix}{slot}"
                name = (placement.text.get(spelling.lower()) or "").strip()
                fnet = (nets_by_name.get((inst_index, name.casefold()))
                        if name else None)
                if fnet is None:
                    emt.metrics[
                        "emt_signal: recorder_channel_unresolved"] += 1
                    continue
                connect(fnet.key,
                        f"{fnet.key!r}/recorder/{element}/{spelling}",
                        "recorderChannel", False, spelling,
                        f"{placement.inst.path}/{element}",
                        (inst_index, element))

    # ---- within-case radiolink pairing -----------------------------------
    for tx_end, rx_end in flat.radio_pairs:
        keys = {}
        for role, end, reads in (("tx", tx_end, True), ("rx", rx_end, False)):
            port = next((p for p in end.component.ports if p.is_signal
                         and p.is_signal_source != reads), None)
            keys[role] = None if port is None else _flat_signal_key(
                flat, end.instance, port.point)
        if keys["tx"] in subjects and keys["rx"] in subjects:
            emt.equipment.add((subjects[keys["rx"]],
                               EMT["SignalNet.TransmittingSignalNet"],
                               subjects[keys["tx"]]))
            emt.metrics["emt_signal: radio_pair"] += 1
        else:
            emt.metrics["emt_signal: radio_pair_unstated"] += 1
    emt.metrics["emt_signal: radio_cross_project"] += len(
        flat.cross_radio_ends)


def signal_graph(equipment: rdflib.Graph) -> dict[str, dict]:
    """The flattened control-signal graph, read from the DOCUMENT alone:
    ``net IRI -> {name, width, conflict, connections, transmitters}``.

    The inverse of :func:`_add_signal_graph`, in the shape the passive
    and active read-backs established: it reads only what the
    interchange document says -- the net subjects, each connection's
    kind, direction, identifiers and joins, the tap slices, the
    radiolink pairing -- so a document that mis-states a direction, a
    width, a slice or a pairing reads back wrong rather than being
    repaired. ``connections`` is a list of dicts, one per endpoint,
    with ``targets`` sorted; list order is not meaningful and a
    comparison treats it as a multiset.
    """
    nets: dict[str, dict] = {}
    for subject in equipment.subjects(RDF.type, EMT["SignalNet"]):
        width = equipment.value(subject, EMT["SignalNet.width"])
        conflict = equipment.value(subject, EMT["SignalNet.widthConflict"])
        nets[str(subject)] = {
            "name": str(equipment.value(subject,
                                        CIM["IdentifiedObject.name"])),
            "width": None if width is None else int(width),
            "conflict": str(conflict) == "true",
            "connections": [],
            "transmitters": sorted(
                str(tx) for tx in equipment.objects(
                    subject, EMT["SignalNet.TransmittingSignalNet"])),
        }
    for connection in equipment.subjects(RDF.type, EMT["SignalConnection"]):
        net = equipment.value(connection, EMT["SignalConnection.SignalNet"])
        if net is None or str(net) not in nets:
            continue
        entry: dict[str, Any] = {}
        for field_name, term in (("kind", "connectionKind"),
                                 ("port", "portName"),
                                 ("path", "placementPath"),
                                 ("value", "value")):
            value = equipment.value(connection,
                                    EMT[f"SignalConnection.{term}"])
            entry[field_name] = None if value is None else str(value)
        entry["writes"] = str(equipment.value(
            connection, EMT["SignalConnection.writes"])) == "true"
        for field_name in ("width", "offset"):
            value = equipment.value(
                connection, EMT[f"SignalConnection.{field_name}"])
            entry[field_name] = None if value is None else int(value)
        entry["targets"] = sorted(
            str(t) for t in equipment.objects(
                connection, EMT["SignalConnection.IdentifiedObject"]))
        nets[str(net)]["connections"].append(entry)
    return nets


def block_bodies(equipment: rdflib.Graph) -> dict[str, dict]:
    """Every detailed model's stated type and numeric parameters, read
    from the DOCUMENT alone: ``model IRI -> {definition, equipment,
    parameters}``.

    The inverse the signal graph's ``IdentifiedObject`` joins resolve
    against: an endpoint names a subject, and this says what that
    subject IS -- its type's stated ``definitionName`` and, per
    parameter, ``(numericValue, stated unit)`` verbatim, the driving
    components' conversion-free rule. It covers every
    ``cim:DetailedModelDynamics`` whichever population minted it;
    ``equipment`` carries the ``DetailedModelDynamics.Equipment`` target
    where one is stated and None for the equipment-less absorbed models,
    so a caller can partition exactly as the document does. A document
    that drops a value, mis-states a unit or types a model against the
    wrong definition reads back wrong rather than being repaired.
    """
    out: dict[str, dict] = {}
    for dmd in equipment.subjects(RDF.type, CIM["DetailedModelDynamics"]):
        type_iri = equipment.value(
            dmd, CIM["DetailedModelDynamics.DetailedModelTypeDynamics"])
        defn = equipment.value(type_iri,
                               EMT["LibraryModelType.definitionName"])
        joined = equipment.value(dmd, CIM["DetailedModelDynamics.Equipment"])
        entry: dict[str, Any] = {
            "definition": None if defn is None else str(defn),
            "equipment": None if joined is None else str(joined),
            "parameters": {},
        }
        for value in equipment.subjects(
                CIM["ParameterValue.DetailedModelDynamics"], dmd):
            descriptor = equipment.value(
                value, CIM["ParameterValue.ParameterDescriptor"])
            numeric = equipment.value(value,
                                      EMT["ParameterValue.numericValue"])
            if descriptor is None or numeric is None:
                continue
            name = str(equipment.value(descriptor,
                                       CIM["IdentifiedObject.name"]))
            unit = equipment.value(
                descriptor, CIM["ParameterDescriptor.engineeringUnit"])
            entry["parameters"][name] = (float(numeric),
                                         None if unit is None else str(unit))
        out[str(dmd)] = entry
    return out


def output_channels(equipment: rdflib.Graph,
                    study: rdflib.Graph | None = None) -> dict[str, Any]:
    """What the study records, read from the interchange DOCUMENTS alone:
    the pgb channels, the recorder2_0 file recorders, and the rate.

    The channel list an EMTDC run writes is a projection of the drawing
    -- every recorded channel is a pgb placement, its attributes are the
    placement's own form parameters, its dim is the signal net's width
    and its rate is the plot step -- so the enumeration is read back
    from what the documents already state: the block bodies for the
    typed, parameterized placements (title text on the subject's name,
    group and unit as verbatim text values, scale, limits and the
    transfer/multiple-run/polar flags as numeric values), the signal
    graph for each channel's net and width, and the study document for
    the rate. Channel ORDER is deliberately absent: the recorded index
    is the solver's own execution ordering, a per-run product like an
    SSH state, not a statement of the drawing. A document that drops a
    text value, joins a channel to the wrong net or mis-states a width
    reads back wrong rather than being repaired.
    """
    bodies = block_bodies(equipment)
    text_of: dict[str, dict[str, str]] = {iri: {} for iri in bodies}
    for value in equipment.subjects(RDF.type, CIM["ParameterValue"]):
        owner = equipment.value(value,
                                CIM["ParameterValue.DetailedModelDynamics"])
        if owner is None or str(owner) not in text_of:
            continue
        if equipment.value(value, EMT["ParameterValue.numericValue"]) \
                is not None:
            continue
        descriptor = equipment.value(
            value, CIM["ParameterValue.ParameterDescriptor"])
        stated = equipment.value(value, CIM["ParameterValue.value"])
        if descriptor is None or stated is None:
            continue
        name = str(equipment.value(descriptor, CIM["IdentifiedObject.name"]))
        text_of[str(owner)][name] = str(stated)

    readers: dict[str, list[dict]] = {}
    for connection in equipment.subjects(RDF.type, EMT["SignalConnection"]):
        kind = equipment.value(connection,
                               EMT["SignalConnection.connectionKind"])
        if str(kind) not in ("port", "recorderChannel"):
            continue
        if str(equipment.value(connection,
                               EMT["SignalConnection.writes"])) == "true":
            continue
        net = equipment.value(connection, EMT["SignalConnection.SignalNet"])
        width = equipment.value(net, EMT["SignalNet.width"]) \
            if net is not None else None
        port = equipment.value(connection, EMT["SignalConnection.portName"])
        entry = {
            "kind": str(kind),
            "slot": None if port is None else str(port),
            "net": None if net is None else str(
                equipment.value(net, CIM["IdentifiedObject.name"])),
            "width": None if width is None else int(width),
        }
        for target in equipment.objects(
                connection, EMT["SignalConnection.IdentifiedObject"]):
            readers.setdefault(str(target), []).append(entry)

    out: dict[str, Any] = {"plotStep": None, "channels": {}, "recorders": {}}
    if study is not None:
        for case in study.subjects(RDF.type, EMT["SimulationCase"]):
            step = study.value(case, EMT["SimulationCase.plotStep"])
            if step is not None:
                out["plotStep"] = float(step)
    for iri, body in bodies.items():
        if body["definition"] not in ("master:pgb", "master:recorder2_0"):
            continue
        recorder = body["definition"] == "master:recorder2_0"
        name = equipment.value(URIRef(iri), CIM["IdentifiedObject.name"])
        entry = {
            "title": None if name is None else str(name),
            "text": text_of.get(iri, {}),
            "numeric": {key: value for key, (value, _unit)
                        in body["parameters"].items()},
            # a pgb reads its one drawn pin; a recorder reads its named
            # slots -- the join kind is the population's own
            "reads": sorted(
                ((r["slot"], r["net"], r["width"])
                 for r in readers.get(iri, ())
                 if r["kind"] == ("recorderChannel" if recorder
                                  else "port")),
                key=lambda item: (item[0] or "", item[1] or "")),
        }
        out["recorders" if recorder else "channels"][iri] = entry
    return out


def declared_ports(equipment: rdflib.Graph) -> dict[str, list[dict]]:
    """Every model type's declared interface, read from the DOCUMENT
    alone: ``type IRI -> ports in sequence order``.

    The inverse of :func:`_add_model_ports`. Each port is the seven-key
    declaration verbatim -- name, mode, dimension, dataType,
    electricalType, internal, condition -- ordered by the stated
    sequenceNumber, so a document that drops a port, renames one or
    reorders two reads back wrong rather than being repaired.
    """
    ports: dict[str, list] = {}
    for subject in equipment.subjects(RDF.type, EMT["ModelPort"]):
        owner = equipment.value(
            subject, EMT["ModelPort.DetailedModelTypeDynamics"])
        if owner is None:
            continue
        entry = {"sequence": int(equipment.value(
            subject, EMT["ModelPort.sequenceNumber"]))}
        for key in ("portName", "mode", "dimension", "dataType",
                    "electricalType", "condition"):
            value = equipment.value(subject, EMT[f"ModelPort.{key}"])
            entry[key] = None if value is None else str(value)
        entry["internal"] = str(equipment.value(
            subject, EMT["ModelPort.internal"])) == "true"
        ports.setdefault(str(owner), []).append(entry)
    return {owner: sorted(entries, key=lambda e: e["sequence"])
            for owner, entries in ports.items()}


def _add_simulation_case(emt: EmtModel, settings: dict,
                         project: str) -> None:
    """The study settings, in SI seconds.

    A setting the project does not state is counted and omitted -- the
    profile has no way to say "unknown timestep" and a fabricated one
    would be indistinguishable from a stated one.
    """
    subject = _named(emt, emt.study,
                     mrid(project, "SimulationCase", "settings"),
                     EMT, "SimulationCase", project)
    for param, (attribute, divisor) in SETTINGS_SECONDS.items():
        value = _as_float(settings.get(param))
        if value is None or value <= 0.0:
            emt.diagnostics.emit("emt_setting_missing", param)
            continue
        emt.study.add((subject, EMT[f"SimulationCase.{attribute}"],
                       _literal(value / divisor)))
    start_type = _as_float(settings.get("StartType")) or 0.0
    emt.study.add((subject, EMT["SimulationCase.startFromSnapshot"],
                   _literal(bool(start_type))))
    # SnapType 0 means the run writes no snapshot, and SnapTime keeps
    # whatever value the dialog last held -- a time nothing happens at.
    if _as_float(settings.get("SnapType")):
        snapshot = _as_float(settings.get("SnapTime"))
        if snapshot is not None:
            emt.study.add((subject, EMT["SimulationCase.snapshotTime"],
                           _literal(snapshot)))
    # The engine-facing remainder of the Settings paramlist, VERBATIM:
    # the project's own string, stated as data with no unit to normalise
    # and no decoding -- a flag word's bits are PSCAD's manual, named
    # not restated. An absent key is counted and omitted, exactly like
    # an absent timestep; a stated empty string is a statement.
    for param, attribute in SETTINGS_VERBATIM.items():
        value = settings.get(param)
        if value is None:
            emt.diagnostics.emit("emt_setting_missing", param)
            continue
        emt.study.add((subject, EMT[f"SimulationCase.{attribute}"],
                       _literal(value)))


def simulation_case(study: rdflib.Graph) -> dict[str, str]:
    """Every stated ``emt:SimulationCase`` property, read from the study
    DOCUMENT alone: ``attribute -> literal string``.

    The inverse of :func:`_add_simulation_case`, in the established
    read-back shape: it reads what the serialized document says and
    repairs nothing, so a dropped key or a converted value reads back
    wrong. Values are the document's literals verbatim -- the three SI
    settings as their converted seconds, the adopted keys as the
    project's own strings -- and the caller does any arithmetic.
    """
    prefix = str(EMT["SimulationCase."])
    out: dict[str, str] = {}
    for subject in study.subjects(RDF.type, EMT["SimulationCase"]):
        for predicate, value in study.predicate_objects(subject):
            name = str(predicate)
            if name.startswith(prefix):
                out[name[len(prefix):]] = str(value)
    return out


def emit_emt_files(flat: FlatProject, model: CimModel, out_dir, *,
                   eq_model_id: str, scenario_time: str,
                   modeling_authority_set: str | None = None,
                   version: str = "1",
                   project: HirProject | None = None,
                   source: bool = True) -> list:
    """Write the add-on documents for one case.

    ``project`` is the case already read, passed straight through to
    :func:`build_emt`. ``source`` also writes the source document
    (``--no-source`` on the CLI): an interchange consumer needs nothing
    it states, so it can ask for the interchange documents alone.
    """
    from pscx.surface import EMT_SOURCE_PROFILE_URI

    if modeling_authority_set is None:
        modeling_authority_set = DEFAULT_MODELING_AUTHORITY_SET
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    emt = build_emt(flat, model, project)
    DIAGNOSTICS.extend(emt.diagnostics)
    documents = [
        ("EMT", emt.equipment, EMT_PROFILE_URI, (eq_model_id,)),
        # a study configuration depends on no network model: asserting a
        # DependentOn here would tie one set of solver settings to one
        # network and make it unreusable
        ("EMTSIM", emt.study, EMT_SIMULATION_PROFILE_URI, ()),
    ]
    if source:
        # the source document's one class of outward reference is the
        # emt:DetailedModelDynamics.IdentifiedObject join into the
        # interchange document -- the closure oracle asserts it is the
        # only one -- so its header states the dependency the join makes
        documents.append(
            ("EMTSRC", emt.source, EMT_SOURCE_PROFILE_URI,
             (mrid(emt.project, "FullModel", f"EMT/{version}"),)))
    paths = []
    for suffix, graph, profile_uri, depends in documents:
        model_id = mrid(emt.project, "FullModel", f"{suffix}/{version}")
        full_model_header(graph, model_id, profile_uri, scenario_time,
                          version, depends,
                          modeling_authority_set=modeling_authority_set)
        path = out / f"{emt.project}_{suffix}.xml"
        path.write_bytes(serialize_graph(graph))
        paths.append(path)
    return paths

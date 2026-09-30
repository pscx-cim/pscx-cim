"""What the exchange must carry for a reader to rebuild an ``HirProject``.

``HIR -> .pscx`` is written and under test, so the reverse direction has a
finite target: every field of every dataclass in :mod:`pscx.hir`. Each one
is CARRIED (the nine emitted documents state it), DERIVABLE (a rule
reconstructs it from what they state, plus ``master.pslx``) or MISSING (it
has to be added), and a MISSING field names the term that would carry it.
Every CARRIED and MISSING row also names its DOCUMENT
(:data:`DOCUMENT_OF`), so a bucket emitter lands where the frozen
partition says instead of re-litigating the boundary.

The denominator is fields, not XML attributes. An attribute inventory
weights ``paramlist@crc``, which PSCAD regenerates, far above the module
hierarchy, which nothing can regenerate. A field list has no such
weighting problem, because every entry is read by code that must produce
a file.

Two things this table is careful about.

*Presence is not derivability.* ``HirComponent.classid`` appears in no
emitted document and costs nothing: it is always ``UserCmp``. Every
DERIVABLE verdict here carries the rule that reconstructs it.

*A retained lxml element is not available in this direction.* The HIR
built by the loader holds live elements, which is why ``.pscx -> HIR ->
.pscx`` returns an OPAQUE subtree for free. A HIR rebuilt from documents
holds nothing, so OPAQUE is classified like everything else.
"""

import dataclasses
from typing import NamedTuple

import pytest
from conftest import master_available

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found")


class Field(NamedTuple):
    """One HIR field's verdict, and what it would take to fill it."""

    verdict: str
    #: None unless the verdict is MISSING.
    home: str | None
    #: The qualified classes and properties that would carry it.
    terms: tuple
    #: The derivation rule, or what the count above is of.
    note: str


CARRIED = "CARRIED"
DERIVABLE = "DERIVABLE"
MISSING = "MISSING"

#: Where a MISSING field's term lives. ``STD`` is standard CGMES DL or the
#: core-CIM detailed-model family this repo adopts, and costs
#: no published vocabulary at all. ``EMT`` is a term the emt: vocabulary
#: already declares. ``NEW`` is a term nothing declares yet.
STD = "std"
EMT = "emt"
NEW = "new"

#: Every field of every dataclass in :mod:`pscx.hir`.
INVENTORY: dict[str, Field] = {
    # -- the loader's own channel -------------------------------------
    # No banked value is a reconstruction target: the exchange documents
    # state none of them, and a HIR built from the exchange has read
    # nothing it cannot name, so its channel is empty. (`write_project`
    # consults the channel in the OTHER direction -- putting a banked
    # attribute back into a .pscx -- which changes nothing about
    # rebuilding a HIR from the exchange.)
    "Unrecognized.kind": Field(DERIVABLE, None, (), "empty by construction"),
    "Unrecognized.owner": Field(DERIVABLE, None, (), "empty by construction"),
    "Unrecognized.name": Field(DERIVABLE, None, (), "empty by construction"),
    "Unrecognized.value": Field(DERIVABLE, None, (), "empty by construction"),
    "Unrecognized.span": Field(DERIVABLE, None, (), "empty by construction"),
    "Unrecognized.xpath": Field(DERIVABLE, None, (), "empty by construction"),

    # -- what the loader chose not to interpret -----------------------
    "Opaque.tag": Field(
        CARRIED, None,
        ("emt:ToolPayload.payloadKind",),
        "payloadKind is drawn from OPAQUE_TAGS"),
    # `element` is three facts, not one: the subtree, the tag of the
    # parent it was read from, and its index under that parent. The
    # writer reads all three and appending instead of inserting at the
    # index is a different document. The parent tag is derivable -- a
    # tag never appears under two parents -- the other two are not.
    "Opaque.element": Field(
        CARRIED, None,
        ("emt:ToolPayload.content", "emt:ToolPayload.sequenceNumber"),
        "subtree and index carried verbatim, and the content re-parses "
        "canonicalize()-equal. The parent tag stays derivable from the "
        "tag"),
    "Opaque.span": Field(DERIVABLE, None, (), "no source document to name"),

    # -- name/value groups --------------------------------------------
    # 'global_address' iff the key set is exactly {'gaddress'} on a
    # placement paramlist. The project-level paramlists are the ones that
    # need the name stated.
    "HirParams.name": Field(
        CARRIED, None,
        ("emt:SourceParameterList.parameterSetName",),
        "derivable for the placement paramlists by the gaddress rule; "
        "stated verbatim on the project, definition and canvas lists"),
    "HirParams.values": Field(
        CARRIED, None,
        ("cim:ParameterValue", "emt:SourceParameterList",
         "emt:SourceParameterList.IdentifiedObject",
         "emt:SourceParameterList.scope",
         "emt:SourceParameterList.sequenceNumber",
         "emt:SourceParameter", "emt:SourceParameter.SourceParameterList",
         "emt:SourceParameter.parameterName", "emt:SourceParameter.value",
         "emt:SourceParameter.sequenceNumber"),
        "the placement and wire entries are the stated-parameter "
        "surface's; the project, definition and canvas lists travel as "
        "SourceParameterList rows, names, values and order verbatim; a "
        "port's seven keys ride its emt:ModelPort; the Sub and container "
        "lists are the substitution statement's; the Settings-element "
        "lists sit inside the root <List> payloads"),
    # The one paramlist entry whose child content is data, not editor
    # bookkeeping: a matrix parameter's <row> texts. The value attribute
    # states only a dimension, the rows ARE the matrix, and the source
    # tool reads a matrix stated without them as that coupling disabled
    # -- a different network no elaboration-level gate can see, because
    # matrix contents never reach the netlist.
    "HirParams.rows": Field(
        CARRIED, None,
        ("emt:MatrixRow", "emt:MatrixRow.ParameterValue",
         "emt:MatrixRow.sequenceNumber", "emt:MatrixRow.value"),
        "verbatim text in row order, and the row count equals the "
        "declared dimension on every parameter that declares one"),
    "HirParams.span": Field(DERIVABLE, None, (), "no source document to name"),
    "HirParams.lines": Field(
        DERIVABLE, None,
        (),
        "the written document's own"),

    # -- global substitutions -----------------------------------------
    "HirSubstitution.id": Field(DERIVABLE, None, (), "named by nothing"),
    "HirSubstitution.classid": Field(
        DERIVABLE, None,
        (),
        "'GlobalSubstitution'"),
    "HirSubstitution.params": Field(
        CARRIED, None,
        ("emt:Substitution", "emt:Substitution.value",
         "emt:Substitution.group", "emt:Substitution.sequenceNumber"),
        "name, value and group verbatim, in table order; cat is the "
        "empty string on every Sub that states it and is restated by the "
        "pinned rule"),
    "HirSubstitution.span": Field(DERIVABLE, None, (), "no source document"),

    "HirSubstitutionList.classid": Field(
        DERIVABLE, None,
        (),
        "['Sub', 'ValueSet']"),
    "HirSubstitutionList.name": Field(DERIVABLE, None, (), "None"),
    "HirSubstitutionList.subs": Field(
        DERIVABLE, None,
        (),
        "every Sub in the 'Sub' list; 'ValueSet' empty"),
    "HirSubstitutionList.span": Field(
        DERIVABLE, None,
        (),
        "no source document"),

    "HirSubstitutions.tag": Field(
        DERIVABLE, None,
        (),
        "write 'GlobalSubstitutions'; PSCAD normalises the misspelling on "
        "save"),
    "HirSubstitutions.name": Field(
        DERIVABLE, None,
        (),
        "'Default'"),
    "HirSubstitutions.params": Field(
        DERIVABLE, None,
        (),
        "one paramlist, {'Current': ''}"),
    "HirSubstitutions.lists": Field(DERIVABLE, None, (), "two, always"),
    "HirSubstitutions.span": Field(DERIVABLE, None, (), "no source document"),

    # -- a definition's declared interface ----------------------------
    # The DL document names the port's geometry by this id and the
    # record states the declaration in sequence order, and ids are
    # draw-order accidents: declaration-order ids need not ascend. The id
    # is therefore the join between the two, and
    # emt:ModelPort.sourceIdentifier states it. A placement needs no such
    # term, because the DL names carry its id.
    "HirPort.id": Field(
        CARRIED, None,
        ("emt:ModelPort.sourceIdentifier",),
        "every port carries an element id, and the reader requires the "
        "stated join, raising where a document set omits it"),
    "HirPort.classid": Field(DERIVABLE, None, (), "'Port'"),
    "HirPort.x": Field(
        CARRIED, None,
        ("cim:Diagram", "cim:DiagramObject", "cim:DiagramObjectPoint",
         "cim:DiagramObjectPoint.xPosition"),
        "definition-local: the DL document gives a definition its own "
        "SYMBOL diagram, separate from its schematic, because the two "
        "are different coordinate spaces"),
    "HirPort.y": Field(
        CARRIED, None,
        ("cim:DiagramObjectPoint.yPosition",),
        "definition-local"),
    # Exactly seven keys: `name` is emt:ModelPort.portName and the other
    # six are the terms below. The `cond` key is a condition, not a
    # conductor count: a port may state a real predicate like `(Mode)`,
    # so the term is emt:ModelPort.condition.
    "HirPort.params": Field(
        CARRIED, None,
        ("emt:ModelPort.internal", "emt:ModelPort.condition",
         "emt:ModelPort.mode", "emt:ModelPort.dataType",
         "emt:ModelPort.electricalType", "emt:ModelPort.dimension"),
        "seven keys, verbatim on the source record's ModelPorts; a "
        "port-declaring definition may be placed by nothing, so the "
        "record is whole-copy; dict order comes back canonical, "
        "as a paramlist comes back in form order"),
    "HirPort.span": Field(DERIVABLE, None, (), "no source document"),

    # -- placements ---------------------------------------------------
    "HirComponent.classid": Field(
        DERIVABLE, None,
        (),
        "'UserCmp'"),
    # Load-bearing: every payload that references anything references a
    # placement by this id (<ref link>, <channel id>, <Curve link>,
    # <Control link>, <call link>). Carry the payload and drop the id and
    # every one of them dangles.
    "HirComponent.id": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.name",),
        "verbatim in the DL names, kind attached, so a number shared "
        "across kinds resolves; the payload references resolve against "
        "the same raw ids, so no separate identifier term is needed"),
    "HirComponent.defn": Field(
        CARRIED, None,
        ("emt:DetailedModelDynamics.definitionReference",),
        "the stated string verbatim, because the type join merges "
        "spellings and cannot recover every one"),
    "HirComponent.x": Field(
        CARRIED, None,
        ("cim:DiagramObject", "cim:DiagramObject.Diagram",
         "cim:DiagramObjectPoint.DiagramObject"),
        "every placement, the hosted devices included"),
    "HirComponent.y": Field(
        CARRIED, None,
        (),
        "every placement, the hosted devices included"),
    # The ONE field DL carries part of and cannot carry all of. `orient`
    # is 0-7: a quarter turn in the low two bits and a MIRROR in x above
    # 4. `cim:DiagramObject.rotation` is an angle, an angle has no
    # mirror, and folding the bit into the angle would state a rotation
    # nobody drew. So DL carries the quarter turn and the source document
    # states the mirror.
    "HirComponent.orient": Field(
        CARRIED, None,
        ("cim:DiagramObject.rotation", "emt:DetailedModelDynamics.mirrored"),
        "DL keeps the quarter turn and the source subject states the "
        "mirror on a reflected placement. Every stated orient is a "
        "canonical '0'-'7', so the two rebuild the exact string"),
    # Not the `Name` parameter: the two can disagree, so one cannot stand
    # in for the other. It rides the drawn subject as `description`,
    # because `name` is where the subject's identity lives -- the same
    # trade DL makes on a DiagramObject.
    "HirComponent.name": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.description",),
        "wherever stated, independent of the Name parameter"),
    "HirComponent.disable": Field(
        DERIVABLE, None,
        (),
        "'false' wherever stated"),
    "HirComponent.layer": Field(
        DERIVABLE, None,
        (),
        "'' wherever stated"),
    "HirComponent.params": Field(
        CARRIED, None,
        ("cim:ParameterValue",),
        "verbatim including blanks; one subject per paramlist keeps the "
        "list structure, spelling-keyed descriptors keep two spellings "
        "apart, and dict order comes back as the form's rather than the "
        "file's"),
    "HirComponent.span": Field(DERIVABLE, None, (), "no source document"),

    # -- wires --------------------------------------------------------
    "HirWire.classid": Field(
        DERIVABLE, None,
        (),
        "hosted + host kind decides all five"),
    "HirWire.id": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.name",),
        "verbatim in the DL names; an id-less wire travels as a canvas "
        "ordinal, which is also how it comes back"),
    "HirWire.name": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.description",),
        "carried where real; '' is omitted by the surface and defaulted "
        "by the read-back, and the omission is asserted lossless over "
        "every omitted wire"),
    "HirWire.x": Field(
        CARRIED, None,
        (),
        "the origin is the first point of the polyline, which is exact "
        "because a wire's first <vertex> is (0,0)"),
    "HirWire.y": Field(
        CARRIED, None,
        (),
        "the first point of the polyline"),
    "HirWire.orient": Field(
        CARRIED, None,
        (),
        "'0', so no wire exercises the mirror that HirComponent.orient "
        "does"),
    "HirWire.disable": Field(
        DERIVABLE, None,
        (),
        "'false' wherever stated"),
    "HirWire.layer": Field(
        DERIVABLE, None,
        (),
        "'' wherever stated"),
    "HirWire.defn": Field(
        CARRIED, None,
        ("emt:DetailedModelDynamics.definitionReference",),
        "verbatim where stated. A wire is its own type, so the type join "
        "does not carry it; a wire stating only defn='' is carried by "
        "the extended wire rule"),
    "HirWire.vertices": Field(
        CARRIED, None,
        ("cim:DiagramObjectPoint", "cim:DiagramObjectPoint.sequenceNumber",
         "cim:DiagramObjectPoint.xPosition",
         "cim:DiagramObjectPoint.yPosition"),
        "offsets numbered from 1, because 301 puts sequenceNumber above "
        "zero strictly"),
    "HirWire.params": Field(
        CARRIED, None,
        ("cim:ParameterValue",),
        "verbatim; a named wire with no paramlist comes back with none, "
        "not with one empty one"),
    # A hosted device is a placement inside a wire. Containment
    # by a canvas is ContainingDefinition's; containment by a wire is
    # its own join.
    "HirWire.hosted": Field(
        CARRIED, None,
        ("emt:DetailedModelDynamics.HostingWire",),
        "each hosting wire carried by the surface, so both ends of the "
        "join have subjects"),
    "HirWire.span": Field(DERIVABLE, None, (), "no source document"),

    # -- pages --------------------------------------------------------
    "HirCanvas.classid": Field(
        DERIVABLE, None,
        (),
        "a function of the definition's classid"),
    # A page's own settings are values against a definition, and
    # cim:ParameterValue.DetailedModelDynamics ranges over placements.
    "HirCanvas.params": Field(
        CARRIED, None,
        ("emt:SourceParameterList",),
        "verbatim under the canvas scope, because "
        "cim:ParameterValue.DetailedModelDynamics ranges over placements "
        "and a page is not one"),
    "HirCanvas.components": Field(
        CARRIED, None,
        ("emt:DetailedModelDynamics.ContainingDefinition",),
        "stated on every drawn subject -- the structural form of the "
        "page the name grammar already carries"),
    "HirCanvas.wires": Field(
        CARRIED, None,
        ("emt:DetailedModelDynamics.ContainingDefinition",),
        "stated on every drawn subject, the omitted wires asserted "
        "reconstructible by the extended omission rule"),
    "HirCanvas.opaque": Field(
        CARRIED, None,
        ("emt:ToolPayload.IdentifiedObject",),
        "owned by the page's definition; the kind says which parent a "
        "writer reinserts under"),
    "HirCanvas.span": Field(DERIVABLE, None, (), "no source document"),

    # -- the parameter form -------------------------------------------
    "HirFormParameter.name": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.name",),
        "each declared parameter a descriptor on its definition's type "
        "whose sequenceNumber states the form position -- a descriptor "
        "without one is a stated extra, not a form parameter"),
    "HirFormParameter.type": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.parameterType",),
        "verbatim on the declared descriptors"),
    "HirFormParameter.desc": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.description",),
        "on the declared descriptors"),
    "HirFormParameter.group": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.group",),
        "verbatim where stated, the empty string included"),
    "HirFormParameter.unit": Field(
        CARRIED, None,
        ("cim:ParameterDescriptor.engineeringUnit",),
        "every declared unit is carried; the units on the detailed "
        "models' descriptors are master's, which the reader resolves "
        "itself"),
    "HirFormParameter.intent": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.intent",),
        "where stated; the output-writer gate"),
    "HirFormParameter.dim": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.dimension",),
        "where stated"),
    "HirFormParameter.content_type": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.contentType",),
        "where stated"),
    "HirFormParameter.minimum": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.minimum",),
        "where stated, a blank stated as a blank"),
    "HirFormParameter.maximum": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.maximum",),
        "where stated, a blank stated as a blank"),
    "HirFormParameter.value": Field(
        CARRIED, None,
        ("cim:ParameterDescriptor.typicalValue",),
        "every stated default is carried verbatim, a blank as a blank. "
        "The detailed models' descriptors differ: there a blank default "
        "states nothing"),
    "HirFormParameter.condition": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.condition",),
        "where stated, a blank stated as a blank"),
    # master states it and a case-authored definition does not, which is
    # the lever working: a master definition is resolved by the reader.
    "HirFormParameter.condition_type": Field(
        DERIVABLE, None,
        (),
        "None on a case-authored parameter"),
    "HirFormParameter.opaque": Field(
        CARRIED, None,
        ("emt:ToolPayload.IdentifiedObject",),
        "help, choice, regex, error_msg, vis -- owned by the surface's "
        "declared descriptors"),
    "HirFormParameter.span": Field(DERIVABLE, None, (), "no source document"),

    "HirFormCategory.name": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.name",),
        "every category named, on the source record's ParameterCategory "
        "subjects"),
    "HirFormCategory.visible": Field(
        CARRIED, None,
        ("emt:ParameterCategory.visible",),
        "verbatim where stated"),
    "HirFormCategory.condition": Field(
        CARRIED, None,
        ("emt:ParameterCategory.condition",),
        "verbatim where stated, a blank stated as a blank"),
    "HirFormCategory.condition_type": Field(
        DERIVABLE, None,
        (),
        "None"),
    "HirFormCategory.parameters": Field(
        CARRIED, None,
        ("emt:ParameterDescriptor.ParameterCategory",),
        "each descriptor joining the category that first declares its "
        "spelling -- no spelling repeats within a definition"),
    "HirFormCategory.span": Field(DERIVABLE, None, (), "no source document"),

    # -- solver source ------------------------------------------------
    "HirSegment.name": Field(
        CARRIED, None,
        ("emt:ModelScript", "emt:ModelScript.segmentName"),
        "every segment named"),
    "HirSegment.id": Field(DERIVABLE, None, (), "named by nothing"),
    "HirSegment.text": Field(
        CARRIED, None,
        ("emt:ModelScript.source",),
        "verbatim including whitespace; a declared-empty segment states "
        "source=''"),
    "HirSegment.tree": Field(
        DERIVABLE, None,
        (),
        "a parse of text; non-empty iff text is"),
    "HirSegment.span": Field(DERIVABLE, None, (), "no source document"),

    # -- the definitions the case carries -----------------------------
    "HirDefinition.classid": Field(
        CARRIED, None,
        ("emt:IdentifiedObject.sourceClass",),
        "stated on every definition, uniformly: the canvas-classid rule "
        "derives FROM this, so the circular pair needed one stated "
        "anchor"),
    "HirDefinition.name": Field(
        CARRIED, None,
        ("emt:ModelDefinition", "emt:ModelDefinition.definitionName"),
        "unique per case, which is also the name join to the DL page; a "
        "case-authored name may shadow a master one"),
    "HirDefinition.id": Field(DERIVABLE, None, (), "named by nothing"),
    "HirDefinition.group": Field(
        CARRIED, None,
        ("emt:ModelDefinition.group",),
        "where stated, the empty string included"),
    "HirDefinition.params": Field(
        CARRIED, None,
        ("emt:SourceParameterList",),
        "under the definition scope"),
    "HirDefinition.form_name": Field(
        CARRIED, None,
        ("cim:IdentifiedObject.description",),
        "the English title, on the definition's own type, never equal to "
        "name"),
    "HirDefinition.form": Field(
        CARRIED, None,
        ("emt:ParameterCategory",
         "emt:ParameterCategory.ModelDefinition",
         "emt:ParameterCategory.sequenceNumber"),
        "the declared categories in form order"),
    "HirDefinition.ports": Field(
        CARRIED, None,
        ("emt:ModelPort", "emt:ModelPort.DetailedModelTypeDynamics",
         "emt:ModelPort.portName", "emt:ModelPort.sequenceNumber"),
        "the declared ports, whole-copy on the source record"),
    "HirDefinition.segments": Field(
        CARRIED, None,
        ("emt:ModelScript.ModelDefinition", "emt:ModelScript.sequenceNumber"),
        "the declared segments, in declaration order"),
    "HirDefinition.canvas": Field(
        CARRIED, None,
        ("cim:Diagram",),
        "the DL document states one schematic cim:Diagram per page, "
        "named by the definition -- an empty page included, which "
        "reconstruct_drawing keeps"),
    "HirDefinition.opaque": Field(
        CARRIED, None,
        ("emt:ToolPayload", "emt:ToolPayload.IdentifiedObject"),
        "Gfx, svg, references"),
    "HirDefinition.span": Field(DERIVABLE, None, (), "no source document"),

    # -- drawing layers -----------------------------------------------
    "HirLayer.name": Field(
        MISSING, STD,
        ("cim:VisibilityLayer", "cim:IdentifiedObject.name"),
        "the declared layers"),
    # A layer's enabled or disabled state has no DL property either way,
    # so its term is new.
    "HirLayer.state": Field(
        MISSING, NEW,
        ("emt:VisibilityLayer.state",),
        "the declared layer state, verbatim"),
    "HirLayer.id": Field(DERIVABLE, None, (), "named by nothing"),
    "HirLayer.span": Field(DERIVABLE, None, (), "no source document"),

    # -- the project root ---------------------------------------------
    "HirProject.path": Field(
        DERIVABLE, None,
        (),
        "the file the writer writes"),
    "HirProject.name": Field(
        CARRIED, None,
        (),
        "cim:IdentifiedObject.name in EQ and the study document"),
    "HirProject.version": Field(
        CARRIED, None,
        ("emt:ModelProject", "emt:ModelProject.sourceFormatVersion"),
        "stated verbatim"),
    "HirProject.target": Field(
        CARRIED, None,
        ("emt:ModelProject.targetPlatform",),
        "stated once per case, as one triple"),
    "HirProject.schema": Field(
        DERIVABLE, None,
        (),
        "a function of version: 5.0.x -> '', 4.5.x -> '0'"),
    "HirProject.params": Field(
        CARRIED, None,
        ("emt:SimulationCase", "emt:SourceParameterList"),
        "the engine-facing settings stay the study document's "
        "(SETTINGS_SECONDS and SETTINGS_VERBATIM); the root paramlists "
        "travel whole as SourceParameterList rows"),
    "HirProject.definitions": Field(
        CARRIED, None,
        ("emt:ModelDefinition.ModelProject",),
        "every definition joining the one ModelProject"),
    # 600-2 puts VisibilityLayer.VisibleObjects at 1..n, so an empty
    # layer stays unemitted.
    "HirProject.layers": Field(
        MISSING, STD, ("cim:VisibilityLayer",), "the declared layers"),
    "HirProject.substitutions": Field(
        CARRIED, None,
        ("emt:Substitution.ModelProject",),
        "a container on every 5.0-era case, its presence and fixed shape "
        "restated by pinned rules off the carried sourceFormatVersion, "
        "the Subs stated individually"),
    "HirProject.settings": Field(
        DERIVABLE, None,
        (),
        "the writer never writes it; the root <List> is OPAQUE and comes "
        "back whole"),
    "HirProject.opaque": Field(
        CARRIED, None,
        ("emt:ToolPayload.IdentifiedObject",),
        "the root List among them, which is also how "
        "HirProject.settings comes back whole"),
    "HirProject.unrecognized": Field(
        DERIVABLE, None,
        (),
        "empty by construction"),
}

#: The five document names a settled row can land in. ``STANDARD_FIVE``
#: is EQ/TP/SC/SSH/OP taken together -- which of the five is the
#: standard's own business -- and ``SOURCE`` is the source document,
#: the verbatim stated text of the case file
#: (:data:`pscx.surface.EMT_SOURCE_PROFILE_URI`).
STANDARD_FIVE = "standard-five"
DL_DOCUMENT = "DL"
EMT_DOCUMENT = "EMT"
EMTSIM_DOCUMENT = "EMTSIM"
SOURCE_DOCUMENT = "source"

#: The frozen document partition: every CARRIED and MISSING row's
#: document, decided by boundary 1 -- a row is interchange if and only
#: if an engine that cannot read ``.pscx`` needs it. The DERIVABLE rows
#: name none: a rule is not carried anywhere.
#:
#: The rows that took a decision, with the join that decided them:
#:
#: * ``HirPort.params`` and ``HirDefinition.ports`` (emt:ModelPort) are
#:   EMT, because an engine join reads a port declaration: an
#:   emt:SignalConnection resolves its portName against the type's
#:   declared interface. The two rows move together.
#: * ``HirProject.params`` is EMTSIM: the five solver settings an engine
#:   runs with are interchange (emt:SimulationCase carries them);
#:   the verbatim text of the project paramlists is HirParams.values'
#:   row, which is SOURCE.
#: * ``HirComponent.defn`` is SOURCE for the same reason the surface is
#:   per drawn element: full coverage is every drawn placement.
#:   The detailed-model definitionNames in the EMT document are an
#:   engine-facing projection; they do not carry the row.
#: * ``HirLayer.name`` / ``HirProject.layers`` are DL -- the standard
#:   class is DL-profile cim:VisibilityLayer -- while ``HirLayer.state``
#:   is SOURCE, because an emt: property cannot enter a standard
#:   document and a layer's enabled/disabled state is drawing fidelity,
#:   not an engine quantity.
#: * The two ModelPort rows are the only EMT rows. All other engine data
#:   (detailed models, the passives' R/L/C, the signal graph, phase
#:   resolution, terminals) is PROJECTED from the flattened model, and
#:   no HIR field is carried by those projections -- which is boundary 1
#:   seen from the other side.
#: * ``HirDefinition.canvas`` is DL: whether a definition draws a page
#:   is stated by the DL document's schematic cim:Diagram, named by the
#:   definition. An IRI join from the source document to that page would
#:   break the source document's closure, so the name is the join.
#: * ``HirProject.target`` is SOURCE, stated by
#:   emt:ModelProject.targetPlatform rather than derived from a rule.
DOCUMENT_OF: dict[str, str] = {
    "Opaque.tag": SOURCE_DOCUMENT,
    "Opaque.element": SOURCE_DOCUMENT,
    "HirParams.name": SOURCE_DOCUMENT,
    "HirParams.values": SOURCE_DOCUMENT,
    "HirParams.rows": SOURCE_DOCUMENT,
    "HirSubstitution.params": SOURCE_DOCUMENT,
    "HirPort.id": SOURCE_DOCUMENT,
    "HirPort.x": DL_DOCUMENT,
    "HirPort.y": DL_DOCUMENT,
    "HirPort.params": EMT_DOCUMENT,
    "HirComponent.id": SOURCE_DOCUMENT,
    "HirComponent.defn": SOURCE_DOCUMENT,
    "HirComponent.x": DL_DOCUMENT,
    "HirComponent.y": DL_DOCUMENT,
    "HirComponent.orient": DL_DOCUMENT,
    "HirComponent.name": SOURCE_DOCUMENT,
    "HirComponent.params": SOURCE_DOCUMENT,
    "HirWire.id": SOURCE_DOCUMENT,
    "HirWire.name": SOURCE_DOCUMENT,
    "HirWire.x": DL_DOCUMENT,
    "HirWire.y": DL_DOCUMENT,
    "HirWire.orient": DL_DOCUMENT,
    "HirWire.defn": SOURCE_DOCUMENT,
    "HirWire.vertices": DL_DOCUMENT,
    "HirWire.params": SOURCE_DOCUMENT,
    "HirWire.hosted": SOURCE_DOCUMENT,
    "HirCanvas.params": SOURCE_DOCUMENT,
    "HirCanvas.components": SOURCE_DOCUMENT,
    "HirCanvas.wires": SOURCE_DOCUMENT,
    "HirCanvas.opaque": SOURCE_DOCUMENT,
    "HirFormParameter.name": SOURCE_DOCUMENT,
    "HirFormParameter.type": SOURCE_DOCUMENT,
    "HirFormParameter.desc": SOURCE_DOCUMENT,
    "HirFormParameter.group": SOURCE_DOCUMENT,
    "HirFormParameter.unit": SOURCE_DOCUMENT,
    "HirFormParameter.intent": SOURCE_DOCUMENT,
    "HirFormParameter.dim": SOURCE_DOCUMENT,
    "HirFormParameter.content_type": SOURCE_DOCUMENT,
    "HirFormParameter.minimum": SOURCE_DOCUMENT,
    "HirFormParameter.maximum": SOURCE_DOCUMENT,
    "HirFormParameter.value": SOURCE_DOCUMENT,
    "HirFormParameter.condition": SOURCE_DOCUMENT,
    "HirFormParameter.opaque": SOURCE_DOCUMENT,
    "HirFormCategory.name": SOURCE_DOCUMENT,
    "HirFormCategory.visible": SOURCE_DOCUMENT,
    "HirFormCategory.condition": SOURCE_DOCUMENT,
    "HirFormCategory.parameters": SOURCE_DOCUMENT,
    "HirSegment.name": SOURCE_DOCUMENT,
    "HirSegment.text": SOURCE_DOCUMENT,
    "HirDefinition.classid": SOURCE_DOCUMENT,
    "HirDefinition.name": SOURCE_DOCUMENT,
    "HirDefinition.group": SOURCE_DOCUMENT,
    "HirDefinition.params": SOURCE_DOCUMENT,
    "HirDefinition.form_name": SOURCE_DOCUMENT,
    "HirDefinition.form": SOURCE_DOCUMENT,
    "HirDefinition.ports": EMT_DOCUMENT,
    "HirDefinition.segments": SOURCE_DOCUMENT,
    "HirDefinition.canvas": DL_DOCUMENT,
    "HirDefinition.opaque": SOURCE_DOCUMENT,
    "HirLayer.name": DL_DOCUMENT,
    "HirLayer.state": SOURCE_DOCUMENT,
    "HirProject.name": STANDARD_FIVE,
    "HirProject.target": SOURCE_DOCUMENT,
    "HirProject.version": SOURCE_DOCUMENT,
    "HirProject.params": EMTSIM_DOCUMENT,
    "HirProject.definitions": SOURCE_DOCUMENT,
    "HirProject.layers": DL_DOCUMENT,
    "HirProject.substitutions": SOURCE_DOCUMENT,
    "HirProject.opaque": SOURCE_DOCUMENT,
}


def test_the_document_partition_is_frozen_over_every_settled_row():
    # The column is total and closed: exactly the CARRIED and MISSING
    # rows name a document, every name is one of the five, and the
    # per-document split is pinned so a bucket emitter that moves a row
    # -- or a new row that dodges the decision -- fails here by name.
    from collections import Counter

    settled = {name for name, entry in INVENTORY.items()
               if entry.verdict != DERIVABLE}
    assert set(DOCUMENT_OF) == settled, (
        f"undecided: {sorted(settled - set(DOCUMENT_OF))}; "
        f"decided and gone: {sorted(set(DOCUMENT_OF) - settled)}")
    assert Counter(DOCUMENT_OF.values()) == {
        SOURCE_DOCUMENT: 53, DL_DOCUMENT: 12, EMT_DOCUMENT: 2,
        STANDARD_FIVE: 1, EMTSIM_DOCUMENT: 1}
    # boundary 1, held row by row: nothing MISSING that would land in the
    # source document names an interchange home in the same breath -- an
    # EMT-homed term in a SOURCE row only says who declared the term, so
    # the one thing to check is that no row is decided into a document
    # outside the closed set
    assert not (set(DOCUMENT_OF.values())
                - {STANDARD_FIVE, DL_DOCUMENT, EMT_DOCUMENT,
                   EMTSIM_DOCUMENT, SOURCE_DOCUMENT})


#: The join that makes a placement in the add-on document and a
#: cim:Breaker in EQ one device. No HIR field names it -- it is what the
#: reconstruction resolves placements THROUGH -- so it would fall out of
#: a field-by-field count that did not say so.
JOIN_TERMS = ("emt:DetailedModelDynamics.IdentifiedObject",)

def _hir_classes():
    import inspect

    from pscx import hir

    return {name: obj for name, obj in vars(hir).items()
            if inspect.isclass(obj) and dataclasses.is_dataclass(obj)
            and obj.__module__ == "pscx.hir"}


def test_the_inventory_names_every_field_of_every_hir_dataclass():
    # The property that makes this a finite, checkable target rather than
    # an argued one. A twelfth field on HirProject, or a seventeenth
    # dataclass, fails here rather than being silently unclassified.
    classes = _hir_classes()
    assert len(classes) == 16
    fields = {f"{name}.{field.name}"
              for name, cls in classes.items()
              for field in dataclasses.fields(cls)}
    assert len(fields) == 117
    assert fields == set(INVENTORY), (
        f"unclassified: {sorted(fields - set(INVENTORY))}; "
        f"named and gone: {sorted(set(INVENTORY) - fields)}")


def test_the_inventory_verdicts_split_as_pinned():
    from collections import Counter

    verdicts = Counter(entry.verdict for entry in INVENTORY.values())
    # CARRIED is the drawing (DL), the stated-parameter surface and the
    # source record (the source document), and the port declarations
    # (EMT). HirPort.id is carried rather than derived because it is the
    # join between a port's geometry and its declaration, HirParams.rows
    # because a matrix parameter's rows are data, and HirProject.target
    # because targetPlatform states it.
    assert verdicts == {DERIVABLE: 48, MISSING: 3, CARRIED: 66}
    # Every MISSING field names a home and at least one term. A CARRIED
    # one names no home -- there is nothing left to add -- and may name
    # the terms that DO carry it, which is where a reader looks to find
    # them. A DERIVABLE one names neither: its note is the rule.
    for name, (verdict, home, terms, note) in INVENTORY.items():
        if verdict == MISSING:
            assert home in (STD, EMT, NEW) and terms, name
        elif verdict == DERIVABLE:
            assert home is None and not terms, name
        else:
            assert home is None, name
        assert note, name


def test_the_missing_fields_split_by_the_home_of_their_term():
    from collections import Counter

    homes = Counter(entry.home for entry in INVENTORY.values()
                    if entry.verdict == MISSING)
    # The MISSING rows are the three layer rows. 600-2 makes an empty
    # cim:VisibilityLayer a Violation, so all three are carried together
    # once a case puts a drawn element on a declared layer. The layer state is NEW because no
    # standard or emt: term states it.
    assert homes == {STD: 2, NEW: 1}


def test_the_emt_vocabulary_term_counts_match_their_pins():
    # The number the redirect is waiting on. Terms are counted by
    # qualified name, because that is what a resolving namespace publishes
    # and what can never be retracted afterwards.
    from pscx.cimxml import VOCABULARY_PATH

    declared = _declared_terms(VOCABULARY_PATH)
    # Every term is declared beside its producer and its inverse: the
    # forward direction's detailed models and terminals, the ModelPort
    # interface, the signal graph (emt:SignalNet, emt:SignalConnection),
    # the right-of-way record (emt:RightOfWay, emt:RightOfWayRecord and
    # the wire-side statement across cim:Line and the hosted-device
    # model), the verbatim study settings (SETTINGS_VERBATIM), the source
    # record (the definition record, the SourceParameterList row shapes,
    # the containment joins), the port binding
    # (ModelPort.sourceIdentifier) and the matrix rows (emt:MatrixRow).
    assert len(declared) == 123

    needed = {term for entry in INVENTORY.values()
              for term in entry.terms} | set(JOIN_TERMS)
    emt_needed = {t for t in needed if t.startswith("emt:")}
    standard = {t for t in needed if t.startswith("cim:")}
    assert needed == emt_needed | standard

    already = {t for t in emt_needed if t[len("emt:"):] in declared}
    fresh = emt_needed - already
    # Every emt: term the inventory needs is declared beside its
    # producer except the held layer state, which waits for a case that
    # exercises a non-empty layer.
    assert len(emt_needed) == 69
    assert len(already) == 68
    assert sorted(fresh) == ["emt:VisibilityLayer.state"]
    # The 55 declared terms the reconstruction-side rows do not cite
    # serve the forward direction: the ModelTerminal family, the phase
    # count, the LibraryModelType family (the reconstruction cites the
    # verbatim definitionReference instead; the types still travel), the
    # fourteen signal-graph terms, the thirteen right-of-way terms and
    # the seventeen study settings -- the resolved graph, the evaluated
    # record and the study definition are engine data, and a
    # reconstructor rebuilds the drawn clues and the verbatim source
    # text from the source document instead.
    assert len(declared) - len(already) == 55
    unconsumed = sorted(declared - {t[len("emt:"):] for t in emt_needed})
    assert len(unconsumed) == 55
    families = {term.split(".")[0] for term in unconsumed}
    assert families == {
        "ConnectivityNode", "DetailedModelDynamics", "LibraryModelType",
        "Line", "ModelTerminal", "ParameterValue", "RightOfWay",
        "RightOfWayRecord", "SignalConnection", "SignalNet",
        "SimulationCase"}
    # 15 standard terms, and not one of them costs a published
    # identifier.
    assert len(standard) == 15


def _declared_terms(path) -> set:
    """Every emt: class and property the vocabulary declares.

    Read through the generator's own parser rather than a second reader:
    a term list this file derived independently would agree with the
    vocabulary on the day it was written and never again.
    """
    import os
    import sys

    from conftest import REPO_ROOT

    sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
    import gen_emt_shacl

    vocabulary = gen_emt_shacl.Vocabulary(path)
    return set(vocabulary.classes) | set(vocabulary.properties)


def test_no_term_the_inventory_needs_is_declared_twice_over():
    # A NEW-homed field naming a term the vocabulary already declares
    # would inflate the published count for nothing, and an EMT-homed one
    # naming a term nothing declares would deflate it.
    from pscx.cimxml import VOCABULARY_PATH

    declared = _declared_terms(VOCABULARY_PATH)
    for name, (verdict, home, terms, _note) in INVENTORY.items():
        if verdict != MISSING:
            continue
        local = {t[len("emt:"):] for t in terms if t.startswith("emt:")}
        if home == NEW:
            assert not (local & declared), f"{name} claims new: {local}"
        elif home == EMT:
            assert local <= declared, f"{name} claims declared: {local}"
        else:
            assert not local, f"{name} is std and names an emt: term"


"""Derive the emt: SHACL shapes from the emt: RDFS vocabulary.

Datatype, cardinality and value-type constraints are a mechanical
projection of the vocabulary -- which is how the ENTSO-E constraint files
relate to the ENTSO-E vocabularies -- so they are generated rather than
transcribed. Constraints the vocabulary cannot state (value ranges,
non-empty strings) live in VALUE_CONSTRAINTS below and are authored.

    python tools/gen_emt_shacl.py            # rewrite the shapes file
    python tools/gen_emt_shacl.py --check    # exit 1 if it is stale

The output is byte-deterministic: everything is emitted in sorted order.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
CIMS = "{http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#}"
XML = "{http://www.w3.org/XML/1998/namespace}"

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCABULARY = REPO_ROOT / "src" / "pscx" / "profiles" / "emt" / "EMT-AP-Voc-RDFS.rdf"
SHAPES = REPO_ROOT / "src" / "pscx" / "profiles" / "emt" / "EMT-AP-Con-SHACL.ttl"

#: CIM primitive -> xsd datatype, as the 600-2 constraint files map them.
XSD_OF_PRIMITIVE = {
    "String": "xsd:string",
    "Float": "xsd:float",
    "Integer": "xsd:integer",
    "Boolean": "xsd:boolean",
}

#: Constraints the RDFS has no way to state. Keyed by property qualname;
#: each entry becomes one further PropertyShape on the owning NodeShape.
VALUE_CONSTRAINTS = {
    "LibraryModelType.modelingTool": ("length", {"sh:minLength": "1"}),
    "LibraryModelType.definitionName": ("length", {"sh:minLength": "1"}),
    "ModelTerminal.sequenceNumber": ("valueRange", {"sh:minInclusive": "1"}),
    "ModelTerminal.phase": ("valueRange", {"sh:minInclusive": "1"}),
    "ModelDefinition.definitionName": ("length", {"sh:minLength": "1"}),
    "ModelPort.portName": ("length", {"sh:minLength": "1"}),
    "ModelPort.sequenceNumber": ("valueRange", {"sh:minInclusive": "1"}),
    "ModelScript.segmentName": ("length", {"sh:minLength": "1"}),
    "ModelScript.sequenceNumber": ("valueRange", {"sh:minInclusive": "1"}),
    # ModelScript.source carries no minLength on purpose: a segment can be
    # declared and empty, and a definition that declares an empty segment
    # is not the same as one that declares none.
    "ParameterCategory.sequenceNumber": ("valueRange",
                                         {"sh:minInclusive": "1"}),
    # scope names one of three statement sites and an empty one names none;
    # a parameter name is never empty in any measured source, so an empty
    # one is a writer defect the shape should surface. The verbatim-text
    # attributes (group, condition, minimum, ...) carry no minLength on
    # purpose: a stated empty string is source text and both occur.
    "SourceParameterList.scope": ("length", {"sh:minLength": "1"}),
    "SourceParameterList.sequenceNumber": ("valueRange",
                                           {"sh:minInclusive": "1"}),
    "SourceParameter.parameterName": ("length", {"sh:minLength": "1"}),
    "SourceParameter.sequenceNumber": ("valueRange",
                                       {"sh:minInclusive": "1"}),
    "Substitution.sequenceNumber": ("valueRange", {"sh:minInclusive": "1"}),
    "ToolPayload.payloadKind": ("length", {"sh:minLength": "1"}),
    "ToolPayload.content": ("length", {"sh:minLength": "1"}),
    "ToolPayload.sequenceNumber": ("valueRange", {"sh:minInclusive": "1"}),
    "ConnectivityNode.phaseCount": ("valueRange", {"sh:minInclusive": "1"}),
    "RightOfWayRecord.key": ("length", {"sh:minLength": "1"}),
    "RightOfWayRecord.sequenceNumber": ("valueRange",
                                        {"sh:minInclusive": "1"}),
    # values and unit carry no minLength: both are omitted rather than
    # written empty, so an empty literal here is a writer defect the
    # shape should surface, and it does -- through datatype/nodeKind --
    # while a header row legitimately states neither.
    "Line.length": ("length", {"sh:minLength": "1"}),
    "Line.frequency": ("length", {"sh:minLength": "1"}),
    "Line.conductorCount": ("valueRange", {"sh:minInclusive": "1"}),
    "ParameterDescriptor.sequenceNumber": ("valueRange",
                                           {"sh:minInclusive": "1"}),
    "SimulationCase.timeStep": ("valueRange", {"sh:minExclusive": "0"}),
    "SimulationCase.duration": ("valueRange", {"sh:minExclusive": "0"}),
    "SimulationCase.plotStep": ("valueRange", {"sh:minExclusive": "0"}),
    "SignalNet.width": ("valueRange", {"sh:minInclusive": "1"}),
    "SignalConnection.width": ("valueRange", {"sh:minInclusive": "1"}),
    "SignalConnection.offset": ("valueRange", {"sh:minInclusive": "1"}),
}

def _attribute(datatype: str, multiplicity: str) -> dict:
    return {"dataType": datatype, "range": None, "domain": None,
            "domainIRI": None, "multiplicity": multiplicity}


def _association(target: str, multiplicity: str) -> dict:
    return {"dataType": None, "domain": None, "domainIRI": None,
            "range": f"http://iec.ch/TC57/CIM100#{target}",
            "multiplicity": multiplicity}


#: Standard CIM classes this profile WRITES into the add-on document,
#: because a standard CIM class is preferred to an emt: one. They sit
#: outside the CGMES subset, so no ENTSO-E shape targets them and without
#: these entries nothing constrains them at all -- the vocabulary declares no cim: term by design, so a
#: shapes file generated from the vocabulary alone would leave every one of
#: these subjects unchecked while every positive test stayed green.
#:
#: A shapes file may target a class it does not declare: CONSTRAINING IS
#: NOT DECLARING, which is what lets this exist without adding a cim: term
#: to the vocabulary and breaking its rule to never touch cim:.
#:
#: The specs are the same shape a parsed vocabulary property has, so one
#: generator serves both.
ADOPTED: dict[str, dict] = {
    "DetailedModelDynamics": {
        "identifiedObject": True,
        "properties": [
            ("DynamicsFunctionBlock.enabled",
             _attribute("Boolean", "M:1..1")),
            ("DetailedModelDynamics.DetailedModelTypeDynamics",
             _association("DetailedModelTypeDynamics", "M:1..1")),
            # 0..1, which is core CIM's own multiplicity for the
            # association (Grid18 v15: Equipment end 0..1, DMD end 0..n,
            # "the detailed model dynamics this models this equipment").
            # A mapped passive's model states it, since that is the join
            # an EMT engine rebuilds the element through. An unmapped
            # kind's detailed model, which stands for equipment the
            # standard documents cannot express, legally states none.
            ("DetailedModelDynamics.Equipment",
             _association("Equipment", "M:0..1")),
        ],
    },
    "ParameterDescriptor": {
        "identifiedObject": True,
        "properties": [
            ("DetailedModelDescriptor.DetailedModelTypeDynamics",
             _association("DetailedModelTypeDynamics", "M:1..1")),
            # 0..1, not 1..1: a sequence number is a FORM position, and a
            # descriptor for a name a placement states that no form
            # declares has none -- the absence is what lets a reader tell
            # a declared form apart from the stated extras.
            ("ParameterDescriptor.sequenceNumber",
             _attribute("Integer", "M:0..1")),
            ("ParameterDescriptor.engineeringUnit",
             _attribute("String", "M:0..1")),
            ("ParameterDescriptor.typicalValue",
             _attribute("String", "M:0..1")),
        ],
    },
    "ParameterValue": {
        # NOT an IdentifiedObject: it subclasses nothing in core, so it
        # carries no mRID and no name -- what the parameter IS has already
        # been said on the descriptor.
        "identifiedObject": False,
        "properties": [
            ("ParameterValue.value", _attribute("String", "M:1..1")),
            ("ParameterValue.ParameterDescriptor",
             _association("ParameterDescriptor", "M:1..1")),
            ("ParameterValue.DetailedModelDynamics",
             _association("DetailedModelDynamics", "M:1..1")),
        ],
    },
}

#: Every emt: object is an IdentifiedObject, and CGMES instance files
#: carry mRID and name explicitly; the shapes hold ours to that too.
#: `description` is optional everywhere and carries prose a source file
#: states -- a form parameter's label, a definition's English title, a
#: drawn element's own name -- never an identity.
IDENTIFIED_OBJECT = [
    ("IdentifiedObject.mRID", "xsd:string", 1, 1),
    ("IdentifiedObject.name", "xsd:string", 1, 1),
    ("IdentifiedObject.description", "xsd:string", 0, 1),
]

DESCRIPTIONS = {
    "datatype": "This constraint validates the datatype of the property (attribute).",
    "cardinality": "This constraint validates the cardinality of the property.",
    "valueType": "This constraint validates the value type of the association "
                 "at the used direction.",
    "valueRange": "This constraint validates the value range of the property.",
    "length": "This constraint validates the length of the property value.",
}


class Vocabulary:
    """The parsed emt: RDFS: concrete classes, their inheritance, and the
    properties whose domain is each class."""

    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        #: The namespace is the vocabulary's own xml:base, never a second
        #: copy of the string: the shapes must be constrained in exactly
        #: the namespace the vocabulary declares its terms in.
        self.namespace = root.get(XML + "base") + "#"
        self.classes: dict[str, dict] = {}
        self.properties: dict[str, dict] = {}
        for description in root:
            about = description.get(RDF + "about") or ""
            if not about.startswith("#"):
                continue
            name = about[1:]
            types = [t.get(RDF + "resource") for t in
                     description.findall(RDF + "type")]
            stereotypes = [s.get(RDF + "resource") or ""
                           for s in description.findall(CIMS + "stereotype")]
            if any(t and t.endswith("rdf-schema#Class") for t in types):
                self.classes[name] = {
                    "subClassOf": _resource(description, RDFS + "subClassOf"),
                    "concrete": any(s.endswith("#concrete")
                                    for s in stereotypes),
                }
            elif any(t and t.endswith("#Property") for t in types):
                self.properties[name] = {
                    "domain": _local(_resource(description, RDFS + "domain")),
                    "domainIRI": _resource(description, RDFS + "domain"),
                    "range": _resource(description, RDFS + "range"),
                    "dataType": _local(_resource(description,
                                                 CIMS + "dataType")),
                    "multiplicity": _local(
                        _resource(description, CIMS + "multiplicity")),
                }

    def ancestry(self, name: str) -> list[str]:
        """The class and its emt: superclasses, most-derived first. A
        cim: superclass ends the walk -- this profile declares no cim:
        term, so it inherits no cim: property either."""
        chain = []
        current: str | None = name
        while current in self.classes:
            chain.append(current)
            parent = self.classes[current]["subClassOf"]
            current = _local(parent) if parent and parent.startswith("#") else None
        return chain

    def concrete_subclasses(self, name: str) -> list[str]:
        """Every instantiable class the range admits, in declared order.

        A value-type shape compares the referenced object's OWN rdf:type
        against a list, with no subclass reasoning, so an abstract range
        has to be spelled out as its concrete descendants -- which is
        exactly what the ENTSO-E constraint files do (Terminal
        .ConductingEquipment lists 36 classes).
        """
        return [c for c in self.classes
                if self.classes[c]["concrete"] and name in self.ancestry(c)]

    def properties_of(self, class_name: str) -> list[str]:
        owned = {c: i for i, c in enumerate(self.ancestry(class_name))}
        return sorted(
            (q for q, p in self.properties.items() if p["domain"] in owned),
            key=lambda q: (owned[self.properties[q]["domain"]], q),
        )

    def external_properties(self) -> list[str]:
        """Properties whose domain is a STANDARD CIM class.

        An attribute that belongs on an existing cim: class is declared
        here with ClassName.attribute naming, and its subject's rdf:type
        lives in another document -- so its shape is targeted by
        sh:targetSubjectsOf rather than by sh:targetClass. Targeting by
        class would silently validate nothing, which is the failure mode
        the whole "every emitted class is covered by a shape" oracle
        exists to prevent.
        """
        return sorted(
            q for q, p in self.properties.items()
            if not (p["domainIRI"] or "#").startswith("#")
        )


def _resource(element, tag: str) -> str | None:
    child = element.find(tag)
    return child.get(RDF + "resource") if child is not None else None


def _local(uri: str | None) -> str | None:
    return uri.rsplit("#", 1)[-1] if uri else None


def multiplicity_bounds(multiplicity: str | None) -> tuple[int, int | None]:
    """``M:0..1`` -> (0, 1); ``M:1..n`` -> (1, None); ``M:1`` -> (1, 1)."""
    text = (multiplicity or "M:0..n").removeprefix("M:")
    low, _, high = text.partition("..")
    high = high or low
    return int(low), None if high == "n" else int(high)


def _shape(name: str, kind: str, path: str, order: int,
           constraints: dict[str, str], message: str) -> str:
    lines = [f"emtc:{name}-{kind}", "        rdf:type        sh:PropertyShape ;"]
    for predicate, value in sorted(constraints.items()):
        lines.append(f"        {predicate:<15s} {value} ;")
    lines += [
        f'        sh:description  "{DESCRIPTIONS[kind]}" ;',
        f'        sh:message      "{message}" ;',
        f'        sh:name         "{name}-{kind}" ;',
        f"        sh:order        {order} ;",
        f"        sh:path         {path} ;",
        "        sh:severity     sh:Violation .",
    ]
    return "\n".join(lines)


def _cardinality_constraints(low: int, high: int | None) -> dict[str, str]:
    constraints = {}
    if low:
        constraints["sh:minCount"] = str(low)
    if high is not None:
        constraints["sh:maxCount"] = str(high)
    return constraints


def property_shapes(vocabulary: Vocabulary, qualname: str, order: int,
                    spec: dict | None = None,
                    path: str | None = None) -> tuple[list[str], list[str]]:
    """``(shape texts, shape names)`` for one property.

    ``spec`` and ``path`` default to the vocabulary's own declaration.
    An ADOPTED standard property passes both, because it is declared
    nowhere in the vocabulary and its path is in ``cim:``.
    """
    spec = vocabulary.properties[qualname] if spec is None else spec
    path = _path(qualname) if path is None else path
    low, high = multiplicity_bounds(spec["multiplicity"])
    texts, names = [], []

    def add(kind, shape_path, constraints, message):
        texts.append(_shape(qualname, kind, shape_path, order, constraints,
                            message))
        names.append(f"emtc:{qualname}-{kind}")

    cardinality = _cardinality_constraints(low, high)
    if cardinality:
        add("cardinality", path, cardinality,
            f"Cardinality violation. Bounds are {low}..{high or 'n'}")
    if spec["dataType"]:
        add("datatype", path,
            {"sh:datatype": XSD_OF_PRIMITIVE[spec["dataType"]],
             "sh:nodeKind": "sh:Literal"},
            "The datatype is not literal or it violates the xsd datatype.")
    elif spec["range"]:
        target = spec["range"]
        if target.startswith("#"):
            # a range inside this profile is present in the same document,
            # so its rdf:type can be reached and checked
            admitted = vocabulary.concrete_subclasses(_local(target))
            listed = " ".join(f"emt:{name}" for name in admitted)
            add("valueType", f"( emt:{qualname} rdf:type )",
                {"sh:nodeKind": "sh:IRI", "sh:in": f"( {listed} )"},
                "One of the following does not conform: 1) The value type "
                "shall be IRI; 2) The value type shall be an instance of "
                f"the class: emt:{_local(target)}")
        else:
            # a range in cim: lives in ANOTHER document: this profile
            # attaches by association, so the target's rdf:type is simply
            # not here and only the reference form can be constrained. The
            # cross-file join oracle is what checks it resolves.
            add("valueType", path,
                {"sh:nodeKind": "sh:IRI"},
                "The value type shall be an IRI referencing an instance of "
                f"the class: cim:{_local(target)}")
    if qualname in VALUE_CONSTRAINTS:
        kind, constraints = VALUE_CONSTRAINTS[qualname]
        add(kind, path, constraints,
            f"The value violates the declared {kind}.")
    return texts, names


def _path(qualname: str) -> str:
    return f"emt:{qualname}"


def generate(vocabulary: Vocabulary) -> str:
    out = [header(vocabulary.namespace)]
    shared: list[str] = []
    shared_names: dict[str, list[str]] = {}
    for order, (qualname, datatype, low, high) in enumerate(IDENTIFIED_OBJECT):
        shared.append(_shape(
            qualname, "cardinality", f"cim:{qualname}", order,
            _cardinality_constraints(low, high),
            f"Cardinality violation. Bounds are {low}..{high}"))
        shared.append(_shape(
            qualname, "datatype", f"cim:{qualname}", order,
            {"sh:datatype": datatype, "sh:nodeKind": "sh:Literal"},
            "The datatype is not literal or it violates the xsd datatype."))
        shared_names.setdefault("all", []).extend(
            [f"emtc:{qualname}-cardinality", f"emtc:{qualname}-datatype"])

    blocks: dict[str, str] = {}
    node_shapes: dict[str, list[str]] = {}
    for class_name in sorted(vocabulary.classes):
        if not vocabulary.classes[class_name]["concrete"]:
            continue
        # a class that subclasses nothing is not an IdentifiedObject and
        # carries no mRID and no name -- emt:SignalConnection, exactly as
        # cim:ParameterValue ("identifiedObject": False in ADOPTED)
        identified = vocabulary.classes[
            vocabulary.ancestry(class_name)[-1]]["subClassOf"] is not None
        names = list(shared_names["all"]) if identified else []
        for order, qualname in enumerate(vocabulary.properties_of(class_name)):
            texts, shape_names = property_shapes(vocabulary, qualname, order)
            for text, shape_name in zip(texts, shape_names):
                blocks[shape_name] = text
            names.extend(shape_names)
        node_shapes[class_name] = sorted(names)

    # the adopted standard classes, targeted by class in cim: -- the
    # vocabulary declares none of them, so nothing above reaches them
    overlap = set(ADOPTED) & set(vocabulary.classes)
    if overlap:
        raise ValueError(
            f"ADOPTED names a class the vocabulary also declares: "
            f"{sorted(overlap)}. One name cannot be both standard and ours")
    for class_name in sorted(ADOPTED):
        entry = ADOPTED[class_name]
        names = list(shared_names["all"]) if entry["identifiedObject"] else []
        for order, (qualname, spec) in enumerate(entry["properties"]):
            texts, shape_names = property_shapes(
                vocabulary, qualname, order, spec=spec,
                path=f"cim:{qualname}")
            for text, shape_name in zip(texts, shape_names):
                blocks[shape_name] = text
            names.extend(shape_names)
        properties = " ,\n                        ".join(sorted(names))
        blocks[f"emtc:{class_name}"] = (
            f"emtc:{class_name}\n"
            "        rdf:type        sh:NodeShape ;\n"
            f"        sh:property     {properties} ;\n"
            f"        sh:targetClass  cim:{class_name} ."
        )

    for qualname in vocabulary.external_properties():
        texts, shape_names = property_shapes(vocabulary, qualname, 0)
        for text, shape_name in zip(texts, shape_names):
            blocks[shape_name] = text
        properties = " ,\n                        ".join(sorted(shape_names))
        blocks[f"emtc:{qualname}-nodeShape"] = (
            f"emtc:{qualname}-nodeShape\n"
            "        rdf:type        sh:NodeShape ;\n"
            f"        sh:property     {properties} ;\n"
            f"        sh:targetSubjectsOf  emt:{qualname} ."
        )

    for text in shared:
        blocks[text.split("\n", 1)[0].strip()] = text
    for class_name, names in sorted(node_shapes.items()):
        properties = " ,\n                        ".join(names)
        blocks[f"emtc:{class_name}"] = (
            f"emtc:{class_name}\n"
            "        rdf:type        sh:NodeShape ;\n"
            f"        sh:property     {properties} ;\n"
            f"        sh:targetClass  emt:{class_name} ."
        )
    out.extend(blocks[key] for key in sorted(blocks))
    return "\n\n".join(out) + "\n"


HEADER = """\
# The constraints for the EMT extension profile.
#
# GENERATED from src/pscx/profiles/emt/EMT-AP-Voc-RDFS.rdf by tools/gen_emt_shacl.py;
# edit the vocabulary (or that script's VALUE_CONSTRAINTS table) and
# regenerate. The vocabulary and these constraints are published together.
@prefix cim:     <http://iec.ch/TC57/CIM100#> .
@prefix dcat:    <http://www.w3.org/ns/dcat#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix emt:     <{emt}> .
@prefix emtc:    <{emtc}#> .
@prefix owl:     <http://www.w3.org/2002/07/owl#> .
@prefix rdf:     <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix sh:      <http://www.w3.org/ns/shacl#> .
@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .

emtc:Ontology  rdf:type       owl:Ontology ;
        dcterms:conformsTo    "urn:iso:std:iec:61970-600-2:ed-1" ;
        dcterms:creator       "pscx-cim"@en ;
        dcterms:description   "The constraints for the EMT extension profile."@en ;
        dcterms:identifier    "5a3d1c7e-9b02-5f44-a7d1-6c2e8b0f3d95" ;
        dcterms:language      "en-GB" ;
        dcterms:publisher     "pscx-cim"@en ;
        dcterms:title         "EMT Extension Constraints"@en ;
        owl:versionIRI        <{emtc}/1.0> ;
        owl:versionInfo       "1.0.0"@en ;
        dcat:keyword          "EMT" ;
        dcat:theme            "constraints"@en ."""


def header(namespace: str) -> str:
    """The prefix block and ontology header, in the vocabulary's own
    namespace. The constraints namespace is the vocabulary's plus
    ``/Constraints``, so the two can never name different authorities."""
    return HEADER.format(emt=namespace,
                         emtc=namespace.removesuffix("#") + "/Constraints")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed shapes are stale")
    args = parser.parse_args(argv)
    text = generate(Vocabulary(VOCABULARY))
    if args.check:
        current = SHAPES.read_text() if SHAPES.exists() else ""
        if current != text:
            print(f"{SHAPES} is stale; run python tools/gen_emt_shacl.py")
            return 1
        return 0
    SHAPES.write_text(text)
    print(f"wrote {SHAPES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

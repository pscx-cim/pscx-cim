"""One catalog document for a whole tool library, as CIM instance data.

The case documents describe a library definition only when a case places
it, and then only the slice that placement exercises. The catalog states
the library side once per library version, so a consumer can discover
every model and every declared parameter from CIM alone, without a
``.pslx`` parser of its own: one ``emt:LibraryModelType`` per definition
the library declares, one ``cim:ParameterDescriptor`` per declared form
parameter, one ``emt:ParameterCategory`` per form group, one
``emt:ModelPort`` per declared port, and one ``emt:ToolPayload`` per
opaque fragment a parameter declaration retains (a Choice parameter's
``<choice>`` entries above all) -- every statement under exactly the
terms the case documents already use, so a consumer resolves a case's
side and the catalog's with one rule. The join from a case document to
the catalog is by ``(modelingTool, toolVersion, definitionName)`` -- the
resolution key the vocabulary stipulates -- never by mRID.

The module is an encode/decode pair around one selection:
:func:`library_surface` reads the declared surface out of the library,
:func:`build_catalog` encodes that surface as the graph, and
:func:`read_catalog` decodes a written catalog back to the same plain
data -- which is what lets a test state the inverse without a second
implementation of the selection. :func:`build_shapes` projects the same
surface onto SHACL shapes that check a case document's stated values
against the declarations they name.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import rdflib
from rdflib import RDF, XSD, BNode, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import SH

from pscx.cimxml import (
    CIM,
    CIM_NS,
    DEFAULT_SCENARIO_TIME,
    EMT,
    EMT_NS,
    FORM_ATTRIBUTES,
    MD_NS,
    VOCABULARY_PATH,
    full_model_header,
    payload_text,
    serialize_graph,
)
from pscx.mrid import MridCollision, mrid

#: md:Model.profile of the catalog document: the fourth document of the
#: one vocabulary, its URI following the pattern the other three set.
EMT_LIBRARY_PROFILE_URI = "https://w3id.org/pscx-cim/ns/CIM/EMTLibrary/1.0"


# --------------------------------------------------------------------------
# The selection: what the library declares
# --------------------------------------------------------------------------


def library_surface(project, registry) -> dict[str, dict]:
    """The declared surface of one library, as plain data.

    Keyed by the qualified definition name. Every value is a string (or a
    tuple of them), because the catalog document can carry nothing else
    back. A form may declare one parameter name more than once as
    condition-gated variants; each declaration is its own descriptor,
    distinguished by its position, and a consumer resolves a stated value
    against whichever declaration its environment enables.
    """
    namespace = _namespace(project)
    surface: dict[str, dict] = {}
    for definition in sorted(project.definitions, key=lambda d: d.name or ""):
        if not definition.name:
            continue
        qualified = f"{namespace}:{definition.name}"
        categories = []
        descriptors = []
        sequence = 0
        for cat_sequence, category in enumerate(definition.form, start=1):
            categories.append({
                "sequenceNumber": str(cat_sequence),
                "name": category.name or f"category {cat_sequence}",
                "visible": category.visible or None,
                "condition": category.condition or None,
            })
            for parameter in category.parameters:
                if not parameter.name:
                    continue
                sequence += 1
                payloads = []
                for item in parameter.opaque:
                    parent = item.element.getparent()
                    if parent is None:
                        continue
                    payloads.append({
                        "payloadKind": item.tag,
                        "sequenceNumber": str(parent.index(item.element) + 1),
                        "content": payload_text(item.element),
                    })
                descriptors.append({
                    "sequenceNumber": str(sequence),
                    "name": parameter.name,
                    "category": str(cat_sequence),
                    "description": parameter.desc or None,
                    "engineeringUnit": parameter.unit or None,
                    "typicalValue": parameter.value or None,
                    "attributes": {
                        attribute: getattr(parameter, field)
                        for field, attribute in FORM_ATTRIBUTES
                        if getattr(parameter, field) not in (None, "")
                    },
                    "payloads": payloads,
                })
        ports = []
        component = registry.get((namespace, definition.name))
        if component is not None:
            for port_sequence, port in enumerate(component.ports, start=1):
                if not port.name:
                    continue
                ports.append({
                    "sequenceNumber": str(port_sequence),
                    "portName": port.name,
                    "mode": _text(port.mode),
                    "dimension": _text(port.dim_name or port.dim),
                    "dataType": _text(port.datatype),
                    "electricalType": _text(port.electype),
                    "internal": _text(port.internal),
                    "condition": _text(port.condition),
                })
        surface[qualified] = {
            "description": definition.form_name or None,
            "modelingTool": "PSCAD",
            "toolVersion": project.version or None,
            "categories": categories,
            "descriptors": descriptors,
            "ports": ports,
        }
    return surface


def _namespace(project) -> str:
    return project.name or os.path.splitext(
        os.path.basename(project.path))[0]


def _text(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# --------------------------------------------------------------------------
# Encode: the surface -> one graph
# --------------------------------------------------------------------------


class _Catalog:
    """The one graph, and the minted-mRID ledger that keeps seeds honest."""

    def __init__(self) -> None:
        self.graph = rdflib.Graph()
        self.graph.bind("cim", CIM_NS)
        self.graph.bind("emt", EMT_NS)
        self.graph.bind("md", MD_NS)
        self.minted: dict[str, str] = {}

    def named(self, mrid_value: str, namespace: rdflib.Namespace,
              class_name: str, name: str) -> URIRef:
        existing = self.minted.get(mrid_value)
        if existing is not None:
            raise MridCollision(
                f"two objects were given mRID {mrid_value}: a {existing} "
                f"and a {class_name}; the fix is a seed that tells them "
                f"apart")
        self.minted[mrid_value] = class_name
        subject = URIRef(f"urn:uuid:{mrid_value}")
        self.graph.add((subject, RDF.type, namespace[class_name]))
        self.graph.add((subject, CIM["IdentifiedObject.mRID"],
                        Literal(mrid_value)))
        self.graph.add((subject, CIM["IdentifiedObject.name"], Literal(name)))
        return subject

    def add(self, subject: URIRef, predicate: URIRef, value) -> None:
        if value is None or value == "":
            return
        self.graph.add((subject, predicate, Literal(str(value))))


def build_catalog(project, registry) -> _Catalog:
    """Every definition the library declares, as one graph."""
    catalog = _Catalog()
    namespace = _namespace(project)
    for qualified, declared in library_surface(project, registry).items():
        seed = f"type/{qualified}"
        subject = catalog.named(
            mrid(namespace, "LibraryModelType", seed),
            EMT, "LibraryModelType", qualified)
        catalog.add(subject, CIM["IdentifiedObject.description"],
                    declared["description"])
        catalog.add(subject, EMT["LibraryModelType.definitionName"],
                    qualified)
        catalog.add(subject, EMT["LibraryModelType.modelingTool"],
                    declared["modelingTool"])
        catalog.add(subject, EMT["LibraryModelType.toolVersion"],
                    declared["toolVersion"])
        by_category: dict[str, URIRef] = {}
        for category in declared["categories"]:
            cat_subject = catalog.named(
                mrid(namespace, "ParameterCategory",
                     f"{seed}/category/{category['sequenceNumber']}"),
                EMT, "ParameterCategory", category["name"])
            by_category[category["sequenceNumber"]] = cat_subject
            catalog.graph.add((cat_subject,
                               EMT["ParameterCategory.ModelDefinition"],
                               subject))
            catalog.add(cat_subject, EMT["ParameterCategory.sequenceNumber"],
                        category["sequenceNumber"])
            catalog.add(cat_subject, EMT["ParameterCategory.visible"],
                        category["visible"])
            catalog.add(cat_subject, EMT["ParameterCategory.condition"],
                        category["condition"])
        for declaration in declared["descriptors"]:
            # the position is part of the seed because a form may declare
            # one name more than once, as condition-gated variants
            descriptor = catalog.named(
                mrid(namespace, "ParameterDescriptor",
                     f"{seed}/{declaration['sequenceNumber']}"
                     f"/{declaration['name']}"),
                CIM, "ParameterDescriptor", declaration["name"])
            catalog.graph.add(
                (descriptor,
                 CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
                 subject))
            catalog.graph.add(
                (descriptor, EMT["ParameterDescriptor.ParameterCategory"],
                 by_category[declaration["category"]]))
            catalog.add(descriptor, CIM["IdentifiedObject.description"],
                        declaration["description"])
            catalog.add(descriptor, CIM["ParameterDescriptor.sequenceNumber"],
                        declaration["sequenceNumber"])
            catalog.add(descriptor, CIM["ParameterDescriptor.engineeringUnit"],
                        declaration["engineeringUnit"])
            catalog.add(descriptor, CIM["ParameterDescriptor.typicalValue"],
                        declaration["typicalValue"])
            for attribute, value in declaration["attributes"].items():
                catalog.add(descriptor,
                            EMT[f"ParameterDescriptor.{attribute}"], value)
            for ordinal, payload in enumerate(declaration["payloads"],
                                              start=1):
                payload_subject = catalog.named(
                    mrid(namespace, "ToolPayload",
                         f"{seed}/{declaration['sequenceNumber']}"
                         f"/{declaration['name']}/payload/{ordinal}"),
                    EMT, "ToolPayload", payload["payloadKind"])
                catalog.graph.add(
                    (payload_subject, EMT["ToolPayload.IdentifiedObject"],
                     descriptor))
                catalog.add(payload_subject, EMT["ToolPayload.payloadKind"],
                            payload["payloadKind"])
                catalog.add(payload_subject,
                            EMT["ToolPayload.sequenceNumber"],
                            payload["sequenceNumber"])
                catalog.add(payload_subject, EMT["ToolPayload.content"],
                            payload["content"])
        for port in declared["ports"]:
            port_subject = catalog.named(
                mrid(namespace, "ModelPort",
                     f"{seed}/port/{port['sequenceNumber']}"),
                EMT, "ModelPort", port["portName"])
            catalog.graph.add((port_subject,
                               EMT["ModelPort.DetailedModelTypeDynamics"],
                               subject))
            catalog.add(port_subject, EMT["ModelPort.portName"],
                        port["portName"])
            catalog.add(port_subject, EMT["ModelPort.sequenceNumber"],
                        port["sequenceNumber"])
            catalog.add(port_subject, EMT["ModelPort.mode"], port["mode"])
            catalog.add(port_subject, EMT["ModelPort.dimension"],
                        port["dimension"])
            catalog.add(port_subject, EMT["ModelPort.dataType"],
                        port["dataType"])
            catalog.add(port_subject, EMT["ModelPort.electricalType"],
                        port["electricalType"])
            catalog.add(port_subject, EMT["ModelPort.internal"],
                        port["internal"])
            catalog.add(port_subject, EMT["ModelPort.condition"],
                        port["condition"])
    full_model_header(
        catalog.graph,
        mrid(_namespace(project), "FullModel",
             f"Library/{project.version or ''}"),
        EMT_LIBRARY_PROFILE_URI, DEFAULT_SCENARIO_TIME,
        project.version or "1")
    return catalog


def write_catalog(project, registry, out_path: str) -> _Catalog:
    """Build and serialize in one step; the CLI's whole job."""
    catalog = build_catalog(project, registry)
    with open(out_path, "wb") as handle:
        handle.write(serialize_graph(catalog.graph))
    return catalog


# --------------------------------------------------------------------------
# The per-definition shapes: what the library declares, as constraints
# --------------------------------------------------------------------------

_SPARQL_TARGET = """\
SELECT ?this WHERE {{
  ?this <{cim}ParameterValue.ParameterDescriptor>/<{cim}DetailedModelDescriptor.DetailedModelTypeDynamics>/<{emt}LibraryModelType.definitionName> {name} .
}}"""

_CHOICE_KEY = re.compile(r"<choice>\s*([^=<]*?)\s*=")


def _variant_constraints(graph: rdflib.Graph,
                         declaration: dict) -> list[BNode]:
    """The property shapes one declaration imposes on a stated value.

    The bounds constrain ``emt:ParameterValue.numericValue``, the number
    the stated value evaluates to, because the stated text may be an
    expression or a signal name. A Choice constrains the same number to
    its entries' keys, because a case may state key 1 as ``1.0``.
    """
    constraints = []
    attributes = declaration["attributes"]
    bounds = []
    if attributes.get("parameterType") in ("Real", "Integer"):
        for key, predicate in (("minimum", SH.minInclusive),
                               ("maximum", SH.maxInclusive)):
            try:
                bounds.append((predicate, float(attributes[key])))
            except (KeyError, ValueError):
                pass
    if bounds:
        shape = BNode()
        graph.add((shape, SH.path, EMT["ParameterValue.numericValue"]))
        graph.add((shape, SH.datatype, XSD.float))
        for predicate, bound in bounds:
            graph.add((shape, predicate, Literal(bound)))
        constraints.append(shape)
    if attributes.get("parameterType") == "Choice":
        try:
            keys = [float(match.group(1))
                    for payload in declaration["payloads"]
                    if payload["payloadKind"] == "choice"
                    for match in [_CHOICE_KEY.match(payload["content"])]
                    if match]
        except ValueError:
            keys = []
        if keys:
            points = []
            for key in keys:
                point = BNode()
                graph.add((point, SH.minInclusive, Literal(key)))
                graph.add((point, SH.maxInclusive, Literal(key)))
                points.append(point)
            shape = BNode()
            graph.add((shape, SH.path, EMT["ParameterValue.numericValue"]))
            graph.add((shape, SH.datatype, XSD.float))
            graph.add((shape, SH["or"],
                       Collection(graph, BNode(), points).uri))
            constraints.append(shape)
    return constraints


def build_shapes(surface: dict[str, dict]) -> rdflib.Graph:
    """One SHACL node shape per library definition that bounds a value.

    The shape targets every ``cim:ParameterValue`` whose descriptor's
    type names the definition, which is how the case documents already
    state a placement's values, so the shapes validate those documents
    as written. For each parameter name the shape states an implication:
    a value of that name satisfies at least one of the name's
    declarations. The condition that selects a declaration is an
    expression in the tool's language, so a name declared twice accepts
    what either declaration accepts. A name with an unconstrained
    declaration imposes nothing. Evaluating the target needs SHACL-AF.
    """
    graph = rdflib.Graph()
    graph.bind("sh", SH)
    graph.bind("cim", CIM_NS)
    graph.bind("emt", EMT_NS)
    name_path = Collection(graph, BNode(), [
        CIM["ParameterValue.ParameterDescriptor"],
        CIM["IdentifiedObject.name"]]).uri
    for qualified, declared in sorted(surface.items()):
        variants: dict[str, list[list[BNode]]] = {}
        for declaration in declared["descriptors"]:
            variants.setdefault(declaration["name"], []).append(
                _variant_constraints(graph, declaration))
        implications = []
        for name, options in variants.items():
            if not all(options):
                continue
            named = BNode()
            graph.add((named, SH.path, name_path))
            graph.add((named, SH.hasValue, Literal(name)))
            other = BNode()
            graph.add((other, SH["not"], named))
            branches = [other]
            for constraints in options:
                branch = BNode()
                for constraint in constraints:
                    graph.add((branch, SH.property, constraint))
                branches.append(branch)
            implications.append(
                Collection(graph, BNode(), branches).uri)
        if not implications:
            continue
        shape = URIRef(f"urn:pscx:shape:{quote(qualified)}")
        target = BNode()
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.target, target))
        graph.add((target, RDF.type, SH.SPARQLTarget))
        graph.add((target, SH.select, Literal(_SPARQL_TARGET.format(
            cim=CIM_NS, emt=EMT_NS, name=json.dumps(qualified)))))
        graph.add((shape, SH.message, Literal(
            f"a stated value lies outside what {qualified} declares")))
        for implication in implications:
            graph.add((shape, SH["or"], implication))
    return graph


def write_shapes(surface: dict[str, dict], out_path: str) -> rdflib.Graph:
    """Build and serialize the shapes as Turtle."""
    graph = build_shapes(surface)
    graph.serialize(out_path, format="turtle")
    return graph


# --------------------------------------------------------------------------
# Decode: a written catalog -> the same plain data
# --------------------------------------------------------------------------


def read_catalog(path: str) -> dict[str, dict]:
    """The surface a written catalog document states, in
    :func:`library_surface`'s exact shape."""
    graph = rdflib.Graph()
    graph.parse(path, format="xml")

    def text(subject, predicate) -> str | None:
        value = graph.value(subject, predicate)
        return None if value is None else str(value)

    surface: dict[str, dict] = {}
    subjects: dict[Any, str] = {}
    for subject in graph.subjects(RDF.type, EMT["LibraryModelType"]):
        qualified = text(subject, EMT["LibraryModelType.definitionName"])
        subjects[subject] = qualified
        surface[qualified] = {
            "description": text(subject, CIM["IdentifiedObject.description"]),
            "modelingTool": text(subject, EMT["LibraryModelType.modelingTool"]),
            "toolVersion": text(subject, EMT["LibraryModelType.toolVersion"]),
            "categories": [],
            "descriptors": [],
            "ports": [],
        }
    categories: dict[Any, str] = {}
    for subject in graph.subjects(RDF.type, EMT["ParameterCategory"]):
        owner = graph.value(subject, EMT["ParameterCategory.ModelDefinition"])
        sequence = text(subject, EMT["ParameterCategory.sequenceNumber"])
        categories[subject] = sequence
        surface[subjects[owner]]["categories"].append({
            "sequenceNumber": sequence,
            "name": text(subject, CIM["IdentifiedObject.name"]),
            "visible": text(subject, EMT["ParameterCategory.visible"]),
            "condition": text(subject, EMT["ParameterCategory.condition"]),
        })
    payloads_of: dict[Any, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ToolPayload"]):
        owner = graph.value(subject, EMT["ToolPayload.IdentifiedObject"])
        payloads_of.setdefault(owner, []).append({
            "payloadKind": text(subject, EMT["ToolPayload.payloadKind"]),
            "sequenceNumber": text(subject, EMT["ToolPayload.sequenceNumber"]),
            "content": text(subject, EMT["ToolPayload.content"]),
        })
    for subject in graph.subjects(RDF.type, CIM["ParameterDescriptor"]):
        owner = graph.value(
            subject, CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"])
        attributes = {}
        for _field, attribute in FORM_ATTRIBUTES:
            value = text(subject, EMT[f"ParameterDescriptor.{attribute}"])
            if value is not None:
                attributes[attribute] = value
        surface[subjects[owner]]["descriptors"].append({
            "sequenceNumber": text(
                subject, CIM["ParameterDescriptor.sequenceNumber"]),
            "name": text(subject, CIM["IdentifiedObject.name"]),
            "category": categories[graph.value(
                subject, EMT["ParameterDescriptor.ParameterCategory"])],
            "description": text(subject, CIM["IdentifiedObject.description"]),
            "engineeringUnit": text(
                subject, CIM["ParameterDescriptor.engineeringUnit"]),
            "typicalValue": text(
                subject, CIM["ParameterDescriptor.typicalValue"]),
            "attributes": attributes,
            "payloads": sorted(
                payloads_of.get(subject, []),
                key=lambda p: int(p["sequenceNumber"])),
        })
    for subject in graph.subjects(RDF.type, EMT["ModelPort"]):
        owner = graph.value(subject, EMT["ModelPort.DetailedModelTypeDynamics"])
        surface[subjects[owner]]["ports"].append({
            "sequenceNumber": text(subject, EMT["ModelPort.sequenceNumber"]),
            "portName": text(subject, EMT["ModelPort.portName"]),
            "mode": text(subject, EMT["ModelPort.mode"]),
            "dimension": text(subject, EMT["ModelPort.dimension"]),
            "dataType": text(subject, EMT["ModelPort.dataType"]),
            "electricalType": text(subject, EMT["ModelPort.electricalType"]),
            "internal": text(subject, EMT["ModelPort.internal"]),
            "condition": text(subject, EMT["ModelPort.condition"]),
        })
    for declared in surface.values():
        declared["categories"].sort(
            key=lambda c: int(c["sequenceNumber"]))
        declared["descriptors"].sort(
            key=lambda d: int(d["sequenceNumber"]))
        declared["ports"].sort(key=lambda p: int(p["sequenceNumber"]))
    return surface


# --------------------------------------------------------------------------
# The standing audit: the catalog states only declared vocabulary
# --------------------------------------------------------------------------


def undeclared_terms(graph: rdflib.Graph) -> list[str]:
    """Every ``emt:`` term the catalog states that the published
    vocabulary does not declare -- classes and predicates both. The
    catalog's adoption bill is paid in full, so anything here is a defect
    in the builder or the vocabulary, and the CLI refuses to write it
    quietly."""
    declared = set()
    for element in ET.parse(VOCABULARY_PATH).getroot():
        about = element.get(
            "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", "")
        declared.add(about.lstrip("#").split("#")[-1])
    used = {str(p)[len(EMT_NS):] for _s, p, _o in graph
            if str(p).startswith(EMT_NS)}
    used |= {str(o)[len(EMT_NS):] for _s, p, o in graph
             if p == RDF.type and str(o).startswith(EMT_NS)}
    return sorted(used - declared)

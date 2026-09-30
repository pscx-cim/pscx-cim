"""What every emitted document shares, with no dependency inside pscx.

The namespaces and the vocabulary files the documents are written
against, the ``md:FullModel`` header, the deterministic RDF/XML
serializer, the two literal encodings the source record and the
library catalog both state, and the datatype step a SHACL validator
needs before it reads any of the documents. The catalog path reads a library and writes
one document, so it imports this module and not the case pipeline that
the other documents are emitted from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import rdflib
from rdflib import RDF, Literal, URIRef

#: The vocabulary namespace. One namespace for every add-on document: a
#: component kind that is given a typed class in place of its detailed
#: model keeps its home, which is the whole reason the namespace is named
#: for the domain (EMT) and not for a vendor.
#:
#: The authority is a permanent identifier, not a hosting account, because
#: an RDF namespace is compared as a STRING: once one consumer joins
#: against it, moving it silently unjoins every reference. It is ``https``
#: while every CIM namespace beside it is ``http``, and the two are
#: distinct strings. "Normalizing" this to http would break every join.
#: NOTE: the w3id.org redirect is not registered yet, so the URI does not
#: currently resolve; it is stable as an identifier regardless, which is
#: what RDF needs of it.
EMT_NS = "https://w3id.org/pscx-cim/ns/CIM/EMT#"

CIM_NS = "http://iec.ch/TC57/CIM100#"

#: The published pair, versioned in-repo.
PROFILE_DIR = Path(__file__).resolve().parent / "profiles" / "emt"
VOCABULARY_PATH = PROFILE_DIR / "EMT-AP-Voc-RDFS.rdf"
SHAPES_PATH = PROFILE_DIR / "EMT-AP-Con-SHACL.ttl"

EMT = rdflib.Namespace(EMT_NS)
CIM = rdflib.Namespace(CIM_NS)


#: The form-parameter attributes the surface's descriptors do not carry,
#: paired with the ``emt:ParameterDescriptor`` attribute each is stated
#: as. ``desc``, ``unit`` and ``value`` already ride the descriptor as
#: standard CIM and are deliberately absent here.
FORM_ATTRIBUTES = (
    ("type", "parameterType"),
    ("group", "group"),
    ("intent", "intent"),
    ("dim", "dimension"),
    ("content_type", "contentType"),
    ("minimum", "minimum"),
    ("maximum", "maximum"),
    ("condition", "condition"),
)


def _uri(mrid_value: str) -> URIRef:
    return URIRef(f"urn:uuid:{mrid_value}")


def _split_iri(iri: str) -> tuple[str, str]:
    cut = max(iri.rfind("#"), iri.rfind("/")) + 1
    return iri[:cut], iri[cut:]


def serialize_graph(graph: rdflib.Graph) -> bytes:
    """Deterministic RDF/XML: hand-rolled, because rdflib's serializers
    iterate store subjects in per-process hash order.

    Subjects sort by IRI, predicates within a subject sort by (IRI,
    object); every writer (per-profile files, the emt: add-on) shares
    this ordering so exports diff cleanly. Subjects keep the
    ``urn:uuid:<mRID>`` style everywhere: ``#_<uuid>`` (the conformity
    specimens' rdf:ID style) is a DIFFERENT IRI, and add-on files join
    to EQ purely by IRI, so mixing styles would fail silently.
    """
    by_subject: dict[URIRef, list[tuple]] = {}
    for s, p, o in sorted(graph):
        if not isinstance(s, URIRef):
            raise TypeError(f"non-IRI subject cannot be emitted: {s!r}")
        by_subject.setdefault(s, []).append((p, o))

    bound = {}
    for prefix, ns in sorted(graph.namespaces()):
        if prefix and str(ns) not in bound:
            bound[str(ns)] = prefix
    used = {str(RDF)}
    for entries in by_subject.values():
        for p, o in entries:
            used.add(_split_iri(str(p))[0])
            if p == RDF.type:
                used.add(_split_iri(str(o))[0])
    prefixes: dict[str, str] = {}
    minted = 0
    for ns in sorted(used):
        if ns == str(RDF):
            prefixes[ns] = "rdf"
        elif ns in bound and bound[ns] != "rdf":
            prefixes[ns] = bound[ns]
        else:
            prefixes[ns] = f"ns{minted}"
            minted += 1

    def qname(iri: Any) -> str:
        ns, local = _split_iri(str(iri))
        return f"{prefixes[ns]}:{local}"

    out = ['<?xml version="1.0" encoding="utf-8"?>', "<rdf:RDF"]
    for ns, prefix in sorted(prefixes.items(), key=lambda kv: kv[1]):
        out.append(f"  xmlns:{prefix}={quoteattr(ns)}")
    out[-1] += ">"
    for subject in sorted(by_subject):
        entries = by_subject[subject]
        types = sorted(o for p, o in entries if p == RDF.type)
        head = qname(types[0]) if types else "rdf:Description"
        out.append(f"  <{head} rdf:about={quoteattr(str(subject))}>")
        for p, o in entries:
            if types and p == RDF.type and o == types[0]:
                continue
            pq = qname(p)
            if isinstance(o, URIRef):
                out.append(f"    <{pq} rdf:resource={quoteattr(str(o))}/>")
            elif isinstance(o, Literal):
                if o.language:
                    attr = f" xml:lang={quoteattr(o.language)}"
                elif o.datatype:
                    attr = f" rdf:datatype={quoteattr(str(o.datatype))}"
                else:
                    attr = ""
                out.append(f"    <{pq}{attr}>{escape(str(o))}</{pq}>")
            else:
                raise TypeError(f"non-IRI, non-literal object: {o!r}")
        out.append(f"  </{head}>")
    out.append("</rdf:RDF>")
    return ("\n".join(out) + "\n").encode("utf-8")


#: 61970-552 model-description namespace for the md:FullModel header.
MD_NS = "http://iec.ch/TC57/61970-552/ModelDescription/1#"

#: scenarioTime sentinel: PSCAD cases carry no wall-clock scenario, and
#: a generated timestamp would break byte-reproducibility. Callers may
#: override.
DEFAULT_SCENARIO_TIME = "1970-01-01T00:00:00Z"

#: modelingAuthoritySet default: the project's own w3id identity base,
#: which the root of EMT_NS and the mRID seed namespace already name. The field is
#: a mandatory header attribute in IEC 61970-552 and the one PowSyBl's
#: ``fullModels`` query requires before it will read SSH at all; both
#: measured consumers require the triple's PRESENCE and never interpret
#: the value, so the requirements are: a valid absolute URI, honest, and
#: byte-stable. It names the emitting authority, so a downstream
#: organization restamps it with the CLI override. It is never restamped
#: per document.
DEFAULT_MODELING_AUTHORITY_SET = "https://w3id.org/pscx-cim/ModelingAuthority"


def full_model_header(graph: rdflib.Graph, model_id: str, profile_uri: str,
                      scenario_time: str, version: str,
                      depends_on: tuple[str, ...] = (),
                      modeling_authority_set: str =
                      DEFAULT_MODELING_AUTHORITY_SET) -> URIRef:
    """Add the md:FullModel document header (552 packaging).

    ``depends_on`` lists the model ids this document joins to: the EQ
    model for TP and OP, and whatever model an add-on document joins to.
    A new target is a parameter, never a rewrite. Joins are by IRI only,
    so every id keeps the urn:uuid style.
    """
    subject = _uri(model_id)
    graph.bind("md", MD_NS)
    graph.add((subject, RDF.type, URIRef(MD_NS + "FullModel")))
    graph.add((subject, URIRef(MD_NS + "Model.profile"),
               Literal(profile_uri)))
    graph.add((subject, URIRef(MD_NS + "Model.scenarioTime"),
               Literal(scenario_time)))
    graph.add((subject, URIRef(MD_NS + "Model.version"), Literal(version)))
    graph.add((subject, URIRef(MD_NS + "Model.modelingAuthoritySet"),
               Literal(modeling_authority_set)))
    for dependency in depends_on:
        graph.add((subject, URIRef(MD_NS + "Model.DependentOn"),
                   _uri(dependency)))
    return subject


def payload_text(element) -> str:
    """One retained OPAQUE subtree as the exact string the source states.

    lxml serializes the element WITH its tail, and the tail belongs to
    the parent's text flow, not to the payload, so it is sliced back off.
    The tail must be whitespace, and that is asserted here rather than
    assumed. The result re-parses to a ``canonicalize()``-equal element,
    which is the round trip the literal encoding must survive.
    """
    from lxml import etree as ET

    text = ET.tostring(element, encoding="unicode")
    tail = element.tail
    if tail:
        if not text.endswith(tail) or tail.strip():
            raise ValueError(
                f"a payload's tail is not the whitespace serialization "
                f"assumes: {tail!r} after <{element.tag}>")
        text = text[: -len(tail)]
    return text


def coerce_datatypes(data_graph: rdflib.Graph,
                     shape_graph: rdflib.Graph) -> rdflib.Graph:
    """A copy of ``data_graph`` with its plain literals typed for SHACL.

    A CIMXML document states every value as a plain literal, and CGMES
    tooling assigns each value its datatype when it loads the document.
    A SHACL validator does not take that step, so a ``sh:datatype`` or a
    numeric bound in the shapes fails on every plain value. This function
    takes the step: a plain literal whose predicate is the path of an
    ``sh:datatype`` shape receives that datatype.

    The datatypes come from the shapes and not from the RDFS vocabulary,
    because the shapes also constrain the adopted core CIM classes, which
    the vocabulary does not declare. A lexical form that does not parse
    in its datatype stays plain, so the datatype constraint still reports
    it as malformed.
    """
    from rdflib.namespace import SH, XSD

    declared = {}
    for shape in shape_graph.subjects(SH.datatype, None):
        path = shape_graph.value(shape, SH.path)
        datatype = shape_graph.value(shape, SH.datatype)
        # A plain literal is already an xsd:string in RDF 1.1, but rdflib
        # compares "Voltage" and "Voltage"^^xsd:string as distinct terms.
        # sh:in compares terms, so typing a string would make a shape
        # reject a value it lists as permitted.
        if isinstance(path, URIRef) and datatype != XSD.string:
            declared[path] = datatype
    coerced = rdflib.Graph()
    for s, p, o in data_graph:
        if isinstance(o, Literal) and o.datatype is None and p in declared:
            # normalize=False keeps the stated lexical form. rdflib would
            # otherwise rewrite "1970-01-01T00:00:00Z" as "...+00:00", and
            # the 61970-600-1 scenarioTime rule tests for the trailing Z.
            typed = Literal(str(o), datatype=declared[p], normalize=False)
            if typed.value is not None:
                o = typed
        coerced.add((s, p, o))
    return coerced

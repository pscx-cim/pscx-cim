"""Serialized graphs must be byte-reproducible.

The serialized XML must not depend on the order in which triples were
inserted into the graph. Otherwise two logically equal graphs produce
different files, which breaks diffs, golden tests and downstream consumers
keying on names.
"""


def test_serialized_xml_is_insertion_order_independent():
    # Fails if serialize_graph's element ordering leaks triple insertion
    # order (rdflib's plain serializer emits subjects in store order, so
    # two logically equal graphs would produce different files).
    import rdflib

    from pscx.cim import serialize_graph

    cim = rdflib.Namespace("http://iec.ch/TC57/CIM100#")
    triples = [
        (rdflib.URIRef(f"urn:uuid:0000-{i}"), p, o)
        for i in range(4)
        for p, o in (
            (rdflib.RDF.type, cim.Terminal),
            (cim["IdentifiedObject.name"], rdflib.Literal(f"t{i}")),
            (cim["ACDCTerminal.sequenceNumber"], rdflib.Literal(i)),
        )
    ]
    forward, backward = rdflib.Graph(), rdflib.Graph()
    for t in triples:
        forward.add(t)
    for t in reversed(triples):
        backward.add(t)
    xml = serialize_graph(forward)
    assert xml == serialize_graph(backward)
    reparsed = rdflib.Graph()
    reparsed.parse(data=xml, format="xml")
    assert set(reparsed) == set(triples)

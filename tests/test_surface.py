"""The stated-parameter surface: its read-back and its fallbacks.

:func:`pscx.surface.add_drawn_surface` walks the HIR and writes triples;
:func:`pscx.surface.reconstruct_surface` and
:func:`pscx.surface.restated_join` read triples and know nothing about the
HIR or the builder. The reserved name grammar (``drawn/``, ``type/``)
partitions the source document's namespace against the detailed models,
and the inverse reads that grammar strictly.
"""

import pytest
import rdflib
from conftest import master_available

from pscx.emt import CIM

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)

RDF_TYPE = rdflib.RDF.type


def test_the_join_is_read_strictly():
    # Two defect controls on the inverse. A join stated by a subject
    # outside the drawn grammar is a defect and raises; two subjects of
    # one drawn element stating different restatement sets is a defect
    # and raises -- each paramlist feeds the same flat placements, so
    # one element states one set, whichever subject is asked.
    from pscx.emt import EMT
    from pscx.surface import restated_join

    join_of = EMT["DetailedModelDynamics.IdentifiedObject"]
    name_of = CIM["IdentifiedObject.name"]
    target = rdflib.URIRef("urn:uuid:t")

    graph = rdflib.Graph()
    stray = rdflib.URIRef("urn:uuid:stray")
    graph.add((stray, name_of, rdflib.Literal("type/definition/P/x")))
    graph.add((stray, join_of, target))
    with pytest.raises(ValueError):
        restated_join(graph)

    graph = rdflib.Graph()
    first = rdflib.URIRef("urn:uuid:a")
    second = rdflib.URIRef("urn:uuid:b")
    for subject, name in ((first, "drawn/P/User/1/0"),
                          (second, "drawn/P/User/1/1")):
        graph.add((subject, RDF_TYPE, CIM["DetailedModelDynamics"]))
        graph.add((subject, name_of, rdflib.Literal(name)))
    graph.add((first, join_of, target))
    with pytest.raises(ValueError):
        restated_join(graph)
    graph.add((second, join_of, target))
    assert restated_join(graph) == {("P", "User", "1"): ("urn:uuid:t",)}


def test_an_unnamed_definition_and_an_id_less_wire_take_their_fallbacks(tmp_path):
    # Both fallbacks are driven directly rather than left as code that
    # has never run. One
    # unnamed definition loses its page and is counted; one id-less named
    # wire takes the ordinal and comes back under it.
    from pscx.diagnostics import Diagnostics
    from pscx.hir import (
        HirCanvas,
        HirComponent,
        HirDefinition,
        HirParams,
        HirProject,
        HirWire,
    )
    from pscx.surface import _elements_of

    project = HirProject(
        path="synthetic.pscx", name="synthetic",
        definitions=[
            HirDefinition(
                classid="UserCmpDefn", name=None, id="1",
                canvas=HirCanvas(classid="UserCanvas", components=[
                    HirComponent(classid="UserCmp", id="7",
                                 defn="master:resistor", x="0", y="0",
                                 orient="0")])),
            HirDefinition(
                classid="UserCmpDefn", name="Main", id="2",
                canvas=HirCanvas(classid="UserCanvas", wires=[
                    HirWire(classid="Bus", id=None, name="BUS1", x="0",
                            y="0", orient="0",
                            params=[HirParams(None, {"BaseKV": "13.8"})]),
                ])),
        ])
    bus = Diagnostics()
    elements = _elements_of(project, bus)
    assert bus.by_code() == {"surface_definition_unnamed": 1,
                             "surface_element_without_id": 1}
    assert [(e.page, e.kind, e.ident) for e in elements] == [
        ("Main", "Wire", "#0")]

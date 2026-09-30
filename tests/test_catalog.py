"""The library catalog is an inverse pair around one selection.

``pscx catalog`` states a library's declared surface as CIM instance
data; :func:`pscx.catalog.read_catalog` decodes a written catalog back
to the same plain data. The tests here hold the pair to equality -- over
a fixture library that always runs, and over master.pslx where
PSCAD_MASTER names one -- and pin the master catalog's subject counts so
the gate can never pass over an empty document.
"""

import hashlib

import pytest
from conftest import MASTER_PSLX, master_available

#: A library declaring the shapes the catalog must carry: a Choice
#: parameter with its entries, a bounded Real with unit and default, one
#: spelling declared twice as condition-gated variants, a conditional
#: category, and a declared port. The ports sit under ``<graphics>``
#: because that is where a definition states them.
LIBRARY = """\
<?xml version="1.0" encoding="UTF-8"?>
<project name="lib" version="5.0.2">
  <Definition name="widget">
    <form name="Widget Model">
      <category name="Configuration">
        <parameter name="Kind" type="Choice" desc="Which variant"
                   group="Variant" content_type="Literal" intent="Input"
                   dim="1">
          <value>0</value>
          <help>Which kind of widget</help>
          <vis>G == 1</vis>
          <choice>0 = Plain</choice>
          <choice>1 = Fancy</choice>
          <regex>^[A-Z]+$</regex>
          <error_msg>Letters only</error_msg>
        </parameter>
        <parameter name="G" type="Real" desc="Gain" unit="pu" min="0"
                   max="10">
          <value>1.0</value>
          <help/>
          <vis/>
          <regex/>
          <error_msg/>
        </parameter>
      </category>
      <category name="Advanced" visible="false">
        <cond>Kind == 1</cond>
        <parameter name="G" type="Real" desc="Fancy gain">
          <cond>Kind == 1</cond>
          <value>2.0</value>
        </parameter>
      </category>
    </form>
    <graphics>
      <Port x="0" y="0">
        <paramlist>
          <param name="name" value="A"/>
          <param name="mode" value="1"/>
          <param name="dim" value="3"/>
          <param name="electype" value="1"/>
        </paramlist>
      </Port>
    </graphics>
  </Definition>
</project>
"""


def _fixture_library(tmp_path):
    from pscx.io import load_definitions, read_project

    path = tmp_path / "lib.pslx"
    path.write_text(LIBRARY)
    project = read_project(str(path))
    registry = {}
    load_definitions(str(path), registry)
    return project, registry


#: Every module ``pscx catalog`` may load. The catalog path is the library
#: reader plus the catalog and what every document shares; a module from
#: the case pipeline joining this set is a decision to take here, not an
#: import to add in passing.
LIBRARY_PATH = {
    "pscx", "pscx.catalog", "pscx.cimxml", "pscx.cli", "pscx.common",
    "pscx.diagnostics", "pscx.expr", "pscx.geometry", "pscx.guards",
    "pscx.hir", "pscx.io", "pscx.lower", "pscx.model", "pscx.mrid",
    "pscx.preproc", "pscx.units",
}


def test_the_catalog_path_loads_no_case_pipeline_module(tmp_path):
    import subprocess
    import sys

    library = tmp_path / "lib.pslx"
    library.write_text(LIBRARY)
    # a fresh interpreter, because this one has imported the whole package
    code = (
        "import sys\n"
        "from pscx.cli import main\n"
        f"assert main(['catalog', {str(library)!r}, "
        f"'--out', {str(tmp_path / 'catalog.xml')!r}, "
        f"'--shapes', {str(tmp_path / 'shapes.ttl')!r}]) == 0\n"
        "print(' '.join(m for m in sys.modules if m.startswith('pscx')))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    loaded = set(proc.stdout.splitlines()[-1].split())
    assert loaded - LIBRARY_PATH == set()


#: The SHA-256 of the master.pslx the pinned master counts were recorded
#: against.
RECORDED_MASTER_SHA256 = (
    "b71bf6dad765d8d6ca53d1ddf4f3e8a3101fad04bee23fd4cd76084e353d00d9")


def _skip_unless_recorded_master(what: str) -> None:
    digest = hashlib.sha256()
    with open(MASTER_PSLX, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != RECORDED_MASTER_SHA256:
        pytest.skip(f"{what} were recorded against a different master.pslx")


def test_the_catalog_round_trips_what_the_library_declares(tmp_path):
    from pscx.catalog import library_surface, read_catalog, write_catalog

    project, registry = _fixture_library(tmp_path)
    declared = library_surface(project, registry)
    out = str(tmp_path / "catalog.xml")
    write_catalog(project, registry, out)

    assert read_catalog(out) == declared

    # the equality above is the gate; the shape below is what makes it
    # non-vacuous -- a selection that dropped the choices, the bounds or
    # the second declaration of one spelling would still be "equal"
    widget = declared["lib:widget"]
    assert widget["toolVersion"] == "5.0.2"
    assert [d["name"] for d in widget["descriptors"]] == ["Kind", "G", "G"]
    kind, gain, fancy = widget["descriptors"]
    assert [p["content"] for p in kind["payloads"]] == [
        '<help>Which kind of widget</help>',
        '<vis>G == 1</vis>',
        '<choice>0 = Plain</choice>',
        '<choice>1 = Fancy</choice>',
        '<regex>^[A-Z]+$</regex>',
        '<error_msg>Letters only</error_msg>',
    ]
    assert kind["attributes"]["group"] == "Variant"
    assert kind["attributes"]["contentType"] == "Literal"
    assert kind["attributes"]["dimension"] == "1"
    assert kind["attributes"]["intent"] == "Input"
    assert [p["payloadKind"] for p in gain["payloads"]] == [
        "help", "vis", "regex", "error_msg",
    ]
    assert gain["attributes"]["minimum"] == "0"
    assert gain["attributes"]["maximum"] == "10"
    assert gain["engineeringUnit"] == "pu"
    assert fancy["attributes"]["condition"] == "Kind == 1"
    assert fancy["category"] != gain["category"]
    assert widget["categories"][1]["visible"] == "false"
    assert widget["categories"][1]["condition"] == "Kind == 1"
    port, = widget["ports"]
    assert (port["portName"], port["dimension"], port["mode"]) == \
        ("A", "3", "1")


def test_the_catalog_states_only_declared_vocabulary(tmp_path):
    # The adoption bill is paid: every emt: class and predicate the
    # catalog states is declared by the published vocabulary. Fails when
    # a term is added to the builder without its declaration, or dropped
    # from the vocabulary while the builder still states it.
    from pscx.catalog import build_catalog, undeclared_terms

    project, registry = _fixture_library(tmp_path)
    catalog = build_catalog(project, registry)
    assert undeclared_terms(catalog.graph) == []


def test_the_catalog_document_conforms_to_the_shapes(tmp_path):
    import shacl_harness

    from pscx.catalog import build_catalog
    from pscx.cimxml import SHAPES_PATH

    project, registry = _fixture_library(tmp_path)
    catalog = build_catalog(project, registry)
    conforms, report = shacl_harness.validate(catalog.graph,
                                              [str(SHAPES_PATH)])
    assert conforms, report


@pytest.mark.slow
@pytest.mark.skipif(
    not master_available(),
    reason=f"master.pslx not found at {MASTER_PSLX}; "
    "set PSCAD_MASTER to a local master.pslx",
)
def test_the_master_catalog_round_trips_and_pins_its_counts(tmp_path):
    # The pinned counts belong to the master.pslx whose digest is
    # RECORDED_MASTER_SHA256. A different master.pslx legitimately
    # declares a different surface, and that is a skip, never a silent
    # pass over different numbers.
    _skip_unless_recorded_master("counts")

    from pscx.catalog import library_surface, read_catalog, write_catalog
    from pscx.io import load_master, master_project

    project = master_project()
    registry = load_master()
    out = str(tmp_path / "catalog.xml")
    catalog = write_catalog(project, registry, out)

    assert read_catalog(out) == library_surface(project, registry)

    counts = {}
    for class_name in catalog.minted.values():
        counts[class_name] = counts.get(class_name, 0) + 1
    assert counts == {
        "LibraryModelType": 392,
        "ParameterCategory": 998,
        "ParameterDescriptor": 8957,
        "ModelPort": 2734,
        "ToolPayload": 18503,
    }


def _stated(graph, definition, name, number, tag):
    """One placement of ``definition`` stating ``name`` as ``number``,
    with plain literals, the way a case document writes it."""
    from rdflib import RDF, Literal, URIRef

    from pscx.emt import CIM, EMT

    kind = URIRef(f"urn:test:type:{definition}")
    descriptor = URIRef(f"urn:test:descriptor:{definition}/{name}")
    value = URIRef(f"urn:test:value:{tag}")
    graph.add((kind, RDF.type, EMT["LibraryModelType"]))
    graph.add((kind, EMT["LibraryModelType.definitionName"],
               Literal(definition)))
    graph.add((descriptor, RDF.type, CIM["ParameterDescriptor"]))
    graph.add((descriptor, CIM["IdentifiedObject.name"], Literal(name)))
    graph.add((descriptor,
               CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"],
               kind))
    graph.add((value, RDF.type, CIM["ParameterValue"]))
    graph.add((value, CIM["ParameterValue.ParameterDescriptor"], descriptor))
    graph.add((value, CIM["ParameterValue.value"], Literal(number)))
    graph.add((value, EMT["ParameterValue.numericValue"], Literal(number)))


@pytest.mark.parametrize("definition, name, number, accepted", [
    ("lib:widget", "Kind", "1.0", True),     # a key, stated as a float
    ("lib:widget", "Kind", "7", False),      # not a key
    ("lib:other", "Kind", "7", True),        # another definition's Kind
    ("lib:widget", "G", "99", True),         # the unbounded variant accepts
])
def test_the_shapes_check_a_stated_value_against_its_declaration(
        tmp_path, definition, name, number, accepted):
    import rdflib
    import shacl_harness

    from pscx.catalog import library_surface, write_shapes

    project, registry = _fixture_library(tmp_path)
    shapes = str(tmp_path / "shapes.ttl")
    write_shapes(library_surface(project, registry), shapes)
    graph = rdflib.Graph()
    _stated(graph, definition, name, number, "v")
    conforms, report = shacl_harness.validate(graph, [shapes])
    assert conforms is accepted, report


@pytest.mark.parametrize("number, accepted", [
    ("0", True), ("10", True), ("-0.5", False), ("11", False),
])
def test_the_shapes_bound_a_parameter_declared_once(tmp_path, number,
                                                    accepted):
    # Dropping the condition-gated variant leaves G declared once, with
    # the bounds 0 and 10.
    import rdflib
    import shacl_harness

    from pscx.catalog import library_surface, write_shapes

    project, registry = _fixture_library(tmp_path)
    surface = library_surface(project, registry)
    surface["lib:widget"]["descriptors"].pop()
    shapes = str(tmp_path / "shapes.ttl")
    write_shapes(surface, shapes)
    graph = rdflib.Graph()
    _stated(graph, "lib:widget", "G", number, "v")
    conforms, report = shacl_harness.validate(graph, [shapes])
    assert conforms is accepted, report

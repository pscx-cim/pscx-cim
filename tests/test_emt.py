"""The emt: extension profile: vocabulary, shapes, and parameter identity."""

import os
import xml.etree.ElementTree as ET

import pytest
import shacl_harness
from conftest import REPO_ROOT, master_available

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)

#: Concrete classes the vocabulary declares and no producer emits yet, each
#: with the reason it is declared first and the work that drains it.
#:
#: The rule this suspends stays the default: a published identifier can
#: gain terms and can never retract them, so a class nobody emits would be
#: published, shaped and unreachable, and the vocabulary carries no
#: open-ended exemption list. A class earns an entry here when it states
#: how a carried design joins to the network documents. A join is
#: expensive to change afterwards, because moving it unjoins every
#: consumer that already resolved against it, so the classes of one join
#: are declared together as one design before their emitters exist.
#:
#: The exemption is bounded two ways. An entry names the work that
#: removes it, so the list drains rather than accumulates. Nothing here is
#: published while the w3id redirect is unfiled, and the trigger for
#: filing it is an artifact leaving this machine, so a retraction is
#: available until then. A class still on this list when that trigger
#: fires is deleted rather than published.
#:
#: The dict is empty: every declared concrete class has its producer, which
#: is the class-side precondition for the w3id redirect. It is where
#: the next declared-ahead class is named and drained from.
DECLARED_AHEAD_OF_ITS_PRODUCER: dict = {}


def _vocabulary():
    """``(classes, properties)`` declared by the RDFS, as local names.
    Each class maps to ``(is concrete, superclass local name or None)``."""
    from pscx.cimxml import VOCABULARY_PATH

    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    rdfs = "{http://www.w3.org/2000/01/rdf-schema#}"
    classes, properties = {}, set()
    for description in ET.parse(VOCABULARY_PATH).getroot():
        about = description.get(rdf + "about") or ""
        if not about.startswith("#"):
            continue
        types = [t.get(rdf + "resource") or ""
                 for t in description.findall(rdf + "type")]
        if any(t.endswith("rdf-schema#Class") for t in types):
            cims = "{http://iec.ch/TC57/1999/rdf-schema-extensions-19990926#}"
            parent = description.find(rdfs + "subClassOf")
            parent = parent.get(rdf + "resource") if parent is not None else ""
            classes[about[1:]] = (
                any((s.get(rdf + "resource") or "").endswith("#concrete")
                    for s in description.findall(cims + "stereotype")),
                parent[1:] if parent.startswith("#") else None,
            )
        elif any(t.endswith("#Property") for t in types):
            properties.add(about[1:])
    return classes, properties


def test_every_term_declared_ahead_of_its_producer_names_bucket_and_emitter():
    # A per-CLASS reason, not a per-batch rule. "The design classes are
    # declared first" is a rule, and a rule cannot say which class it
    # forgot -- so each entry carries its own words, names the bucket whose
    # emitter drains it, and is substantive enough to argue with. Without
    # this the table is an allowlist, which the vocabulary does not carry.
    for name, why in DECLARED_AHEAD_OF_ITS_PRODUCER.items():
        assert len(why.split()) >= 12, f"{name}: {why!r}"
        assert "bucket" in why, f"{name} names no bucket: {why!r}"
        assert "emitter" in why, f"{name} names no producer: {why!r}"


def test_the_namespace_is_one_string_across_the_publication():
    # The one thing here that can never move once a consumer joins against
    # it, so the emitter's constants and the published documents must name
    # it identically. Nothing else checks this: the vocabulary declares its
    # terms RELATIVE to xml:base (`rdf:about="#ModelTerminal"`), so a
    # constant that drifted from the base would still strip to the same
    # local names and leave every other test green while every emitted
    # term silently pointed at a namespace the publication never defined.
    from pscx.cimxml import EMT_NS, SHAPES_PATH, VOCABULARY_PATH
    from pscx.emt import EMT_PROFILE_URI, EMT_SIMULATION_PROFILE_URI

    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    owl = "{http://www.w3.org/2002/07/owl#}"
    root = ET.parse(VOCABULARY_PATH).getroot()
    # xml:base carries no '#'; the namespace is the base plus the separator
    assert root.get("{http://www.w3.org/XML/1998/namespace}base") + "#" == EMT_NS
    header = next(d for d in root
                  if (d.get(rdf + "about") or "").endswith("#Ontology"))
    assert header.get(rdf + "about") == EMT_NS + "Ontology"
    assert (header.find(owl + "versionIRI").get(rdf + "resource")
            == EMT_PROFILE_URI)

    from pscx.surface import EMT_SOURCE_PROFILE_URI

    shapes = SHAPES_PATH.read_text()
    assert f"@prefix emt:     <{EMT_NS}> ." in shapes
    # the profile URIs are siblings under one authority, never a mix
    root_uri = EMT_NS.removesuffix("EMT#")
    profile_uris = (EMT_PROFILE_URI, EMT_SIMULATION_PROFILE_URI,
                    EMT_SOURCE_PROFILE_URI)
    for uri in profile_uris:
        assert uri.startswith(root_uri), uri
    assert len(set(profile_uris)) == 3


def test_committed_shapes_are_the_vocabulary_projected():
    # The shapes are a mechanical projection of the vocabulary, so a
    # vocabulary change that never reached them would leave the new term
    # unconstrained while every positive test stayed green.
    import sys

    sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
    import gen_emt_shacl

    assert gen_emt_shacl.main(["--check"]) == 0, (
        "src/pscx/profiles/emt/EMT-AP-Con-SHACL.ttl is stale; run "
        "python tools/gen_emt_shacl.py")


def test_the_rendered_schema_matches_the_vocabulary():
    # The Markdown is what a reviewer reads instead of RDF/XML, so a
    # vocabulary change that never reached it would be a schema change
    # nobody could see in the diff.
    import io
    import sys

    from pscx.cimxml import PROFILE_DIR, VOCABULARY_PATH

    sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
    import dump_profile

    rendered = io.StringIO()
    dump_profile.dump_markdown(*dump_profile.parse(VOCABULARY_PATH),
                               out=rendered)
    committed = (PROFILE_DIR / "EMT.md").read_text()
    assert committed == rendered.getvalue(), (
        "src/pscx/profiles/emt/EMT.md is stale; run python tools/dump_profile.py "
        "src/pscx/profiles/emt/EMT-AP-Voc-RDFS.rdf --markdown --out src/pscx/profiles/emt/EMT.md")


def test_every_concrete_emt_class_is_targeted_by_a_shape():
    # The same standard the standard documents are held to: "0 violations"
    # is vacuous for a class no shape targets, so the coverage is asserted
    # rather than assumed -- both directions, so a shape for a class the
    # vocabulary dropped also fails.
    from rdflib.namespace import SH

    from pscx.cimxml import CIM_NS, EMT_NS, SHAPES_PATH

    classes, _properties = _vocabulary()
    concrete = {name for name, (is_concrete, _parent) in classes.items()
                if is_concrete}
    shapes = shacl_harness.shapes((str(SHAPES_PATH),))
    targeted = {str(c) for c in shapes.objects(None, SH.targetClass)}

    assert {t.removeprefix(EMT_NS)
            for t in targeted if t.startswith(EMT_NS)} == concrete
    # The adopted standard classes carry most of this document and the
    # vocabulary declares none of them, so their coverage cannot come from
    # `concrete` and has to be asserted against the generator's own table.
    import sys
    sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
    import gen_emt_shacl
    assert {t.removeprefix(CIM_NS)
            for t in targeted if t.startswith(CIM_NS)} == set(
                gen_emt_shacl.ADOPTED)
    # and nothing is targeted in a third namespace
    assert not [t for t in targeted
                if not t.startswith((EMT_NS, CIM_NS))]


def test_one_parameter_is_one_key_however_a_placement_spells_it():
    # A parameter's identity in this module is its case-folded key: the
    # descriptor set is keyed by it (two spellings are one parameter)
    # and so is the mRID seed of every cim:ParameterValue. A
    # placement that stated the same key twice would therefore seed two
    # values onto ONE IRI, and rdflib merges them into a subject carrying
    # two cim:ParameterValue.value literals with nothing raised.
    #
    # Both branches that can do it are here, because an unexercised
    # branch is where this returns. A hosted device declares no
    # ComponentDef at all, so nothing folds its paramlist for it; and a
    # component can state a name its own form never declares, which no
    # declared-name fold covers either.
    import types

    from pscx.emt import _stated_parameters

    device = types.SimpleNamespace(params={"Foo": "1", "foo": "2"})
    definition = types.SimpleNamespace(defaults={"Bar": ""}, units={})
    component = types.SimpleNamespace(
        definition=definition,
        params={"Bar": "9", "Foo": "1", "foo": "2"})

    for carrier in (device, component):
        keys = [key for key, _spelling in _stated_parameters(carrier)]
        assert len(keys) == len(set(keys)), _stated_parameters(carrier)

    # ...and the surviving spelling is the LAST in document order, which is
    # the spelling pscx.cim._stated reads the value from. A fold that kept
    # the first would publish a name whose value another rule resolves.
    assert dict(_stated_parameters(device))["foo"] == "foo"
    assert dict(_stated_parameters(component))["foo"] == "foo"


def test_a_model_types_identity_covers_its_parameter_set_and_key_space():
    # A type's mRID seed has to keep two things apart that are otherwise
    # silently conflated.
    #
    # This fails if the DESCRIPTOR SET stops being part of the identity:
    # the set is the union over the placements of ONE document, and one
    # project name can be carried by two different case files, so a
    # definition placed differently in the two would mint one subject
    # carrying two parameter sets and two sequenceNumbers, with no error
    # anywhere. It also fails if a definition name and a
    # placement path share one key space, where a definition named
    # "Main/1014742664" would take over a device's type.
    from pscx.emt import _type_seed

    two = [("a", "A"), ("b", "B")]
    one = [("a", "A")]
    definition = ("definition", "master:peswitch")

    assert _type_seed(definition, two) == _type_seed(definition, two)
    assert _type_seed(definition, two) != _type_seed(definition, one)
    # the ORDER is part of it too: sequenceNumber is read off this list
    assert _type_seed(definition, two) != _type_seed(definition, two[::-1])
    assert _type_seed(("definition", "x"), two) != \
        _type_seed(("placement", "x"), two)

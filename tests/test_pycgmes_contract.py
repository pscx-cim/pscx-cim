"""Characterization of the pycgmes facts the emitter depends on.

Each test pins a contract the generic RDF/XML writer builds on; a pycgmes
upgrade that changes it must fail here, not deep inside emission.
"""

import rdflib
from pycgmes.resources.ACLineSegment import ACLineSegment
from pycgmes.utils.constants import NAMESPACES
from pycgmes.utils.profile import Profile

MRID = "11111111-2222-3333-4444-555555555555"


def test_aclinesegment_constructs_and_round_trips_values():
    # Fails if pycgmes renames mRID/r/x or coerces the values.
    seg = ACLineSegment(mRID="11111111-2222-3333-4444-555555555555", r=1.25, x=2.5)
    assert seg.mRID == "11111111-2222-3333-4444-555555555555"
    assert seg.r == 1.25
    assert seg.x == 2.5


def test_profile_attributes_are_flat_class_attr_dicts():
    # Fails if cgmes_attributes_in_profile changes its return shape, on
    # which the generic writer's single loop depends.
    seg = ACLineSegment(mRID="11111111-2222-3333-4444-555555555555", r=1.25)
    attrs = seg.cgmes_attributes_in_profile(Profile.EQ)
    assert isinstance(attrs, dict) and attrs
    for key, entry in attrs.items():
        klass, _, attr = key.partition(".")
        assert klass and attr, f"key {key!r} is not 'Class.attr'"
        assert "value" in entry and "namespace" in entry


def test_eq_and_sc_profiles_partition_aclinesegment_differently():
    # 13 EQ vs 5 SC attributes for ACLineSegment in pycgmes 2.0.6; fails if
    # an upgrade moves attributes between profiles (the writer would then
    # emit different files).
    seg = ACLineSegment(mRID="11111111-2222-3333-4444-555555555555")
    eq = seg.cgmes_attributes_in_profile(Profile.EQ)
    sc = seg.cgmes_attributes_in_profile(Profile.SC)
    assert len(eq) == 13, sorted(eq)
    assert len(sc) == 5, sorted(sc)
    # ...and the entries carry CLASS DEFAULTS, not absence, which is why
    # the profile view alone cannot say what a document should carry: a
    # writer emitting these would fabricate data.
    defaults = {key.rpartition(".")[2]: entry["value"]
                for key, entry in eq.items()}
    assert defaults["name"] == ""
    assert defaults["aggregate"] is False
    assert defaults["x"] == 0.0 and defaults["gch"] == 0.0


def test_cim_namespace_is_cim100():
    # Fails if pycgmes targets a different CIM version namespace; every
    # emitted rdf:Description URI hangs off this.
    assert NAMESPACES["cim"] == "http://iec.ch/TC57/CIM100#"


def test_the_writer_supplies_the_mrid_pycgmes_never_surfaces():
    # `IdentifiedObject.mRID` is a required explicit attribute in a CGMES
    # 3.0 instance file, and the profile view never returns it -- in ANY
    # profile, for any class. Both halves are pinned here, because a
    # release that started surfacing it would leave the writer emitting
    # the triple twice and no other test would say why.
    from pycgmes.resources.ACLineSegment import ACLineSegment

    from pscx.cim import CimModel, profile_graph

    seg = ACLineSegment(mRID=MRID, r=1.25)
    for profile in Profile:
        assert not [key for key in seg.cgmes_attributes_in_profile(profile)
                    if key.endswith(".mRID")], profile

    model = CimModel(project="mrid-contract")
    model.new(ACLineSegment, mRID=MRID, r=1.25)
    graph = profile_graph(model, Profile.EQ)
    assert (rdflib.URIRef(f"urn:uuid:{MRID}"),
            rdflib.URIRef(NAMESPACES["cim"] + "IdentifiedObject.mRID"),
            rdflib.Literal(MRID)) in graph


def test_an_enum_passed_to_the_constructor_loses_its_class():
    # The contract behind "assign enums with CimModel.set, never through
    # the constructor". Every pycgmes enum field is annotated ``str``, so
    # pydantic validates a MEMBER down to its bare value and str() then
    # yields "generator" where the profile's sh:in lists
    # cim:SynchronousMachineKind.generator -- an IRI that resolves to
    # nothing and reads as a typo rather than as an error. Both halves are
    # pinned: the loss through the constructor, and the survival through
    # post-construction assignment.
    from pycgmes.resources.SynchronousMachine import SynchronousMachine
    from pycgmes.resources.SynchronousMachineKind import SynchronousMachineKind

    through_constructor = SynchronousMachine(
        mRID=MRID, type=SynchronousMachineKind.generator)
    assert str(through_constructor.type) == "generator"

    assigned = SynchronousMachine(mRID=MRID)
    assigned.type = SynchronousMachineKind.generator
    assert str(assigned.type) == "SynchronousMachineKind.generator"


def test_the_writer_refuses_a_bare_enum_value():
    # ...and the convention is not left as a thing to remember:
    # profile_graph raises on the stripped value rather than writing a
    # document that validates nowhere and says something subtly false.
    import pytest
    from pycgmes.resources.SynchronousMachine import SynchronousMachine
    from pycgmes.resources.SynchronousMachineKind import SynchronousMachineKind

    from pscx.cim import CimModel, profile_graph

    model = CimModel(project="enum-contract")
    stripped = model.new(SynchronousMachine, mRID=MRID,
                         type=SynchronousMachineKind.generator)
    with pytest.raises(ValueError, match="bare enum value"):
        profile_graph(model, Profile.EQ)

    # the same field assigned the way the emitter assigns it comes out a
    # namespace-qualified IRI, so the raise is about the loss and not
    # about the attribute
    model.set(stripped, "type", SynchronousMachineKind.generator)
    graph = profile_graph(model, Profile.EQ)
    assert (None, None, rdflib.URIRef(
        NAMESPACES["cim"] + "SynchronousMachineKind.generator")) in graph

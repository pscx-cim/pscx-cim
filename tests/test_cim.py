"""CIM mapping + generic RDF/XML writer."""

import math

import pytest


def test_two_objects_with_one_mrid_raise_and_the_error_names_both():
    # The negative control for the refusal. A collision that overwrote
    # would drop the first object from the document while the run still
    # exited 0, and a counted piece of equipment that is not in the file
    # is worse than a bad enum, which profile_graph also raises on.
    #
    # Fails if a collision is survivable, or if the message does not name
    # the two objects that shared the identity. The names are what a
    # reader needs: an mRID is uuid5 over one seed, so two objects
    # colliding means one seed was used twice.
    from pscx.cim import CimModel
    from pscx.mrid import MridCollision, mrid

    seed = "Main/1234/BR"
    shared = mrid("hand-built", "Breaker", seed)
    model = CimModel(project="hand-built")

    class _Res:
        def __init__(self, name):
            self.mRID, self.name = shared, name

    model.add(_Res("first"))
    with pytest.raises(MridCollision) as raised:
        model.add(_Res("second"))
    text = str(raised.value)
    assert shared in text
    assert "first" in text and "second" in text
    assert "pscx/mrid.py" in text
    # ...and the first object is still there: nothing was lost on the way
    # to raising
    assert model.resources[shared].name == "first"


def test_the_measurement_kinds_selector_cannot_drift_from_the_quantities():
    # One table, two views. The "is this equipment or a meter" question
    # and the "what does it measure" question answered from one source,
    # so a kind cannot be a measurement point with no quantity named for
    # it, nor carry a quantity while being emitted as equipment.
    from pscx.rules import (
        MASTER_KIND_TO_CIM,
        MEASUREMENT_KINDS,
        OUTPUT_QUANTITY,
        UNTYPED_OUTPUTS,
    )

    assert MEASUREMENT_KINDS == frozenset(
        kind for kind, _param in set(OUTPUT_QUANTITY) | UNTYPED_OUTPUTS)
    assert not MEASUREMENT_KINDS & set(MASTER_KIND_TO_CIM)


def test_branch_reactance_hand_computed_values():
    # Fails if the phasor conversion is wrong in formula or sign:
    # x_L = 2*pi*f*L -> f=60, L=0.1 H gives 37.699111843077517 ohm;
    # x_C = -1/(2*pi*f*C) -> f=60, C=1 uF gives -2652.5823848649226 ohm
    # (both hand-derived), and a pure resistor stays x=0 at any f.
    from pscx.cim import branch_reactance

    assert abs(branch_reactance(60.0, 0.1, 0.0) - 37.699111843077517) < 1e-9
    assert abs(branch_reactance(60.0, 0.0, 1e-6) - (-2652.5823848649226)) < 1e-9
    assert branch_reactance(60.0, 0.0, 0.0) == 0.0
    # series L+C at the same frequency superpose
    both = branch_reactance(60.0, 0.1, 1e-6)
    assert abs(both - (37.699111843077517 - 2652.5823848649226)) < 1e-9


def test_source_injection_rule_signs_base_and_liveness():
    # The rule itself, held to its three verified authorities: the load
    # sign convention ("positive sign means flow out from a node", so
    # an injection INTO the network is negative), the form's own MVA
    # base, and liveness by presence in
    # the selector-respected parameter set. The benchmark's bus-30 row:
    # Pinit 2.50 pu / Qinit 0.832 pu on 100 MVA states -250 MW / -83.2
    # Mvar. Fails if the sign flips, the base is hardcoded, a stated
    # zero comes back signed, or a gated-off pair is defaulted instead
    # of absent.
    from pscx.rules import SOURCE_FORMS, source_initial_injection

    form = SOURCE_FORMS["source3"]
    assert source_initial_injection(
        form, {"pinit": 2.5, "qinit": 0.832, "mva": 100.0}) == (-250.0, -83.2)
    assert source_initial_injection(
        form, {"pinit": 2.5, "qinit": 0.832, "mva": 90.0}) == (-225.0, -74.88)
    p_mw, q_mvar = source_initial_injection(
        form, {"pinit": 0.0, "qinit": 0.0, "mva": 100.0})
    assert math.copysign(1.0, p_mw) == 1.0 == math.copysign(1.0, q_mvar)
    # pair live but no live base, and pair gated off entirely
    assert source_initial_injection(
        form, {"pinit": 2.5, "qinit": 0.832}) is None
    assert source_initial_injection(form, {"mva": 100.0}) is None
    # forms without the triple never state an injection
    assert source_initial_injection(
        SOURCE_FORMS["source1"],
        {"pinit": 2.5, "qinit": 0.832, "mva": 100.0}) is None
    assert source_initial_injection(None, {}) is None

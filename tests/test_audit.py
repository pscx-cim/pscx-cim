"""The CGMES class catalogue in :mod:`pscx.audit`.

The catalogue reads CGMES classes, their terminal cardinalities and their
profiles off pycgmes, the SHACL shapes and the conformity files. These
checks hold what it reads against what those sources state.
"""

import pytest


@pytest.fixture(scope="module")
def catalogue():
    import os

    from pscx.audit import ENTSOE_SHACL, cim_catalogue

    if not os.path.isdir(ENTSOE_SHACL):
        pytest.skip("set ENTSOE_SHACL to the ENTSO-E CGMES SHACL directory")
    return cim_catalogue()


# --------------------------------------------------------------------------
# The CIM side: what the catalogue reads off CGMES
# --------------------------------------------------------------------------


def test_the_catalogue_separates_classes_from_datatypes_and_enums(catalogue):
    # The 526 "classes" of the resource package are 439 classes, 27 CIM
    # datatypes and 59 enumerations. Only the first can hold a resource.
    # Fails if pycgmes reorganises the package and the three start being
    # counted as one thing.
    assert len(catalogue) == 439
    assert "ACLineSegment" in catalogue and "Resistance" not in catalogue
    assert all(cim_class.doc for cim_class in catalogue.values())


def test_terminal_cardinality_comes_from_shapes_and_from_specimens(catalogue):
    # The two sources are independent and must not contradict each other
    # where they overlap: the shapes STATE that a switch has two
    # terminals, the conformity files EXHIBIT 861 breakers with two.
    # Fails if the SHACL inverse-path reading breaks (every cardinality
    # goes None) or if the specimen reader joins on the wrong id.
    import os

    from pscx.audit import CGMES_SPECIMENS

    if not os.path.isdir(CGMES_SPECIMENS):
        pytest.skip("set CGMES_SPECIMENS to the CGMES 3 conformity models")
    assert catalogue["Switch"].stated_terminals == (2, 2)
    assert catalogue["Breaker"].stated_terminals == (2, 2)
    assert catalogue["LinearShuntCompensator"].stated_terminals == (1, 1)
    assert catalogue["CsConverter"].stated_dc_terminals == (2, 2)
    assert catalogue["ACLineSegment"].observed_terminals == frozenset({2})
    assert catalogue["ConformLoad"].observed_terminals == frozenset({1})
    assert catalogue["PowerTransformer"].observed_terminals == frozenset({2, 3})
    for name, cim_class in catalogue.items():
        stated, observed = cim_class.stated_terminals, cim_class.observed_terminals
        if stated and observed:
            assert all(stated[0] <= n <= stated[1] for n in observed), name


def test_a_class_joins_a_profile_only_on_a_substantive_attribute(catalogue):
    # In the catalogue, `IdentifiedObject.*` is placed in every profile,
    # so counting it puts all 439 classes in all ten and the
    # "which profiles do we emit nothing from" question answers itself
    # wrongly. Fails if that correction is dropped -- the tell is every
    # profile reporting the same class count.
    counts = {}
    for cim_class in catalogue.values():
        for profile in cim_class.profiles:
            counts[profile] = counts.get(profile, 0) + 1
    assert counts["EQ"] == 158
    assert counts["TP"] == 20
    assert len(set(counts.values())) > 5

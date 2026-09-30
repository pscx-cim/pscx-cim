"""Arm coverage: what only the symbolic interpreter can say.

Concrete expansion descends into the arms that hold and reports what it
emitted. It therefore cannot answer "which arms did nothing ever take" --
the arms it never reached are the ones it never mentions. These are the
three findings that needs, and the numbers master's own library gives.
"""

import pytest
from pins import defect

#: master ships 53 conditional arms whose guard chain requires one
#: condition both ways, so no environment can select them: 48 in `unity`,
#: which opens `#IF IType == 0` twice, and 5 in `db_xfmr_3p2w`, whose
#: Matrix-Fill omits an `#ENDIF`.
UNSATISFIABLE_ARMS = defect(
    53, "script_unreachable_arm",
    "conditional arms master states and can never reach: 48 in unity's "
    "doubled `#IF IType == 0`, 5 in db_xfmr_3p2w's Matrix-Fill, which "
    "omits an #ENDIF and buries its MagModel 4/5/6 rows in the 1/2/3 arm",
    permanent="the guard chain requires one condition both ways, so NO "
    "environment selects them -- it is a property of the shipped script "
    "text, and only a different master could move it")


def _definition(name, text, segment="Dsdyn"):
    from pscx.guards import parse_script
    from pscx.model import ComponentDef

    return ComponentDef(namespace="test", name=name, ports=(), defaults={},
                        guard_trees=((segment, parse_script(text)),))


NESTED = """\
#IF A==1
x
#ELSE
#IF B==1
y
#ELSE
z
#ENDIF
#ENDIF
"""


def test_an_unselected_arm_is_told_apart_from_an_unreachable_one():
    # Hand-derived over NESTED with A=1: the outer chain's first arm is
    # taken, its #ELSE is reached-but-not-taken, and the inner chain is
    # never reached at all -- so its two arms are dead for a different
    # reason and asking why leads somewhere different. Fails if the two
    # are lumped together, which would report 3 unexercised arms and send
    # a reader looking for a B that no placement ever gets to state.
    from pscx.coverage import ArmCoverage

    seen = ArmCoverage()
    definition = _definition("nested", NESTED)
    seen.observe(definition, {"a": 1, "b": 1})

    assert [str(s) for s in seen.selected()] == [
        "nested/Dsdyn conditional arm 0 A==1"]
    assert [str(s) for s in seen.unexercised()] == [
        "nested/Dsdyn conditional arm 1 #ELSE"]
    assert sorted(str(s) for s in seen.unreachable()) == [
        "nested/Dsdyn conditional arm 0 B==1",
        "nested/Dsdyn conditional arm 1 #ELSE",
    ]


def test_observing_both_environments_leaves_only_the_arms_neither_takes():
    # The other direction, so the first test cannot pass by reporting
    # everything dead: two placements between them take three of the four
    # arms, and the one left is the inner #ELSE that needs B!=1.
    from pscx.coverage import ArmCoverage

    seen = ArmCoverage()
    definition = _definition("nested", NESTED)
    seen.observe(definition, {"a": 1, "b": 1})
    seen.observe(definition, {"a": 0, "b": 1})

    assert len(seen.selected()) == 3
    assert [str(s) for s in seen.unexercised()] == [
        "nested/Dsdyn conditional arm 1 #ELSE"]
    assert seen.unreachable() == []


def test_an_arm_no_environment_can_take_is_found_with_no_environment():
    # The finding the concrete side structurally cannot produce: it only
    # asks about arms it already chose, so an arm nothing can choose is
    # invisible to it forever. A condition opened twice buries the later
    # arms inside the earlier one -- master's `unity` does exactly this.
    # Fails if the detector needs a placement to fire.
    from pscx.coverage import ArmCoverage

    doubled = "#IF A==1\n#IF A==1\nx\n#ELSE\ny\n#ENDIF\n#ENDIF\n"
    seen = ArmCoverage()
    seen.index(_definition("doubled", doubled))

    found = seen.unsatisfiable()
    assert [(str(site), expr) for site, expr in found] == [
        ("doubled/Dsdyn conditional arm 1 #ELSE", "A==1")]
    assert seen.placed == set()


def test_a_condition_retested_outside_the_first_block_is_satisfiable():
    # Negative control. The rule is about one CHAIN demanding a condition
    # two ways, not about a condition appearing twice in a segment: two
    # sibling blocks testing the same thing are ordinary. Fails if the
    # detector keys on the expression rather than on the path to the arm,
    # which would report most of master as unreachable.
    from pscx.coverage import ArmCoverage

    siblings = "#IF A==1\nx\n#ELSE\nw\n#ENDIF\n#IF A==1\ny\n#ELSE\nz\n#ENDIF\n"
    seen = ArmCoverage()
    seen.index(_definition("siblings", siblings))
    assert seen.unsatisfiable() == []


def test_contradiction_detects_one_selector_required_at_two_values():
    # The same detector applied to the alternatives grammar: requiring
    # `(E)==0` and `(E)==1` at once is what a #CASE nested inside a #CASE
    # arm would produce, which is the compounding of alternatives that the
    # node types make unsayable. This states what it would look like if
    # they allowed it.
    from pscx.guards import contradiction

    assert contradiction(((("(W2E)==0"), True), (("(W2E)==3"), True))) == "W2E"
    assert contradiction(((("(W2E)==0"), True), (("(W2O)==3"), True))) is None
    assert contradiction((("A", True), ("B", False))) is None


# --------------------------------------------------------------------------
# The library itself
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def master_coverage():
    """Every arm of every master definition, indexed with no placement."""
    from conftest import master_available

    if not master_available():
        pytest.skip("PSCAD master.pslx not found")
    from pscx.coverage import ArmCoverage
    from pscx.io import load_master

    seen = ArmCoverage()
    seen.index_all(definition for (space, _name), definition
                   in load_master().items() if space == "master")
    return seen


@pytest.mark.slow
def test_master_states_arms_it_can_never_reach(master_coverage):
    # The shipped-library defect, pinned by definition so
    # a master upgrade that fixes one is visible as a fall rather than as
    # a number that merely moved.
    from collections import Counter

    found = master_coverage.unsatisfiable()
    by_definition = Counter(site.definition for site, _expr in found)
    assert len(found) == UNSATISFIABLE_ARMS
    assert dict(by_definition) == {"unity": 48, "db_xfmr_3p2w": 5}
    # every contradicting condition turns on one of the two defects'
    # parameters: unity's input/output type choices and db_xfmr_3p2w's
    # magnetizing model
    import re

    parameters = {re.match(r"[A-Za-z_]\w*", expr).group(0)
                  for _site, expr in found}
    assert parameters == {"IType", "OType", "MagModel"}

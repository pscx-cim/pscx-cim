"""``memberof`` is written by PSCAD, not declared by any form.

A placed component carries it in its paramlist and no definition
declares it, so the loader has no form to read a meaning off. A value in
it is REPORTED rather than absorbed.
"""

import pytest


def test_a_stated_memberof_is_reported_rather_than_absorbed():
    # The reading given to a non-empty memberof is exercised on
    # placements built by hand.
    from pscx import nets
    from pscx.diagnostics import Diagnostics
    from pscx.hir import HirComponent, HirParams
    from pscx.model import ComponentDef

    definition = ComponentDef(namespace="lib", name="widget", ports=(),
                              defaults={})
    registry = {("lib", "widget"): definition}

    def placement(value):
        return HirComponent(
            classid="UserCmp", id="1", defn="lib:widget", x="0", y="0",
            orient="0", params=[HirParams(None, {"memberof": value})])

    bus = Diagnostics()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(nets, "DIAGNOSTICS", bus)
        placed = nets._place_components(
            [placement(""), placement("   "), placement("Group A")],
            registry, lambda value, span=None: value)

    # all three are placed -- the entry is never a reason to drop a
    # component -- and only the one that says something is reported
    assert len(placed) == 3
    assert bus.by_code() == {"memberof_nonempty": 1}
    assert bus.records()[0].detail.startswith("lib:widget=")

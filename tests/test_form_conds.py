"""A form ``<cond>`` says what it gates, and all three sayings gate alike.

The liveness machinery evaluates a parameter's own ``<cond>`` and its
category's against the placement's environment, and acts on neither
``type``. The HIR models the type and the lowering drops it, so the gate
has no branch on which the three kinds of saying can diverge.
"""


def test_the_cond_type_is_modeled_in_the_hir_and_dropped_by_the_lowering():
    # Honoured identically, asserted structurally rather than by sampling:
    # the type is modeled in the HIR, where a future reader can act on
    # it, and dropped by the lowering into FormParameterDef, which is what
    # the liveness gate reads. So there is no code path on which an
    # Enable, a Visible and an untyped cond can diverge. Fails if the type
    # stops being modeled (a reader loses the distinction) or starts
    # being lowered (the gate gains a branch nothing decided to add).
    from dataclasses import fields

    from pscx.hir import HirFormCategory, HirFormParameter
    from pscx.model import FormParameterDef

    assert "condition_type" in {f.name for f in fields(HirFormParameter)}
    assert "condition_type" in {f.name for f in fields(HirFormCategory)}
    lowered = {f.name for f in fields(FormParameterDef)}
    assert {"condition", "category_condition"} <= lowered
    assert "condition_type" not in lowered
    assert "category_condition_type" not in lowered

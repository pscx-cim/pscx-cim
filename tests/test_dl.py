"""The orient projection the diagram layout applies.

:func:`pscx.dl.rotation_degrees` is the projection ``orient -> angle``.
It is not injective, and the mirror it drops is stated here as its own
assertion.
"""


def test_rotation_degrees_maps_each_mirrored_orient_to_its_unmirrored_angle():
    # The residual, stated in both directions on a synthetic orient.
    # `rotation_degrees` maps eight orients onto four angles: it is
    # exactly the projection that loses the mirror, and a drawing oracle
    # that applies it on BOTH sides cannot see that loss, so this is the
    # assertion that states it.
    from pscx.dl import rotation_degrees

    turns = {orient: rotation_degrees(str(orient)) for orient in range(8)}
    assert turns == {0: 0.0, 1: 90.0, 2: 180.0, 3: 270.0,
                     4: 0.0, 5: 90.0, 6: 180.0, 7: 270.0}
    # ...and that is a collision, not a coincidence: four pairs of orients
    # share an angle, so no reader of the documents can tell them apart.
    assert len(set(turns.values())) == 4
    assert rotation_degrees(None) is None
    assert rotation_degrees("east") is None

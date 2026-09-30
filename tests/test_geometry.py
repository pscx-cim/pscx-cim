"""The orientation transform, held against an independent construction.

`rotate_port` is a table lookup, so asserting it against a table copied out
of itself would prove nothing. The mirror and the rotation are built here
from their own definitions and composed, which is what makes the ORDER of
the composition the thing under test.
"""

from pscx.geometry import rotate_port

#: One offset per quadrant plus the two axes and the origin. The origin and
#: the axis points are fixed or near-fixed by parts of the group and cannot
#: distinguish its elements on their own; the asymmetric offsets can.
OFFSETS = [(0, 0), (3, 0), (0, 3), (3, 5), (-3, 5), (3, -5), (-3, -5), (7, 2)]


def _mirror_x(point):
    x, y = point
    return (-x, y)


def _rotate_ccw(point, quarters):
    for _ in range(quarters % 4):
        x, y = point
        point = (-y, x)
    return point


def test_the_orientation_transform_is_a_mirror_then_a_rotation():
    # orient 0-7 is an element of the dihedral group of order 8: the four
    # rotations, and the same four after a mirror in X. Fails if the table
    # stops being that group -- a transposed row is a component whose ports
    # land somewhere its wires do not.
    for orient in range(8):
        for offset in OFFSETS:
            point = _mirror_x(offset) if orient >= 4 else offset
            assert rotate_port(*offset, orient) == _rotate_ccw(point, orient), (
                f"orient {orient} on {offset}"
            )


def test_negative_control_rotating_before_mirroring_differs():
    # The whole content of the rule is that the mirror comes FIRST, so the
    # test above is only worth anything if the other order is a different
    # transform. It agrees for six of the eight values and disagrees for
    # orient 5 and 7 -- exactly the shape of a bug that passes every case
    # that never draws one.
    disagreeing = set()
    for orient in range(8):
        for offset in OFFSETS:
            rotated = _rotate_ccw(offset, orient)
            other = _mirror_x(rotated) if orient >= 4 else rotated
            if rotate_port(*offset, orient) != other:
                disagreeing.add(orient)
    assert disagreeing == {5, 7}

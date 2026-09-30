"""Port rotation, point-on-segment, disjoint sets."""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

Point = tuple[int, int]


def rotate_port(px: int, py: int, orient: int) -> Point:
    """Map a port's library-local offset to canvas-local, honouring ``orient``.

    Derived empirically by maximising exact
    port-to-wire-vertex coincidence, testing all 8 elements of the dihedral
    group independently for each orient value. All eight agree with::

        rotate_ccw(orient % 4) applied AFTER mirroring in X when orient >= 4

    Every orient value matched the algebraic prediction by a clear margin.
    The mirror comes FIRST: rotating and then mirroring is wrong for
    orient 5 and 7.
    """
    if orient >= 4:
        px = -px
    return [(px, py), (-py, px), (-px, -py), (py, -px)][orient % 4]


def point_on_segment(point: Point, start: Point, end: Point) -> bool:
    """Exact integer collinearity test; PSCAD coordinates are always integral."""
    (x, y), (x1, y1), (x2, y2) = point, start, end
    if (x2 - x1) * (y - y1) != (y2 - y1) * (x - x1):
        return False
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


class DisjointSet:
    """Union-find over arbitrary hashable keys (coordinates or sentinels)."""

    def __init__(self) -> None:
        self._parent: dict[Any, Any] = {}

    def find(self, item: Any) -> Any:
        self._parent.setdefault(item, item)
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: Any, b: Any) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

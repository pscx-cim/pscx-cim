"""A radiolink receiver pairs with the transmitter its Source names.

The Source SELECTS the transmitter rather than being checked afterwards
on whichever same-named one a lookup returned first. A pairing made by
name alone is a pairing already made on the wrong end, so the check is a
negative control that places two same-named transmitters on different
canvases.
"""

import pytest
from conftest import master_available

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)


def test_negative_control_two_transmitters_share_a_name(monkeypatch):
    # Two real transmitters called "SIG" on two different canvases, and
    # an RX whose Source names the second. Pairing by name alone takes
    # the first and counts a source mismatch AFTER the fact -- which is a
    # pairing already made, on the wrong end.
    from collections import Counter

    from pscx.elaborate import pair_radio_ends

    class _Inst:
        def __init__(self, canvas):
            self.canvas = canvas

    class _End:
        def __init__(self, name, canvas, mode, source=""):
            self.name, self.mode, self.source = name, mode, source
            self.instance = _Inst(canvas)

    first = _End("SIG", "PageA", "1")
    second = _End("SIG", "PageB", "1")
    rx = _End("SIG", "Main", "0", source="PageB")

    stats = Counter()
    pairs = pair_radio_ends([rx], [first, second], stats)
    assert len(pairs) == 1
    assert pairs[0][0] is second, "the source names PageB, not PageA"
    assert stats["radio_source_mismatch"] == 0

    # ...and an RX naming a canvas that hosts no such transmitter does not
    # pair at all, where pairing by name alone would pair it and note a
    # mismatch
    stats = Counter()
    stray = _End("SIG", "Main", "0", source="PageZ")
    assert pair_radio_ends([stray], [first, second], stats) == []
    assert stats["radio_source_mismatch"] == 1

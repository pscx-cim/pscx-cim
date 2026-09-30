"""The two premises a geometry GENERATOR would rest on.

Nothing generates geometry today. But the reverse direction -- a CIM case
drawn back onto a PSCAD canvas -- needs to know two things before any
router is worth writing, and both are properties of what PSCAD ships
rather than of what this repo does. They are asserted here because a
premise has to be checkable, not because anything reads them yet.

  1. Crossing wires do NOT connect, so a naive ladder layout is CORRECT.
  2. There is no within-canvas electrical name bridge, so electrical
     connectivity has to be DRAWN. Geometry is mandatory, not optional.
"""

import pytest
from conftest import master_available

from pscx.model import Component, Port
from pscx.nets import _build_nodes


def _port(name: str, x: int, y: int, mode: str) -> Port:
    return Port(name=name, x=x, y=y, mode=mode, electype="0", dim="1",
                internal=False, local=(0, 0))


def _placed(name: str, port: Port) -> Component:
    return Component(defn=f"master:{name}", x=0, y=0, orient=0,
                     ports=[port], params={})


#: Two wires that CROSS at (10, 0) and share no vertex. Neither one's
#: endpoints lie on the other, which is the whole point: the
#: T-junction rule fires on an endpoint landing mid-segment, and a
#: crossing has no endpoint to land.
CROSSING = [((0, 0), (20, 0)), ((10, -10), (10, 10))]


def test_crossing_wires_do_not_connect():
    # Seen from the layout side. Hand-derived: two nodes, one per wire,
    # because step 3 of _build_nodes iterates ENDPOINTS and asks whether
    # each lies on some other segment -- and (10, 0) is an endpoint of
    # neither wire.
    #
    # Fails if the node builder ever unions on intersection rather than on
    # incidence, which would silently short every case that draws a bus
    # bar across a column of feeders.
    # Three components, not two: the far end of the horizontal wire is
    # there so "2 nodes" cannot pass by the ports failing to attach at
    # all. Its port must land on the SAME node as the near end.
    left = _placed("resistor", _port("A", 0, 0, "3"))
    right = _placed("resistor", _port("A", 20, 0, "3"))
    down = _placed("resistor", _port("A", 10, -10, "3"))
    nodes, conflicts, _dsu = _build_nodes([left, right, down], CROSSING, [])
    assert sorted(len(node.ports) for node in nodes) == [1, 2]
    assert conflicts == 0


def test_a_mode_four_pin_at_the_crossing_connects_them():
    # The control for the test above, and the mechanism a generated layout
    # would have to emit if it ever DID want two crossing wires joined.
    # Same two wires, same two components, one `pin` added at (10, 0).
    #
    # Fails if the mode-4 pass-through rule stops firing, which would make
    # the test above pass for the wrong reason -- "nothing connects" rather
    # than "connection is by incidence".
    left = _placed("resistor", _port("A", 0, 0, "3"))
    down = _placed("resistor", _port("A", 10, -10, "3"))
    pin = _placed("pin", _port("N", 10, 0, "4"))
    nodes, _conflicts, _dsu = _build_nodes([left, down, pin], CROSSING, [])
    assert len(nodes) == 1
    assert len(nodes[0].ports) == 3


@pytest.mark.skipif(not master_available(), reason="needs master.pslx")
def test_no_master_name_bridge_carries_an_electrical_port():
    # The second premise, checked against the shipped library rather than
    # against this repo's reading of it: a name can bridge a SIGNAL net
    # and cannot bridge an electrical one, because the components that
    # carry a name declare mode 1 or mode 2 ports and nothing else.
    #
    # `xnode` is the single exception and it is the boundary bridge, not a
    # within-canvas one: flatten() joins a placed boundary port
    # on the PARENT canvas to the same-named xnode on the CHILD canvas,
    # and nothing anywhere joins two xnodes to each other.
    #
    # Fails if a future master ships a within-canvas electrical name
    # bridge, at which point a layout generator may stop drawing wires --
    # and until then it may not.
    from pscx.io import load_master

    registry = load_master()
    modes = {}
    for kind in ("datalabel", "import", "export", "radiolink", "xnode", "pin"):
        definition = registry[("master", kind)]
        modes[kind] = sorted({port.mode for port in definition.ports})
    assert modes == {
        "datalabel": ["1"], "import": ["2"], "export": ["1"],
        "radiolink": ["1", "2"],
        "xnode": ["3"], "pin": ["4"],
    }


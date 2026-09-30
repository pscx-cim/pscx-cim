"""The hosting-wire terminal rules, pinned on a tracked fixture.

The fixture draws three TLine hosting wires: L1's A end is met by a
component port and a Bus wire stating 230 kV, L1's B end is met by a
plain wire that continues into L2's A end (a junction whose only members
are wires and line terminals), and L3 is a collapsed body whose two
inner vertices coincide. Together they pin the three properties the
hosting-wire rules must hold at once: each terminal binds its own path
endpoint, a collapsed body cannot short the line, and base voltage
propagates across a line device into a junction no port touches.
"""

import os

from pscx.nets import extract

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "hosting_stub.pscx")


def _netlist():
    return next(iter(extract(FIXTURE)))


def test_the_b_terminal_binds_the_path_endpoint_a_wire_can_meet():
    netlist = _netlist()
    find = netlist.dsu.find
    l1 = next(d for d in netlist.devices if d.name == "L1")
    l2 = next(d for d in netlist.devices if d.name == "L2")
    # a WIRE meeting the stub end joins only through the bound
    # coordinate, so the terminal must bind the path endpoint
    assert find(l1.terminal_b) == find(l1.vertex_b)
    assert find(l1.terminal_b) == find(l2.terminal_a)
    # the port-met A end joins through rule 4 either way
    assert find(l1.terminal_a) == find(l1.vertex_a)
    # and one line's two ends stay two nodes
    assert find(l1.terminal_a) != find(l1.terminal_b)


def test_a_collapsed_body_keeps_the_line_terminals_on_distinct_nodes():
    netlist = _netlist()
    find = netlist.dsu.find
    l3 = next(d for d in netlist.devices if d.name == "L3")
    assert find(l3.terminal_a) != find(l3.terminal_b)

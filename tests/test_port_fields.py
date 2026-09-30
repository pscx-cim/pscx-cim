"""The internal-port flag, which keeps a port out of both graphs.

A port master declares with internal="true" is a terminal of a component's
own internal circuit. It joins neither the electrical nor the signal graph,
and the exclusion is made in one place that every graph builder inherits.
"""

from collections import Counter

import pytest
from conftest import master_available
from pins import coverage

from pscx.common import ELECTRICAL_MODES, SIGNAL_MODES

#: Ports master declares with internal="true", and the definitions that
#: declare them. Two pins and not one: the population says the shape is
#: exercised at all, and the definition count says it is concentrated in
#: the devices whose internal per-phase structure the library draws
#: inside the symbol rather than spread thinly over the registry.
MASTER_INTERNAL_PORTS = coverage(
    103, "ports master.pslx declares internal, out of 2,734 declared "
    "ports in all -- the terminals of a component's OWN internal "
    "circuit, which a Branch row inside the definition attaches to and "
    "no wire on any canvas reaches")
MASTER_INTERNAL_PORT_DEFINITIONS = coverage(
    27, "definitions declaring at least one of them -- the loads, "
    "sources, UMEC transformers, machines, MMC cells and filters whose "
    "internal circuit the library draws inside the symbol")
#: A third, because the flag spans both graphs and the totals above do
#: not say so: the internal ports that declare an ELECTRICAL mode. The
#: remainder declare a signal mode, one of each direction, which is what
#: makes the exclusion a rule about the flag rather than a habit of the
#: electrical side.
MASTER_INTERNAL_ELECTRICAL_PORTS = coverage(
    101, "of the internal ports, the ones whose mode is electrical -- "
    "every internal port but master:unity's A1 and B1, which are a "
    "signal input and a signal output")


@pytest.mark.skipif(not master_available(), reason="needs master.pslx")
def test_an_internal_port_joins_neither_graph():
    # The exclusion is made in ONE place -- Port.is_electrical and
    # Port.is_signal both end in `and not self.internal` -- and every
    # graph builder downstream inherits it instead of re-deciding. So
    # this asserts the population and that single point together: drop
    # the clause from either property and an internal port starts
    # offering itself to the net builder, silently shorting a device's
    # internals onto the network.
    from pscx.io import load_master
    from pscx.model import Port

    registry = load_master()
    internal = [port for definition in registry.values()
                for port in definition.ports if port.internal]
    owners = {key for key, definition in registry.items()
              if any(port.internal for port in definition.ports)}
    assert len(internal) == MASTER_INTERNAL_PORTS
    assert len(owners) == MASTER_INTERNAL_PORT_DEFINITIONS
    # the denominator the pin's own text states, asserted here so the
    # population reads as a fraction of something rather than as a
    # number beside a remembered one
    assert sum(len(definition.ports)
               for definition in registry.values()) == 2734
    # placed the way nets.py places one, because the exclusion lives on
    # the PLACED port and a PortDef carries the flag without reading it
    placed = [Port(name=p.name, x=0, y=0, mode=p.mode, electype=p.electype,
                   dim=p.dim, internal=p.internal, local=(p.x, p.y),
                   dim_name=p.dim_name, datatype=p.datatype)
              for p in internal]
    assert not any(port.is_electrical or port.is_signal
                   or port.is_signal_source for port in placed)
    # ...and not vacuously. The flag is what excludes them and nothing
    # else does: nearly all declare an electrical mode, and the rest
    # declare a SIGNAL mode, one of each direction, so the rule spans
    # both graphs rather than being an electrical-side habit.
    modes = Counter(port.mode for port in placed)
    assert sum(n for mode, n in modes.items()
               if mode in ELECTRICAL_MODES) == MASTER_INTERNAL_ELECTRICAL_PORTS
    assert sorted(mode for mode in modes if mode in SIGNAL_MODES) == ["1", "2"]
    signal = {(key[1], port.name)
              for key, definition in registry.items()
              for port in definition.ports
              if port.internal and port.mode in SIGNAL_MODES}
    assert signal == {("unity", "A1"), ("unity", "B1")}

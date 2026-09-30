"""The two spellings a definition can state its ports in.

A 4.5-era definition declares its boundary ports inside `<svg>`, which the
HIR banks opaque; a v5 definition declares them in `<graphics><Port>`, which
the HIR reads. Most master.pslx definitions state BOTH.

The translation is measured rather than assumed, from the pairs the
dual-stated definitions provide: a definition stating one boundary twice
states the same port in both vocabularies, so the svg spelling's words are
defined by the v5 numbers beside them.
"""

from collections import Counter

import pytest
from conftest import MASTER_PSLX, master_available
from lxml import etree
from pins import coverage

#: Pairs of the same named port stated in both vocabularies, in the master
#: definitions where the pairing is unambiguous -- both lists naming each
#: port once. The other dual definitions repeat a name on one side or the
#: other and no name-pairing over them means anything.
MASTER_TRANSLATION_PAIRS = coverage(
    893, "unambiguously paired ports, from which the svg vocabulary's "
    "words are read off the v5 numbers beside them")

#: The svg `mode` word for each numeric mode. The EMPTY string is the case
#: that has to be measured rather than guessed: it is not a missing value
#: but electrical, which is also the only mode whose word can be omitted.
SVG_MODE_WORDS = {"Input": "1", "Output": "2", "Electrical": "3",
                  "Short": "4", "": "3"}

#: svg `type` is DUAL-USE and the port's mode says which use: on an
#: electrical port it carries the Component Wizard's Electrical Type, on a
#: signal port the data type. One attribute, two domains, and nothing in
#: the spelling itself distinguishes them.
SVG_ELECTYPE_WORDS = {"NonRemovable": "0", "Removable": "1",
                      "Switched": "2", "Ground": "3"}
SVG_DATATYPE_WORDS = {"Logical": "1", "Integer": "2", "Real": "3",
                      "Complex": "4"}

#: Where the two spellings DISAGREE, and the whole set of it: master's
#: `conjugate` states its two `Dim` ports as Integer and Real in svg while
#: `<graphics>` types both Complex, which is what a complex-conjugate block
#: passes. The v5 spelling is the live one and the svg text beside it is
#: stale, so the graphics-wins rule is not only about the doubling hazard --
#: it is also the rule that reads the CURRENT type. Named rather than
#: tolerated: a third disagreeing port is a finding, not a tolerance.
DISAGREEING_DATATYPE_PORTS = {"IN:Dim", "OUT:Dim"}


def _params(port):
    return {p.get("name"): p.get("value")
            for p in port.findall("paramlist/param")}


def _paired_ports(path):
    """The unambiguously paired (svg, graphics) ports of one file."""
    pairs = []
    for definition in etree.parse(path).iter("Definition"):
        svg = definition.findall("svg/port")
        graphics = definition.findall("graphics/Port")
        if not (svg and graphics):
            continue
        svg_names = [p.get("name") for p in svg]
        graphics_names = [_params(p).get("name") for p in graphics]
        if (len(set(svg_names)) == len(svg_names)
                and len(set(graphics_names)) == len(graphics_names)):
            by_name = dict(zip(graphics_names, graphics))
            pairs += [(p, by_name[p.get("name")]) for p in svg
                      if p.get("name") in by_name]
    return pairs


@pytest.mark.slow
@pytest.mark.skipif(not master_available(), reason="needs master.pslx")
def test_the_svg_vocabulary_translates_to_the_v5_numbers():
    # The translation table, read off the registry rather than declared:
    # where one definition states a port twice, the words and the numbers
    # describe the same port. Mode is a total function on this population
    # -- every pair agrees -- and the empty word is electrical rather than
    # absent. `type` is asserted only in the use the port's own mode
    # selects; a signal port's `type` says nothing about its electype and
    # comparing them would assert a coincidence.
    pairs = _paired_ports(MASTER_PSLX)
    assert len(pairs) == MASTER_TRANSLATION_PAIRS

    modes = Counter((svg.get("mode"), _params(gfx).get("mode"))
                    for svg, gfx in pairs)
    assert {word for word, _number in modes} == set(SVG_MODE_WORDS)
    assert all(SVG_MODE_WORDS[word] == number for word, number in modes)
    assert modes[("", "3")] > 0

    electrical = [(svg, gfx) for svg, gfx in pairs
                  if _params(gfx).get("mode") in ("3", "4")]
    assert {svg.get("type") for svg, _gfx in electrical} \
        == set(SVG_ELECTYPE_WORDS)
    assert all(SVG_ELECTYPE_WORDS[svg.get("type")]
               == _params(gfx).get("electype") for svg, gfx in electrical)

    signal = [(svg, gfx) for svg, gfx in pairs
              if _params(gfx).get("mode") in ("1", "2")]
    assert {svg.get("type") for svg, _gfx in signal} == set(SVG_DATATYPE_WORDS)
    disagreeing = {svg.get("name") for svg, gfx in signal
                   if SVG_DATATYPE_WORDS[svg.get("type")]
                   != _params(gfx).get("datatype")}
    assert disagreeing == DISAGREEING_DATATYPE_PORTS

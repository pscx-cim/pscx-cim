"""The global-substitution fail-open: what ``$(x)`` becomes when nothing
defines ``x``.

``nets.extract`` resolves ``$(Name)`` references in every placed
component's parameter values against the case's own ``<Sub>`` list. A
reference the list does not answer falls back to the string ``"0"`` --
a fabricated number in a field that states a real one, indistinguishable
downstream from a value the drawing typed.

The count splits two ways, and the split is derived rather than assumed:

  INTRINSIC  a name PSCAD substitutes itself at build time. The witness
             is master.pslx -- it declares no ``<Sub>`` at all and still
             places components whose parameters reference ``$(Name)`` and
             ``$(Rank)``, which a shipped library could not do if those
             came from a user's globals.
  MISSING    any other name the case does not define in a ``<Sub>``, so
             the case is asking for a global it never wrote down.

The two claims want different answers -- the first is a substitution
mechanism nothing here implements, the second is a hole in the drawing --
so they are counted apart.
"""

import os

import pytest
from conftest import master_available

pytestmark = pytest.mark.skipif(
    not master_available(),
    reason="PSCAD master.pslx not found")

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "substitutions.pscx")


def _extract(path, monkeypatch):
    """Extract one case onto a private bus, returning (components, bus)."""
    from pscx import nets
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    monkeypatch.setattr(nets, "DIAGNOSTICS", bus)
    components = [c for netlist in nets.extract(path)
                  for c in netlist.components]
    return components, bus


def _placed(components, element_id):
    found, = [c for c in components if c.element_id == element_id]
    return found


# --------------------------------------------------------------------------
# The fixture, and its negative control
# --------------------------------------------------------------------------

def test_a_resolved_reference_takes_the_global_and_reports_nothing(monkeypatch):
    # The negative control the whole count rests on. The fixture's
    # <Sub> says freq = 60.0 and the resistor's R is "$(freq) [ohm]", so
    # this fails if a resolved reference is counted anyway -- which would
    # make the counts a measurement of how many $() there are rather than
    # of how many fail.
    components, bus = _extract(FIXTURE, monkeypatch)
    resolved = _placed(components, "5")

    assert resolved.params["R"] == "60.0 [ohm]"
    assert [r for r in bus.records()
            if r.code.startswith("substitution_")
            and r.detail == "freq"] == []


def test_an_unresolved_global_is_counted_and_names_itself(monkeypatch):
    # The fail-open itself. The fixture defines freq and NOT rload, so
    # "$(rload) [ohm]" becomes "0 [ohm]" -- a resistance nobody typed.
    # This fails if that substitution stays silent, or if the detail does
    # not name which global went missing (a count with no name cannot be
    # split, which is the whole finding).
    components, bus = _extract(FIXTURE, monkeypatch)
    missing = _placed(components, "6")

    assert missing.params["R"] == "0 [ohm]"
    assert bus.count("substitution_unresolved: rload") == 1


def test_an_intrinsic_reference_is_counted_apart_from_a_missing_global(
        monkeypatch):
    # Two claims, two codes. $(Name) and $(Rank) are supplied by PSCAD and
    # by no <Sub> anywhere, so counting them beside $(rload) would say 3
    # globals are missing from a fixture that is missing 1. Fails if
    # either name lands under the other code.
    components, bus = _extract(FIXTURE, monkeypatch)
    link = _placed(components, "7")

    assert link.params["caption"] == "0 [0]"
    assert bus.count("substitution_intrinsic: Name") == 1
    assert bus.count("substitution_intrinsic: Rank") == 1
    assert bus.count("substitution_unresolved: Name") == 0
    assert bus.count("substitution_unresolved: Rank") == 0


def test_an_unresolved_substitution_reports_the_line_of_its_param(
        monkeypatch):
    # A span, not provenance: the substituter is reading one <param>
    # element of one file, so the honest answer is that file and that
    # line. The fixture's rload param is on line 29, counted
    # from the XML declaration on line 1. Fails if the span is invented
    # from the case root or omitted.
    _components, bus = _extract(FIXTURE, monkeypatch)
    record, = [r for r in bus.records() if r.detail == "rload"]

    assert (record.span.file, record.span.line) == ("substitutions.pscx", 29)


# --------------------------------------------------------------------------
# Which test decides the split
# --------------------------------------------------------------------------

def test_master_declares_no_sub_and_references_only_intrinsic_globals():
    # The witness that classifies a name. master.pslx is a shipped
    # library, it declares ZERO <Sub>, and it still places <User>
    # components whose parameter values read
    # "$(Name) [$(Rank)]". A library cannot depend on a user project's
    # globals, so PSCAD supplies those two itself.
    #
    # Both directions. Fails if master ever declares a <Sub> (the
    # inference would be unsound), and equally if master references a
    # third name the intrinsic set does not carry -- a name we would then
    # be counting as a hole in someone's drawing.
    import re

    from lxml import etree as ET

    from pscx.common import MASTER_PSLX
    from pscx.nets import PSCAD_INTRINSIC_SUBSTITUTIONS

    root = ET.parse(MASTER_PSLX).getroot()
    referenced = set()
    for user in root.iter("User"):
        for param in user.iter("param"):
            referenced.update(re.findall(r"\$\((\w+)\)",
                                         param.get("value") or ""))

    assert list(root.iter("Sub")) == []
    assert referenced == set(PSCAD_INTRINSIC_SUBSTITUTIONS)

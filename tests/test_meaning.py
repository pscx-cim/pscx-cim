"""What the writer changes about the CASE, not about the document.

``test_roundtrip.py`` grades ``write_project`` on the document: what the
written XML states against what the source XML stated. That is the right
instrument for "how much of the file does the HIR hold", and it is the
wrong one for "does the file still mean what it meant", because a
document that differs can still mean the same thing.

This module grades the other thing. It compares ``flatten(original)``
against ``flatten(written)`` -- the case as the extraction front end
elaborates it -- and the comparison is the ELABORATED PARAMETER
ENVIRONMENT: every instance's ``env`` and every placed component's
resolved ``params``, after ``$(NAME)`` substitution has run.

The two comparisons answer different questions and neither subsumes the
other. A document that comes back byte-identical means the same thing
trivially; a document that comes back different may still mean the same
thing, and the only way to know is to elaborate both and look.
"""

import os
import shutil

import pytest
from conftest import master_available
from lxml import etree as ET

pytestmark = pytest.mark.skipif(
    not master_available(),
    reason="PSCAD master.pslx not found")

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "substitutions.pscx")


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------

def round_trip_into(case_path: str, dest_dir: str) -> str:
    """``write_project(load_project(case))`` serialised, with its libraries.

    Two properties of the destination are load-bearing, and getting either
    wrong invalidates the comparison rather than failing it:

    ``nets.extract`` resolves a foreign namespace by globbing ``*.pslx`` in
    the CASE'S OWN directory, so the written file's siblings are copied in
    beside it. Without them a case that places a library definition
    flattens with unresolved ports and reports a difference that is an
    artefact of where the file was put.

    ``nets.extract`` also names the case's namespace ``root.get("name") or
    the file's basename``, so the basename is preserved. A harness that
    relied on the root ``name`` would break on the first case that does
    not state one.
    """
    from pscx.hir import load_project
    from pscx.write import write_project

    written = write_project(load_project(case_path))
    out = os.path.join(dest_dir, os.path.basename(case_path))
    with open(out, "wb") as handle:
        handle.write(ET.tostring(written, xml_declaration=True,
                                 encoding="UTF-8"))
    source_dir = os.path.dirname(case_path)
    for sibling in os.listdir(source_dir):
        if sibling.endswith(".pslx"):
            shutil.copy(os.path.join(source_dir, sibling),
                        os.path.join(dest_dir, sibling))
    return out


def environment(flat) -> dict:
    """The resolved parameter environment, keyed by stable identity.

    ``Instance.path`` is NOT the key, and that is the subtle one: a path
    segment is the instancing component's ``Name`` parameter, which is
    itself substituted. A lost global renames the instance, so keying on
    the path would compare a node against nothing and report the loss as
    an absence rather than as the changed value it is. ``Instance.index``
    is the DFS order of a tree whose shape substitution cannot change, and
    ``Component.element_id`` is the file's own id, which the writer
    preserves.
    """
    resolved = {}
    for inst in flat.instances:
        resolved["instance", inst.index] = tuple(sorted(inst.env.items()))
        for comp in inst.netlist.components:
            resolved["component", inst.index, comp.element_id] = tuple(
                sorted(comp.params.items()))
    return resolved


# --------------------------------------------------------------------------
# The negative controls
#
# The gate's own claim is that a difference it reports is a difference in
# the case. That claim needs both directions: something the round trip
# must NOT change, and something it must.
# --------------------------------------------------------------------------

def _rooted(tmp_path, filename="substitutions.pscx", keep_globals=True):
    """The substitution fixture, given a station so ``flatten`` reaches it.

    The fixture is shaped for ``nets.extract``, which walks every
    definition that has a schematic. ``flatten`` builds an INSTANCE TREE
    instead, and a tree needs a root: the project's roots are the modules
    a ``StationCanvas`` WireBranch hosts, and the fixture has no station,
    so it flattens to zero instances. A minimal station is spliced in here
    rather than added to the fixture itself, because the fixture is the
    substitution tests' and this module has no business changing what they
    read.

    ``keep_globals=False`` deletes the ``<GlobalSubstitutions>`` block,
    which gives a case with no globals at all: ``$(freq)`` is then
    unresolvable BEFORE the round trip as well as after, so the writer has
    nothing left to lose and the environment must survive. That is the
    control for every other reason two elaborations could differ.
    """
    root = ET.parse(FIXTURE).getroot()
    if not keep_globals:
        for block in list(root.iter("GlobalSubstitutions")):
            block.getparent().remove(block)
    station = ET.SubElement(root.find("definitions"), "Definition",
                            classid="StationDefn", name="Station", id="3")
    schematic = ET.SubElement(station, "schematic", classid="StationCanvas")
    wire = ET.SubElement(schematic, "Wire", classid="WireBranch", name="STUB",
                         x="180", y="180", orient="0", id="99",
                         defn="(null):STUB", disable="false")
    for x, y in ((0, 0), (0, 18), (54, 54), (54, 72)):
        ET.SubElement(wire, "vertex", x=str(x), y=str(y))
    ET.SubElement(wire, "User", classid="UserCmp", name="Main", id="98",
                  x="0", y="0", orient="0", disable="false",
                  defn=f"{root.get('name')}:Main")
    path = tmp_path / filename
    path.write_bytes(ET.tostring(root))
    return str(path)


def test_a_case_without_globals_round_trips_with_an_unchanged_environment(tmp_path):
    # The green control. Strip the globals and the case has no
    # substitution for the writer to lose. If its environment still
    # diverged, the gate would be reporting the round trip itself
    # (serialisation, reparse, the attributes the writer drops) rather
    # than substitution.
    from pscx.elaborate import flatten

    case = _rooted(tmp_path, keep_globals=False)
    scratch = tmp_path / "written"
    scratch.mkdir()

    before = environment(flatten(case))
    after = environment(flatten(round_trip_into(case, str(scratch))))

    assert before == after
    assert len(before) > 1, "a comparison over an empty environment passes"


def test_perturbing_one_written_parameter_is_reported_as_a_divergence(tmp_path):
    # The red control. Same case as the green one above -- so the only
    # thing that changed is the perturbation -- with a single parameter
    # value edited in the written file. One `<param>` out of a file whose
    # environment holds hundreds of entries, and the comparison has to
    # find it.
    #
    # Without this the gate could be green because nothing exercised it,
    # and a gate that passes over zero real differences is not a gate.
    from pscx.elaborate import flatten

    case = _rooted(tmp_path, keep_globals=False)
    scratch = tmp_path / "written"
    scratch.mkdir()
    written = round_trip_into(case, str(scratch))

    before = environment(flatten(case))
    root = ET.parse(written).getroot()
    target, = [p for p in root.iter("param")
               if p.get("name") == "R" and "freq" in (p.get("value") or "")]
    target.set("value", "999.0 [ohm]")
    with open(written, "wb") as handle:
        handle.write(ET.tostring(root))
    after = environment(flatten(written))

    assert before != after
    differing = [key for key in before if before.get(key) != after.get(key)]
    assert len(differing) == 1, differing


def test_the_round_trip_returns_the_value_the_case_stated(tmp_path):
    # The fixture declares freq = 60.0 and its resistor states
    # R = "$(freq) [ohm]", so the case answers its own reference. The round
    # trip returns that answer, and not the "0 [ohm]" an unresolved
    # reference falls back to.
    #
    # Named at the value, not at the count: a test that only asserted "the
    # environment matches" would pass over an empty environment, and one
    # that asserted the environments are equal would pass if the writer
    # corrupted both sides the same way.
    from pscx.elaborate import flatten

    case = _rooted(tmp_path)
    scratch = tmp_path / "written"
    scratch.mkdir()

    before = environment(flatten(case))
    after = environment(flatten(round_trip_into(case, str(scratch))))
    key = ("component", 0, "5")

    assert dict(before[key])["R"] == "60.0 [ohm]"
    assert dict(after[key])["R"] == "60.0 [ohm]"


def test_the_written_case_states_its_substitution_at_the_depth_it_read_it(
        tmp_path):
    # The check the environment comparison cannot make. `nets` resolves
    # $(NAME) from root.iter("Sub"), which is RECURSIVE, so a <Sub>
    # emitted directly under the container -- or under the wrong <List>,
    # or at the root -- resolves exactly as well while the document says
    # something the file did not. Every value test in this module would
    # stay green through that.
    #
    # Fails if the writer flattens the nesting in a way only the document
    # can see.
    from pscx.hir import load_project
    from pscx.write import write_project

    written = write_project(load_project(FIXTURE))
    tree = ET.ElementTree(written)
    paths = [tree.getpath(sub) for sub in written.iter("Sub")]

    assert paths == ["/project/GlobalSubstitutions/List/Sub"]
    sub, = written.iter("Sub")
    assert (sub.get("id"), sub.get("classid")) == ("20", "Sub")
    assert sub.getparent().get("classid") == "ListNode"
    assert {p.get("name"): p.get("value")
            for p in sub.findall("paramlist/param")} == {"name": "freq",
                                                         "value": "60.0"}


def test_a_reference_the_case_never_defines_is_not_counted_as_a_loss(
        tmp_path):
    # Specificity, and the line between this finding and the rule for
    # unresolved references. The fixture's OTHER two components reference names nothing defines --
    # $(rload), and the intrinsic $(Name)/$(Rank) -- so they already read
    # "0" before the round trip and still read "0" after.
    #
    # An unresolved reference becomes "0", and that is the right answer
    # there. This module guards the level above it: a
    # reference the case does answer, whose answer the writer must keep.
    # If those two were counted together the comparison would count $()
    # references rather than losses, and the unresolved-reference rule
    # would look reopened when it is not.
    from pscx.elaborate import flatten

    case = _rooted(tmp_path)
    scratch = tmp_path / "written"
    scratch.mkdir()

    before = environment(flatten(case))
    after = environment(flatten(round_trip_into(case, str(scratch))))

    for element_id, param, value in (("6", "R", "0 [ohm]"),
                                     ("7", "caption", "0 [0]")):
        key = ("component", 0, element_id)
        assert dict(before[key])[param] == value
        assert dict(after[key])[param] == value

"""The instrument for hir.py's own claim: read a project, write it back.

``hir.py`` says an OPAQUE subtree is "retained, so a writer can put it back
unchanged". :mod:`pscx.write` is the smallest writer that lets the claim be
checked, and this module is the check. It compares canonical XML rather
than bytes, because attribute order and inter-element whitespace are not
what the claim is about.
"""

import collections
import os

import pytest

from pscx.hir import load_project
from pscx.write import canonicalize, write_project

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
ROUNDTRIP = os.path.join(FIXTURES, "roundtrip.pscx")


def _written(path):
    """``(source root, written root)`` for one file, diagnostics muted."""
    from lxml import etree as ET

    from pscx.diagnostics import DIAGNOSTICS
    from pscx.io import _XML_PARSER

    with DIAGNOSTICS.suppressed():
        project = load_project(path)
    return ET.parse(path, _XML_PARSER).getroot(), write_project(project)


def _find(root, tag):
    found = root.find(f".//{tag}")
    assert found is not None, f"{tag} missing from the written document"
    return found


def test_an_opaque_subtree_comes_back_unchanged():
    # The claim hir.py line 16 makes, and the reason this fixture carries
    # four of them at four different depths: root (`bookmarks`),
    # definition graphics (`Gfx`), canvas (`grouping`) and form parameter
    # (`choice`). Each is collected by a DIFFERENT `_opaque` call site, so
    # one surviving says nothing about the others.
    #
    # Fails if the writer drops an opaque subtree, re-serialises it from a
    # model that never read its children, or reattaches it to the wrong
    # parent -- the last being the live risk, since `_definition` merges
    # the definition's opaque children with `graphics`'s into one list.
    source, written = _written(ROUNDTRIP)
    for tag in ("bookmarks", "Gfx", "grouping", "choice"):
        assert canonicalize(_find(written, tag)) == \
            canonicalize(_find(source, tag)), tag


def test_the_opaque_subtrees_keep_their_parents():
    # Placement, not merely presence: a `Gfx` re-emitted as a child of
    # Definition instead of graphics is still "retained" and still wrong.
    # Fails if the writer appends every opaque element at one level.
    _source, written = _written(ROUNDTRIP)
    parents = {tag: _find(written, tag).getparent().tag
               for tag in ("bookmarks", "Gfx", "grouping", "choice")}
    assert parents == {"bookmarks": "project", "Gfx": "graphics",
                       "grouping": "schematic", "choice": "parameter"}


def test_the_modeled_skeleton_comes_back():
    # Non-vacuity in the other direction: the opaque assertions above pass
    # trivially against a writer that emits nothing but opaque elements.
    # Fails if a MODELED field -- the ones the HIR claims to understand --
    # never reaches the document.
    _source, written = _written(ROUNDTRIP)
    assert written.get("name") == "roundtrip_fixture"
    assert written.get("Target") == "EMTDC"
    defn = _find(written, "Definition")
    assert defn.get("name") == "Main"
    assert _find(written, "segment").text == "$A $B $R 0 0"
    assert _find(written, "value").text == "1.0"
    assert _find(written, "cond").text == "View == 1"
    assert [(v.get("x"), v.get("y")) for v in written.iter("vertex")] == \
        [("0", "0"), ("18", "0")]
    user = next(u for u in written.iter("User")
                if u.get("defn") == "master:resistor")
    assert user.find("paramlist/param").get("value") == "1.0 [ohm]"


def test_a_usercmpdefn_states_a_form_empty_included():
    # The rule of the class, with its negative control. Every shipped
    # UserCmpDefn carries a <form>: PSCAD null-references loading one
    # that does not, and its own save keeps the bare <form/> -- both
    # directions confirmed at the tool -- so an empty form is stated by
    # rule, never carried. The control strips the form from the HIR and
    # the written document must state <form/> anyway; the counter-check
    # is that the rule does not over-apply to a class that never states
    # one.
    from pscx.diagnostics import DIAGNOSTICS

    with DIAGNOSTICS.suppressed():
        project = load_project(ROUNDTRIP)
    defn = next(d for d in project.definitions if d.name == "Main")
    defn.classid = "UserCmpDefn"
    defn.form = []
    defn.form_name = None
    written = write_project(project)
    form = written.find(".//Definition[@name='Main']/form")
    assert form is not None, "the rule did not state the empty form"
    assert len(form) == 0 and not form.attrib

    defn.classid = "StationDefn"
    written = write_project(project)
    assert written.find(".//Definition[@name='Main']/form") is None, (
        "the rule over-applies to a class that never states a form")


def test_a_banked_attribute_comes_back_on_the_element_that_had_it():
    # The channel banks each attribute with the XPath of the element that
    # carried it, and the writer resolves that path against the written
    # tree. Placement is the claim, not presence: `sparkle` sits on the
    # second of the canvas's two Users, so a writer that sprays the value
    # over every User, or hands it to the first of the tag, fails on the
    # first one having gained it rather than on the second having lost it.
    from pscx.diagnostics import DIAGNOSTICS

    with DIAGNOSTICS.suppressed():
        project = load_project(ROUNDTRIP)
    banked = {(item.owner, item.name) for item in project.unrecognized
              if item.kind == "attribute"}
    assert banked >= {("project", "gizmo"), ("Definition", "flavour"),
                      ("Port", "spin"), ("Wire", "wavelength"),
                      ("User", "sparkle")}
    written = write_project(project)
    assert written.get("gizmo") == "7"
    assert _find(written, "Definition").get("flavour") == "vanilla"
    assert _find(written, "graphics").get("viewBox") == "0 0 10 10"
    assert _find(written, "Port").get("spin") == "42"
    assert _find(written, "schematic").get("zoomlevel") == "6"
    assert _find(written, "Wire").get("wavelength") == "12"
    users = [u for u in written.iter("User")]
    assert [u.get("sparkle") for u in users] == [None, "true"]


@pytest.mark.parametrize("tag", ["bookmarks", "Gfx", "grouping", "choice"])
def test_removing_an_opaque_subtree_is_detected(tag):
    # The negative control for the round trip itself: canonicalize must
    # notice a missing subtree. Without it, "IDENTICAL" could mean the
    # comparison is blind rather than the writer faithful.
    source, written = _written(ROUNDTRIP)
    victim = _find(written, tag)
    victim.getparent().remove(victim)
    assert canonicalize(written) != canonicalize(source)


# --------------------------------------------------------------------------
# xmldiff: a differ nobody here wrote, over the fixture
# --------------------------------------------------------------------------


def _canonical_trees(path):
    from lxml import etree as ET

    source, written = _written(path)
    return (ET.ElementTree(ET.fromstring(canonicalize(source))),
            ET.ElementTree(ET.fromstring(canonicalize(written))))


def test_xmldiff_states_the_fixture_loss_exactly():
    # An aligner nobody here wrote, over the fixture and its written
    # form. On a document this small the alignment is not in doubt, so
    # the edit script can be stated in full: two nodes moved,
    # and NO attribute deleted -- every unrecognized attribute the
    # fixture carries is back on the element its xpath names, and an
    # aligner that pairs elements for itself agrees none is missing.
    #
    # Fails if the writer loses anything the fixture MODELS, drops or
    # misplaces a banked attribute, or gains a loss the fixture does not
    # intend to demonstrate.
    from xmldiff import main as xmain

    source, written = _canonical_trees(ROUNDTRIP)
    actions = xmain.diff_trees(source, written)
    by_kind: dict = collections.defaultdict(set)
    for action in actions:
        by_kind[type(action).__name__].add(getattr(action, "name", None)
                                           or action.node)
    # both moves are order the HIR does not hold: <graphics> against
    # <schematic>, and <Wire> against the <User> the canvas drew after it
    assert set(by_kind) == {"MoveNode"}
    assert len(by_kind["MoveNode"]) == 2

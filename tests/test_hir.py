"""The HIR and its unrecognized channel.

The channel is a DISCOVERY instrument, so the tests that matter are the
ones that would catch it going quiet: a fixture stating things nobody
models must come back banked. An empty inventory is a suspicious result,
not a triumph.
"""

import os

import pytest
from conftest import REPO_ROOT, master_available

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "unrecognized.pscx")


def test_the_unrecognized_channel_banks_every_unmodeled_fixture_item():
    # Non-vacuity, which is the whole risk here: an inventory can be empty
    # because the source has no surprises or because the channel is not
    # wired into the path that reads it, and those look identical from
    # outside. The fixture states four things no PSCAD file does. Fails if
    # any of them passes through unnoticed.
    from pscx.hir import load_project

    project = load_project(FIXTURE)
    found = {item.key: item for item in project.unrecognized}
    for key in ("project@gizmo", "User@sparkle", "Wire@wavelength",
                "Port@spin", "User/flourish"):
        assert key in found, sorted(found)
    assert found["User@sparkle"].value == "true"
    assert found["User/flourish"].kind == "element"
    assert found["User@sparkle"].kind == "attribute"


def test_a_banked_item_says_where_it_was_read():
    # A finding with no location is a finding nobody acts on. Both halves
    # come from the element itself, so nothing threads a path.
    # Hand-checked against the fixture's own line numbers -- fails if the
    # span is the parent's, the document's, or absent.
    from pscx.hir import load_project

    found = {i.key: i.span for i in load_project(FIXTURE).unrecognized}
    assert found["project@gizmo"].line == 2
    assert found["User@sparkle"].line == 26
    assert found["User/flourish"].line == 30
    assert {span.file for span in found.values()} == {"unrecognized.pscx"}


def test_an_hir_node_built_without_a_document_carries_no_span():
    # The rule diagnostics follow, asserted here for the same reason: a plausible-looking wrong line number sends a reader to the
    # wrong element and is worse than an honest absence. A tree built in
    # memory has no line, so the loader must report None rather than
    # invent one. Fails if a default line ever creeps in.
    from lxml import etree as ET

    from pscx.io import span_of

    built = ET.fromstring("<project name='x'><definitions/></project>")
    assert span_of(built) is None


def test_the_hir_evaluates_nothing():
    # "Lossless and unevaluated" is the point: a value is the expression
    # the file states, units and all, and a script segment is kept as text
    # AND as its guard tree. Fails if the loader resolves, substitutes or
    # unit-converts anything -- at which point the HIR is a second
    # extractor and the two would drift.
    from pscx.hir import load_project

    project = load_project(FIXTURE)
    canvas = project.definitions[0].canvas
    assert canvas.components[0].params[0].values == {"R": "1.0 [ohm]"}
    assert canvas.components[0].defn == "master:resistor"
    assert canvas.wires[0].vertices == [("0", "0"), ("18", "0")]
    assert project.layers[0].state == "disabled"


def test_a_drawing_subtree_is_retained_rather_than_banked():
    # "We chose not to model this" and "we do not know what this is" are
    # different claims, and mixing them makes the inventory useless: 21,373
    # Gfx elements would drown every real finding. Opaque subtrees are kept
    # as the element itself, so a writer can put them back. Fails if a
    # drawing primitive appears in the inventory, or if retaining one means
    # losing it.
    from lxml import etree as ET

    from pscx.hir import OPAQUE_TAGS, load_project

    if not master_available():
        pytest.skip("PSCAD master.pslx not found")
    from pscx.common import MASTER_PSLX

    project = load_project(MASTER_PSLX)
    banked = {item.name for item in project.unrecognized
              if item.kind == "element"}
    assert not banked & OPAQUE_TAGS, sorted(banked & OPAQUE_TAGS)

    retained = [o for d in project.definitions for o in d.opaque]
    assert len(retained) > 1000
    assert all(isinstance(o.element, ET._Element) for o in retained)


def test_an_unrecognized_thing_is_a_gap_counted_once_per_kind():
    # It is "we don't know what this means", the value survives verbatim,
    # and a consumer can tell it apart from a value we invented -- so GAP,
    # not ERROR. And counted once per DISTINCT thing with the repetitions
    # aggregated: 22,210 `User@z` attributes are one thing we do not
    # understand, not 22,210.
    from pscx.diagnostics import CATALOG, Diagnostics, Severity
    from pscx.hir import _report, load_project

    assert CATALOG["hir_unrecognized"].severity is Severity.GAP

    bus = Diagnostics()
    project = load_project(FIXTURE)
    _report(project, bus)
    records = bus.records()
    assert len(records) == len({i.key for i in project.unrecognized})
    assert all(r.severity is Severity.GAP for r in records)
    assert all(r.span is not None for r in records)
    assert bus.by_code()["hir_unrecognized"] == len(project.unrecognized)


#: PSCAD spells the global-substitution container two ways. `nets.py`
#: never notices, because it iterates `<Sub>` anywhere and never looks at
#: the container; the HIR names the container and so had a blind spot the
#: extractor does not.
GLOBAL_SUBSTITUTION_TAGS = ("GlobalSubstitutions", "GlobalSubsitutions")


def _respelt(tmp_path, source, spelling):
    """A copy of ``source`` with its substitution container re-spelt."""
    from lxml import etree as ET

    root = ET.parse(source).getroot()
    for tag in GLOBAL_SUBSTITUTION_TAGS:
        for element in root.iter(tag):
            element.tag = spelling
    path = tmp_path / f"{spelling}.pscx"
    path.write_bytes(ET.tostring(root))
    return str(path)


@pytest.mark.parametrize("spelling", GLOBAL_SUBSTITUTION_TAGS)
def test_the_hir_reads_a_substitution_under_either_spelling(
        tmp_path, spelling):
    # A differential on one file: the same project, its container spelt
    # both of PSCAD's ways, must yield the same substitution. Fails if
    # the loader answers only to the spelling it was written against, in
    # which case the misspelt copy comes back with nothing and the file
    # still looks fully modeled.
    from pscx.hir import load_project

    source = os.path.join(REPO_ROOT, "tests", "fixtures",
                          "substitutions.pscx")
    project = load_project(_respelt(tmp_path, source, spelling))

    assert project.globals == {"freq": "60.0"}
    container, = project.substitutions
    assert container.tag == spelling


@pytest.mark.parametrize("spelling", GLOBAL_SUBSTITUTION_TAGS)
def test_neither_spelling_reaches_the_unrecognized_channel(tmp_path, spelling):
    # The other half. Being unrecognized is the safe state because it is
    # the counted one, so a spelling the loader reads must leave the
    # channel. Fails if
    # the container is both read AND banked, which would double-count it.
    from pscx.hir import load_project

    source = os.path.join(REPO_ROOT, "tests", "fixtures",
                          "substitutions.pscx")
    project = load_project(_respelt(tmp_path, source, spelling))

    assert [i.key for i in project.unrecognized
            if "ubstit" in i.key or "ubsit" in i.key] == []


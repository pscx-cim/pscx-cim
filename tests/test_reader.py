"""The reader's composition contract.

:mod:`pscx.reader` composes the proven per-document inverses into one
``HirProject``. What it adds -- and what these tests hold -- is the
composition itself: the completeness table that names every HIR field's
source, kept equal to the reconstruction inventory so neither drifts
alone; the payload parent map; the orient rebuild from its two carried
halves; and a whole fixture round trip through the emitted documents.
"""

import dataclasses
import inspect
import os

import pytest
from conftest import master_available

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "substitutions.pscx")


def test_the_reader_names_every_hir_field_once():
    # The completeness contract: a new HIR field arrives as a failure by
    # name here and in read_documents, never as a silently defaulted
    # attribute on every reconstructed project.
    from pscx import hir, reader

    fields = {f"{name}.{field.name}"
              for name, cls in vars(hir).items()
              if inspect.isclass(cls) and dataclasses.is_dataclass(cls)
              and cls.__module__ == "pscx.hir"
              for field in dataclasses.fields(cls)}
    assert fields == set(reader.FIELD_SOURCE)
    reader._assert_covered()


def test_the_reader_and_the_inventory_agree_field_by_field():
    # The inventory says what the exchange carries; this table says what
    # the reader does about it. Held equal directionally: a CARRIED row
    # must be read from a document, a MISSING row must come back held,
    # and a DERIVABLE row may be a rule, empty, or read from a document
    # that happens to state it (the DL names carry the element ids the
    # inventory never required) -- but never held.
    from test_reconstruction_inventory import CARRIED, INVENTORY, MISSING

    from pscx import reader

    for name, entry in INVENTORY.items():
        source = reader.FIELD_SOURCE[name]
        if entry.verdict == CARRIED:
            assert source == reader.FROM_DOCUMENT, name
        elif entry.verdict == MISSING:
            assert source == reader.HELD, name
        else:
            assert source != reader.HELD, name


def test_every_payload_kind_names_one_parent_inside_the_opaque_set():
    # The map is the derivable half of a payload's address: the index is
    # carried, the parent is this rule. Its keys are opaque tags, and its
    # values are the places the writer can put one back.
    from pscx.hir import OPAQUE_TAGS
    from pscx.reader import OPAQUE_PARENT_OF

    assert set(OPAQUE_PARENT_OF) <= set(OPAQUE_TAGS)
    assert set(OPAQUE_PARENT_OF.values()) == {
        "project", "Definition", "graphics", "schematic", "parameter"}


def test_a_rebuilt_orient_is_the_stated_string_for_all_eight():
    # The one field two documents carry between them: DL keeps the
    # quarter turn as an angle, the source subject keeps the mirror.
    # Every stated orient is a canonical '0'-'7', so the rebuild must
    # return the exact string, not an equivalent.
    from pscx.dl import rotation_degrees
    from pscx.reader import _orient
    from pscx.record import _mirror

    for stated in "01234567":
        assert _orient(rotation_degrees(stated), _mirror(stated)) == stated
    assert _orient(None, False) is None


def test_the_port_key_order_names_exactly_the_record_port_keys():
    from pscx.reader import PORT_KEY_ORDER
    from pscx.record import PORT_KEYS

    assert set(PORT_KEY_ORDER) == {key for key, _attribute in PORT_KEYS}


@pytest.mark.skipif(not master_available(),
                    reason="PSCAD master.pslx not found")
def test_the_fixture_round_trips_through_the_documents(tmp_path):
    # The whole pipe on the smallest real case: emit the nine documents,
    # read them back, and the things the fixture exists to state -- its
    # substitution table, its definitions, its root attributes -- come
    # back stated, with the substitution nesting at its full depth
    # (a flat <Sub> list is the exact corruption test_meaning.py was
    # built to catch).
    from pscx.cim import emit_case
    from pscx.diagnostics import DIAGNOSTICS, Diagnostics
    from pscx.hir import load_project
    from pscx.reader import read_documents
    from pscx.write import write_project

    with DIAGNOSTICS.suppressed():
        paths = emit_case(FIXTURE, tmp_path)
        original = load_project(FIXTURE)
    bus = Diagnostics()
    project = read_documents(paths, bus=bus)

    assert project.globals == original.globals
    assert {d.name for d in project.definitions} == {
        d.name for d in original.definitions if d.name}
    assert (project.name, project.version, project.schema,
            project.target) == (original.name, original.version,
                                original.schema, original.target)

    written = write_project(project)
    tree = written.getroottree()
    # List[1], not List: the fixture states only a Sub list, and the
    # container comes back in the pinned shape -- Sub then an
    # empty ValueSet -- so the rebuilt document states two.
    assert [tree.getpath(sub) for sub in written.iter("Sub")] == [
        "/project/GlobalSubstitutions/List[1]/Sub"]
    assert [node.get("classid")
            for node in written.iter("List")] == ["Sub", "ValueSet"]
    declared = {p.get("name"): p.get("value")
                for sub in written.iter("Sub")
                for p in sub.findall("paramlist/param")}
    assert declared == {"name": "freq", "value": "60.0"}

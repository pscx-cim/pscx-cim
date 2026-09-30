"""``pscx.io.load_definitions`` reads master.pslx through the HIR. Does it
build what a direct walk of the element tree builds?

Every field of every ``ComponentDef`` in master is derivable from its
``HirDefinition``, with the derivation map established by ablation. The
question here is whether the registry is the same, and the comparison
needs something on the other side of it.

That something is ``tests/direct_definitions.py``: the direct-XML reader,
preserved with nothing on the extraction path importing it, purely to be
the other side of the comparison. Without it this file
would compare an HIR-derived ``ComponentDef`` against an HIR-derived
``ComponentDef`` -- green, and proving nothing. A gate that compares a
thing to itself is worse than one that passes over zero cases, because it
does not even look empty.

The trap this file is written around: ``ComponentDef`` declares FOUR of
its eleven fields ``compare=False`` -- ``defaults``, ``units``,
``guard_trees`` and ``form_parameters`` -- so
``built == loaded`` is true of two objects whose parameter defaults
disagree entirely. ``test_componentdef_equality_ignores_the_excluded_fields``
is the control that keeps that sentence from being prose, and the
comparison below reads the field list off the dataclass rather than
naming it, so a twelfth field cannot arrive uncompared.
"""

import dataclasses
from collections import Counter

import pytest
from conftest import master_available
from direct_definitions import load_definitions_directly
from pins import coverage, gap

#: Definitions in master.pslx the registry and the direct-XML reader agree
#: on, field by field. The number to read is the SHORTFALL against
#: master's 392, and it is zero. A fall means reading through the HIR
#: changes what the extraction path holds, which it must not do.
DERIVED_DEFINITIONS = coverage(
    392,
    "master definitions whose registry entry agrees with the direct-XML "
    "reader on all eleven declared fields -- the whole library, so reading "
    "master through the HIR builds the registry the element tree built")

#: Distinct things master.pslx states that the HIR cannot give a meaning
#: to. This is what the library load does NOT file, and the number is here
#: so the policy is stated in a quantity rather than in prose: every
#: extraction resolves against master, so every extraction would carry
#: these, unchanged run to run and about a library the case did not write.
MASTER_UNRECOGNIZED_KINDS = gap(
    46, "hir_unrecognized",
    "distinct unrecognized kinds master.pslx alone banks, which the "
    "library load discards rather than files -- a registry build reads a "
    "document for its definitions and claims nothing about the rest of it")

#: The same channel counted by occurrence rather than by kind: what the
#: DIAGNOSTICS block of every single extraction would have grown by.
MASTER_UNRECOGNIZED_ITEMS = gap(
    25260, "hir_unrecognized",
    "occurrences behind those kinds in master.pslx, aggregated onto them "
    "by the per-kind rule -- the size of the report a case would "
    "inherit from the library it resolves against")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def master_readers():
    """``(registry, direct, project, dropped)`` over master.

    Module-scoped because reading master.pslx is the expensive thing in
    this file and every test here wants the same one. ``registry`` is what
    the extraction path actually holds, cache and all; ``direct`` is the
    preserved reader's answer; ``project`` is the HIR the registry was
    built from, which the ablation test needs.
    """
    if not master_available():
        pytest.skip("PSCAD master.pslx not found")
    from pscx.common import MASTER_PSLX
    from pscx.io import load_master, master_project

    registry = load_master()
    direct: dict = {}
    dropped: list = []
    load_definitions_directly(MASTER_PSLX, direct, dropped)
    return registry, direct, master_project(), dropped


def _fields():
    from pscx.model import ComponentDef

    return [f.name for f in dataclasses.fields(ComponentDef)]


def _compare(registry, direct, agree, disagree):
    """Field by field and never with ``==``, into shared counters."""
    for key, want in direct.items():
        got = registry[key]
        for field in _fields():
            if getattr(want, field) == getattr(got, field):
                agree[field] += 1
            else:
                disagree.setdefault(field, []).append(key)


# --------------------------------------------------------------------------
# master
# --------------------------------------------------------------------------


def test_the_two_readers_agree_on_the_definition_key_space(master_readers):
    # Settled before any content is compared, because a comparison over a
    # silently smaller set is the same failure as a gate that passes over
    # zero cases. Fails if master grows a second namespace, an unnamed
    # definition or a name stated twice -- each of which makes the
    # name-keyed view the audit takes lossy, and the first of which makes
    # the namespace the load derives from the project ambiguous.
    registry, direct, project, _dropped = master_readers
    from pscx.audit import master_definitions

    assert len(project.definitions) == DERIVED_DEFINITIONS
    assert len(registry) == DERIVED_DEFINITIONS
    assert len(direct) == DERIVED_DEFINITIONS
    assert set(registry) == set(direct)

    unnamed = [d for d in project.definitions if not d.name]
    assert unnamed == []
    assert len(master_definitions()) == DERIVED_DEFINITIONS
    assert {namespace for namespace, _name in registry} == {"master"}
    repeated = [n for n, c in Counter(d.name for d in project.definitions).items()
                if c > 1]
    assert repeated == []


def test_the_registry_is_what_the_direct_xml_reader_built(master_readers):
    # The comparison. Field by field over the whole library, with the
    # field list read off the dataclass so a new field arrives compared
    # rather than ignored. Fails if reading master through the HIR changed
    # anything the extraction path holds.
    registry, direct, _project, dropped = master_readers

    agree: Counter = Counter()
    disagree: dict[str, list] = {}
    _compare(registry, direct, agree, disagree)
    assert disagree == {}, {f: keys[:5] for f, keys in disagree.items()}
    assert set(agree) == set(_fields())
    assert all(n == DERIVED_DEFINITIONS for n in agree.values()), agree

    # A port whose coordinates do not parse is dropped by both readers, so
    # a rise here would be agreement reached by both sides discarding the
    # same thing. master states none.
    assert dropped == []


def test_every_compared_componentdef_field_is_populated(master_readers):
    # Eleven fields all agreeing is only a result if the fields carry
    # something: a tuple that is empty in both readers agrees for free. So
    # each field's population is asserted. Fails if a field goes empty
    # library-wide, at which point its agreement stops proving anything.
    registry, _direct, _project, _dropped = master_readers

    populated = {field: sum(1 for d in registry.values() if getattr(d, field))
                 for field in _fields()}
    assert populated == {
        "namespace": 392, "name": 392, "ports": 336, "defaults": 350,
        "writer_directives": 83, "branch_decls": 81,
        "computations_text": 103, "model_data_text": 33, "units": 216,
        "guard_trees": 357, "form_parameters": 350}
    # and by content, not merely by definition count
    assert sum(len(d.ports) for d in registry.values()) == 2734
    assert sum(len(d.defaults) for d in registry.values()) == 8955
    assert sum(len(d.guard_trees) for d in registry.values()) == 1027


def test_each_componentdef_field_derives_from_its_stated_hir_collection(master_readers):
    # Non-vacuity from the input side, and the derivation map as a
    # by-product. A builder that ignored what it was handed would still
    # agree with a registry it never consulted, so each of the three
    # HirDefinition collections is emptied in turn and the ComponentDef
    # fields that move are collected library-wide. Nine of the eleven
    # move; ``namespace`` and ``name`` do not, because they come from the
    # project and the definition's own attribute. Fails if a field turns
    # out to be fed by something other than what this claims -- or by
    # nothing, which would mean it agrees for free.
    _registry, _direct, project, _dropped = master_readers
    import copy

    from pscx.io import component_def

    moved: dict[str, set] = {"ports": set(), "form": set(), "segments": set()}
    for definition in project.definitions:
        whole = component_def("master", definition)
        for attribute in moved:
            stripped = copy.copy(definition)
            setattr(stripped, attribute, [])
            got = component_def("master", stripped)
            moved[attribute] |= {f for f in _fields()
                                 if getattr(got, f) != getattr(whole, f)}

    assert moved["ports"] == {"ports"}
    assert moved["form"] == {"defaults", "units", "writer_directives",
                             "form_parameters"}
    assert moved["segments"] == {
        "writer_directives", "branch_decls", "computations_text",
        "model_data_text", "guard_trees"}
    # every field but the two the definition does not state in a
    # collection at all
    assert set().union(*moved.values()) == set(_fields()) - {"namespace",
                                                             "name"}


def test_componentdef_equality_ignores_the_excluded_fields(master_readers):
    # The control on the method. ``ComponentDef`` excludes four fields
    # from ``__eq__``, so a builder that got every parameter default
    # wrong, every declared unit wrong and every guard tree wrong would
    # still compare equal to the definition it was derived from. Fails if
    # the exclusions change and this file's field-by-field comparison
    # stops being the thing that catches them.
    registry, _direct, _project, _dropped = master_readers
    from pscx.model import ComponentDef

    excluded = [f.name for f in dataclasses.fields(ComponentDef)
                if not f.compare]
    assert excluded == ["defaults", "units", "guard_trees", "form_parameters"]

    real = registry[("master", "resistor")]
    assert real.defaults and real.units and real.guard_trees
    tampered = dataclasses.replace(
        real, defaults={"BOGUS": "1"}, units={"bogus": "H"},
        guard_trees=(("Bogus", ()),), form_parameters=(("Bogus",),))

    assert tampered == real, "the trap is gone; this file's method can relax"
    caught = [f for f in _fields()
              if getattr(tampered, f) != getattr(real, f)]
    assert caught == excluded


# --------------------------------------------------------------------------
# The one reader, and what it is not allowed to say
# --------------------------------------------------------------------------


def test_master_is_read_only_through_the_hir_loader(monkeypatch):
    # What "one parse of master.pslx" means, asserted rather than read off
    # the imports: both surviving views of the library come out of one
    # call to ``load_project`` each. Fails if either grows a second
    # reader.
    if not master_available():
        pytest.skip("PSCAD master.pslx not found")
    from pscx import hir
    from pscx.audit import master_definitions
    from pscx.common import MASTER_PSLX
    from pscx.io import load_definitions

    read: list[str] = []
    real = hir.load_project

    def counting(path, bus=None):
        read.append(path)
        return real(path, bus)

    monkeypatch.setattr(hir, "load_project", counting)

    registry: dict = {}
    load_definitions(MASTER_PSLX, registry)
    assert len(registry) == DERIVED_DEFINITIONS
    assert read == [MASTER_PSLX]

    # The audit's view is memoised, so the read has to be forced to be
    # counted -- and that memo is the reason a second one would be free
    # to go unnoticed.
    read.clear()
    master_definitions.cache_clear()
    try:
        assert len(master_definitions()) == DERIVED_DEFINITIONS
        assert read == [MASTER_PSLX]
    finally:
        # and dropped again: what the memo holds is a project, a project
        # holds the elements it kept opaque, and an lxml element holds the
        # document -- so a memo left populated here is master's whole tree
        # carried by this worker for the rest of the run.
        master_definitions.cache_clear()


def test_the_library_load_files_nothing_from_the_unrecognized_channel(
        master_readers):
    # The policy, in the two quantities it is about. Loading a library
    # builds a registry of definitions; the HIR's channel describes a
    # WHOLE document, most of which a registry build neither reads nor
    # claims anything about. So the channel is silenced for the duration
    # of the load and the three loader diagnostics are untouched. Fails if
    # a registry build starts filing findings against every case that
    # resolves against master.
    import pscx.io as io_module
    from pscx.common import MASTER_PSLX
    from pscx.diagnostics import CATALOG, Diagnostics, Severity
    from pscx.hir import _report
    from pscx.io import load_definitions

    _registry, _direct, project, _dropped = master_readers
    bus = Diagnostics()
    saved, io_module.DIAGNOSTICS = io_module.DIAGNOSTICS, bus
    try:
        registry: dict = {}
        load_definitions(MASTER_PSLX, registry)
    finally:
        io_module.DIAGNOSTICS = saved
    assert len(registry) == DERIVED_DEFINITIONS
    assert bus.records() == []

    # ...and what it would have filed, so the policy is a number rather
    # than an assurance. Off the fixture's project rather than a fresh
    # one: two live HirProjects in a worker is master's tree twice.
    channel = Diagnostics()
    _report(project, channel)
    assert len(channel.records()) == MASTER_UNRECOGNIZED_KINDS
    assert channel.by_code()["hir_unrecognized"] == MASTER_UNRECOGNIZED_ITEMS

    # It is a GAP, and `above` is strict, so `--fail-on gap` -- the CLI's
    # default -- fails on ERROR alone and no exit code would have moved.
    # The cost is the report and every count pinned over it, which is a
    # quieter failure than a nonzero exit and not a smaller one.
    assert CATALOG["hir_unrecognized"].severity is Severity.GAP
    assert channel.above(Severity.GAP) == []


def test_an_unparseable_library_is_reported_not_raised(
        tmp_path):
    # ``load_project`` raises on a file lxml cannot read, and
    # ``load_definitions`` is the caller that must not. The tolerance is
    # restated here from the registry's side; ``test_spans.py`` holds the
    # spans. Fails if the HIR's contract reaches a caller whose whole job
    # is to survive a broken library.
    from lxml import etree as ET

    import pscx.io as io_module
    from pscx.diagnostics import Diagnostics
    from pscx.hir import load_project
    from pscx.io import load_definitions

    broken = tmp_path / "broken.pslx"
    broken.write_text('<?xml version="1.0"?>\n<project name="x">\n</projectt>\n')
    with pytest.raises(ET.XMLSyntaxError):
        load_project(str(broken))

    bus = Diagnostics()
    saved, io_module.DIAGNOSTICS = io_module.DIAGNOSTICS, bus
    try:
        registry: dict = {}
        load_definitions(str(broken), registry)
    finally:
        io_module.DIAGNOSTICS = saved
    assert registry == {}
    assert [r.code for r in bus.records()] == ["unparseable_library"]

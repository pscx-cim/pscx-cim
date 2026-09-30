"""The shape coverage itself, as an oracle rather than a report.

``shape_inventory.SHAPES`` claims to classify every file in the ENTSO-E
distribution. This module holds it to that in both directions, and checks
that every verdict states its own reason and every loaded file targets
something.
"""

import os

import pytest
import shape_inventory
from pins import coverage
from shape_inventory import SHACL_DIR, SHAPES, Excluded, Loaded

pytestmark = pytest.mark.skipif(
    not os.path.isdir(SHACL_DIR),
    reason="set ENTSOE_SHACL to the ENTSO-E CGMES SHACL directory",
)

SHAPE_FILE_COUNT = coverage(
    74, "shape files in the ENTSO-E CGMES distribution, every one of them "
    "classified. A count, not a threshold: it moves when the upstream "
    "clone moves, and the test that fails then names the file.")

LOADED_FILE_COUNT = coverage(
    38, "shape files loaded, the rest excluded with a stated reason. The "
    "loaded ones include the file that makes an Analog's unit mandatory, "
    "the three that validate the md:FullModel header, and the "
    "DiagramLayout files, which apply because the drawing is emitted as "
    "a DL document.")


def _distribution_files() -> set:
    return {name for name in os.listdir(SHACL_DIR) if name.endswith(".ttl")}


def test_every_file_in_the_distribution_is_classified():
    # BOTH directions, which is what makes this an inventory rather than
    # a list. A file that appears upstream and nobody reads leaves its
    # constraints, such as `C:456:TP:Terminal:switch`, unevaluated; a
    # file in the table that upstream removed is a reason that applies to
    # nothing.
    on_disk = _distribution_files()
    classified = set(SHAPES)
    assert sorted(on_disk - classified) == [], (
        "unclassified shape files: read each one and give it a verdict")
    assert sorted(classified - on_disk) == [], (
        "classified files that are not in the distribution")
    assert len(on_disk) == SHAPE_FILE_COUNT


def test_every_shape_file_verdict_states_its_own_reason():
    # A per-FILE reason, not a per-family rule. "The InverseAssociation
    # variants are excluded" is a rule, and a rule cannot say which file
    # it forgot -- so each entry carries its own words and they have to
    # be substantive enough to argue with.
    for name, verdict in SHAPES.items():
        assert len(verdict.why.split()) >= 12, f"{name}: {verdict.why!r}"
        if isinstance(verdict, Excluded):
            assert verdict.kind in ("PROFILE", "TOOLING"), name
        else:
            assert verdict.documents, name
            assert set(verdict.documents) <= {
                "EQ", "TP", "SC", "SSH", "OP", "DL", "MERGED",
                "HEADER"}, name


def test_every_tooling_exclusion_names_the_failing_mechanism():
    # The distinction the field exists for. A PROFILE exclusion says we
    # emit no such document, which is a decision this repo owns and can
    # revisit. A TOOLING exclusion says pyshacl or rdflib cannot evaluate
    # the file faithfully -- a defect somewhere else, and recording it as
    # a choice would hide it. So each one must name the mechanism.
    tooling = {name: v for name, v in SHAPES.items()
               if isinstance(v, Excluded) and v.kind == "TOOLING"}
    assert tooling, "no tooling exclusion at all -- verify rather than assume"
    for name, verdict in tooling.items():
        assert any(word in verdict.why
                   for word in ("pyshacl", "rdflib", "sh:class")), name


def test_the_loaded_verdicts_equal_the_shape_files_the_harness_loads():
    # The derivation, asserted rather than trusted: the per-document
    # shape lists come out of this table, so a file loaded here reaches a
    # document and a file nobody classified reaches none.
    derived = set()
    for files in (*shape_inventory.PROFILE_SHAPES.values(),
                  shape_inventory.MERGED_SHAPES,
                  shape_inventory.HEADER_SHAPES):
        derived |= set(files)
    loaded = {name for name, v in SHAPES.items() if isinstance(v, Loaded)}
    assert derived == loaded
    assert len(loaded) == LOADED_FILE_COUNT
    for name in loaded:
        assert os.path.exists(os.path.join(SHACL_DIR, name)), name


def test_every_loaded_file_contributes_a_constraint_to_something():
    # A loaded file that declares no target at all would inflate the
    # count above while validating nothing. Every one must carry either a
    # targetClass or a SPARQL target -- the header files use the latter.
    import rdflib
    from rdflib.namespace import SH

    loaded = sorted(name for name, v in SHAPES.items()
                    if isinstance(v, Loaded))
    for name in loaded:
        graph = rdflib.Graph()
        graph.parse(os.path.join(SHACL_DIR, name), format="turtle")
        targets = (set(graph.objects(None, SH.targetClass))
                   | set(graph.objects(None, SH.target))
                   | set(graph.objects(None, SH.targetObjectsOf))
                   | set(graph.objects(None, SH.targetSubjectsOf)))
        assert targets, f"{name} targets nothing and validates nothing"


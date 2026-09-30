"""The DC class family, against the shape files that would validate it.

Nothing in the emitted model uses the DC class family yet, so this file
is the step before the mapping rather than a check on it.
"""

import os

import pytest


def test_every_dc_class_is_targeted_by_a_shape_file_already_loaded():
    # Checked BEFORE mapping, and for a measured reason: loading a new class family and validating it against a
    # shape subset that never covered it passes vacuously, and
    # here it would be self-inflicted.
    #
    # The answer is that nothing has to be added -- the DC family's
    # constraints live in 301/452 Equipment, 456 Topology and the three
    # 600-2 Simple files, all of which are already LOADED against named
    # documents. Asserted per class so a future file exclusion cannot
    # quietly take one of them away.
    import rdflib
    import shape_inventory as inventory

    if not os.path.isdir(inventory.SHACL_DIR):
        pytest.skip("set ENTSOE_SHACL to the ENTSO-E CGMES SHACL directory")

    sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
    wanted = {"CsConverter", "ACDCConverterDCTerminal", "DCNode",
              "DCTopologicalNode", "DCConverterUnit", "DCLine",
              "DCLineSegment", "DCBreaker", "DCGround", "DCTerminal",
              "DCSeriesDevice", "DCShunt", "DCDisconnector"}
    covered: dict[str, set] = {}
    for name, verdict in inventory.SHAPES.items():
        if not isinstance(verdict, inventory.Loaded):
            continue
        graph = rdflib.Graph()
        graph.parse(os.path.join(inventory.SHACL_DIR, name), format="turtle")
        for target in set(graph.objects(None, sh.targetClass)):
            local = str(target).rsplit("#", 1)[-1]
            if local in wanted:
                covered.setdefault(local, set()).add(verdict.documents)

    missing = sorted(wanted - set(covered))
    assert not missing, (
        f"{missing} would be emitted against no loaded shape file, which "
        f"passes validation vacuously")
    # and the three profiles a DC network is stated across are all
    # represented, so the coverage is not one document deep
    profiles = {p for entries in covered.values()
                for documents in entries for p in documents}
    assert {"EQ", "TP", "SSH"} <= profiles, sorted(profiles)

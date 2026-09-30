"""The IEEE 39-bus benchmark from the PSCAD website, emitted by pscx.

The case is downloaded separately and placed under ``benchmarks/ieee39``.
Without it, every test here skips with one named reason.
"""

import math
import os

import pytest

BENCHMARK_DIR = os.path.join(os.path.dirname(__file__), os.pardir,
                             "benchmarks", "ieee39")
CASE = os.path.join(BENCHMARK_DIR, "ieee_39_bus_v5.pscx")

#: The vendor's own bytes. Hashed rather than described, because a
#: benchmark re-fetched from the knowledge base is only the same
#: benchmark if it is the same file.
VENDOR_ARTIFACTS = {
    "ieee-39-bus.zip":
        "749ab9b30eecdf9afe3317564cd0cfc71632505e8973217e2c33ea75fe5e3ab5",
    "ieee_39_bus.pscx":
        "1e545f56b30f8bd983735fa75ed18f0143cc16d05a2f2441521932857e9ef473",
}

#: The 4.5.5 original, which states all 68 of its boundary ports in the
#: unread spelling and is therefore this repo's only witness that they go
#: unread.
ORIGINAL = os.path.join(BENCHMARK_DIR, "ieee_39_bus.pscx")

pytestmark = pytest.mark.skipif(
    not os.path.exists(CASE),
    reason=f"IEEE 39 benchmark not present at {BENCHMARK_DIR}")


def test_the_benchmark_files_match_the_vendor_digests():
    # The downloaded zip and the case it ships are the vendor's bytes.
    # Absence is a failure and not a skip, because the module's own guard
    # already said the benchmark is here.
    import hashlib
    import zipfile

    for name, digest in sorted(VENDOR_ARTIFACTS.items()):
        path = os.path.join(BENCHMARK_DIR, name)
        assert os.path.exists(path), path
        with open(path, "rb") as handle:
            assert hashlib.sha256(handle.read()).hexdigest() == digest, name

    # the zip ships exactly one case
    with zipfile.ZipFile(os.path.join(BENCHMARK_DIR,
                                      "ieee-39-bus.zip")) as bundle:
        assert bundle.namelist() == ["ieee_39_bus.pscx"]

    from lxml import etree
    assert etree.parse(ORIGINAL).getroot().get("version") == "4.5.5"
    assert etree.parse(CASE).getroot().get("version") == "5.0.2"


def test_the_hir_reads_no_ports_from_the_4_5_original_and_all_from_the_resave():
    # The does-something half of the unread spelling, on the one pair of
    # files in the tree that isolates it: the same 71 definitions, saved
    # twice. In the 4.5.5 original all 34 line modules declare their
    # `in`/`out` boundary inside <svg> and the HIR reads NOTHING -- every
    # definition comes out portless, so each module instance places no
    # electrical boundary and the same-named xnodes inside have nothing to
    # link to. The 5.0.2 resave states the same boundaries in
    # <graphics><Port> and the HIR reads all 68.
    from pscx import pscad_topology as pt

    read = {}
    for path in (ORIGINAL, CASE):
        definitions: dict = {}
        pt.load_definitions(path, definitions)
        read[path] = (len(definitions),
                      sum(1 for d in definitions.values() if d.ports),
                      sum(len(d.ports) for d in definitions.values()))

    assert read[ORIGINAL] == (71, 0, 0)
    assert read[CASE] == (71, 34, 68)


def _islanded_from_a_source(model):
    """True when some emitted equipment cannot reach a source injection.

    A power flow over such a network returns NaN for every bus the slack
    does not reach, and an assertion over NaN is vacuous. Walked over the
    emitted model itself: two nodes are joined by a piece of equipment
    when its terminals share them, and an OPEN switch joins nothing.
    """
    # The EARTH is exempt, in both directions, because a load flow's
    # reference is the slack, ground breaks propagation, and no path
    # runs from the slack to earth and back. So a grounding class's node
    # is not REQUIRED to be reachable, and a closed earthing switch is not
    # a path between two live nodes.
    from pscx.rules import GROUNDING_CLASSES

    terminals = {}
    for resource in model.resources.values():
        if type(resource).__name__ != "Terminal":
            continue
        owner = model.resources[resource.ConductingEquipment]
        if type(owner).__name__ in GROUNDING_CLASSES:
            continue
        terminals.setdefault(resource.ConductingEquipment, []).append(
            resource.ConnectivityNode)

    adjacency, seeds = {}, set()
    for eq_mrid, nodes in terminals.items():
        equipment = model.resources[eq_mrid]
        if type(equipment).__name__ in ("ExternalNetworkInjection",):
            seeds.update(nodes)
        if getattr(equipment, "open", False):
            continue
        for node in nodes:
            adjacency.setdefault(node, set()).update(
                other for other in nodes if other != node)
    if not seeds:
        return True

    reached, frontier = set(seeds), list(seeds)
    while frontier:
        node = frontier.pop()
        for neighbour in adjacency.get(node, ()):
            if neighbour not in reached:
                reached.add(neighbour)
                frontier.append(neighbour)
    return any(node not in reached
               for nodes in terminals.values() for node in nodes)


@pytest.mark.slow
def test_the_emitted_benchmark_is_one_island_on_one_base_voltage(tmp_path):
    from pscx.cim import build_cim
    from pscx.elaborate import flatten

    model = build_cim(flatten(CASE))
    assert not _islanded_from_a_source(model)
    voltages = {r.mRID: r.nominalVoltage for r in model.resources.values()
                if type(r).__name__ == "BaseVoltage"}
    placeholders = [t.name for t in model.resources.values()
                    if type(t).__name__ == "TopologicalNode"
                    and voltages[t.BaseVoltage] == 1.0]
    # the earth is the one node with no stated voltage; every conducting
    # node -- the four port-less junctions included -- resolves to 230 kV
    assert placeholders == ["GND"]
    assert model.diagnostics.by_code()["cim_basevoltage_unknown"] == 1


@pytest.mark.slow
def test_the_ssh_states_the_dispatch_and_eq_the_magnetizing(tmp_path):
    # The two joins on the benchmark itself. SSH: all ten sources state
    # their live Pinit/Qinit pairs ("At the Terminal", fixed control, on
    # the form's stated 100 MVA) in the load convention, no placeholder
    # counted. EQ: all twelve transformers
    # declare the non-ideal model with Im1 = 2 % on 100 MVA at 230 kV,
    # so every end 1 states b = -0.02 x 100 / 230^2 siemens, with g
    # carrying the eight nonzero NLL fields. Those fields hold the classic
    # winding resistances, and the rule still maps the form's declared
    # no-load-loss semantics.
    from pscx.cim import build_cim
    from pscx.elaborate import flatten

    model = build_cim(flatten(CASE))
    sources = [r for r in model.resources.values()
               if type(r).__name__ == "ExternalNetworkInjection"]
    assert len(sources) == 10
    assert all(s.p < 0.0 for s in sources)
    assert model.diagnostics.count("cim_source_injection_placeholder") == 0
    # every source carries all of: a voltage-mode
    # RegulatingControl targeting the live Es, the uniform reference
    # designation, and the stated 10 ohm / 80 deg internal impedance
    # restated as the degenerate IEC 60909 ranges (ZSeq = 0 states the
    # zero sequence equal to the positive)
    for s in sources:
        control = model.resources[s.RegulatingControl]
        assert control.targetValue > 0.0
        assert s.controlEnabled is True
        assert s.referencePriority == 1
        assert s.maxR1ToX1Ratio == pytest.approx(
            1.0 / math.tan(math.radians(80.0)))
        assert s.maxInitialSymShCCurrent == pytest.approx(
            230.0e3 / (math.sqrt(3.0) * 10.0))
        assert s.maxZ0ToZ1Ratio == 1.0
    ends1 = [r for r in model.resources.values()
             if type(r).__name__ == "PowerTransformerEnd"
             and r.endNumber == 1]
    assert len(ends1) == 12
    for end in ends1:
        assert end.b == pytest.approx(-0.02 * 100.0 / 230.0 ** 2, rel=1e-12)
        assert end.g >= 0.0
    assert sum(1 for end in ends1 if end.g > 0.0) == 8
    assert model.diagnostics.count("cim_xfmr_no_magnetizing") == 0



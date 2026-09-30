"""The CGMES class catalogue, AC reachability, and the triage of unmapped kinds.

:func:`cim_catalogue` reads every CGMES class pycgmes declares, with the
terminal cardinality the ENTSO-E SHACL states for it, the terminal counts
the CGMES conformity specimens exhibit, and the profiles its own
attributes are placed in. :func:`master_definitions` is master.pslx by
definition name, with the form text the extractor drops. :func:`dc_map`
finds which nodes of one case an AC source reaches, and :mod:`pscx.dc`
builds on it. :data:`TRIAGE` records where each kind the ``cim_unmapped``
diagnostic counts belongs, decided by reading its form.
"""

from __future__ import annotations

import glob
import importlib
import os
import pkgutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

#: Where the ENTSO-E SHACL shapes and the CGMES conformity specimens live.
#: Both are optional. An unset or missing directory leaves the cardinalities
#: it would supply unset.
ENTSOE_SHACL = os.environ.get("ENTSOE_SHACL", "")
CGMES_SPECIMENS = os.environ.get("CGMES_SPECIMENS", "")


# --------------------------------------------------------------------------
# The CIM side
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CimClass:
    """One CGMES class, with its terminal cardinality and profiles."""

    name: str
    #: Class comment plus every attribute comment, as pycgmes renders the
    #: RDFS into a docstring.
    doc: str
    #: AC terminal cardinality the SHACL states, as ``(min, max)``.
    stated_terminals: tuple[int, int] | None
    #: DC terminal cardinality the SHACL states.
    stated_dc_terminals: tuple[int, int] | None
    #: Terminal counts the conformity specimens actually exhibit.
    observed_terminals: frozenset[int]
    profiles: frozenset[str]


def _pycgmes_classes() -> dict[str, Any]:
    """Every pycgmes RESOURCE class, by name.

    The resource package also ships CIM datatypes and enumerations; only
    the ``Base`` subclasses are classes a resource can be an instance of.
    """
    from pycgmes import resources
    from pycgmes.utils.base import Base

    found = {}
    for module in pkgutil.iter_modules(resources.__path__):
        loaded = importlib.import_module(f"pycgmes.resources.{module.name}")
        obj = getattr(loaded, module.name, None)
        if isinstance(obj, type) and issubclass(obj, Base):
            found[module.name] = obj
    return found


def _shacl_terminal_shapes(directory: str) -> dict[str, dict]:
    """``{class: {"ac"|"dc": (min, max)}}`` as the ENTSO-E shapes state it.

    The shapes reach terminals the only way RDF can -- an inverse path
    from ``Terminal.ConductingEquipment`` -- so the count is stated on the
    EQUIPMENT shape and inherited by every ``sh:targetClass`` it names.
    """
    import rdflib
    from rdflib.namespace import RDF, SH

    cim = rdflib.Namespace("http://iec.ch/TC57/CIM100#")
    inverse = {cim["Terminal.ConductingEquipment"]: "ac",
               cim["ACDCConverterDCTerminal.DCConductingEquipment"]: "dc"}
    graph = rdflib.Graph()
    for path in sorted(glob.glob(os.path.join(
            directory, "*-AP-Con-Complex-SHACL.ttl"))):
        graph.parse(path, format="turtle")

    stated: dict[str, dict] = defaultdict(dict)
    for shape in graph.subjects(RDF.type, SH.PropertyShape):
        path = graph.value(shape, SH.path)
        if path is None:
            continue
        side = inverse.get(graph.value(path, SH.inversePath))
        if side is None:
            continue
        low, high = graph.value(shape, SH.minCount), graph.value(
            shape, SH.maxCount)
        if low is None and high is None:
            continue
        bounds = (int(low) if low is not None else 0,
                  int(high) if high is not None else 99)
        for node in graph.subjects(SH.property, shape):
            for target in graph.objects(node, SH.targetClass):
                stated[str(target).rsplit("#", 1)[-1]][side] = bounds
    return dict(stated)


def _observed_terminals(directory: str) -> dict[str, set[int]]:
    """Terminal counts per class as the CGMES conformity specimens draw
    them -- independent of the shapes, and covering classes the shapes say
    nothing about (a ConformLoad has one terminal in every one of them)."""
    from lxml import etree

    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    observed: dict[str, set[int]] = defaultdict(set)
    for path in sorted(glob.glob(os.path.join(directory, "*", "*EQ*.xml"))):
        try:
            root = etree.parse(path).getroot()
        except Exception:  # noqa: BLE001,S112 -- a specimen we cannot read
            continue      # is missing evidence, never a failure of the audit
        types: dict[str, str] = {}
        counts: Counter = Counter()
        for element in root:
            tag = str(element.tag).rsplit("}", 1)[-1]
            about = element.get(f"{rdf}about") or element.get(f"{rdf}ID") or ""
            types[about.lstrip("#_")] = tag
            if tag not in ("Terminal", "ACDCConverterDCTerminal"):
                continue
            for child in element:
                name = str(child.tag).rsplit("}", 1)[-1]
                if name in ("Terminal.ConductingEquipment",
                            "ACDCConverterDCTerminal.DCConductingEquipment"):
                    ref = (child.get(f"{rdf}resource") or "").lstrip("#_")
                    counts[ref] += 1
        for ref, count in counts.items():
            if ref in types:
                observed[types[ref]].add(count)
    return dict(observed)


@lru_cache(maxsize=1)
def cim_catalogue() -> dict[str, CimClass]:
    """Every CGMES class pycgmes declares, with its evidence attached."""
    import dataclasses as dc

    stated = _shacl_terminal_shapes(ENTSOE_SHACL) \
        if os.path.isdir(ENTSOE_SHACL) else {}
    observed = _observed_terminals(CGMES_SPECIMENS) \
        if os.path.isdir(CGMES_SPECIMENS) else {}

    classes = _pycgmes_classes()
    identified = {f.name for f in dc.fields(classes["IdentifiedObject"])}
    catalogue: dict[str, CimClass] = {}
    for name, cls in classes.items():
        profiles = set()
        for field_ in dc.fields(cls):
            if field_.name in identified:
                # `IdentifiedObject.*` is placed in EVERY profile, so
                # counting it would put every class in all ten.
                # A class belongs to a profile when something SUBSTANTIVE
                # of its own lands there.
                continue
            extra = getattr(field_.default, "json_schema_extra", None) or {}
            for profile in extra.get("in_profiles", ()):
                profiles.add(getattr(profile, "name", str(profile)))
        ancestry = tuple(base.__name__ for base in cls.__mro__)
        bounds = _inherited(stated, ancestry)
        catalogue[name] = CimClass(
            name=name, doc=cls.__doc__ or "",
            stated_terminals=bounds.get("ac"),
            stated_dc_terminals=bounds.get("dc"),
            observed_terminals=frozenset(observed.get(name, ())),
            profiles=frozenset(profiles))
    return catalogue


def _inherited(stated: dict[str, dict], ancestry: tuple[str, ...]) -> dict:
    """The nearest ancestor's stated cardinality. A shape written for
    ``Switch`` targets every switch subclass explicitly, but a class the
    shapes miss still inherits the constraint its parent carries."""
    for name in ancestry:
        if name in stated:
            return stated[name]
    return {}


# --------------------------------------------------------------------------
# The PSCAD side
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def master_definitions() -> dict[str, Any]:
    """master.pslx by definition name -- unevaluated, with the form text a
    ComponentDef drops because the extractor has no use for it.

    A view of :func:`pscx.io.master_project`, not a second read of the
    library. The audit wants both views of the same definitions and the
    registry cannot serve this one, but which reader master gets read with
    is not the audit's decision to hold.

    Keyed by name alone, which is narrower than the registry's
    ``(namespace, name)``. Master states one namespace and no name twice,
    so nothing is dropped here today; the narrowing is latent and would
    cost on a library that stated two.
    """
    from pscx.io import master_project

    return {d.name: d for d in master_project().definitions if d.name}


@dataclass(frozen=True)
class DcMap:
    """Where an AC source can reach in one case, and where it cannot."""

    #: Per-phase LIR nodes an AC source reaches through equipment we can
    #: name -- galvanically, across a transformer's coupling, or along a
    #: hosting-wire device.
    ac: frozenset
    #: Nodes it cannot. Empty when the case declares no AC source at all,
    #: because "nothing energises this" and "we found no source" are
    #: different findings and only the first is about the circuit.
    dc: frozenset
    #: ``kind -> placements`` touching BOTH sides. These are converters,
    #: structurally: nothing else can stand between an energised network
    #: and one that no source reaches.
    bridges: dict
    #: ``kind -> placements`` wholly on the unreached side.
    dc_side: dict
    determined: bool


def dc_map(flat, graph=None) -> DcMap:
    """Which nodes an AC source can reach, and which it cannot.

    The rule is topological and names no converter kind, so it finds
    converters nothing has heard of -- which is the whole point, since
    no converter kind is mapped.

    Conduction here is NOT the galvanic-island rule. That one
    asks which nodes share a voltage LEVEL, so it cuts at a transformer
    and pools whatever a converter's own branch rows join -- which is
    exactly the AC/DC boundary, and why an island can pool two voltages.
    This asks the complementary question: what does a source REACH. So a
    transformer couples (it carries power between two AC networks) and a
    component we cannot name does not, because whether it conducts at
    50 Hz is precisely what we do not know about it.

    The consequence to keep in view: the unreached set is an OVER-
    estimate. An unmapped series element in the middle of an AC network
    truncates reachability just as a converter does, and the two are
    indistinguishable until the series element is mapped. It shrinks as
    the mapping grows, which is the direction that makes it useful.
    """
    from pscx.cim import source_is_dc
    from pscx.lir import _phase_pairs, build_lir
    from pscx.rules import (
        DEVICE_KIND_TO_CIM,
        MASTER_KIND_TO_CIM,
        MEASUREMENT_KINDS,
        STRUCTURAL_KINDS,
        ZERO_IMPEDANCE_KINDS,
    )

    nameable = (set(MASTER_KIND_TO_CIM) | set(DEVICE_KIND_TO_CIM)
                | set(MEASUREMENT_KINDS) | set(ZERO_IMPEDANCE_KINDS)
                | set(STRUCTURAL_KINDS))
    if graph is None:
        graph = build_lir(flat)

    dc_sources = {id(comp) for inst in flat.instances
                  for comp in inst.netlist.components
                  if source_is_dc(inst, comp, comp.master_kind or comp.kind)}

    adjacent: dict = defaultdict(set)
    touched: dict = defaultdict(set)
    seeds: set = set()

    def link(a, b) -> None:
        adjacent[a].add(b)
        adjacent[b].add(a)

    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        kind = (comp.master_kind or comp.kind) if comp else None
        if comp is not None:
            for node in (u, v):
                if not graph.nodes[node]["is_ground"]:
                    touched[(kind, id(comp))].add(node)
        if graph.nodes[u]["is_ground"] or graph.nodes[v]["is_ground"]:
            continue
        # a branch with no component is the project's own wiring, not a
        # component whose behaviour is in question
        if comp is None or (kind in nameable
                            and MASTER_KIND_TO_CIM.get(kind)
                            not in ("PowerTransformer",
                                    "ExternalNetworkInjection")):
            link(u, v)

    for (kind, placement), nodes in touched.items():
        role = MASTER_KIND_TO_CIM.get(kind)
        if role == "ExternalNetworkInjection":
            if dc_sources and placement in dc_sources:
                # A source whose own form says DC is not what this sweep
                # means by an AC source, and seeding from one INVERTS the
                # answer: hvdc_fourier's converter comes back with its DC
                # poles reached and its AC terminal unreached, because the
                # only thing energising the reached set is the DC source
                # on the smoothing reactor. The rule is "what an AC source
                # reaches", and this is what makes the seeds mean it.
                continue
            seeds |= nodes
        elif role == "PowerTransformer":
            # a transformer carries power between its windings without
            # conducting between them; for reachability that is a link
            ordered = sorted(nodes, key=str)
            for node in ordered[1:]:
                link(ordered[0], node)

    # a hosting wire's body is the device, not a branch, so its
    # two terminals are joined by nothing the LIR draws
    dims = {node.key: node.dim for node in flat.nodes}
    for inst in flat.instances:
        for dev in inst.netlist.devices:
            if DEVICE_KIND_TO_CIM.get(dev.kind) is None:
                continue
            key_a = flat.flat_node_key(inst, dev.terminal_a)
            key_b = flat.flat_node_key(inst, dev.terminal_b)
            if key_a not in dims or key_b not in dims:
                continue
            for end_a, end_b in _phase_pairs((key_a, None), (key_b, None),
                                             dims):
                link(end_a, end_b)

    reached = set(seeds)
    queue = list(seeds)
    while queue:
        node = queue.pop()
        for other in adjacent[node]:
            if other not in reached:
                reached.add(other)
                queue.append(other)

    every = {n for n in graph.nodes if not graph.nodes[n]["is_ground"]}
    unreached = (every - reached) if seeds else set()
    bridges: Counter = Counter()
    dc_side: Counter = Counter()
    for (kind, _placement), nodes in touched.items():
        if not nodes or not unreached:
            continue
        if nodes & unreached and nodes & reached:
            bridges[kind] += 1
        elif nodes <= unreached:
            dc_side[kind] += 1
    return DcMap(frozenset(reached), frozenset(unreached), dict(bridges),
                 dict(dc_side), bool(seeds))


# --------------------------------------------------------------------------
# Triage
# --------------------------------------------------------------------------
#
# Where each unmapped kind belongs, decided by reading its form.
#
# A kind whose role is topology, such as a transmission line's interface
# stub, is STRUCTURAL: the connectivity graph already expresses it, so it
# belongs in `STRUCTURAL_KINDS` rather than in a mapping.

#: STANDARD        a CGMES class exists; the note says what the mapping
#:                 loses.
#: EMT CLASS       CIM has no class, and the concept is stable and shared
#:                 enough for a typed ``emt:`` class.
#: DETAILED MODEL  the kind is specific to PSCAD and stays a
#:                 ``cim:DetailedModelDynamics`` placement.
#: STRUCTURAL      a topology role the graph already expresses; it belongs
#:                 in `STRUCTURAL_KINDS` and leaves the ``cim_unmapped``
#:                 ranking.
Triage = tuple  # (destination, cim class or None, what it loses)

def _t(destination: str, cim_class: str | None, reason: str) -> Triage:
    """One triage decision. A call rather than a bare tuple so the reasons
    below read as prose instead of as comma-separated fragments."""
    return (destination, cim_class, reason)


TRIAGE: dict[str, Triage] = {
    # ---- converters and their pieces ------------------------------------
    "g6p200_2": _t(
        "STANDARD", "CsConverter",
        "a line-commutated bridge, and CsConverter states exactly its "
        "steady-state quantities -- alpha, gamma, ratedIdc, "
        "numberOfValves, and DP/DN as ACDCConverterDCTerminal.polarity. "
        "What it loses is the model KIND: g6p200_2 is six switching "
        "valves with firing control, CsConverter is one operating point"),
    "peswitch": _t(
        "EMT CLASS", None,
        "a single valve. CIM models the CONVERTER, never its switches, so "
        "no one-to-one class exists, and many placements aggregate into "
        "one VsConverter. Mapping needs an aggregation rule "
        "(which switches form one converter) before it needs a class; the "
        "typed emt: class is what holds a valve until then"),
    "mmc_HalfCell": _t(
        "EMT CLASS", None,
        "an MMC submodule. CGMES 3.0 stops at VsConverter and has no "
        "notion of a cell, a capacitor voltage or a level count; many "
        "cells aggregate into one converter arm"),
    "mmc_FullCell": _t(
        "EMT CLASS", None,
        "as mmc_HalfCell, with a full bridge instead of a half"),
    "dc_mac_2w": _t(
        "EMT CLASS", None,
        "a DC machine. CGMES's rotating machines are AC only "
        "(SynchronousMachine, AsynchronousMachine both RegulatingCondEq "
        "on an AC terminal); a DC machine has no class in any profile"),
    # ---- machines --------------------------------------------------------
    # sync_machine is MAPPED and therefore absent: the triage is the triage
    # of the unmapped ranking, so a kind leaves it by being mapped rather
    # than by keeping an entry that describes work already done. What the
    # decision was lives in the mapping.
    "sqc100": _t(
        "STANDARD", "AsynchronousMachine",
        "squirrel-cage induction, which is what AsynchronousMachine's "
        "converterFedDrive/efficiency/ratedMechanicalPower describe. "
        "Loses the slip-dependent torque model"),
    "wound_rotor": _t(
        "STANDARD", "AsynchronousMachine",
        "loses more than sqc100 does: the rotor is BROUGHT OUT in PSCAD "
        "as extra terminals and CIM's machine has one terminal, so the "
        "rotor circuit has nowhere to attach"),
    "pm_machine": _t(
        "STANDARD", "SynchronousMachine",
        "permanent-magnet excitation is a field CIM does not state; the "
        "terminal behaviour is a synchronous machine's"),
    "spim_uw": _t(
        "STANDARD", "AsynchronousMachine",
        "single-phase induction. Loses the main/auxiliary winding split, "
        "which is the whole of what makes it single-phase"),
    # ---- transformers ----------------------------------------------------
    "xfmr-2w": _t(
        "STANDARD", "PowerTransformer",
        "two ends. What it waits on is a judgment, not an absence: "
        "each winding has TWO external terminals and a "
        "CIM end has one, so the mapping must decide that a winding's two "
        "terminals are one end's terminal plus a neutral"),
    "xfmr-3w": _t(
        "STANDARD", "PowerTransformer",
        "three ends, and the same two-terminals-per-winding judgment "
        "xfmr-2w needs before either can be written"),
    "xfmr-3w2": _t(
        "STANDARD", "PowerTransformer",
        "three ends; the second single-phase 3-winding variant, and the "
        "same judgment again"),
    "xfmr-3p3w": _t(
        "STANDARD", "PowerTransformer",
        "three ends, three phases -- the same shape as the xfmr-3p2w "
        "already mapped, with one more winding"),
    "umec-xfmr-3w": _t(
        "STANDARD", "PowerTransformer",
        "three ends. UMEC is a core model: it changes the magnetising "
        "branch, which the emitted transformer already counts as absent "
        "(`cim_xfmr_no_magnetizing`)"),
    **{name: _t(
        "STANDARD", "PowerTransformer",
        "a duality-based magnetic-circuit transformer. The winding count "
        "is in the kind's own name and reaches eleven, so the terminals "
        "reach 66; CIM takes any number of PowerTransformerEnds, and what "
        "is lost is the magnetic circuit the model exists to state")
       for name in ("duality_3limb3wdg_tf", "duality_3limb4wdg_tf",
                    "duality_3limb5wdg_tf", "duality_3limb7wdg_tf",
                    "duality_3limb8wdg_tf", "duality_3limb11wdg_tf",
                    "duality_5limb3wdg_tf", "duality_5limb4wdg_tf")},
    # ---- passive and shunt ----------------------------------------------
    "varrlc": _t(
        "STANDARD", "EquivalentBranch",
        "the same class the fixed R, L and C already take. What it loses "
        "is the variation itself: CIM states one impedance, and this "
        "component exists to change its own"),
    "rtcbranch": _t(
        "STANDARD", "EquivalentBranch",
        "as varrlc -- runtime-configurable is a variation CIM has no way "
        "to state"),
    "filter-hp": _t(
        "STANDARD", "LinearShuntCompensator",
        "a shunt filter branch: bPerSection and gPerSection carry the C "
        "and the R, nomU the rating. Loses the tuning frequency and the "
        "series inductance, which is what makes it a FILTER rather than a "
        "capacitor bank"),
    "svc": _t(
        "STANDARD", "StaticVarCompensator",
        "which states the operating range (inductiveRating, "
        "capacitiveRating, slope, sVCControlMode) that this form does. "
        "Loses the firing control and the TCR/TSC branches beneath it"),
    "arrester": _t(
        "STANDARD", "SurgeArrester",
        "an AuxiliaryEquipment class that exists and carries nothing but "
        "identity -- so the mapping states WHERE an arrester is and not "
        "one number about it. The nonlinear V-I curve has no home"),
    # ---- lines -----------------------------------------------------------
    "newpi": _t(
        "STANDARD", "ACLineSegment",
        "a PI section IS a line segment with lumped r/x/b, and the form "
        "states them directly. Loses the inter-phase coupling, which "
        "CIM puts on MutualCoupling and we emit nothing from"),
    "pi_section2": _t(
        "STANDARD", "ACLineSegment",
        "as newpi, from per-length values and a length"),
    # ---- injections ------------------------------------------------------
    "src_ccin_1": _t(
        "STANDARD", "EquivalentInjection",
        "an ideal current source. CIM has no current source at all -- "
        "the mapped source classes state a voltage or a regulated "
        "injection -- so the honest match is an injection, which states "
        "P and Q where this states amperes and an angle"),
    "cinj": _t(
        "STANDARD", "EquivalentInjection",
        "harmonic current injection. The fundamental maps as src_ccin_1 "
        "does; the harmonic spectrum, which is the entire point of the "
        "component, has no home in any CGMES profile"),
    "photovoltaic_source": _t(
        "STANDARD", "PhotoVoltaicUnit",
        "with the PowerElectronicsConnection that CGMES requires beside "
        "it. Loses the array physics the form states -- irradiance, cell "
        "temperature, series resistance -- which is what distinguishes "
        "this from a constant injection"),
    # ---- faults ----------------------------------------------------------
    "tpflt": _t(
        "EMT CLASS", None,
        "a fault APPLICATION: a shunt impedance switched in at a stated "
        "time for a stated duration. The EQ profile has no class for one "
        "and should not -- it describes a network, not a study. The "
        "concept is stable and shared by every EMT tool, which is what "
        "earns it a typed class rather than key-value residue"),
    "fault_sw": _t(
        "EMT CLASS", None,
        "single-phase fault; as tpflt, and the same typed class"),
    # ---- topology, not equipment ----------------------------------------
    "tline_interface": _t(
        "STRUCTURAL", None,
        "the stub that joins a canvas to a TLine right-of-way. It is the "
        "same role `xnode` plays across a module boundary, and "
        "the flat graph already expresses it -- so it belongs in "
        "STRUCTURAL_KINDS and should leave the ranking, not gain a class"),
    "cable_interface": _t(
        "STRUCTURAL", None,
        "as tline_interface, for a Cable right-of-way rather than an "
        "overhead one"),
    "Line_Trm": _t(
        "STRUCTURAL", None,
        "a line terminal stub; five terminals and no form at all"),
    "breakout2": _t(
        "STRUCTURAL", None,
        "a 6-phase to twin 3-phase splitter -- the same role as `breakout` "
        "and `breakout3`, which are already structural"),
    "multirate_eni": _t(
        "STRUCTURAL", None,
        "the interface between two solution rates. A solver boundary, "
        "with no electrical behaviour of its own"),
    # ---- tool-specific residue ------------------------------------------
    **{name: _t(
        "DETAILED MODEL", None,
        "a case-local user model with no form, no declared unit and no "
        "name beyond an ordinal. Nothing can be said about it that "
        "its detailed model's descriptors do not already say")
       for name in ("model1", "model2", "model3", "model4", "model5",
                    "model6", "model7", "cow", "test_cblck_two")},
}


def triage() -> dict[str, Triage]:
    """Where each unmapped kind belongs, and why. See :data:`TRIAGE`."""
    return dict(TRIAGE)

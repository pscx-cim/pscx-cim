"""CIM emission: LIR -> pycgmes resources -> per-profile RDF/XML.

The master-def -> CIM class map is a data table; an
electrical component whose kind has no entry is counted loudly, never
silently dropped. The writer is generic: it iterates
``cgmes_attributes_in_profile(profile)`` and emits one rdflib graph per
profile -- no per-class serialization anywhere.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import rdflib
from pycgmes.resources.ACLineSegment import ACLineSegment
from pycgmes.resources.Analog import Analog
from pycgmes.resources.BaseVoltage import BaseVoltage
from pycgmes.resources.Breaker import Breaker
from pycgmes.resources.ConnectivityNode import ConnectivityNode
from pycgmes.resources.Disconnector import Disconnector
from pycgmes.resources.EnergyConsumer import EnergyConsumer
from pycgmes.resources.EquivalentBranch import EquivalentBranch
from pycgmes.resources.EquivalentInjection import EquivalentInjection
from pycgmes.resources.ExternalNetworkInjection import (
    ExternalNetworkInjection,
)
from pycgmes.resources.GeneratingUnit import GeneratingUnit
from pycgmes.resources.GeographicalRegion import GeographicalRegion
from pycgmes.resources.Ground import Ground
from pycgmes.resources.GroundDisconnector import GroundDisconnector
from pycgmes.resources.GroundingImpedance import GroundingImpedance
from pycgmes.resources.Line import Line
from pycgmes.resources.LinearShuntCompensator import LinearShuntCompensator
from pycgmes.resources.LoadResponseCharacteristic import (
    LoadResponseCharacteristic,
)
from pycgmes.resources.PhaseCode import PhaseCode
from pycgmes.resources.PowerTransformer import PowerTransformer
from pycgmes.resources.PowerTransformerEnd import PowerTransformerEnd
from pycgmes.resources.RegulatingControl import RegulatingControl
from pycgmes.resources.RegulatingControlModeKind import (
    RegulatingControlModeKind,
)
from pycgmes.resources.SubGeographicalRegion import SubGeographicalRegion
from pycgmes.resources.Substation import Substation
from pycgmes.resources.SynchronousMachine import SynchronousMachine
from pycgmes.resources.SynchronousMachineKind import SynchronousMachineKind
from pycgmes.resources.SynchronousMachineOperatingMode import (
    SynchronousMachineOperatingMode,
)
from pycgmes.resources.Terminal import Terminal
from pycgmes.resources.TopologicalNode import TopologicalNode
from pycgmes.resources.UnitMultiplier import UnitMultiplier
from pycgmes.resources.UnitSymbol import UnitSymbol
from pycgmes.resources.VoltageLevel import VoltageLevel
from pycgmes.resources.WindingConnection import WindingConnection
from pycgmes.utils.base import Base
from pycgmes.utils.constants import NAMESPACES
from rdflib import RDF, Literal, URIRef

from pscx.cimxml import (
    DEFAULT_MODELING_AUTHORITY_SET,
    DEFAULT_SCENARIO_TIME,
    _uri,
    full_model_header,
    serialize_graph,
)
from pscx.diagnostics import DIAGNOSTICS, Diagnostics, Provenance
from pscx.emission import (
    component_ends,
    device_ends,
    drawn_elements,
    drawn_sides,
    emission_widths,
    paired_phases,
    phase_code,
    placement_ends,
)
from pscx.expr import condition_holds
from pscx.geometry import DisjointSet
from pscx.lineconst import line_impedances
from pscx.lir import build_lir, galvanic_islands, switch_pairs
from pscx.lower import resolve_numeric
from pscx.mrid import MridCollision, mrid
from pscx.rules import (
    CONDUCTION_KINDS,
    DERIVED_KV_TOLERANCE,
    DEVICE_KIND_TO_CIM,
    GROUND_REFERENCE_CLASS,
    GROUND_ROLES,
    GROUNDING_CLASSES,
    LOAD_FORMS,
    MACHINE_FORMS,
    MASTER_KIND_TO_CIM,
    MEASUREMENT_KINDS,
    NUMERIC_FORM_TYPES,
    OUTPUT_QUANTITY,
    SOURCE_CAPABILITY_BOUND_MW,
    SOURCE_FORMS,
    STRUCTURAL_KINDS,
    UNTYPED_OUTPUTS,
    ZERO_IMPEDANCE_KINDS,
    LoadResponse,
    MachineForm,
    MachineRating,
    fixed_load_response,
    ground_role,
    magnetizing_admittance,
    names_a_neutral,
    shunt_admittance,
    source_initial_injection,
    source_short_circuit,
)

if TYPE_CHECKING:
    from pycgmes.utils.profile import BaseProfile

    from pscx.elaborate import FlatProject



def source_is_dc(inst, comp, kind: str) -> bool:
    """Does this source's own form say it is a DC source?

    ``source1`` declares "Source Type: 0 = AC, 1 = DC" and gates its two
    magnitude fields on the answer -- ``Es`` is enabled by
    ``(Ctrl!=2 && ACDC==0)`` and ``Esd`` by ``(Ctrl!=2 && ACDC==1)``. So
    the field is not decoration: it says which of the two numbers on the
    form is the one the case states, and reading the wrong one reports a
    default nobody typed as a stated value.

    EXTERNAL control is deliberately not DC here even when ``ACDC`` says
    so. With ``Ctrl==2`` the magnitude is whatever an input signal
    carries, so the form states no operating point of either kind. A
    DC-typed source used as an arbitrary waveform, such as one phase of a
    three-phase set, is an AC source in every way that matters to
    reachability.
    """
    form = SOURCE_FORMS.get(kind)
    if form is None or form.dc is None:
        return False
    if form.control and _param_numeric(inst, comp, form.control) == 2.0:
        return False
    return _param_numeric(inst, comp, form.dc) == 1.0


def machine_rating(inst, comp, form: MachineForm) -> MachineRating:
    """The nameplate the form STATES, through the selector it states it
    under. Never the inactive field."""
    vbase = _param_numeric(inst, comp, form.line_to_neutral_kv)
    rated_u = vbase * math.sqrt(3.0) if vbase else None

    by_mva = _param_numeric(inst, comp, form.rating_choice)
    if by_mva is None:
        return MachineRating(None, rated_u, "cim_machine_rating_unselected")
    if by_mva:
        rated_s = _param_numeric(inst, comp, form.rated_mva)
    else:
        ibase = _param_numeric(inst, comp, form.line_current_ka)
        # three-phase apparent power from per-phase quantities:
        # 3 * V_L-N [kV] * I_line [kA] = MVA
        rated_s = None if (vbase is None or ibase is None) else \
            3.0 * vbase * ibase

    if rated_s and _param_numeric(inst, comp, form.scale_choice):
        count = _param_numeric(inst, comp, form.coherent_count)
        if count:
            rated_s *= count
    if not rated_s:
        return MachineRating(None, rated_u, "cim_machine_rating_missing")
    return MachineRating(rated_s, rated_u, None)


def live_numeric_parameters(inst, comp) -> tuple[list[dict], list[str]]:
    """Every live, numeric form parameter of one placement, evaluated.

    LIVE is the form's own word for it: a parameter counts only when its
    category ``<cond>`` and its own ``<cond>`` both hold for THIS
    placement's parameter environment -- the same gates that decide
    writer activity, honoured identically whatever type they carry. A
    gated-off field is not data: with ``IorMVA == 0`` a machine form's
    ``MVA`` entry holds the form default 300.0 for machines rated from 2
    to 892 MVA, and reading it blind would state 300 every time. NUMERIC
    is the form's declared type (:data:`pscx.rules.NUMERIC_FORM_TYPES`);
    a Text parameter carries a name, not a quantity.

    Returns ``(stated, unresolved)``. Each stated entry carries the
    parameter's match key, its published spelling, the evaluated value
    VERBATIM in the form's declared unit (never converted
    past it), that unit, the form's own default as the descriptor's
    typicalValue, and the parameter's position in the form, which is the
    order a reader of that form expects. ``unresolved`` names the live,
    numeric-typed parameters whose value is not a number for this
    placement -- an input signal or an unresolvable name drives it --
    so the caller can count each rather than invent a number. The probe
    is suppressed exactly like the detailed-model walk's: failing to
    resolve is an ANSWER here, not a defect to report twice.
    """
    definition = getattr(comp, "definition", None)
    if definition is None:
        return [], []
    stated: list[dict] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for index, parameter in enumerate(definition.form_parameters):
        if parameter.type not in NUMERIC_FORM_TYPES:
            continue
        key = parameter.name.lower()
        if key in seen:
            continue
        if not (condition_holds(parameter.category_condition, comp.params)
                and condition_holds(parameter.condition, comp.params)):
            continue
        seen.add(key)
        with DIAGNOSTICS.suppressed():
            value = _param_numeric(inst, comp, parameter.name)
        if value is None:
            unresolved.append(parameter.name)
            continue
        typical = (definition.defaults.get(parameter.name) or "").strip()
        stated.append({
            "key": key,
            "spelling": parameter.name,
            "value": value,
            "unit": parameter.unit,
            "typical": typical or None,
            "index": index,
        })
    return stated, unresolved


def live_text_parameters(comp) -> list[dict]:
    """Every live, Text-typed form parameter one placement states, verbatim.

    The counterpart of :func:`live_numeric_parameters` for the RECORDING
    kinds (:data:`pscx.rules.RECORDING_KINDS`), whose text is what the
    study records its results as -- a channel's title, group and unit, a
    recorder's file and slot names -- where every other signal block's
    text is a label or a wiring clue the resolved graph already carries.
    LIVE is the same gate: both form ``<cond>`` levels must hold for this
    placement's parameters. The value is the placement's own stated text,
    verbatim and unsubstituted, and a blank states nothing, exactly as the
    lossless-verbatim rule reads a blank.
    """
    definition = getattr(comp, "definition", None)
    if definition is None:
        return []
    stated: list[dict] = []
    seen: set[str] = set()
    lowered = {name.lower(): value for name, value in comp.params.items()}
    for parameter in definition.form_parameters:
        if parameter.type != "Text":
            continue
        key = parameter.name.lower()
        if key in seen:
            continue
        if not (condition_holds(parameter.category_condition, comp.params)
                and condition_holds(parameter.condition, comp.params)):
            continue
        seen.add(key)
        value = lowered.get(key)
        if value is None or not str(value).strip():
            continue
        stated.append({
            "key": key,
            "spelling": parameter.name,
            "value": str(value),
        })
    return stated


def _where(project: str, inst, comp=None, *, element=None) -> Provenance:
    """Provenance for a diagnostic raised past the loaders.

    Elaboration reads a merged view of many files, so there is no single
    source line to point at; the instance path and the element id are what
    identify the thing that produced the finding, and they are exact.

    ``element`` names the identity directly, for a carrier that has no
    element id of its own: a hosted device is a ``<User>`` on a wire and
    the WIRE's id is what identifies it.
    """
    if element is None and comp is not None:
        element = str(comp.element_id)
    return Provenance(case=project, canvas=inst.canvas, instance=inst.path,
                      element=element)


@dataclass
class CimModel:
    """Every CIM resource emitted for one case, its coverage metrics, and
    its diagnostics.

    The two are separate because they answer different questions. A METRIC
    says what the mapping covered (resistors, ammeters, components
    absorbed as detailed models) and is never zero on a real case. A
    DIAGNOSTIC says a value is missing, approximate or wrong, and carries
    a severity. Only the second can have a threshold applied to it.
    """

    project: str
    resources: dict[str, Base] = field(default_factory=dict)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    #: The per-phase LIR this model was projected from.
    #:
    #: Kept so the add-on profile reads the SAME graph rather than
    #: rebuilding one: the two documents describe one network, and
    #: carrying the graph makes that single source structural rather than
    #: a property of two derivations from one FlatProject that nothing
    #: asserts.
    graph: Any = None
    #: Coverage counts, keyed like the diagnostics were: `cim_mapped: kind`.
    metrics: Counter = field(default_factory=Counter)
    #: ConnectivityNode mRID -> the DRAWN node it is, i.e. the flat or
    #: device-internal LIR key the projection mapped onto it. This is the
    #: projection's own record of itself: the add-on profile reads it to
    #: state each node's conductor count, and the reconstruction oracle
    #: reads it to say which drawn node an emitted node ought to be.
    node_keys: dict[str, Any] = field(default_factory=dict)
    #: One record per mapped R/L/C passive, written at the moment its
    #: equipment is emitted: the element's emitted class and mRID, the
    #: seed and name it carries, and the Branch row's (R, L, C) in the
    #: row's own declared units (ohm, H, uF, stated once
    #: in ``pscx.lir.RLC_DECLARED_UNITS``). This is the projection's own
    #: record of itself, like ``node_keys``: the add-on profile reads it
    #: to state the primitives beside the omega-baked attributes, so an
    #: EMT engine can rebuild the element where the case determines no
    #: frequency and x/bPerSection are counted placeholders.
    passives: list = field(default_factory=list)
    #: One record per emitted DRIVING component --
    #: ExternalNetworkInjection, SynchronousMachine, PowerTransformer --
    #: written at the moment its equipment is emitted: the emitted class
    #: and mRID, the seed and name it carries, and every live, numeric form parameter of the
    #: placement (:func:`live_numeric_parameters`), value verbatim in the
    #: form's declared unit. The passive record's pattern applied to the
    #: components that drive the network: what reaches EQ/SSH is the
    #: CGMES load-flow slice, and without this the EMT parameter set --
    #: the source's frequency and internal impedance, the machine's
    #: reactances and time constants -- reaches no interchange document.
    #: Per emitted equipment, so a per-phase-drawn source's several
    #: injections each carry the one form's statement.
    actives: list = field(default_factory=list)
    #: One record per emitted switch whose state is read off a control
    #: signal -- Breaker, and the GroundDisconnector a grounded breaker
    #: becomes -- written at the moment its equipment is emitted: the
    #: emitted mRID and the signal name its own control parameter
    #: states. This is the projection's own record of itself, like
    #: ``passives``: the add-on profile reads it to state the driver
    #: RELATIONSHIP on the signal graph, where the SSH ``open`` literal
    #: states only the outcome of reading the driver at t=0.
    switches: list = field(default_factory=list)
    #: One record per emitted Analog, written at the moment it is
    #: emitted: the add-on profile reads it to join a meter's writer
    #: endpoints on the signal graph to the measurement subject, which
    #: is the sensor half of a closed control loop.
    measurements: list = field(default_factory=list)
    #: mRID -> attribute names the builder explicitly assigned. pycgmes
    #: defaults are indistinguishable from data by VALUE (a genuine
    #: r=0.0 equals the field default), so membership here -- not value
    #: comparison -- decides what the writer emits. pycgmes resources
    #: are pydantic dataclasses without ``model_fields_set``, hence the
    #: explicit ledger.
    explicit: dict[str, set[str]] = field(default_factory=dict)

    def add(self, resource: Base) -> Base:
        existing = self.resources.get(resource.mRID)
        if existing is not None:
            raise MridCollision(
                f"two objects were given mRID {resource.mRID}: "
                f"{type(existing).__name__} {existing.name!r} and "
                f"{type(resource).__name__} {resource.name!r}. "
                f"An mRID is uuid5 over one (project, kind, id) seed -- "
                f"see pscx/mrid.py -- so this is one seed used twice, and "
                f"the fix is a seed that tells the two objects apart."
            )
        self.resources[resource.mRID] = resource
        return resource

    def new(self, cls: type, **attrs: Any) -> Any:
        """Construct, record which attributes were assigned, register."""
        resource = cls(**attrs)
        self.explicit[resource.mRID] = set(attrs)
        return self.add(resource)

    def set(self, resource: Base, name: str, value: Any) -> None:
        """Post-construction assignment that keeps the ledger honest."""
        setattr(resource, name, value)
        self.explicit[resource.mRID].add(name)


def _control_signal_name(comp) -> str | None:
    """The signal name a switch's ``Name`` parameter states, or None.

    For a breaker the parameter is not a label: it names the control
    signal whose writer fixes the t=0 state, resolved against the
    canvas's signal namespace exactly as PSCAD resolves it.
    """
    return next((str(v).strip() for p, v in comp.params.items()
                 if p.lower() == "name" and v and str(v).strip()), None)


def _case_insensitive(values: dict, name: str) -> list[tuple[str, str]]:
    """Every ``(key, stated value)`` of ``values`` whose key is ``name``
    under case-insensitive matching, blanks excluded."""
    return [(key, str(value).strip()) for key, value in values.items()
            if key and key.lower() == name.lower()
            and value and str(value).strip()]


def _stated(values: dict, name: str) -> str | None:
    """The value stated for ``name``, or None.

    Names match case-insensitively, and a placement may spell one
    name two ways. The two keys are one NAME,
    which says nothing about which of two values is the one meant, so
    the rule here has to be stated rather than fall out of dict order:

    an EXACT match wins where the case states one, because that is the
    spelling the caller asked for and no choice arises; otherwise the
    LAST stated wins. That fallback is deterministic and the reason is
    not visible here: ``comp.params`` is ``dict(definition.defaults)``
    with the placement's ``<param>`` elements laid over it, both walked
    in document order, so insertion order is fixed by the two files and
    never by dict hashing.

    The same merge is why an exact match can be the DEFINITION's default
    rather than the placement's value -- the default is already in the
    dict under its own spelling.

    Where two spellings state DIFFERENT values one of them is lost either
    way. That is counted by :func:`_count_param_case_collisions`, once per
    placement, rather than here -- this is called per probe and under
    ``DIAGNOSTICS.suppressed()`` from the detailed-model parameter walk, so a
    finding raised here would be counted many times or not at all.
    """
    matches = _case_insensitive(values, name)
    if not matches:
        return None
    for key, value in matches:
        if key == name:
            return value
    return matches[-1][1]


def _count_param_case_collisions(diagnostics, project: str, inst,
                                 comp) -> None:
    """Count each parameter this placement states twice with two DIFFERENT
    values.

    A property of the placement, so it is counted once per placement and
    at the point the case is read -- not inside :func:`_stated`, which
    runs per probe and is suppressed by the detailed-model parameter walk.

    Two spellings stating the SAME value are not counted: case-insensitive
    matching makes them one name, and there is nothing to choose.
    """
    for lowered in {key.lower() for key in comp.params if key}:
        matches = _case_insensitive(comp.params, lowered)
        if len({value for _key, value in matches}) > 1:
            diagnostics.emit("cim_param_case_collision", lowered,
                             provenance=_where(project, inst, comp))


def _param_numeric(inst, comp, name: str) -> float | None:
    """A component form parameter as a number in its DECLARED unit,
    instance value first, definition default as fallback;
    names match case-insensitively. None = unresolvable.

    A hosted DEVICE has no ComponentDef -- it is a ``<User>`` on a wire
    and its own paramlist is the whole statement -- so there is
    no default to fall back on and no declared unit to convert into. The
    value is then read as written, which is what its own text states.
    """
    defn = getattr(comp, "definition", None)
    raw = _stated(comp.params, name)
    if raw is None and defn is not None:
        raw = _stated(defn.defaults, name)
    if raw is None:
        return None
    unit = defn.units.get(name.lower()) if defn is not None else None
    return resolve_numeric(inst, raw, unit)


def branch_reactance(freq_hz: float, l_h: float, c_f: float) -> float:
    """Phasor reactance of a series R-L-C branch at the system frequency:
    x = 2*pi*f*L - 1/(2*pi*f*C), each term only when present. SI in, SI
    out (ohm)."""
    x = 0.0
    if l_h:
        x += 2.0 * math.pi * freq_hz * l_h
    if c_f:
        x -= 1.0 / (2.0 * math.pi * freq_hz * c_f)
    return x


def system_frequency(flat: FlatProject) -> float | None:
    """The case's single driving frequency, or None.

    PSCAD has no project-level base frequency (the Settings paramlist
    carries only solver/run options); frequency lives on components.
    The driving sources define the network frequency, so this is the
    UNIQUE resolved ``f`` [Hz] over all source components -- several
    sources at different frequencies, or no source at all, yield None
    (the caller keeps its placeholder; 60 Hz is never assumed).
    """
    values = set()
    for inst in flat.instances:
        for comp in inst.netlist.components:
            kind = comp.master_kind or comp.kind
            if MASTER_KIND_TO_CIM.get(kind) != "ExternalNetworkInjection":
                continue
            f = _param_numeric(inst, comp, "f")
            if f is not None and f > 0:
                values.add(f)
    return values.pop() if len(values) == 1 else None


def transformer_phase_nodes(flat, inst, comp, dims) -> dict[int, list] | None:
    """Per-phase LIR endpoints of each winding side of one transformer.

    Winding-side connection nodes come from placed port names: side w is
    the non-ground ports ending in w (one dim-3 port N{w}, or per-phase
    A{w}/B{w}/C{w}); G* ports are neutrals. Returns None when the two
    sides do not resolve to equal phase counts.
    """
    sides: dict[int, list] = {}
    for node in inst.netlist.nodes:
        for c, p in node.ports:
            if c is not comp:
                continue
            pname = (p.name or "").upper()
            if not pname or pname.startswith("G"):
                continue
            if pname[-1] in "12":
                sides.setdefault(int(pname[-1]), []).append(
                    (pname, flat.flat_node_key(inst, node.key)))
    phase_nodes: dict[int, list] = {}
    for w, ports in sides.items():
        ports = sorted(set(ports))
        if len(ports) == 1:
            key = ports[0][1]
            phase_nodes[w] = [(key, ph)
                              for ph in range(1, dims.get(key, 1) + 1)]
        else:
            # per-phase ports: letter order A < B < C is the phase order
            phase_nodes[w] = [(key, 1) for _n, key in ports]
    if sorted(phase_nodes) != [1, 2] or (
            len(phase_nodes[1]) != len(phase_nodes[2])):
        return None
    return phase_nodes


def _node_name(flat_node, key) -> str:
    """The name of one DRAWN node.

    There is no ``:phase`` suffix: an emission node is the node the case
    drew, however many conductors it carries, so a bus named Rbus is
    called Rbus and not Rbus:1/Rbus:2/Rbus:3.
    """
    if flat_node is not None and flat_node.bus_names:
        return "/".join(flat_node.bus_names)
    # the fallback name must be stable across processes: hash() is
    # randomized per interpreter (PYTHONHASHSEED), so derive it from
    # the same repr(key) seed family the mRIDs use
    digest = hashlib.sha256(repr(key).encode()).hexdigest()[:8]
    if flat_node is not None and flat_node.is_ground:
        return "GND"
    return f"N{digest}"


def build_cim(flat: FlatProject) -> CimModel:
    """Map one flattened project onto pycgmes resources (EQ+TP slice)."""
    graph = build_lir(flat)
    project = flat.project or flat.case
    model = CimModel(project=project, graph=graph)
    flat_nodes = {n.key: n for n in flat.nodes}
    # The projection: every per-phase LIR node (key, phase) belongs to the
    # node the case DREW, and `widths` says how many conductors that one
    # drawn node carries. Device-internal nodes have no
    # FlatNode, so the widths are read off the LIR rather than off
    # FlatNode.dim.
    widths = emission_widths(graph)

    base_voltages: dict[float | None, BaseVoltage] = {}

    def base_voltage_for(kv: float | None) -> BaseVoltage:
        if kv not in base_voltages:
            seed = "unknown" if kv is None else repr(kv)
            # nominalVoltage must be strictly positive (301 valueRange);
            # 1.0 kV is a PLACEHOLDER for nodes with no Bus BaseKV, and
            # the diagnostic keeps it from passing as real data.
            bv = model.new(
                BaseVoltage,
                mRID=mrid(project, "BaseVoltage", seed),
                name=f"{kv} kV" if kv is not None else "unknown",
                nominalVoltage=kv if kv is not None else 1.0,
            )
            if kv is None:
                model.diagnostics.emit("cim_basevoltage_unknown")
            base_voltages[kv] = bv
        return base_voltages[kv]

    voltage_levels: dict[float | None, VoltageLevel] = {}

    def voltage_level_for(kv: float | None) -> VoltageLevel:
        """One VoltageLevel per base voltage: Breaker containment must be
        a Bay/VoltageLevel and injection containment a VoltageLevel
        (452/600-2 sequence-path constraints); Substation is not allowed
        for either."""
        if kv not in voltage_levels:
            seed = "unknown" if kv is None else repr(kv)
            voltage_levels[kv] = model.new(
                VoltageLevel,
                mRID=mrid(project, "VoltageLevel", seed),
                name=f"{kv} kV" if kv is not None else "unknown",
                BaseVoltage=base_voltage_for(kv).mRID,
                Substation=equipment_substation().mRID,
            )
        return voltage_levels[kv]

    substation: list[Substation] = []

    def equipment_substation() -> Substation:
        """One per-project Substation: the 452 containment constraint
        requires every EquivalentBranch inside a VoltageLevel, Line or
        Substation, and Substation.Region is mandatory (600-2), so a
        per-project GeographicalRegion/SubGeographicalRegion pair backs
        it."""
        if not substation:
            gr = model.new(
                GeographicalRegion,
                mRID=mrid(project, "GeographicalRegion", "equipment"),
                name=project,
            )
            sgr = model.new(
                SubGeographicalRegion,
                mRID=mrid(project, "SubGeographicalRegion", "equipment"),
                name=project,
                Region=gr.mRID,
            )
            substation.append(model.new(
                Substation,
                mRID=mrid(project, "Substation", "equipment"),
                name=project,
                Region=sgr.mRID,
            ))
        return substation[0]

    # Zero-impedance conduction (series meters, shorts) is expressed in
    # TP: every pair of DRAWN nodes joined by such an edge shares ONE
    # TopologicalNode, so no phantom equipment enters EQ.
    tn_dsu = DisjointSet()
    for u, v, data in graph.edges(data=True):
        comp = data.get("component")
        if comp is None or data["kind"] != "ammeter":
            continue
        kind = comp.master_kind or comp.kind
        if kind in MEASUREMENT_KINDS or kind in ZERO_IMPEDANCE_KINDS:
            tn_dsu.union(u[0], v[0])
    tn_groups: dict[Any, list] = {}
    for key in widths:
        tn_groups.setdefault(tn_dsu.find(key), []).append(key)

    #: placement -> the LIR (key, phase) endpoints its Branch rows touch;
    #: used for component-level attachment (sources, meters)
    comp_ends = component_ends(graph)

    def external_ends(inst, comp) -> list[tuple]:
        """A component's external, non-ground nodes as DRAWN, each with
        the conductors of it the component touches.

        These are the points at which it meets the network (its internal
        EMF/impedance nodes are the device's own network). A
        three-phase source drawn on one dim-3 port yields ONE end whose
        conductors are (1, 2, 3); the same source drawn per phase yields
        three ends of one conductor each.
        """
        by_node: dict[Any, set] = {}
        for key, phase in comp_ends.get((inst.index, id(comp)), ()):
            if key in flat_nodes and not flat_nodes[key].is_ground:
                by_node.setdefault(key, set()).add(phase)
        return [(key, tuple(sorted(by_node[key])))
                for key in sorted(by_node, key=repr)]

    for inst in flat.instances:
        for comp in inst.netlist.components:
            _count_param_case_collisions(model.diagnostics, project, inst,
                                         comp)

    # Placements are resolved once: the base-voltage derivation below and
    # the emission loops further down must see the same nodes.
    source_placements = []
    transformer_placements = []
    load_placements = []
    machine_placements = []
    for inst in flat.instances:
        for comp in inst.netlist.components:
            kind = comp.master_kind or comp.kind
            cim_class = MASTER_KIND_TO_CIM.get(kind)
            if cim_class == "EnergyConsumer":
                load_placements.append(
                    (inst, comp, kind, external_ends(inst, comp)))
            elif cim_class == "ExternalNetworkInjection":
                source_placements.append(
                    (inst, comp, kind, external_ends(inst, comp)))
            elif cim_class == "PowerTransformer":
                transformer_placements.append(
                    (inst, comp, transformer_phase_nodes(flat, inst, comp,
                                                         widths)))
            elif cim_class in ("SynchronousMachine", "AsynchronousMachine"):
                machine_placements.append(
                    (inst, comp, kind, external_ends(inst, comp),
                     machine_rating(inst, comp, MACHINE_FORMS[kind])))

    # BaseVoltage propagates within a galvanic island: nodes reached
    # without crossing a transformer winding, source internals or ground
    # share one voltage level, so a node that declares none of its own
    # inherits the island's UNIQUE declared kv. Ambiguity or absence keeps
    # the placeholder -- nothing is invented.
    kv_dsu = galvanic_islands(graph)

    # A hosting-wire device that maps to a conductor (TLine ->
    # ACLineSegment) conducts at one nominal voltage, so the walk crosses
    # it like any drawn wire. It has no Branch rows and therefore no LIR
    # edges, and a junction where ONLY line ends meet has no port members
    # and no FlatNode -- so the union is stated on the device's terminal
    # keys directly. Ground stays a sink here for the reason it is one in
    # galvanic_islands. The classification is the CIM one, as everywhere
    # an island boundary is decided.
    for inst in flat.instances:
        for dev in inst.netlist.devices:
            if DEVICE_KIND_TO_CIM.get(dev.kind) != "ACLineSegment":
                continue
            key_a = flat.flat_node_key(inst, dev.terminal_a)
            key_b = flat.flat_node_key(inst, dev.terminal_b)
            if any(k in flat_nodes and flat_nodes[k].is_ground
                   for k in (key_a, key_b)):
                continue
            for pa, pb in paired_phases(widths.get(key_a, 1),
                                        widths.get(key_b, 1)):
                kv_dsu.union((key_a, pa), (key_b, pb))

    # FOUR independent witnesses state a node's nominal voltage: a Bus
    # wire's BaseKV, a transformer winding's ratedU, a
    # three-phase source form's base voltage, and a rotating machine's
    # nameplate. All four are phase-to-phase, matching
    # cim:BaseVoltage.nominalVoltage -- the machine's after the sqrt(3)
    # its own form's "Line-to-Neutral" wording asks for.
    #
    # The machine is the first DERIVED witness, and that changes what
    # agreement can mean. Bus BaseKV, a winding's ratedU and a source's
    # base voltage are all numbers a person typed, and a check could
    # demand they match to the last digit. sqrt(3) x 7.967 is
    # 13.799248783901245 against a bus that says 13.8, and 3 x 0.23 x
    # sqrt(3) is 0.39837 against a source that says 0.398: the same
    # nominal voltage, one of them rounded by the person who typed it.
    # So a derived declaration agrees with another when they name the
    # same voltage to DERIVED_KV_TOLERANCE, and exactness still governs
    # every typed pair. The band is nowhere near wide enough to
    # swallow a real conflict such as 477.8 against 539.0 (11%) or 420
    # against 230 (45%).
    node_declared: dict[tuple, dict[float, bool]] = {}
    island_declared: dict[Any, dict[str, set]] = {}

    def declare(lir_node: tuple, witness: str, kv: float, *,
                derived: bool = False) -> None:
        node_declared.setdefault(lir_node, {}).setdefault(kv, derived)
        island_declared.setdefault(kv_dsu.find(lir_node), {}).setdefault(
            witness, set()).add(kv)

    def one_voltage(declarations: dict) -> float | None:
        """The single voltage a set of declarations names, or None.

        ``declarations`` maps kV to whether that value was DERIVED. Two
        values name one voltage when they are equal, or when at least one
        is derived and they agree to the tolerance; the typed one is
        returned, because it is what the case says and the derivation is
        only evidence that it is right.
        """
        if not declarations:
            return None
        if len(declarations) == 1:
            return next(iter(declarations))
        typed = sorted(kv for kv, is_derived in declarations.items()
                       if not is_derived)
        if len(typed) > 1:
            return None
        for kv, kv_derived in declarations.items():
            for other, other_derived in declarations.items():
                if kv == other:
                    continue
                if not (kv_derived or other_derived):
                    return None
                if abs(kv - other) > DERIVED_KV_TOLERANCE * max(abs(kv),
                                                                abs(other)):
                    return None
        return typed[0] if typed else min(declarations)

    for lir_node, attrs in graph.nodes(data=True):
        if attrs["base_kv"] is not None:
            declare(lir_node, "bus", attrs["base_kv"])
    for inst, comp, phase_nodes in transformer_placements:
        if phase_nodes is None:
            continue
        for w in (1, 2):
            rated_u = _param_numeric(inst, comp, f"V{w}")
            if rated_u:
                for lir_node in phase_nodes[w]:
                    if lir_node in graph:
                        declare(lir_node, "transformer", rated_u)
    for inst, _comp, _kind, ends, rating in machine_placements:
        # The machine is a THIRD equipment-side witness beside the
        # transformer and the source, and an independent one: its
        # nameplate is a property of the machine, not of the node.
        if not rating.rated_u:
            continue
        for key, phases in ends:
            for phase in phases:
                declare((key, phase), "machine", rating.rated_u,
                        derived=True)
    for inst, comp, kind, ends in source_placements:
        form = SOURCE_FORMS.get(kind)
        if form is None:
            continue
        base = _param_numeric(inst, comp, form.base)
        if not base:
            continue
        if source_is_dc(inst, comp, kind):
            # A DC source declares no AC nominal voltage, and this is
            # where base voltage would cross the AC/DC boundary if
            # anything let it. The form's `Vm` field is labelled "Rated
            # Volts (AC:L-G, RMS)" and stays enabled under either source
            # type, so it is READABLE on a DC source and means nothing
            # there -- which is precisely the shape of a witness that
            # would put an AC voltage level on a DC node.
            model.diagnostics.emit("cim_basevoltage_source_dc")
            continue
        if not form.line_to_line:
            # A line-to-ground form states no PHASE-TO-PHASE voltage, and
            # NEITHER reading of it is declared. Taking the value as stated
            # and scaling it by sqrt(3) (one phase of a balanced set) each
            # resolve the same few unresolved cases, and neither is
            # corroborated: no island where one of these sources sits
            # beside an independent witness agrees with either reading.
            # The number a consumer received would rest on a picked
            # convention alone, which every other conflict here refuses.
            model.diagnostics.emit("cim_basevoltage_source_single_phase")
            continue
        for key, phases in ends:
            for phase in phases:
                declare((key, phase), "source", base)

    # The cross-check oracle, and the reason to trust the derivation at
    # all: where an island carries two INDEPENDENT witnesses they must
    # agree. Disagreement is real disagreement in the case data (a source
    # set off nominal, or an island over-merged through equipment this
    # mapping does not yet know) and is counted, never resolved by picking.
    derived_witnesses = frozenset({"machine"})

    def island_declarations(by_witness: dict) -> dict:
        return {kv: witness in derived_witnesses
                for witness, kvs in by_witness.items() for kv in kvs}

    for by_witness in island_declared.values():
        if len(by_witness) < 2:
            continue
        values = {kv for kvs in by_witness.values() for kv in kvs}
        if len(values) == 1:
            model.metrics["cim_basevoltage_cross_check_agree"] += 1
        elif one_voltage(island_declarations(by_witness)) is not None:
            # the derived witness confirms a typed one to within its
            # rounding, which is agreement rather than a second opinion
            model.metrics["cim_basevoltage_cross_check_agree_rounded"] += 1
        else:
            model.diagnostics.emit("cim_basevoltage_cross_check_conflict")

    island_kvs = {root: island_declarations(by_witness)
                  for root, by_witness in island_declared.items()}

    node_kv_memo: dict[tuple, float | None] = {}

    def declared_kv(lir_node: tuple) -> float | None:
        """One node's voltage from what it or its island declares."""
        if lir_node not in node_kv_memo:
            kv: float | None = None
            own = node_declared.get(lir_node, {})
            if own:
                kv = one_voltage(own)
                if kv is None:
                    model.diagnostics.emit("cim_basevoltage_node_conflict")
            else:
                kvs = island_kvs.get(kv_dsu.find(lir_node), {})
                if kvs:
                    kv = one_voltage(kvs)
                    if kv is None:
                        model.diagnostics.emit(
                            "cim_basevoltage_island_conflict")
                    else:
                        model.metrics["cim_basevoltage_from_island"] += 1
            node_kv_memo[lir_node] = kv
        return node_kv_memo[lir_node]

    # ---- the equalities the PROFILE mandates, closed over --------------
    # Everything above weighs DECLARATIONS: four witnesses that state a
    # number, which agree or conflict, and where they conflict nothing is
    # invented. This is a different kind of statement and it is applied
    # differently. 452 requires a switch's two ConnectivityNodes to sit at
    # one nominal voltage -- not as evidence about the network but as a
    # property the emitted document must have -- so it is closed over the
    # result rather than voted on beside the witnesses.
    #
    # It invents nothing: an equality between two nodes that neither
    # declares resolves to nothing at all, and where both ends are known
    # and differ the existing counter says so and no winner is picked.
    # What it does is carry a value ACROSS a boundary the island walk
    # cannot: an island holding two declared voltages propagates nothing
    # into its unknown nodes, while a switch inside it still equates its
    # own two ends.
    #
    # The earth is a SINK and never a source, and the asymmetry is the
    # whole of what makes this safe. One flat node is every ground symbol
    # in the project, so a voltage that reached the earth and
    # was then read back out would leak between parts of a case that share
    # nothing else -- which is the measured reason ground breaks island
    # propagation. But the constraint still applies to a ground-side
    # switch, and the earth has no nominal voltage of its own to satisfy
    # it with: its VoltageLevel is a container the profile obliges us to
    # pick, not a statement the case makes. So it is picked from the
    # equipment standing on it -- the unique voltage of the live ends its
    # ground-side switches reach -- and where those disagree it is
    # counted, not chosen.
    inherited_kv: dict[tuple, float] = {}

    def _drawn_is_ground(key) -> bool:
        node = flat_nodes.get(key)
        return node is not None and node.is_ground

    def resolved_kv(lir_node) -> float | None:
        return declared_kv(lir_node) or inherited_kv.get(lir_node)

    equalities = [(u, v) for u, v in switch_pairs(graph)
                  if u in graph and v in graph]
    live_equalities, earth_equalities = [], []
    for end_a, end_b in equalities:
        ground_a = graph.nodes[end_a]["is_ground"]
        ground_b = graph.nodes[end_b]["is_ground"]
        if not (ground_a or ground_b):
            live_equalities.append((end_a, end_b))
        elif ground_a != ground_b:
            earth_equalities.append(
                (end_b, end_a) if ground_a else (end_a, end_b))

    def close_over(pairs) -> None:
        changing = True
        while changing:
            changing = False
            for end_a, end_b in pairs:
                kv_a, kv_b = resolved_kv(end_a), resolved_kv(end_b)
                if kv_a is not None and kv_b is None:
                    inherited_kv[end_b] = kv_a
                    changing = True
                elif kv_b is not None and kv_a is None:
                    inherited_kv[end_a] = kv_b
                    changing = True

    close_over(live_equalities)

    # The earth's own level, then the ground-side switches closed over it.
    # Two ground switches sharing one earth node must be at ONE nominal
    # voltage or neither document conforms -- there is one earth and one
    # container for it -- so where they resolve they must resolve
    # together, and that equality is as mandated as any other. Where they
    # disagree it is counted and the earth keeps the placeholder, which
    # leaves the switches non-conformant and says so.
    earth_kvs = {resolved_kv(live) for live, _earth in earth_equalities}
    earth_kvs.discard(None)
    if len(earth_kvs) > 1:
        model.diagnostics.emit("cim_ground_voltage_conflict")
    elif earth_kvs:
        for _live, earth in earth_equalities:
            inherited_kv.setdefault(earth, next(iter(earth_kvs)))
        close_over(earth_equalities)
        close_over(live_equalities)
    model.metrics["cim_basevoltage_from_switch"] += len(inherited_kv)

    group_kv_memo: dict[Any, float | None] = {}

    def node_kv(key: Any) -> float | None:
        """The voltage of the electrical node containing the drawn node
        ``key``.

        Nodes joined by zero-impedance equipment (series meters, shorts)
        are ONE node -- an equipotential -- so they resolve to one
        voltage, the unique one any member declares, and so do the
        conductors of one drawn node, which are one drawn node. This is
        the single definition every consumer of a node's voltage uses:
        its TopologicalNode, its ConnectivityNode's container, and the
        containment of equipment terminating on it.

        The earth is outside that, for the reason it is outside island
        propagation: one flat node is every ground symbol in
        the project, so a value pooled onto it is a value leaked between
        parts of the case that share nothing but their earth. A bond to
        earth therefore reads as an equipotential in every direction
        except this one -- the ground node's own voltage comes from its
        own declarations, and the live nodes bonded to it do not take
        theirs from each other through it.
        """
        root = tn_dsu.find(key)
        if root not in group_kv_memo:
            members = ([key] if _drawn_is_ground(key)
                       else [member for member in tn_groups.get(root, [key])
                             if not _drawn_is_ground(member)])
            # No membership guard against the LIR: a junction node whose
            # only members are device terminals has no LIR presence, and
            # its voltage is exactly what the device traversal above
            # carries to it. resolved_kv is total either way.
            kvs = {resolved_kv((member, phase))
                   for member in members
                   for phase in range(1, widths.get(member, 1) + 1)} - {None}
            if len(kvs) > 1:
                model.diagnostics.emit("cim_tn_kv_conflict")
            group_kv_memo[root] = kvs.pop() if len(kvs) == 1 else None
        return group_kv_memo[root]

    tns: dict[Any, TopologicalNode] = {}

    def topological_node(key: Any) -> TopologicalNode:
        """One TN per zero-impedance-merged group; identity and name come
        from the repr-least member so singleton groups behave exactly like
        unmerged nodes."""
        root = tn_dsu.find(key)
        if root not in tns:
            head_key = min(tn_groups.get(root, [key]), key=repr)
            kv = node_kv(head_key)
            name = _node_name(flat_nodes.get(head_key), head_key)
            tn = model.new(
                TopologicalNode,
                mRID=mrid(project, "TopologicalNode", repr(head_key)),
                name=name,
            )
            # TopologicalNode.BaseVoltage and .ConnectivityNodeContainer
            # are mandatory in TP (600-2 cardinality); an unknown kv gets
            # the counted 1.0 kV placeholder, never a silent guess.
            # The container must be a VoltageLevel: PowSyBl resolves a
            # node's operating voltage through it and treats any node
            # without one as a BOUNDARY point, aborting the import.
            model.set(tn, "BaseVoltage", base_voltage_for(kv).mRID)
            model.set(tn, "ConnectivityNodeContainer",
                      voltage_level_for(kv).mRID)
            tns[root] = tn
        return tns[root]

    nodes: dict[Any, ConnectivityNode] = {}

    def connectivity_node(key: Any) -> ConnectivityNode:
        """ConnectivityNode for one DRAWN node; its TN is the merged
        group's.

        One symbol, one node: a dim-3 port is one ConnectivityNode
        carrying three conductors, not three ConnectivityNodes.

        Called only for a node something in the emitted model stands on --
        mapped equipment through ``terminals_for``, a detailed model
        through the sweep below -- so a drawn node NEITHER reaches gets
        nothing. That is not an optimisation: an electype-3 port
        legitimately connects to no wire, and a node nothing
        addresses is a node no document should claim exists.
        """
        if key not in nodes:
            name = _node_name(flat_nodes.get(key), key)
            tn = topological_node(key)
            # VoltageLevel containment, like the TN's: a consumer
            # resolves the node voltage through the container
            nodes[key] = model.new(
                ConnectivityNode,
                mRID=mrid(project, "ConnectivityNode", repr(key)), name=name,
                TopologicalNode=tn.mRID,
                ConnectivityNodeContainer=voltage_level_for(
                    node_kv(key)).mRID,
            )
            model.node_keys[nodes[key].mRID] = key
        return nodes[key]

    # Whether a drawn node exists is a property of the NETWORK, not of how
    # far the standard mapping happens to reach. Two populations need an
    # address, and their union is minted here, before any equipment is
    # emitted: the nodes mapped equipment terminates on (which
    # `terminals_for` reaches below) and the nodes a detailed model
    # connects to, which by definition no cim: equipment stands on. Both
    # read `unmapped_components` and `placement_ends`, the same two
    # definitions the add-on profile reads, so the two documents cannot
    # describe different sets of connection points.
    detailed_model_ends = placement_ends(flat, graph)
    for unmapped_inst, unmapped_comp, _unmapped_kind in unmapped_components(
            flat):
        for unmapped_key, _unmapped_phase in detailed_model_ends.get(
                (unmapped_inst.index, id(unmapped_comp)), ()):
            connectivity_node(unmapped_key)
    # A hosted DEVICE standard CIM has no class for is absorbed the same
    # way, so its ends need the same addresses. Its connection points are
    # the hosting wire's synthetic terminal keys, not ports.
    for unmapped_inst, unmapped_dev in unmapped_devices(flat):
        model.metrics[f"cim_unmapped_device: {unmapped_dev.kind}"] += 1
        for unmapped_key, _unmapped_phase in device_ends(
                flat, unmapped_inst, unmapped_dev, widths):
            connectivity_node(unmapped_key)

    #: drawn node -> [(terminal_mrid, equipment_mrid)] for Measurement
    #: anchoring
    terminals_at: dict[Any, list] = {}

    def terminals_for(equipment: Base, seed: str,
                      ends: list[tuple]) -> list[str]:
        """One Terminal per drawn node the equipment touches.

        ``ends`` is ``[(drawn node, conductors touched)]``.
        ``Terminal.phases`` states which conductors, where CIM's
        PhaseCode can: absent means ABC by 301's own reading, and stating
        a non-ABC code on one end of a two-terminal element would make
        the other end's code inconsistent under the same profile's
        consistency rule, so it is emitted only for a drawn three-phase
        bundle.

        On a GROUNDING class the same attribute means something else
        entirely, and 301 says so: for the EarthFaultCompensator family,
        GroundDisconnector and Ground, an absent ``phases`` defaults to
        ``N`` rather than to ABC, and the only value it may carry is
        ``PhaseCode.N``. So this states nothing on those terminals -- an
        ABC there would be a Violation, not merely unhelpful -- and counts
        the case where the drawn node would otherwise have named one.

        An end that lands on the ground node while the equipment is NOT a
        grounding class is counted: that is the two-terminal transcription
        of a drawn shunt, faithful to the drawing and inert in a load flow
        whose reference is the slack bus.
        """
        out = []
        grounding = type(equipment).__name__ in GROUNDING_CLASSES
        for seq, (key, phases) in enumerate(ends, start=1):
            cn = connectivity_node(key)
            node = flat_nodes.get(key)
            if node is not None and node.is_ground and not grounding:
                model.diagnostics.emit(
                    "cim_equipment_terminates_on_ground",
                    type(equipment).__name__)
            # ACDCTerminal.connected is SSH data: the wire exists in the
            # drawing, so every terminal is connected; open equipment is
            # Switch.open state, never a floating terminal. A consumer
            # takes the logical AND of a branch's two terminals'
            # ``connected`` flags to decide its in-service status.
            terminal = model.new(
                Terminal,
                mRID=mrid(project, "Terminal", f"{seed}/{seq}"),
                name=f"{equipment.name}:T{seq}",
                sequenceNumber=seq,
                ConductingEquipment=equipment.mRID,
                ConnectivityNode=cn.mRID,
                connected=True,
            )
            code = phase_code(widths.get(key, 1), phases)
            if grounding:
                if code is not None:
                    # 301 admits only PhaseCode.N here, so the conductor
                    # letters the node WOULD have named cannot be stated
                    model.diagnostics.emit(
                        "cim_grounding_terminal_phases_withheld")
            elif code is None:
                model.diagnostics.emit("cim_terminal_phases_unnameable")
            else:
                model.set(terminal, "phases", getattr(PhaseCode, code))
            terminals_at.setdefault(key, []).append(
                (terminal.mRID, equipment.mRID))
            out.append(terminal.mRID)
        return out

    def kv_for(ends) -> float | None:
        for key in ends:
            kv = node_kv(key)
            if kv is not None:
                return kv
        return None

    def operating_kv(ends) -> float | None:
        """The one voltage a piece of equipment operates at.

        An end whose voltage is unknown neither contradicts nor decides:
        a shunt capacitor's other terminal is ground, which by
        construction carries none. Two DIFFERENT KNOWN voltages
        on non-transformer equipment mean the galvanic island reached
        across something this mapping does not model yet, so the span is
        counted rather than resolved by preferring one end.
        """
        kvs = {node_kv(key) for key in ends} - {None}
        if len(kvs) > 1:
            model.diagnostics.emit("cim_equipment_spans_voltage_levels")
            return kv_for(ends)
        return kvs.pop() if kvs else None

    frequency = system_frequency(flat)

    # ---- SSH state sources ----------------------------------------------
    # A breaker reads the control signal named by its Name param; the
    # WRITER of that net fixes the t=0 state. (inst_index, casefolded
    # name) indexes the flat signal nets exactly the way the per-canvas
    # namespace resolves names.
    nets_by_name: dict[tuple, Any] = {}
    for signal_net in flat.signal_nets:
        for net_inst_idx, net_name in signal_net.names:
            nets_by_name[(net_inst_idx, str(net_name).casefold())] = signal_net

    def breaker_open_state(inst, comp) -> bool | None:
        """t=0 open state from the control-signal driver, or None.

        tbreak is closed at t0 (it opens at TO); tbreakn declares INIT
        (0 = Close, 1 = Open); a const drives the level directly
        (signal 1 = open, matching tbreakn's choice coding). Control
        logic (inv, select, datatap, ...) and Sequencer events would
        need simulation, so they stay undetermined.
        """
        name = _control_signal_name(comp)
        if name is None:
            return None
        net = nets_by_name.get((inst.index, name.casefold()))
        if net is None:
            return None
        drivers = {}
        for entry in net.drivers:
            if len(entry) > 2 and hasattr(entry[2], "kind"):
                drivers[id(entry[2])] = (entry[1], entry[2])
        if len(drivers) != 1:
            return None
        driver_inst, driver = next(iter(drivers.values()))
        driver_kind = driver.master_kind or driver.kind
        if driver_kind == "tbreak":
            return False
        if driver_kind == "tbreakn":
            init = _param_numeric(driver_inst, driver, "INIT")
            return None if init is None else bool(init)
        if driver_kind == "const":
            value = _param_numeric(driver_inst, driver, "Value")
            return None if value is None else bool(value)
        return None

    def element_ends(element) -> list[tuple]:
        return [(element.node_a, element.phases_a),
                (element.node_b, element.phases_b)]

    # ---- the ground reference, and what hangs off it ---------------------
    # PSCAD merges every ground symbol in a project onto ONE flat node
    # (`elaborate.flatten` unions each instance's ground key into
    # FLAT_GROUND), so "the earth" is one node by construction. That is
    # what licenses the reconstruction's reading that a one-terminal
    # shunt's implicit second end IS this node.
    # A second one would break that reading, so it is counted loudly.
    ground_keys = sorted((n.key for n in flat.nodes if n.is_ground), key=repr)
    if len(ground_keys) > 1:
        model.diagnostics.emit("cim_multiple_ground_nodes")
    ground_key = ground_keys[0] if ground_keys else None
    #: set when something is emitted whose earth reference is that node,
    #: including the shunts whose ground side is implicit
    ground_referenced: list[bool] = []

    def on_ground(key) -> bool:
        node = flat_nodes.get(key)
        return node is not None and node.is_ground

    def on_neutral(key) -> bool:
        """Whether a drawn node is a winding star point or machine
        neutral rather than a phase conductor."""
        node = flat_nodes.get(key)
        if node is None:
            return False
        return any(names_a_neutral(c.master_kind or c.kind, p.name)
                   for _i, c, p in node.ports)

    def split_ground(element):
        """``(live end, ground end)`` when exactly one end of a drawn
        element is the ground reference, else None.

        An element with BOTH ends on ground states nothing about the
        network and is not a shunt; it is counted rather than assumed
        away.
        """
        ends = element_ends(element)
        grounded = [end for end in ends if on_ground(end[0])]
        if len(grounded) != 1:
            if grounded:
                model.diagnostics.emit("cim_element_both_ends_ground")
            return None
        live = next(end for end in ends if not on_ground(end[0]))
        return live, grounded[0]

    def emit_ground_element(role: str, element, seed: str, name: str,
                            live_key, live_phases, gnd_key, gnd_phases, *,
                            r_ohm: float, x: float, l_h: float, c_f: float,
                            placeholder: bool) -> None:
        """One R/L/C element drawn to earth, as the class its role names."""
        cim_class = GROUND_ROLES[role].cim_class
        model.metrics[f"cim_ground_role: {role}"] += 1
        kv = node_kv(live_key)
        ground_referenced.append(True)

        if role == "neutral_grounding_impedance":
            # r and x are ShortCircuit data on this class, which is CIM's
            # own statement that a neutral grounding impedance is fault
            # data rather than load-flow data. Two terminals: 301 admits
            # one or two for an EarthFaultCompensator and asks for the
            # second "if there is some kind of topology ... important to
            # model on the ground side", which is exactly what PSCAD's
            # shared ground node is.
            impedance = model.new(
                GroundingImpedance,
                mRID=mrid(project, cim_class, seed),
                name=name,
                normallyInService=True,
                inService=True,
                # 452 confines an EarthFaultCompensator to a VoltageLevel,
                # and 301 then forbids an explicit BaseVoltage on it
                EquipmentContainer=voltage_level_for(kv).mRID,
            )
            model.set(impedance, "r", r_ohm)
            model.set(impedance, "x", x)
            if placeholder:
                model.diagnostics.emit("cim_grounding_placeholder_reactance")
            terminals_for(impedance, seed,
                          [(live_key, live_phases), (gnd_key, gnd_phases)])
            return

        # A shunt admittance. ONE terminal: 301 gives every one-terminal
        # ConductingEquipment leaf class exactly one, and the ground side
        # is the implicit earth that class is defined against.
        g, b = shunt_admittance(r_ohm, x)
        if placeholder:
            model.diagnostics.emit("cim_shunt_placeholder_susceptance")
        shunt = model.new(
            LinearShuntCompensator,
            mRID=mrid(project, cim_class, seed),
            name=name,
            gPerSection=g,
            bPerSection=b,
            # ONE section, and it is not a fabrication: maximumSections=1
            # says the bank cannot be switched to any other level, which
            # is what a fixed device is. A switchable bank would state
            # more.
            maximumSections=1,
            normalSections=1,
            sections=1,
            # the bank's far side IS the ground node, which is what this
            # attribute says of a Yn connection
            grounded=True,
            # nothing regulates it; controlEnabled is mandatory SSH state
            # for a RegulatingCondEq and no RegulatingControl is emitted
            controlEnabled=False,
            normallyInService=True,
            inService=True,
            # 452 confines an EnergyConnection to a VoltageLevel, and 301
            # then forbids an explicit BaseVoltage on it
            EquipmentContainer=voltage_level_for(kv).mRID,
        )
        # nomU is "the voltage at which the nominal reactive power may be
        # calculated", i.e. the voltage the bank is rated against, and a
        # consumer computes the power it draws as nomU^2 * (g - jb). With
        # the base voltage unknown that arithmetic is wrong by the square
        # of the ratio, so the placeholder is counted here as well as at
        # the node: it reaches a NUMBER a solver uses, not just a label.
        model.set(shunt, "nomU", kv if kv is not None else 1.0)
        if kv is None:
            model.diagnostics.emit("cim_shunt_nominal_voltage_placeholder")
        terminals_for(shunt, seed, [(live_key, live_phases)])

    # ---- two-terminal R/L/C -> EquivalentBranch, one per DRAWN element --
    # A resistor on a dim-3 port pair is ONE three-phase branch, not
    # three: the LIR's per-phase edges are the conductors of the one
    # element the case drew.
    rlc_elements = drawn_elements(
        ((u, v, data) for u, v, data in graph.edges(data=True)
         if data["kind"] == "rlc" and data.get("component") is not None
         and MASTER_KIND_TO_CIM.get(data["component"].master_kind)
         == "EquivalentBranch"),
        widths,
        diagnostics=model.diagnostics,
    )
    def record_passive(cim_class: str, inst, comp, seed: str, name: str,
                       branch) -> None:
        """The forward record the add-on profile reads (see
        ``CimModel.passives``): the Branch row's values VERBATIM, in
        the row's own declared units, so the add-on never converts a
        number this loop already converted. Per flattened placement,
        not per drawn element: these are engine data -- an instance's
        ``$()``-parameterized L differs per instance environment --
        where the stated-parameter surface is per drawn element
        because it states source text. The two choices are duals."""
        model.passives.append({
            "kind": comp.master_kind or comp.kind,
            "defn": comp.defn,
            "definition": comp.definition,
            "instance": inst.index,
            "element": comp.element_id,
            "seed": seed,
            "name": name,
            "cim_class": cim_class,
            "mrid": mrid(project, cim_class, seed),
            "values": tuple(0.0 if v is None else v
                            for v in branch.values),
        })

    def record_active(cim_class: str, inst, comp, seed: str, name: str,
                      parameters: list) -> None:
        """The forward record the add-on profile reads for a DRIVING
        component (see ``CimModel.actives``): the live, numeric,
        selector-respected form parameters, values verbatim in the
        form's declared units, so the add-on never converts a number
        this loop already converted. Per emitted equipment, like the
        equipment subjects the statements join."""
        model.actives.append({
            "kind": comp.master_kind or comp.kind,
            "defn": comp.defn,
            "definition": comp.definition,
            "instance": inst.index,
            "element": comp.element_id,
            "seed": seed,
            "name": name,
            "cim_class": cim_class,
            "mrid": mrid(project, cim_class, seed),
            "parameters": parameters,
        })

    def active_parameters(inst, comp) -> list:
        """One placement's live numeric parameters, each live-but-
        unresolvable one counted: the form says the field is active and
        its value is an input signal or an unresolvable name, so the
        engine statement omits it rather than inventing a number."""
        stated, unresolved = live_numeric_parameters(inst, comp)
        for spelling in unresolved:
            model.diagnostics.emit("cim_active_parameter_not_numeric",
                                   spelling,
                                   provenance=_where(project, inst, comp))
        return stated

    for element in rlc_elements:
        comp = element.component
        # the Branch row LABEL discriminates the rows of one component,
        # which is what a per-phase-drawn component has three of. The
        # phase is deliberately NOT in the seed: the object is
        # the drawn element, and its identity cannot depend on an index
        # the LIR introduced.
        seed = f"{element.instance.path}/{comp.element_id}/{element.label}"
        name = f"{comp.kind}_{comp.element_id}"
        data = element.edges[0]
        l_h, c_f = data.get("l_h", 0.0), data.get("c_f", 0.0)
        r_ohm = data.get("r_ohm", 0.0)

        placeholder_reactance = bool(l_h or c_f) and frequency is None
        if placeholder_reactance:
            # the reactance of an L/C branch needs the system frequency,
            # and this case does not determine one; x=0.0 is a
            # PLACEHOLDER, never a silent 60 Hz guess
            x = 0.0
            model.diagnostics.emit("cim_branch_placeholder_reactance")
        else:
            x = branch_reactance(frequency or 0.0, l_h, c_f)

        # A drawn shunt is not a series branch. One end on the ground
        # symbol means the element is referenced to earth, and standard
        # CIM states that with a class of its own -- so the role decides,
        # and only where the element states an impedance at all.
        sides = split_ground(element)
        role = None if sides is None else ground_role(
            comp.master_kind or comp.kind, on_neutral=on_neutral(sides[0][0]),
            r_ohm=r_ohm, l_h=l_h, c_f=c_f)
        if role is not None:
            record_passive(GROUND_ROLES[role].cim_class, element.instance,
                           comp, seed, name, data["branch"])
            (live_key, live_phases), (gnd_key, gnd_phases) = sides
            emit_ground_element(
                role, element, seed, name, live_key, live_phases,
                gnd_key, gnd_phases,
                r_ohm=r_ohm, x=x, l_h=l_h, c_f=c_f,
                placeholder=placeholder_reactance)
            continue
        if sides is not None:
            # the element states no R, L or C at all, so it has no
            # admittance to be re-expressed as; the faithful two-terminal
            # transcription stands and stays counted
            model.diagnostics.emit("cim_shunt_states_no_impedance")

        record_passive("EquivalentBranch", element.instance, comp, seed,
                       name, data["branch"])
        branch = model.new(
            EquivalentBranch,
            mRID=mrid(project, "EquivalentBranch", seed),
            name=name,
            r=data.get("r_ohm", 0.0),
            x=x,
            normallyInService=True,
            inService=True,
            # 452 requires ConductingEquipment.BaseVoltage on every
            # EquivalentBranch, while 301 forbids it on anything inside a
            # VoltageLevel: an equivalent states its own voltage and lives
            # in the bare Substation, never in a level.
            BaseVoltage=base_voltage_for(
                operating_kv([element.node_a, element.node_b])).mRID,
            EquipmentContainer=equipment_substation().mRID,
        )
        terminals_for(branch, seed, element_ends(element))

    # ---- switched branches of breaker components -> Breaker -------------
    breaker_elements = drawn_elements(
        ((u, v, data) for u, v, data in graph.edges(data=True)
         if data["kind"] == "breaker" and data.get("component") is not None
         and MASTER_KIND_TO_CIM.get(
             data["component"].master_kind or data["component"].kind)
         == "Breaker"),
        widths,
        diagnostics=model.diagnostics,
    )
    for element in breaker_elements:
        comp = element.component
        # the Branch row LABEL is the seed's row discriminator, master-script
        # text shared with the passive and conduction seeds. It
        # tells a breaker3's three per-phase rows apart when the case draws
        # them on three scalar nodes; in single-line view the component
        # declares one row and is one Breaker. Coordinates would break
        # identity on every drawing edit, and script ordinals would shift
        # on a master reorder. A master.pslx upgrade that renames a label
        # changes these mRIDs.
        seed = f"{element.instance.path}/{comp.element_id}/{element.label}"
        name = f"{comp.kind}_{comp.element_id}"
        # Switch.open (SSH) comes from the control-signal driver where
        # one determines it; the closed default stays counted otherwise.
        # normalOpen (EQ) keeps the same state -- "normally" IS the
        # case's t=0 configuration. retained: breakers bound
        # TopologicalNodes in bus-branch views. No explicit BaseVoltage:
        # equipment in a VoltageLevel inherits the VL's (a 452 SPARQL
        # constraint forbids carrying both).
        open_state = breaker_open_state(element.instance, comp)
        # A switch whose far end is the ground reference is a
        # GroundDisconnector, which is that class's own definition:
        # isolating a circuit from Ground. It keeps both terminals, since
        # 301 requires exactly two of every Switch -- so unlike a shunt,
        # the class says what the object MEANS without changing what it
        # connects.
        sides = split_ground(element)
        grounded_switch = sides is not None and ground_role(
            comp.master_kind or comp.kind, on_neutral=False,
            r_ohm=0.0, l_h=0.0, c_f=0.0) == "ground_switch"
        if grounded_switch:
            model.metrics["cim_ground_role: ground_switch"] += 1
            ground_referenced.append(True)
        switch_class = GroundDisconnector if grounded_switch else Breaker
        breaker = model.new(
            switch_class,
            mRID=mrid(project, switch_class.__name__, seed),
            name=name,
            normalOpen=bool(open_state),
            open=bool(open_state),
            # locked is mandatory SSH state; PSCAD has no operator-lock
            # concept, so nothing ever locks a breaker
            locked=False,
            retained=True,
            normallyInService=True,
            inService=True,
            EquipmentContainer=voltage_level_for(
                operating_kv([element.node_a, element.node_b])).mRID,
        )
        if open_state is None:
            model.diagnostics.emit("cim_breaker_state_default")
        # the driver RELATIONSHIP travels on the signal graph; the
        # `open` literal above is only the outcome of reading it at t=0
        model.switches.append({
            "kind": comp.master_kind or comp.kind,
            "cim_class": switch_class.__name__,
            "instance": element.instance.index,
            "element": comp.element_id,
            "name": name,
            "signal": _control_signal_name(comp),
            "mrid": breaker.mRID,
        })
        # 452's Switch:connection constraint requires a switch's two
        # ConnectivityNodes to sit in VoltageLevels of the SAME nominal
        # voltage, and it is a Violation rather than a warning. That makes
        # the base-voltage placeholder a CONFORMANCE problem here and not
        # only a precision one: a switch with a resolved voltage at one end
        # and the placeholder at the other states two different nominal
        # voltages, and no container choice for the switch itself repairs
        # it, because the constraint is about the nodes.
        ends_kv = {node_kv(key) for key in (element.node_a, element.node_b)}
        if len(ends_kv) > 1:
            model.diagnostics.emit("cim_switch_ends_differ_in_voltage")
        terminals_for(breaker, seed, element_ends(element))

    # ---- zero-impedance conduction -> a closed switch --------------------
    # A series meter or a `short` conducts with no impedance. Stating that
    # by giving the two drawn nodes one TopologicalNode and no equipment is
    # true and reaches NO consumer: cim2pp builds one bus per
    # ConnectivityNode and PowSyBl's calculated bus view is built over
    # ConnectivityNodes and switch topology, so neither follows the merge.
    # An ELEMENT both of them build is the only statement that
    # reaches either, and cim2pp's switch converter reads exactly Breaker,
    # Disconnector, LoadBreakSwitch and Switch. A closed Disconnector is
    # the least wrong: zero impedance, normally closed, isolating rather
    # than interrupting. cim:Jumper is the semantically exacter class -- a
    # removable zero-impedance link is its own definition -- and it is
    # rejected because cim2pp has no converter for it while PowSyBl maps
    # Jumper and Disconnector to the SAME SwitchKind, so Disconnector
    # dominates it on both consumers at once.
    #
    # The TopologicalNode merge STAYS, and that is what decides `retained`.
    # A TopologicalNode is the set of ConnectivityNodes joined through
    # closed switches, so the merge is what a topology processor would
    # produce from this switch rather than a competing claim, and it is
    # derivable from EQ+SSH. 456's
    # `C:456:TP:Terminal:switch` then decides the flag: the terminals of a
    # RETAINED switch shall not share a TopologicalNode, at Violation
    # severity, and these do by construction. retained=false is the value
    # that makes the pair conformant, and it is also what makes PowSyBl's
    # calculated bus view collapse the two nodes the way the merge says.
    conduction_elements = drawn_elements(
        ((u, v, data) for u, v, data in graph.edges(data=True)
         if data["kind"] == "ammeter" and data.get("component") is not None
         and (data["component"].master_kind or data["component"].kind)
         in CONDUCTION_KINDS),
        widths,
        diagnostics=model.diagnostics,
    )
    for element in conduction_elements:
        comp = element.component
        kind = comp.master_kind or comp.kind
        seed = f"{element.instance.path}/{comp.element_id}/{element.label}"
        name = f"{comp.kind}_{comp.element_id}"
        # A bond whose far end is the earth is a switch to ground, which is
        # GroundDisconnector's own definition -- the same reading that put
        # the ground-side breakers there. It keeps both
        # terminals either way, so what changes is what the object MEANS.
        grounded = split_ground(element) is not None
        switch_class = GroundDisconnector if grounded else Disconnector
        if grounded:
            ground_referenced.append(True)
        model.metrics[f"cim_conduction_element: {kind}"] += 1
        switch = model.new(
            switch_class,
            mRID=mrid(project, switch_class.__name__, seed),
            name=name,
            # zero impedance conducting: closed at t=0 and normally, never
            # locked, and no control signal exists to make it undetermined
            # the way a breaker's is
            normalOpen=False,
            open=False,
            locked=False,
            retained=False,
            normallyInService=True,
            inService=True,
            EquipmentContainer=voltage_level_for(
                operating_kv([element.node_a, element.node_b])).mRID,
        )
        # 452's Switch:connection wants both ends in VoltageLevels of one
        # nominal voltage. These two ends are one equipotential, so
        # `node_kv` resolves them through the same merged group and they
        # cannot differ -- asserted rather than assumed, because a future
        # change to the grouping would break the document silently.
        if len({node_kv(key)
                for key in (element.node_a, element.node_b)}) > 1:
            model.diagnostics.emit("cim_switch_ends_differ_in_voltage")
        terminals_for(switch, seed, element_ends(element))

    # ---- source components -> ExternalNetworkInjection at their
    # external node: the one source class both measured consumers turn
    # into solving equipment -- PowSyBl a regulating Generator, cim2pp
    # an ext_grid routed by referencePriority.
    for inst, comp, kind, ends in source_placements:
        if not ends:
            model.diagnostics.emit("cim_source_unplaced")
            continue
        name_base = next(
            (v.strip() for k, v in comp.params.items()
             if k.lower() == "name" and v and v.strip()),
            f"{comp.kind}_{comp.element_id}",
        )
        # The voltage regulation: the form's OPERATING magnitude, which
        # is a different parameter from its base voltage (SOURCE_FORMS).
        # It exists only when the magnitude is internally controlled and
        # the form states it phase-to-phase, the convention a voltage
        # target read against cim:BaseVoltage.nominalVoltage needs.
        # Every other case is undetermined and attributed to a named
        # reason -- never converted, never invented -- and states no
        # RegulatingControl at all.
        form = SOURCE_FORMS.get(kind)
        vm = ph = None
        reason = None
        if form is None or form.setpoint is None:
            reason = "cim_source_setpoint_not_on_form"
        elif form.control and _param_numeric(inst, comp, form.control):
            reason = "cim_source_setpoint_external"
        elif source_is_dc(inst, comp, kind):
            # a voltage regulation target is a phase-to-phase AC
            # magnitude and a DC source has none. The field the target
            # would come from is the one the form DISABLES under this
            # source type, so reading it would state a number nobody
            # typed. The value the case does
            # state (Esd) has nowhere standard to go.
            reason = "cim_source_setpoint_dc"
        elif not form.line_to_line:
            reason = "cim_source_setpoint_single_phase"
        else:
            vm = _param_numeric(inst, comp, form.setpoint)
            ph = _param_numeric(inst, comp, "Ph")
            if vm is None:
                reason = "cim_source_setpoint_unresolved"
        stated_parameters = active_parameters(inst, comp)
        stated_by_key = {entry["key"]: entry["value"]
                         for entry in stated_parameters}
        # The initial condition, where the form states one live: the
        # per-unit Pinit/Qinit pair on the form's own stated MVA base,
        # in the load convention. A placement drawn per phase emits one
        # injection per end and the form states ONE three-phase
        # injection, so each end carries its balanced share and the
        # network total stays the stated total.
        injection = source_initial_injection(form, stated_by_key)
        if injection is not None and len(ends) > 1:
            injection = tuple(value / len(ends) for value in injection)
        # The stated internal impedance, restated in the IEC 60909
        # network-feeder family (degenerate min = max ranges, each with
        # its recorded inverse). One statement per placement: a
        # per-phase-drawn source's ends each carry the same family,
        # because the form states one impedance.
        short_circuit = source_short_circuit(form, stated_by_key)
        for index, (key, phases) in enumerate(ends, start=1):
            seed = f"{inst.path}/{comp.element_id}/{key!r}"
            name = name_base + (f":{index}" if len(ends) > 1 else "")
            record_active("ExternalNetworkInjection", inst, comp, seed,
                          name, stated_parameters)
            kv = kv_for([key])
            source = model.new(
                ExternalNetworkInjection,
                mRID=mrid(project, "ExternalNetworkInjection", seed),
                name=name,
                normallyInService=True,
                inService=True,
                EquipmentContainer=voltage_level_for(kv).mRID,
            )
            # p/q are mandatory SSH attributes (1..1). Where the form
            # states no live initial condition the injection is a
            # solver outcome, not data -- 0.0 is the profile-mandated
            # PLACEHOLDER for it, counted like every other fabricated
            # value (a power-flow consumer otherwise reads the zero as
            # a real injection)
            if injection is None:
                model.set(source, "p", 0.0)
                model.set(source, "q", 0.0)
                model.diagnostics.emit("cim_source_injection_placeholder")
            else:
                model.set(source, "p", injection[0])
                model.set(source, "q", injection[1])
            # referencePriority is mandatory SSH state and the routing
            # key of cim2pp's ext_grid/gen/sgen split. The case states
            # no priority -- PSCAD has no slack concept -- and 0 would
            # demote every source to a non-reference, so the uniform 1
            # is a consumer-mandated designation, counted like the
            # profile-mandated placeholders are.
            model.set(source, "referencePriority", 1)
            model.diagnostics.emit(
                "cim_source_reference_priority_designated")
            # 600-2 mandates the capability envelope (maxP/minP,
            # maxQ/minQ, 1..1 each) and governorSCD; no PSCAD source
            # form states any of them, so the bounds are the symmetric
            # non-binding sentinel and the frequency bias is zero --
            # fabrications, counted once per injection.
            model.set(source, "governorSCD", 0.0)
            model.set(source, "maxP", SOURCE_CAPABILITY_BOUND_MW)
            model.set(source, "minP", -SOURCE_CAPABILITY_BOUND_MW)
            model.set(source, "maxQ", SOURCE_CAPABILITY_BOUND_MW)
            model.set(source, "minQ", -SOURCE_CAPABILITY_BOUND_MW)
            model.diagnostics.emit("cim_source_capability_placeholder")
            if short_circuit is not None:
                for attribute, value in short_circuit.items():
                    model.set(source, attribute, value)
            terminals = terminals_for(source, seed, [(key, phases)])
            if vm is not None:
                # the regulation package: a RegulatingControl in
                # voltage mode at the source's own terminal, target the
                # live operating magnitude [kV]. discrete and
                # targetValueUnitMultiplier are mandatory SSH state
                # (1..1): the regulation is continuous, and the target
                # is stated in kV, so the multiplier is k over the SI
                # volt.
                control = model.new(
                    RegulatingControl,
                    mRID=mrid(project, "RegulatingControl", seed),
                    name=name,
                    Terminal=terminals[0],
                    enabled=True,
                    discrete=False,
                    targetValue=vm,
                )
                model.set(control, "mode",
                          RegulatingControlModeKind.voltage)
                model.set(control, "targetValueUnitMultiplier",
                          UnitMultiplier.k)
                model.set(source, "RegulatingControl", control.mRID)
                model.set(source, "controlEnabled", True)
                # the 600 TP cross-profile shapes require a terminal a
                # RegulatingControl references to state its own
                # cim:Terminal.TopologicalNode -- the bus-branch address
                # of the regulated point -- so the regulating
                # injection's one terminal carries the association its
                # ConnectivityNode already implies.
                model.set(model.resources[terminals[0]],
                          "TopologicalNode",
                          topological_node(key).mRID)
                if ph is not None:
                    # the stated angle has no home in this vocabulary:
                    # ExternalNetworkInjection regulates a magnitude
                    # and states no phase. The live Ph still travels as
                    # a cim:ParameterValue in the add-on, so the loss
                    # from the standard documents is counted, not
                    # silent.
                    model.diagnostics.emit("cim_source_angle_addon_only")
            else:
                # controlEnabled is mandatory SSH state on every
                # RegulatingCondEq; with no stated operating magnitude
                # there is nothing to regulate to, so no
                # RegulatingControl exists and the flag is False.
                model.set(source, "controlEnabled", False)
                model.diagnostics.emit("cim_source_setpoint_undetermined")
                model.diagnostics.emit(reason)

    # ---- loads -> EnergyConsumer at their external node ------------------
    for inst, comp, kind, ends in load_placements:
        if not ends:
            model.diagnostics.emit("cim_load_unplaced")
            continue
        form = LOAD_FORMS[kind]
        scale = 1.0 if form.scale is None else (
            _param_numeric(inst, comp, form.scale) or 0.0)
        # a form stating power per phase describes the same three-phase
        # load the positive-sequence model wants as a total
        phases = 3.0 if form.per_phase else 1.0

        def power(param: str | None, *, scale=scale, phases=phases,
                  inst=inst, comp=comp) -> float:
            if param is None:
                return 0.0
            return (_param_numeric(inst, comp, param) or 0.0) * scale * phases

        p_mw, q_mvar = power(form.active), power(form.reactive)
        # 456 forbids a negative EnergyConsumer.p/q at sh:Violation
        # severity and names the alternative: a load that INJECTS is an
        # EquivalentInjection. A capacitive PSCAD load states exactly that.
        injects = p_mw < 0.0 or q_mvar < 0.0
        cls = EquivalentInjection if injects else EnergyConsumer
        name_base = next(
            (v.strip() for k, v in comp.params.items()
             if k.lower() == "name" and v and v.strip()),
            f"{comp.kind}_{comp.element_id}",
        )
        stated_parameters = active_parameters(inst, comp)
        response = None
        if kind == "fixed_load":
            response = fixed_load_response(
                {p["key"]: p["value"] for p in stated_parameters})
            if isinstance(response, LoadResponse) and injects:
                response = "an EquivalentInjection has no load response"
            if isinstance(response, str):
                model.diagnostics.emit(
                    "cim_load_response_withheld", response,
                    provenance=_where(project, inst, comp))
        # one characteristic per placement, shared by its ends: it states
        # the form, and a seed from the element id rather than a node key
        # keeps its identity independent of node election order
        characteristic = None
        for index, (key, phases) in enumerate(ends, start=1):
            seed = f"{inst.path}/{comp.element_id}/{key!r}"
            name = name_base + (f":{index}" if len(ends) > 1 else "")
            record_active(cls.__name__, inst, comp, seed, name,
                          stated_parameters)
            kv = node_kv(key)
            p_load, q_load = p_mw, q_mvar
            linked = None
            if isinstance(response, LoadResponse):
                if kv is None:
                    model.diagnostics.emit(
                        "cim_load_response_withheld",
                        "the node has no nominal voltage to state the "
                        "power at",
                        provenance=_where(project, inst, comp))
                else:
                    # CGMES states p and q at the node's nominal voltage,
                    # and the form states them at its own rated voltage
                    ratio = kv / response.rated_kv
                    p_load *= ratio ** response.p_voltage
                    q_load *= ratio ** response.q_voltage
                    if characteristic is None:
                        characteristic = model.new(
                            LoadResponseCharacteristic,
                            mRID=mrid(project, "LoadResponseCharacteristic",
                                      f"{inst.path}/{comp.element_id}"),
                            name=name_base,
                            exponentModel=True,
                            pVoltageExponent=response.p_voltage,
                            qVoltageExponent=response.q_voltage,
                            pFrequencyExponent=response.p_frequency,
                            qFrequencyExponent=response.q_frequency,
                        )
                    linked = characteristic
            load = model.new(
                cls,
                mRID=mrid(project, cls.__name__, seed),
                name=name,
                normallyInService=True,
                inService=True,
                # 452 confines both classes to a VoltageLevel or Line, and
                # 301 then forbids an explicit BaseVoltage
                EquipmentContainer=voltage_level_for(kv).mRID,
            )
            if linked is not None:
                model.set(load, "LoadResponse", linked.mRID)
            model.set(load, "p", p_load)
            model.set(load, "q", q_load)
            if injects:
                # 600-2 makes regulationCapability mandatory (1..1) on
                # EquivalentInjection: it says whether the injection can
                # regulate its local voltage. A PSCAD load is a passive
                # admittance with no controller, so the case determines
                # the value rather than leaving it to be invented.
                model.set(load, "regulationCapability", False)
                model.metrics["cim_load_injects"] += 1
            terminals_for(load, seed, [(key, phases)])

    # ---- rotating machines -> SynchronousMachine -------------------------
    for inst, comp, kind, ends, rating in machine_placements:
        form = MACHINE_FORMS[kind]
        if not ends:
            model.diagnostics.emit("cim_machine_unplaced")
            continue
        if len(ends) > 1:
            # CIM's rotating machine has ONE terminal. A machine drawn per
            # phase would present three stator connections with no way to
            # say they are one machine, so nothing is emitted rather than
            # three machines of the full rating invented.
            model.diagnostics.emit("cim_machine_multiple_stator_ends")
            continue
        key, phases = ends[0]
        if len(phases) != 3:
            # the sqrt(3) in machine_rating assumes a three-phase stator,
            # which is what the form's own "Line-to-Neutral" wording is
            # relative to; anything else is not that machine
            model.diagnostics.emit("cim_machine_stator_not_three_phase")
            continue
        if rating.reason:
            model.diagnostics.emit(rating.reason)
            continue

        name = next(
            (v.strip() for k, v in comp.params.items()
             if k.lower() == "name" and v and v.strip()),
            f"{comp.kind}_{comp.element_id}",
        )
        seed = f"{inst.path}/{comp.element_id}"
        # The initial operating point, and only under the selector that
        # states one: icTyp 0 (None) and 2 (Currents) leave P0/Q0 holding
        # whatever the dialog last had.
        stated = _param_numeric(inst, comp, form.ic_choice) == 1.0
        if stated:
            # the form says "Out +" and CIM's RotatingMachine.p/q use the
            # LOAD sign convention, so generation is NEGATIVE p
            # ``+ 0.0`` normalises the negated zero: -0.0 serializes as
            # "-0.0", which reads as a signed quantity where the case
            # states none.
            p_mw = -(_param_numeric(inst, comp, form.active_mw) or 0.0) + 0.0
            q_mvar = -(_param_numeric(inst, comp,
                                      form.reactive_mvar) or 0.0) + 0.0
        else:
            # the same reading as a voltage source's P/Q: the
            # injection is a solver outcome, not a stated quantity
            p_mw = q_mvar = 0.0
            model.diagnostics.emit("cim_machine_setpoint_undetermined")
        # operatingMode is mandatory 1..1 in SSH and states what the
        # machine is DOING, which the sign of the stated real power says
        # exactly.
        if not stated:
            mode = SynchronousMachineOperatingMode.condenser
            model.diagnostics.emit("cim_machine_operating_mode_default")
        elif p_mw < 0.0:
            mode = SynchronousMachineOperatingMode.generator
        elif p_mw > 0.0:
            mode = SynchronousMachineOperatingMode.motor
        else:
            mode = SynchronousMachineOperatingMode.condenser

        record_active("SynchronousMachine", inst, comp, seed, name,
                      active_parameters(inst, comp))
        machine = model.new(
            SynchronousMachine,
            mRID=mrid(project, "SynchronousMachine", seed),
            name=name,
            ratedS=rating.rated_s,
            ratedU=rating.rated_u,
            p=p_mw, q=q_mvar,
            # PSCAD has no slack concept; 0 is CIM's own way of saying
            # "not used as a reference machine", not a stand-in for one.
            referencePriority=0,
            # nothing regulates it: no RegulatingControl is emitted, and
            # controlEnabled is mandatory SSH state
            controlEnabled=False,
            normallyInService=True,
            inService=True,
            # 452 confines a RegulatingCondEq to a VoltageLevel, and 301
            # then forbids an explicit BaseVoltage on it
            EquipmentContainer=voltage_level_for(node_kv(key)).mRID,
        )
        # Three associations the shapes make OPTIONAL and PSCAD states
        # nothing for. Counted rather than invented:
        #  - ratedPowerFactor is nameplate data for IEC 60909 and the form
        #    has no field for it; P0/Q0 are an initial condition, so
        #    P0/sqrt(P0^2+Q0^2) would be a study's operating point wearing
        #    a nameplate's name.
        # Enums go through set(), never the constructor: pydantic
        # validates a member down to its bare string there and the
        # emitted IRI loses its class (see profile_graph).
        #
        # SynchronousMachine.type is "modes that this machine CAN operate
        # in", a different question from what it is doing now: PSCAD's
        # model is the same equations either way, so all three are
        # available to it and the study's direction is operatingMode.
        model.set(machine, "type",
                  SynchronousMachineKind.generatorOrCondenserOrMotor)
        model.set(machine, "operatingMode", mode)
        model.diagnostics.emit("cim_machine_no_rated_power_factor")
        # A GeneratingUnit is what a CONSUMER needs, which is a different
        # question from what the shapes require, and the two answers
        # differ here: 452 constrains the unit only once it exists, and
        # PowSyBl builds a network with zero generators in it without one,
        # and the machines are silently absent. So it is emitted, and its
        # two mandatory operating limits are the nameplate circle for the
        # same reason minQ/maxQ are:
        # |P| <= ratedS is true of the machine and specific to nothing
        # about it. That also satisfies 452's typeDependency for a
        # generatorOrCondenserOrMotor, which wants minOperatingP below
        # zero and maxOperatingP above it.
        unit = model.new(
            GeneratingUnit,
            mRID=mrid(project, "GeneratingUnit", seed),
            name=f"{name}:unit",
            minOperatingP=-rating.rated_s,
            maxOperatingP=rating.rated_s,
            # SSH makes normalPF mandatory 1..1 and the form states no
            # power factor at all (see cim_machine_no_rated_power_factor)
            normalPF=1.0,
            normallyInService=True,
            inService=True,
            EquipmentContainer=equipment_substation().mRID,
        )
        model.set(machine, "GeneratingUnit", unit.mRID)
        model.diagnostics.emit("cim_machine_operating_limits_from_rating")
        # minQ/maxQ, on the other hand, are NOT optional, and a SHACL run
        # says so rather than only a reading of the shape text: 452's SynchronousMachine-reactiveLimits fires
        # when neither they nor a ReactiveCapabilityCurve is present.
        # The form states no reactive capability -- its X1..X10 / Y1..Y10
        # curve is OPEN-CIRCUIT SATURATION, flux against field current,
        # not the Q(P) capability CIM's curve means, and emitting that as
        # one would state limits the case never gave. So what is written
        # is the bound the NAMEPLATE implies and nothing narrower:
        # |Q| <= ratedS, the apparent-power circle. True of every machine
        # and specific to none, which is why it is counted.
        model.set(machine, "minQ", -rating.rated_s)
        model.set(machine, "maxQ", rating.rated_s)
        model.diagnostics.emit("cim_machine_reactive_limits_from_rating")
        terminals_for(machine, seed, [(key, phases)])

    # ---- transformers -> PowerTransformer + PowerTransformerEnd ---------
    for inst, comp, phase_nodes in transformer_placements:
        if phase_nodes is None:
            model.diagnostics.emit("cim_xfmr_side_unresolved")
            continue
        v_rated = {w: _param_numeric(inst, comp, f"V{w}") for w in (1, 2)}
        tmva = _param_numeric(inst, comp, "Tmva")
        xl = _param_numeric(inst, comp, "Xl")
        cul = _param_numeric(inst, comp, "CuL")
        connection = {}
        for w in (1, 2):
            yd = _param_numeric(inst, comp, f"YD{w}")
            connection[w] = {0.0: WindingConnection.Y,
                             1.0: WindingConnection.D}.get(yd)
        # CIM numbers a transformer's ends from the HIGHEST voltage
        # winding: 301's TransformerEnd.endNumber-unique constraint fires
        # unless the end with the lowest endNumber carries the maximum
        # ratedU. PSCAD's winding order is the form's (V1, V2) and says
        # nothing about which is high, so the two orders have to be kept
        # apart -- `windings[n]` is the PSCAD winding of CIM end n.
        windings = sorted((1, 2), key=lambda w: -(v_rated[w] or 0.0))
        if None in (v_rated[1], v_rated[2], tmva, xl, cul) or not tmva:
            model.diagnostics.emit("cim_xfmr_param_missing")
            z_base = None
        else:
            # The full series impedance sits on end 1 by CIM convention,
            # so its ohmic base is END 1's voltage: Zbase = V^2/S
            # (kV^2/MVA = ohm). Xl and CuL are per-unit on the
            # transformer's own base, which is the same number seen from
            # either winding -- the OHMS are not, which is why the base
            # follows the end the impedance is written on.
            z_base = v_rated[windings[0]] ** 2 / tmva
        name_base = next(
            (v.strip() for k, v in comp.params.items()
             if k.lower() == "name" and v and v.strip()),
            f"{comp.kind}_{comp.element_id}",
        )
        # One PowerTransformer per DRAWN unit. A three-phase two-winding
        # transformer in single-line view puts every phase of a side on
        # one dim-3 port, so it is one transformer; the same component in
        # per-phase view draws three units on three scalar node pairs, so
        # it is three. `View` is a form parameter of the component, and
        # `dim` is that choice made observable.
        sides = {w: drawn_sides(phase_nodes[w]) for w in (1, 2)}
        conductors = {
            w: {key: tuple(sorted(p for k, p in phase_nodes[w] if k == key))
                for key in sides[w]}
            for w in (1, 2)
        }
        if len(sides[1]) != len(sides[2]):
            # one side drawn single-line and the other per phase: which
            # per-phase unit meets which conductor of the bundle is not
            # stated anywhere, so nothing is emitted rather than guessed
            model.diagnostics.emit("cim_xfmr_view_mixed")
            continue
        units = len(sides[1])
        stated_parameters = active_parameters(inst, comp)
        # The magnetizing branch, where the placement's model CONTAINS
        # one: the form states it as Im1/NLL, but the definition's own
        # netlist gates the linear magnetizing branch on the Ideal
        # choice -- under "Ideal Transformer Model: Yes" the branch is
        # out of the equivalent circuit and the saturation component,
        # where enabled, supplies a nonlinear core in its place. So end
        # 1 states the admittance exactly where Ideal says No, the pair
        # is live, and the ohmic base exists; everywhere else b = g =
        # 0.0 stays the counted placeholder below.
        stated_by_key = {entry["key"]: entry["value"]
                         for entry in stated_parameters}
        if (z_base is not None
                and stated_by_key.get("ideal") == 0.0
                and stated_by_key.get("im1") is not None
                and stated_by_key.get("nll") is not None):
            magnetizing = magnetizing_admittance(
                stated_by_key["im1"], stated_by_key["nll"], z_base)
        else:
            magnetizing = None
        for idx in range(units):
            unit_nodes = {w: sides[w][idx] for w in (1, 2)}
            seed = f"{inst.path}/{comp.element_id}/{unit_nodes[1]!r}"
            name = name_base + (f":{idx + 1}" if units > 1 else "")
            record_active("PowerTransformer", inst, comp, seed, name,
                          stated_parameters)
            pt = model.new(
                PowerTransformer,
                mRID=mrid(project, "PowerTransformer", seed),
                name=name,
                normallyInService=True,
                inService=True,
                EquipmentContainer=equipment_substation().mRID,
            )
            # Terminals follow the CIM end order, so Terminal 1 belongs to
            # end 1 and both name the high-voltage winding.
            term_mrids = terminals_for(
                pt, seed,
                [(unit_nodes[w], conductors[w][unit_nodes[w]])
                 for w in windings],
            )
            if magnetizing is None:
                # the model states no linear magnetizing branch:
                # b=g=0.0 is a counted placeholder (600-2 requires b
                # present)
                model.diagnostics.emit("cim_xfmr_no_magnetizing")
            for number, w in enumerate(windings, start=1):
                if z_base is not None and number == 1:
                    # CIM convention: full series impedance on end 1
                    r_ohm, x_ohm = cul * z_base, xl * z_base
                else:
                    r_ohm = x_ohm = 0.0
                # the magnetizing branch follows the same convention:
                # the whole shunt admittance on end 1, zeros elsewhere
                if magnetizing is not None and number == 1:
                    g_siemens, b_siemens = magnetizing
                else:
                    g_siemens = b_siemens = 0.0
                # the mRID and the name follow the PSCAD WINDING, not the
                # CIM end number: a rating edit that changed which side is
                # high would otherwise swap two windings' identities
                end = model.new(
                    PowerTransformerEnd,
                    mRID=mrid(project, "PowerTransformerEnd", f"{seed}/{w}"),
                    name=f"{name}:W{w}",
                    endNumber=number,
                    PowerTransformer=pt.mRID,
                    Terminal=term_mrids[number - 1],
                    BaseVoltage=base_voltage_for(v_rated[w] or None).mRID,
                    ratedU=v_rated[w] or 1.0,
                    r=r_ohm, x=x_ohm, b=b_siemens, g=g_siemens,
                )
                if tmva:
                    model.set(end, "ratedS", tmva)
                if connection[w] is not None:
                    model.set(end, "connectionKind", connection[w])

    # ---- hosting-wire devices (TLine -> ACLineSegment, per phase) -------
    for inst in flat.instances:
        for dev in inst.netlist.devices:
            if DEVICE_KIND_TO_CIM.get(dev.kind) != "ACLineSegment":
                # counted where the detailed-model sweep above gave its
                # ends nodes; a METRIC, because a detailed model carries
                # the device
                continue
            key_a = flat.flat_node_key(inst, dev.terminal_a)
            key_b = flat.flat_node_key(inst, dev.terminal_b)
            wid = dev.terminal_a[1]  # ("terminal", wire_id, "A")
            # 452 containment: every ACLineSegment lives in a cim:Line;
            # one Line per hosting wire groups its per-phase segments
            container = model.new(
                Line,
                mRID=mrid(project, "Line", f"{inst.path}/{wid}"),
                name=dev.name or dev.defn or f"line_{wid}",
            )
            # the line's own RowCanvas right-of-way, solved at its own
            # declared frequency (the case's system frequency only where
            # the line states none)
            impedances = line_impedances(flat, inst, dev, frequency) or {}
            # One ACLineSegment per DRAWN line. The right-of-way's phases
            # are the conductors of the one line the case drew, and this
            # is where the projection is an information GAIN rather than a
            # collapse: r/x/bch plus r0/x0/b0ch express the coupling
            # between those conductors, which three independent
            # positive-sequence segments structurally cannot.
            pairs = paired_phases(widths.get(key_a, 1), widths.get(key_b, 1))
            if not pairs:
                model.diagnostics.emit("cim_line_phase_mismatch")
                continue
            seed = f"{inst.path}/{wid}"
            name = dev.name or dev.defn or f"line_{wid}"
            # The conductors of an ideally transposed right-of-way carry
            # ONE set of constants, which is what makes one segment able
            # to state them all. A spread would mean untransposed
            # asymmetry, which a single positive sequence cannot hold, so
            # it is counted rather than resolved by taking one conductor's
            # values.
            solved = [impedances[phase]
                      for phase in sorted({p for pair in pairs for p in pair})
                      if phase in impedances]
            if len({(i.r, i.x, i.b) for i in solved}) > 1:
                model.diagnostics.emit("cim_line_phase_spread")
            impedance = solved[0] if solved else None
            if impedance is not None and not (impedance.x > 0.0
                                              and impedance.r >= 0.0):
                # the 452 value ranges are r >= 0, x > 0; a derived
                # value outside them is a defect in the case data or
                # the derivation, never something to emit
                model.diagnostics.emit("cim_line_impedance_out_of_range")
                impedance = None
            if impedance is None:
                # no usable right-of-way: r/x/bch are PLACEHOLDERS and
                # announce themselves loudly
                r_ohm, x_ohm, b_siemens = 0.0, 1e-6, 0.0
                model.diagnostics.emit("cim_line_placeholder_impedance")
            else:
                r_ohm = impedance.r
                x_ohm = impedance.x
                b_siemens = impedance.b
            line = model.new(
                ACLineSegment,
                mRID=mrid(project, "ACLineSegment", seed),
                name=name,
                r=r_ohm,
                x=x_ohm,
                bch=b_siemens,
                normallyInService=True,
                inService=True,
                BaseVoltage=base_voltage_for(kv_for([key_a, key_b])).mRID,
                EquipmentContainer=container.mRID,
            )
            if impedance is not None:
                # the real part of the same sequence admittance bch is the
                # imaginary part of; the derivation has it either way and
                # SC makes its zero-sequence twin mandatory
                model.set(line, "gch", impedance.g)
            # Zero sequence is the ShortCircuit profile's, not EQ's, and
            # it exists only where the source states it: a tower geometry
            # yields it from the same Kron reduction as the positive
            # sequence, while a manual entry may leave PSCAD to estimate
            # it from ratios we cannot reproduce.
            if impedance is not None and impedance.x0 is not None:
                model.set(line, "r0", impedance.r0)
                model.set(line, "x0", impedance.x0)
                model.set(line, "b0ch", impedance.b0)
                model.set(line, "g0ch", impedance.g0)
                # SC makes shortCircuitEndTemperature mandatory and PSCAD
                # states nothing about it -- a conductor's permitted
                # end-of-fault temperature is an equipment rating, not
                # circuit data. Real CGMES files carry 0, 75, 80 and 160
                # with no convention between them, so 0.0 is written and
                # counted rather than a plausible number invented.
                model.set(line, "shortCircuitEndTemperature", 0.0)
                model.diagnostics.emit("cim_line_sc_temperature_placeholder")
            else:
                model.diagnostics.emit("cim_line_no_zero_sequence")
            terminals_for(line, seed, [
                (key_a, tuple(sorted({a for a, _b in pairs}))),
                (key_b, tuple(sorted({b for _a, b in pairs}))),
            ])

    # ---- the earth reference itself -> cim:Ground ------------------------
    # This is what gives an implicit ground an ADDRESS. A one-terminal
    # shunt states no second end, and 301 says what that means -- "it is
    # assumed the terminal solidly connects to ground" -- but a document
    # that never says WHICH node is ground leaves the reconstruction
    # nothing to rebuild the shunt's per-phase connection from. A
    # cim:Ground names it, in standard CIM, with no extension needed.
    #
    # Emitted only where something references the node, so a case with a
    # ground symbol nothing hangs off does not gain a ConnectivityNode
    # that no equipment reaches (an electype-3 port legitimately
    # connects to no wire).
    if ground_key is not None and (ground_referenced or ground_key in nodes):
        seed = repr(ground_key)
        ground = model.new(
            Ground,
            mRID=mrid(project, GROUND_REFERENCE_CLASS, seed),
            name="GND",
            normallyInService=True,
            inService=True,
            # 452 admits Bay or VoltageLevel for a Ground, and 301 then
            # forbids an explicit BaseVoltage
            EquipmentContainer=voltage_level_for(node_kv(ground_key)).mRID,
        )
        terminals_for(ground, seed,
                      [(ground_key, tuple(range(
                          1, widths.get(ground_key, 1) + 1)))])
        model.metrics["cim_ground_reference"] += 1

    # ---- meters -> Analog, anchored to a Terminal at their node ---------
    for inst, comp, kind in measurement_placements(flat):
        model.metrics[f"cim_measurement: {kind}"] += 1
        # What this PLACEMENT measures, with the form selector gating
        # each #OUTPUT evaluated against its own parameters. A meter
        # measuring nothing yields an empty list and states nothing;
        # one measuring three yields three.
        written = comp.written_signals
        if not written:
            model.diagnostics.emit("cim_measurement_silent", kind)
            continue
        drawn = {key for key, _phase
                 in comp_ends.get((inst.index, id(comp)), ())}
        if not drawn:
            # non-conducting meters (voltmeter family) sit on their
            # port's node
            for node in inst.netlist.nodes:
                if any(c is comp for c, _p in node.ports):
                    drawn.add(flat.flat_node_key(inst, node.key))
        anchors = sorted(
            anchor for key in sorted(drawn, key=repr)
            for anchor in terminals_at.get(key, ())
        )
        if not anchors:
            # a meter on a node with no emitted equipment terminal
            # cannot be anchored, and an Analog with neither
            # Measurement.Terminal nor .PowerSystemResource carries
            # no information -- suppressed and counted, never
            # emitted bare. Mapping the equipment kinds at such a node
            # resurrects the Analog automatically.
            # Counted per suppressed QUANTITY, so that emitted plus
            # suppressed is an identity over measurements.
            for _param, _signal in written:
                model.diagnostics.emit("cim_measurement_unanchored")
            continue
        terminal_mrid, equipment_mrid = anchors[0]
        # every quantity of one placement anchors at the same terminal
        for param, signal in written:
            quantity = OUTPUT_QUANTITY.get((kind, param))
            if quantity is None:
                # A real measurement whose 452 measurementType entry
                # is not established, or an output this table does not
                # name at all. A guessed entry is a valid document
                # stating the wrong quantity, which no downstream
                # assertion can catch.
                model.diagnostics.emit(
                    "cim_measurement_untyped"
                    if (kind, param) in UNTYPED_OUTPUTS
                    else "cim_measurement_unknown_output",
                    f"{kind}.{param}")
                continue
            measurement_type, symbol = quantity
            # The OUTPUT is part of the seed: one placement states
            # several quantities and they cannot share an mRID.
            seed = f"{inst.path}/{comp.element_id}/{param}"
            analog = model.new(
                Analog,
                mRID=mrid(project, "Analog", seed),
                name=signal,
                measurementType=measurement_type,
                Terminal=terminal_mrid,
                PowerSystemResource=equipment_mrid,
            )
            # Enums go through set(), never the constructor: pydantic
            # validates a member down to its bare value and the writer
            # then emits an IRI that resolves to nothing.
            model.set(analog, "unitSymbol", getattr(UnitSymbol, symbol))
            model.set(analog, "unitMultiplier", UnitMultiplier.none)
            # the signal graph joins this meter's writer endpoints to
            # the measurement subject: the sensor half of a closed loop
            # one record per stated quantity: the signal graph joins
            # every Analog a drawn element states, not just the first
            model.measurements.append({
                "instance": inst.index,
                "element": comp.element_id,
                "mrid": analog.mRID,
            })

    # ---- loud accounting for everything electrical we did NOT map -------
    for inst in flat.instances:
        modules = inst.module_component_ids
        for comp in inst.netlist.components:
            if not comp.electrical_ports:
                continue
            kind = comp.master_kind or comp.kind
            if kind in MASTER_KIND_TO_CIM:
                model.metrics[f"cim_mapped: {kind}"] += 1
            elif kind in ZERO_IMPEDANCE_KINDS and (
                    id(comp) not in modules):
                # conduction expressed by TN merging; counted so the
                # conduction stays visible
                model.metrics[f"cim_zero_impedance: {kind}"] += 1
    for inst, comp, kind in unmapped_components(flat):
        model.diagnostics.emit("cim_unmapped", kind,
                               provenance=_where(project, inst, comp))

    return model


def measurement_placements(flat: FlatProject):
    """``(instance, component, kind)`` for every placed meter, with the
    quantities it measures reachable as ``component.written_signals``.

    One definition, for the reason :func:`absorbed_components` is one: the
    emission, the anchoring rule and the measured-quantity identity have
    to describe the same set of placements, and two selectors that agreed
    by inspection would let the Analogs and the count they reconcile
    against drift apart silently.

    A meter with no electrical port is a plotting or control block rather
    than a measurement point, and a module placement is a page.
    """
    for inst in flat.instances:
        modules = inst.module_component_ids
        for comp in inst.netlist.components:
            kind = comp.master_kind or comp.kind
            if kind not in MEASUREMENT_KINDS or not comp.electrical_ports:
                continue
            if id(comp) in modules:
                continue
            yield inst, comp, kind


def measured_quantities(flat: FlatProject) -> int:
    """Every quantity the project's meters measure, each ``#OUTPUT``'s
    form selectors evaluated against its own placement.

    What an Analog count reconciles against. A count of PLACEMENTS is the
    weaker statement and cannot stand in for it: one Analog per meter
    satisfies that sum whatever the Analog says, so a meter reading
    voltage passes it while stating a current.
    """
    return sum(len(comp.written_signals)
               for _inst, comp, _kind in measurement_placements(flat))


def unmapped_devices(flat: FlatProject):
    """``(instance, device)`` for every hosted device with no standard CIM
    class.

    The device half of :func:`unmapped_components`, and one definition for
    the same reason: build_cim gives each of these ends a
    ConnectivityNode and build_emt puts an ``emt:ModelTerminal`` on it,
    and two definitions that agreed by inspection would let the two
    documents describe different sets.

    Only three classids host a device, so this is exactly the
    Cable and WireBranch population -- stated as "not in the map" rather
    than as a list of the two, so a fourth host kind cannot be absorbed
    silently.
    """
    for inst in flat.instances:
        for dev in inst.netlist.devices:
            if dev.kind not in DEVICE_KIND_TO_CIM:
                yield inst, dev


def absorbed_components(flat: FlatProject):
    """``(instance, component, kind, role)`` for every placed component
    the standard documents do not represent -- one worklist, partitioned
    by what the component puts pins into:

    - ``"electrical"``: an electrical component with no standard CIM
      class. This is the electrical detailed-model population and the
      ``cim_unmapped`` accounting, unchanged.
    - ``"signal"``: no electrical ports, but drawn signal pins or
      ``#OUTPUT`` writer directives -- the signal graph can reference
      it, so the interchange states its type and live parameters.
      The directive test is on the DEFINITION: whether any placement's
      writers are active is per instance, but what has a body is a
      property of the kind.
    - ``"bridge"``: a name bridge (datalabel/import/export). These are
      wiring CLUES the flattened nets already express, the signal-side
      analogue of ``STRUCTURAL_KINDS``.
    - ``"drawing"``: no pin the engine could ever reference --
      annotations, arrows, plot frames. The surface states their text;
      the engine has nothing to join them by.

    One definition on purpose: the detailed-model worklist, the
    ``cim_unmapped`` accounting and the signal-graph anchoring extension
    must all describe the same partition of the same set, or "nothing is
    dropped" means nothing. Module placements are pages, not equipment, and
    stay out of every role. A writer endpoint on one is part of the
    recorded remainder, not a population.
    """
    for inst in flat.instances:
        modules = inst.module_component_ids
        for comp in inst.netlist.components:
            if id(comp) in modules:
                continue
            kind = comp.master_kind or comp.kind
            if comp.electrical_ports:
                if (kind in MASTER_KIND_TO_CIM or kind in STRUCTURAL_KINDS
                        or kind in MEASUREMENT_KINDS
                        or kind in ZERO_IMPEDANCE_KINDS):
                    continue
                yield inst, comp, kind, "electrical"
            elif comp.is_name_bridge:
                yield inst, comp, kind, "bridge"
            elif comp.signal_ports or (comp.definition is not None
                                       and comp.definition.writer_directives):
                yield inst, comp, kind, "signal"
            else:
                yield inst, comp, kind, "drawing"


def unmapped_components(flat: FlatProject):
    """``(instance, component, kind)`` for every placed electrical
    component with no standard CIM class.

    The electrical view of :func:`absorbed_components`. It is the
    detailed-model ingestion worklist and the ``cim_unmapped`` accounting
    at once: the two must describe the same set of components for
    "nothing is dropped" to mean anything, so there is one definition of the set
    rather than two that agree by inspection. Meters (measurements),
    structural roles (the graph itself expresses them), zero-impedance
    conduction (a TopologicalNode merge) and module placements (pages,
    not equipment) are represented already and are not part of it.
    """
    for inst, comp, kind, role in absorbed_components(flat):
        if role == "electrical":
            yield inst, comp, kind


def _attribute_meta(resource: Base) -> dict[str, dict]:
    """``qualname -> json_schema_extra`` over the class's MRO, mirroring
    the qualification rule of ``cgmes_attributes_in_profile``."""
    meta: dict[str, dict] = {}
    seen: set[str] = set()
    for parent in reversed(type(resource).__mro__[:-1]):
        if not dataclasses.is_dataclass(parent):
            continue
        for f in dataclasses.fields(parent):
            if f.name in seen:
                continue
            seen.add(f.name)
            extra = getattr(f.default, "json_schema_extra", None) or {}
            meta[f"{parent.apparent_name()}.{f.name}"] = extra
    return meta


def emit_files(flat: FlatProject, out_dir, *,
               scenario_time: str = DEFAULT_SCENARIO_TIME,
               modeling_authority_set: str = DEFAULT_MODELING_AUTHORITY_SET,
               version: str = "1", add_on: bool = True,
               source: bool = True, project=None) -> list:
    """Write one CIMXML document per profile for a flattened case.

    EQ, TP, SC, SSH, OP and DL are standard CGMES, and all but EQ and DL
    declare DependentOn the EQ model. ``add_on`` also writes the emt:
    extension documents (``--no-add-on`` on the CLI); ``source`` also
    writes the source document among them (``--no-source``), which an
    interchange consumer -- an engine included -- needs nothing from.

    DL is written apart from the loop rather than as a sixth entry in it,
    for two reasons that are both about what DL is. It is the only
    document built from the DRAWING rather than from the flattened model,
    so it is handed the case this function read; and
    ``cim:DiagramObjectPoint`` is not an
    ``IdentifiedObject`` and cannot live in a ``CimModel`` keyed by mRID.
    See :mod:`pscx.dl`.

    SC (ShortCircuit) is where the zero sequence lives: CGMES splits a
    line's sequence data across two profiles, r/x/bch in EQ and
    r0/x0/b0ch in SC, so a model that states the coupling between a
    line's conductors necessarily writes both documents.

    Standard-only output is a real answer, not only a test fixture:
    pandapower cim2pp maps ``md:Model.profile`` to its own profile names
    by substring and raises when none matches, and its ``CIMParser``
    defaults ``ignore_errors`` to False. It is also what proves
    the standard documents are byte-for-byte independent of whether the
    add-on was written.
    """
    from pathlib import Path

    from pycgmes.utils.profile import Profile

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = build_cim(flat)
    # One read, handed to both emitters that want the case rather than the
    # flattened model: DL for the drawing, the add-on for the version and
    # the solver settings. A caller that already read the case hands it in
    # -- ``emit_case`` is that caller -- and only when nobody does is the
    # case read here. A case that will not read is not this function's
    # diagnostic -- whoever flattened it filed that -- so both emitters
    # are handed None and write what they can without it.
    if project is None:
        from pscx.io import read_project

        try:
            project = read_project(flat.case)
        except (OSError, SyntaxError):
            project = None
    eq_model_id = mrid(model.project, "FullModel",
                       f"{Profile.EQ.name}/{version}")
    ssh_model_id = mrid(model.project, "FullModel",
                        f"{Profile.SSH.name}/{version}")
    paths = []
    for profile in (Profile.EQ, Profile.TP, Profile.SC, Profile.SSH,
                    Profile.OP):
        graph = profile_graph(model, profile)
        model_id = mrid(model.project, "FullModel",
                        f"{profile.name}/{version}")
        # 600-1's PROF10 draws the dependency graph and TP hangs off SSH,
        # not off EQ: `PROF10-TP` requires one of a TP header's
        # DependentOn targets to declare the SteadyStateHypothesis
        # profile, at sh:Violation severity, while SSH itself must depend
        # on EQ and on nothing else. Every rule in that family reads the
        # DEPENDENCY's own Model.profile, so none of them can fire against
        # one document, which is why only the merged pass can hold the
        # headers to them.
        depends = {
            Profile.EQ: (),
            Profile.TP: (ssh_model_id,),
        }.get(profile, (eq_model_id,))
        full_model_header(graph, model_id, profile.uris[0],
                          scenario_time, version, depends,
                          modeling_authority_set)
        path = out / f"{model.project}_{profile.name}.xml"
        path.write_bytes(serialize_graph(graph))
        paths.append(path)
    from pscx.dl import emit_dl_file

    paths.append(emit_dl_file(flat, out, scenario_time=scenario_time,
                              modeling_authority_set=modeling_authority_set,
                              version=version, project=project))
    DIAGNOSTICS.extend(model.diagnostics)
    if add_on:
        from pscx.emt import emit_emt_files

        paths.extend(emit_emt_files(
            flat, model, out, eq_model_id=eq_model_id,
            scenario_time=scenario_time,
            modeling_authority_set=modeling_authority_set,
            version=version, project=project, source=source))
    return paths


def emit_case(case_path: str, out_dir, **kwargs) -> list:
    """Read once, flatten, emit: the composition every converter wants.

    ``flatten`` and ``emit_files`` each read the case when nobody hands
    them a project, so calling them back to back parses the same bytes
    twice -- and that second read is the shape every new caller copies.
    This is the caller that holds the one read across both. ``workspaces``
    are opened by ``flatten`` and are not reads of the case. A case that
    will not read is handed on as ``None``: ``flatten`` owns that
    diagnostic, and ``emit_files`` writes what it can without the drawing.
    """
    from pscx.elaborate import flatten
    from pscx.io import read_project

    workspaces = kwargs.pop("workspaces", ())
    try:
        project = read_project(case_path)
    except (OSError, SyntaxError):
        project = None
    flat = flatten(case_path, project, workspaces)
    return emit_files(flat, out_dir, project=project, **kwargs)


def profile_graph(model: CimModel, profile: BaseProfile) -> rdflib.Graph:
    """One profile's RDF graph: every resource contributes exactly the
    attributes pycgmes places in that profile."""
    graph = rdflib.Graph()
    for prefix, ns in NAMESPACES.items():
        graph.bind(prefix, ns)
    for resource in model.resources.values():
        attrs = resource.cgmes_attributes_in_profile(profile)
        meta = _attribute_meta(resource)
        explicit = model.explicit.get(resource.mRID, set())
        triples: list[tuple] = []
        substantive_names: set[str] = set()
        for qualname, entry in attrs.items():
            value = entry["value"]
            if value is None or value == "" or value == []:
                continue
            # Only attributes the builder explicitly assigned are data:
            # a value equal to the pycgmes field default (a genuine
            # r=0.0, aggregate=False) is indistinguishable by value, so
            # the ledger decides. Emitting never-set defaults fabricates
            # data and trips constraints like 301's aggregate-notUsed.
            if qualname.rsplit(".", 1)[-1] not in explicit:
                continue
            extra = meta.get(qualname, {})
            predicate = URIRef(entry["namespace"] + qualname)
            if extra.get("is_class_attribute"):
                obj: rdflib.term.Node = _uri(str(value))
            elif extra.get("is_enum_attribute"):
                # CGMES enum values are IRIs in the class namespace
                # (cim:WindingConnection.Y); str(value) is only the
                # "Class.member" local part.
                #
                # And it is only that when the ENUM MEMBER survived to
                # here. Every pycgmes enum field is annotated ``str``, so
                # passing a member to the CONSTRUCTOR lets pydantic
                # validate it down to its bare value and str() then
                # yields "generator" where the profile's sh:in lists
                # cim:SynchronousMachineKind.generator -- an IRI that
                # resolves to nothing and reads as a typo. Assigning
                # through ``CimModel.set`` after construction keeps the
                # member, which is why every enum in this file is set
                # that way. Raising here is what stops the convention
                # from being a thing to remember: the alternative is a
                # document that validates nowhere and says something
                # subtly false.
                local = str(value)
                if "." not in local:
                    raise ValueError(
                        f"{qualname} holds the bare enum value {local!r}: "
                        f"assign enum members with CimModel.set() after "
                        f"construction, or pydantic strips the class name")
                obj = URIRef(entry["namespace"] + local)
            elif isinstance(value, bool):
                # CIMXML literals are PLAIN (no rdf:datatype), exactly
                # like the conformity specimens: PowSyBl's SPARQL layer
                # compares them as plain strings (?seq = "1"), so a
                # typed "1"^^xsd:integer silently matches NOTHING and
                # equipment vanishes from the imported network.
                # Datatypes are the validator's job (RDFS-driven
                # coercion at load), not the document's.
                obj = Literal("true" if value else "false")
            elif isinstance(value, (int, float)):
                obj = Literal(str(value))
            else:
                obj = Literal(value)
            triples.append((predicate, obj))
            if not qualname.startswith("IdentifiedObject."):
                substantive_names.add(qualname)
        # pycgmes places IdentifiedObject.* in EVERY profile, which would
        # leak name-only shadows of e.g. TopologicalNode into EQ. A
        # resource belongs to a profile only if a substantive attribute
        # lands there, or the profile is the class's own.
        if not (substantive_names or profile == resource.recommended_profile):
            continue
        subject = _uri(resource.mRID)
        # In SSH, equipment whose only updatable attribute is
        # Equipment.inService is serialized as bare cim:Equipment -- the
        # conformity specimens' convention, and the type the 600-2 SSH
        # shapes target. Classes with more SSH state (Breaker,
        # ExternalNetworkInjection) keep their concrete type.
        type_name = resource.apparent_name()
        if (getattr(profile, "name", None) == "SSH"
                and substantive_names == {"Equipment.inService"}):
            type_name = "Equipment"
        graph.add((
            subject, RDF.type,
            URIRef(resource.namespace + type_name),
        ))
        # IdentifiedObject.mRID is a required explicit attribute in
        # CGMES 3.0 instance files; pycgmes never surfaces it via
        # cgmes_attributes_in_profile, so it is emitted here.
        graph.add((
            subject,
            URIRef(NAMESPACES["cim"] + "IdentifiedObject.mRID"),
            Literal(resource.mRID),
        ))
        for predicate, obj in triples:
            graph.add((subject, predicate, obj))
    return graph

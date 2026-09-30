"""The mapping rules: what a PSCAD concept BECOMES in CIM, declared once.

This file is the DECLARATIVE half of the mapping -- the tables and the
pure predicates over them -- separated from the code that applies them so
that a rule can be read, cited and inverted without reading an emitter.
What lives here answers "what does this kind become?"; what lives in
``pscx.cim`` and ``pscx.emt`` answers "how is that written down?".

**And the inverse lives beside the choice that needs it.** ``TWO_TERMINAL``
and ``IMPLICIT_GROUND`` are not emission details: they are what
``emission.reconstruct()`` reads to invert the forward choice, and a
forward choice made to suit a consumer owes a recorded inverse. Keeping
the pair in one file is what makes that obligation discharge-able in the
place the decision is made -- a `short` becomes a
``cim:Disconnector`` because two consumers build one, and the inverse
that says a Disconnector may have been a `short` belongs on the same
page, not in a third file nobody thinks to open.

HARD CONSTRAINT: this module imports the standard library and
``pscx.diagnostics`` and NOTHING else. It must never import ``pycgmes``
or ``rdflib``. ``pscx.diagnostics`` is imported by most of the package
including the whole front end, and ``pscx.cli`` defers the emission
import so that an extraction-only run never pays for the CIM stack; a
rules table that dragged pycgmes in would defeat that for every caller.
``tests/test_rules.py`` asserts the constraint so it cannot rot.
"""

from __future__ import annotations

import math
from typing import NamedTuple

MASTER_KIND_TO_CIM = {
    "resistor": "EquivalentBranch",
    "inductor": "EquivalentBranch",
    "capacitor": "EquivalentBranch",
    "breaker1": "Breaker",
    "fixed_load": "EnergyConsumer",
    "reactive_load": "EnergyConsumer",
    "resistive_load": "EnergyConsumer",
    "breaker3": "Breaker",
    "source1": "ExternalNetworkInjection",
    "source3": "ExternalNetworkInjection",
    "source3R": "ExternalNetworkInjection",
    "source_1": "ExternalNetworkInjection",
    "source_3": "ExternalNetworkInjection",
    "xfmr-3p2w": "PowerTransformer",
    "sync_machine": "SynchronousMachine",
}

#: Hosting-wire device kind -> CIM class (the wire body IS the
#: device).
DEVICE_KIND_TO_CIM = {
    "TLine": "ACLineSegment",
}

#: Electrical kinds that are topology roles rather than equipment: they
#: are represented by the graph itself, so no diagnostic is warranted.
STRUCTURAL_KINDS = frozenset({"ground", "xnode", "pin", "nodeloop", "breakout",
                              "breakout3"})

#: Meters are measurement points, never ConductingEquipment -- mapping
#: them as series elements would inject phantom branches into the
#: network. Key = the master kind and the ``#OUTPUT`` parameter that
#: writes the quantity; value = the 452 OP measurementType enum entry and
#: the UnitSymbol that entry is measured in.
#:
#: Keyed on the OUTPUT rather than on the kind, because what a meter
#: measures is a property of its active outputs and not of its type. A
#: `multimeter` declares seven outputs, each gated by its own form
#: selector, and a `nodelabel`'s single one is gated by `MeasV`. A
#: kind-keyed table can only state one fixed quantity per meter, which is
#: true of the three single-output forms and false of the other two.
#:
#: The emission reads this through ``Component.written_signals``, which
#: evaluates those selectors against the placement's own parameters. A
#: meter measuring nothing therefore states nothing, and a meter measuring
#: three states three, without either being a case the emitter tests for.
#:
#: 600-2 makes Measurement.unitSymbol and .unitMultiplier mandatory
#: (1..1), and 452 then restricts an Analog's symbol to W/deg/VA/A/VAr/V/Hz
#: -- so the symbol is not free text but the one entry of that set the
#: measurementType names. The multiplier is `none` throughout: an OP
#: document states what is measured, never a value, so the only
#: multiplier that scales nothing is the unit itself.
OUTPUT_QUANTITY = {
    ("ammeter", "Name"): ("LineCurrent", "A"),
    ("voltmeter", "Name"): ("Voltage", "V"),
    ("voltmetergnd", "Name"): ("Voltage", "V"),
    ("nodelabel", "VName"): ("Voltage", "V"),
    ("multimeter", "CurI"): ("LineCurrent", "A"),
    ("multimeter", "Crms"): ("LineCurrent", "A"),
    ("multimeter", "VolI"): ("Voltage", "V"),
    ("multimeter", "VolILL"): ("Voltage", "V"),
    ("multimeter", "Vrms"): ("Voltage", "V"),
}

#: Meter outputs whose quantity is real but whose 452 measurementType
#: entry is not yet established: active power, reactive power and phase
#: angle. 452 enumerates the permitted entries in its OP constraint file
#: and the name must be read off that enumeration, not inferred from the
#: quantity, so these state a diagnostic instead of a guessed Analog.
#: ``UnitSymbol.W``, ``.VAr`` and ``.deg`` all exist, so only the
#: measurementType side is open.
UNTYPED_OUTPUTS = frozenset({
    ("multimeter", "P"),
    ("multimeter", "Q"),
    ("multimeter", "Ph"),
})

#: The kinds that are measurement points. DERIVED from the quantity table
#: so the "is this equipment" question and the "what does it measure"
#: question cannot answer differently: there is one table, not two that
#: agree by inspection.
MEASUREMENT_KINDS = frozenset(
    kind for kind, _param in set(OUTPUT_QUANTITY) | UNTYPED_OUTPUTS)

#: Kinds whose only electrical role is an ideal zero-impedance
#: connection: expressed in TP as a shared TopologicalNode, not as
#: equipment.
ZERO_IMPEDANCE_KINDS = frozenset({"short"})

#: Kinds that CONDUCT with no impedance, whichever else they also are. A
#: series meter is a measurement point AND a piece of wire; the drawing
#: says so by putting its two ports on two different nodes, and a
#: consumer that does not build an element there builds a network the case
#: does not describe. Membership is not what selects them: the
#: SELECTOR is a Branch row of kind `ammeter` between two drawn nodes, so
#: the voltmeter family drops out on its own -- those forms declare no
#: conducting row at all -- and only ammeter, multimeter and short reach
#: the emission.
CONDUCTION_KINDS = frozenset(MEASUREMENT_KINDS) | ZERO_IMPEDANCE_KINDS

#: Rotating-machine kinds that bring a stator or rotor NEUTRAL out as its
#: own port. A machine's neutral is named N or NN; every other electrical
#: port of one is a phase terminal (the same two ports form the
#: optional-neutral arm).
MACHINE_NEUTRAL_KINDS = frozenset({"sync_machine", "wound_rotor", "sqc100",
                                   "pm_machine", "spim_uw"})


class GroundRole(NamedTuple):
    """One role an element drawn to PSCAD's ground symbol plays, the CIM
    class that states it, and why that class rather than a neighbouring
    one."""

    cim_class: str
    reason: str


#: What an element drawn between a live node and the ground symbol IS, by
#: role, and the roles are the point. A two-terminal EquivalentBranch to
#: the ground node would be faithful to the drawing and inert in a load
#: flow, whose reference is the slack. One class for all of them
#: would be the same category error at a different address, because these
#: are three different devices:
#:
#: * a NEUTRAL grounding impedance carries no current in balanced
#:   operation and its impedance is short-circuit data -- which is
#:   exactly where CGMES puts ``EarthFaultCompensator.r``, in SC and not
#:   in EQ;
#: * a shunt ADMITTANCE on a phase conductor draws current continuously
#:   and belongs in EQ, where a load-flow consumer reads it;
#: * a ground-side SWITCH conducts nothing itself and states a state.
#:
#: The drawing distinguishes the first two by which node the element
#: hangs off -- a winding star point or a phase conductor -- so this is a
#: reading of the case rather than a preference.
GROUND_ROLES = {
    "shunt_resistor": GroundRole(
        "LinearShuntCompensator",
        "a fixed resistance from a phase conductor to earth: a discharge, "
        "damping or numerical-grounding path. Its conductance is what a "
        "load flow needs, so it goes in EQ as gPerSection with "
        "bPerSection zero"),
    "shunt_reactor": GroundRole(
        "LinearShuntCompensator",
        "a fixed inductance from a phase conductor to earth -- a shunt "
        "reactor, whose susceptance is NEGATIVE (-1/wL) in the sign "
        "convention bPerSection uses"),
    "shunt_capacitor": GroundRole(
        "LinearShuntCompensator",
        "a fixed capacitance from a phase conductor to earth -- a shunt "
        "capacitor bank, susceptance +wC. NOT a switchable bank: PSCAD's "
        "capacitor form states one capacitance and no section count, so "
        "maximumSections is 1 and no switching capability is invented"),
    "neutral_grounding_impedance": GroundRole(
        "GroundingImpedance",
        "an impedance between a transformer winding's star point (or a "
        "machine's brought-out neutral) and earth. A neutral grounding "
        "resistor is not a shunt admittance: it carries no current when "
        "the phases are balanced, and CIM agrees -- EarthFaultCompensator "
        "r/x are ShortCircuit data, not EQ"),
    "ground_switch": GroundRole(
        "GroundDisconnector",
        "a switch whose far end is the ground reference, which is the "
        "class's own definition -- isolating a circuit from Ground. It "
        "keeps TWO terminals, because 301 requires exactly two of every "
        "Switch"),
}

#: The class that states the earth reference itself, one per case: it is
#: what gives a one-terminal shunt's implicit second end an address (see
#: :func:`pscx.emission.reconstruct`).
GROUND_REFERENCE_CLASS = "Ground"

#: Roles whose class carries ONE terminal, the ground side being implicit.
#: 301's ``EarthFaultCompensator:numberOfTerminals`` states the semantics
#: for the whole family in its own words: "If the second terminal ... is
#: omitted, it is assumed the terminal solidly connects to ground."
IMPLICIT_GROUND_ROLES = frozenset({"shunt_resistor", "shunt_reactor",
                                   "shunt_capacitor"})

#: Emitted classes 301 treats as grounding-related, whose terminals it
#: constrains to ``PhaseCode.N`` alone -- so a three-conductor drawn node
#: cannot be named ABC on one of them, which is the same boundary
#: the ABC-or-nothing phase rule meets from the other side.
GROUNDING_CLASSES = frozenset({"Ground", "GroundDisconnector",
                               "GroundingImpedance"})


def names_a_neutral(kind: str, port_name: str | None) -> bool:
    """Whether a port of ``kind`` is a winding or stator NEUTRAL.

    A transformer form names its winding star points ``G{w}`` -- the same
    convention :func:`transformer_phase_nodes` reads to exclude them from
    the phase terminals -- and a rotating machine brings its neutral out
    as ``N``/``NN``. Everything else is a phase terminal.
    """
    upper = (port_name or "").upper()
    if not upper:
        return False
    if "XFMR" in kind.upper() or kind.startswith("duality_"):
        return upper.startswith("G")
    if kind in MACHINE_NEUTRAL_KINDS:
        return upper in ("N", "NN")
    return False


def ground_role(kind: str, *, on_neutral: bool, r_ohm: float, l_h: float,
                c_f: float) -> str | None:
    """The role of one element drawn between a live node and ground, or
    None when it states no impedance at all.

    ``on_neutral`` is the discriminator that decides between two genuinely
    different devices, and the DRAWING supplies it: an element hanging off
    a winding star point is a neutral grounding impedance, one hanging off
    a phase conductor is a shunt admittance.

    A None means the element states no R, L or C whatsoever. Such a thing
    has no admittance to state -- 1/0 is not a number -- so the
    two-terminal transcription is left in place and counted rather than an
    infinity invented.
    """
    if MASTER_KIND_TO_CIM.get(kind) == "Breaker":
        return "ground_switch"
    if on_neutral:
        return "neutral_grounding_impedance"
    if c_f:
        return "shunt_capacitor"
    if l_h:
        return "shunt_reactor"
    if r_ohm:
        return "shunt_resistor"
    return None


def shunt_admittance(r_ohm: float, x_ohm: float) -> tuple[float, float]:
    """``(g, b)`` in siemens of an element of impedance ``r + jx``
    referenced to earth.

    This is the numerical difference between the two statements: a
    series branch states an IMPEDANCE and a shunt states an ADMITTANCE, so
    ``Y = 1/(R + jX)`` over the very reactance :func:`branch_reactance`
    gives the branch. A pure resistance yields ``g = 1/R``, ``b = 0``; a
    pure capacitance ``b = +wC``; a pure inductance ``b = -1/(wL)`` --
    the signs ``cim:LinearShuntCompensator.bPerSection`` is defined in.

    It takes the reactance rather than the frequency and the L/C on
    purpose: the caller has already decided what to do about a case that
    determines no frequency (x = 0.0, counted), and deriving it twice is
    how the two answers would eventually differ. ``(0.0, 0.0)`` where the
    impedance is zero in both parts -- which is only reachable behind that
    placeholder, since an element stating no R, L or C at all is refused
    by :func:`ground_role` before it gets here.
    """
    denominator = r_ohm * r_ohm + x_ohm * x_ohm
    if not denominator:
        return 0.0, 0.0
    # ``+ 0.0`` normalises the negated zero a pure resistance produces:
    # -0.0 serializes as "-0.0", which reads as a signed susceptance where
    # the element has none.
    return r_ohm / denominator, -x_ohm / denominator + 0.0


class SourceForm(NamedTuple):
    """Where one source form states its voltages, and in which convention.

    ``base`` names the rated/base voltage of the node the source feeds;
    ``setpoint`` the operating magnitude, which is a DIFFERENT parameter
    on the source1/source3 forms (``Vm`` = "Base Voltage", ``Es`` =
    "Voltage Magnitude") and the same one on the source_1/source_3 forms,
    whose ``Vm`` is the magnitude behind the impedance. ``control`` is
    non-zero when an input signal drives the magnitude instead, so the
    form states no operating point; source3R has no setpoint parameter at
    all -- its magnitude is always an input.

    ``line_to_line`` says whether those kV are phase-to-phase, which is
    the convention cim:BaseVoltage.nominalVoltage uses -- and the one
    cim:RegulatingControl.targetValue must be stated in for a voltage
    regulation read against that base. A line-to-ground form states
    neither quantity: scaling by sqrt(3) would assume the source is one
    phase of a balanced three-phase set, which the form does not state.

    ``dc`` names the parameter on which the form itself chooses between
    an AC and a DC source -- "Source Type: 0 = AC, 1 = DC" in the form's
    own words. Only ``source1`` carries the choice; the three-phase forms
    are AC by construction and the others state no such field.

    ``initial`` names the initial-condition triple ``(P, Q, base MVA)``
    where the form states one: per-unit real and reactive power at the
    terminal, and the three-phase MVA base they are per-unit ON. Only
    the two forms with a "Specified Parameters: At the Terminal" choice
    carry the pair, each gated by its own selector -- ``source3`` under
    ``(Term==1)&&(Ctrl!=2)`` with its always-enabled ``MVA`` field,
    ``source_3`` under ``Spec==1``, which gates its ``Sbase`` with the
    pair. Whether the triple is LIVE for a placement is the condition
    machinery's answer, never a re-derived selector walk.
    """

    base: str
    setpoint: str | None
    control: str | None
    line_to_line: bool
    dc: str | None = None
    initial: tuple[str, str, str] | None = None


class LoadForm(NamedTuple):
    """Where one load form states the power it draws.

    ``active``/``reactive`` name the parameters, ``scale`` an optional
    multiplier. ``per_phase`` says the form states power PER PHASE rather
    than as a three-phase total: the emitted model is a positive-sequence
    one (line-to-line voltages, ohms per phase), so every power in it is a
    three-phase total and a per-phase form is multiplied by three.

    The rated load voltage these forms also carry is deliberately not read
    as a base-voltage witness: fixed_load declares it line-to-GROUND, and
    cases enter it both ways (PLL_Example_1's 38.105 kV is exactly
    66 kV / sqrt(3), while fixedload.pscx puts its system's 230 kV
    line-to-line value in the same field).
    """

    active: str | None
    reactive: str | None
    per_phase: bool
    scale: str | None


#: Read from each form's own parameter descriptions in master.pslx.
LOAD_FORMS = {
    "fixed_load": LoadForm("PO", "QO", True, "Scale"),
    "reactive_load": LoadForm(None, "S", False, None),
    "resistive_load": LoadForm("P", None, False, None),
}


class LoadResponse(NamedTuple):
    """A fixed load's voltage and frequency dependence, as CGMES states it.

    ``rated_kv`` is the line-to-line voltage at which the form states its
    power. CGMES states ``EnergyConsumer.p``/``q`` at the node's nominal
    voltage instead, so the emitter rescales by the voltage exponents.
    """

    p_voltage: float
    q_voltage: float
    p_frequency: float
    q_frequency: float
    rated_kv: float


def fixed_load_response(stated: dict[str, float]) -> LoadResponse | str:
    """The ``LoadResponseCharacteristic`` one ``fixed_load`` states, or the
    reason it cannot be stated exactly.

    ``stated`` maps the form's lower-cased parameter names to their values
    in the declared units. PSCAD's Fixed Load help gives the model as
    ``P = Scale*PO*(1 + KPF*dF)*{KA*(V/V0)^NPA + KB*(V/V0)^NPB +
    KC*(V/V0)^NPC}``, with ``dF`` the per-unit frequency deviation, and
    the same for Q. CGMES's exponent model is ``P0*(V/Vn)^pVoltageExponent``
    with a frequency term ``(f/fn)^pFrequencyExponent``. The two agree
    exactly for a single-part load whose frequency indices are 0 or 1,
    because ``(1 + dF)^k`` equals ``1 + k*dF`` only there. Anything else
    stays in the add-on document, where the form's own parameters travel.

    ``VBO`` is declared line-to-ground, and ``PQdef`` says whether the
    power is stated at ``VBO`` or at the initial voltage ``VPU*VBO``.
    """
    needed = ("parts", "pqdef", "np", "nq", "kpf", "kqf", "vbo", "vpu")
    missing = [name for name in needed if stated.get(name) is None]
    if missing:
        return f"not numeric: {', '.join(missing)}"
    if stated["parts"] != 1.0:
        return "a composite load has one exponent per part"
    for name in ("kpf", "kqf"):
        if stated[name] not in (0.0, 1.0):
            return (f"{name.upper()} = {stated[name]:g} is a linear index "
                    f"that no CGMES frequency exponent equals")
    rated_kv = stated["vbo"] * math.sqrt(3.0)
    if stated["pqdef"] == 1.0:
        rated_kv *= stated["vpu"]
    if rated_kv <= 0.0:
        return "the rated voltage is not positive"
    return LoadResponse(stated["np"], stated["nq"], stated["kpf"],
                        stated["kqf"], rated_kv)


#: Read from each form's own parameter descriptions in master.pslx.
SOURCE_FORMS = {
    "source1": SourceForm("Vm", "Es", "Ctrl", False, dc="ACDC"),
    "source3": SourceForm("Vm", "Es", "Ctrl", True,
                          initial=("Pinit", "Qinit", "MVA")),
    "source3R": SourceForm("Vm", None, None, True),
    "source_1": SourceForm("Vm", "Vm", "Cntrl", False),
    "source_3": SourceForm("Vm", "Vm", "VCtrl", True,
                           initial=("Pinit", "Qinit", "Sbase")),
}


def source_initial_injection(
        form: SourceForm | None,
        stated: dict[str, float]) -> tuple[float, float] | None:
    """The three-phase injection a source placement states, as
    ``(p_mw, q_mvar)`` in the CIM load sign convention, or None.

    ``stated`` is the placement's live numeric form parameters keyed by
    lowercased spelling -- the same selector-respected set the add-on
    profile states -- so a pair the form gates off is simply absent and
    nothing is read blind. All three spellings of ``form.initial`` must
    be present: the per-unit pair without its own stated MVA base is not
    a power.

    The sign is the convention the receiving attributes are defined in:
    the CIM load sign convention -- "positive sign means flow out from a
    node" -- so a source injecting INTO the network states NEGATIVE, the
    negation PowSyBl undoes when it turns the same quantity back into a
    generator setpoint (``targetP = -p`` in its ExternalNetworkInjection
    update). Which attribute receives the pair is deliberately not
    decided here: ExternalNetworkInjection.p/q states the same quantity
    in the same convention as any other source-class spelling of it,
    and the rule is one rule whichever class carries the statement.
    """
    if form is None or form.initial is None:
        return None
    values = [stated.get(name.lower()) for name in form.initial]
    if any(value is None for value in values):
        return None
    p_pu, q_pu, s_base = values
    # ``+ 0.0`` normalises the negated zero a stated 0.0 produces, as in
    # shunt_admittance: "-0.0" is a signed statement the form never made.
    return -p_pu * s_base + 0.0, -q_pu * s_base + 0.0


#: The non-binding capability bound stated where 61970-600-2 mandates
#: ExternalNetworkInjection.maxP/minP/maxQ/minQ (1..1, sh:Violation) and
#: no PSCAD source form states a capability at all. A sentinel, not a
#: measurement: the ideal source behind an impedance is bounded by
#: nothing the case states, so the mandated number is the one that can
#: never bind a consumer's limit check (PowSyBl reads maxP/minP into
#: generator limits and maxQ/minQ into reactive limits; cim2pp carries
#: them into min/max_p_mw/q_mvar). Symmetric on purpose: +BOUND for the
#: maxima, -BOUND for the minima, counted per placement like every
#: profile-mandated placeholder.
SOURCE_CAPABILITY_BOUND_MW = 1e9


def source_short_circuit(
        form: SourceForm | None,
        stated: dict[str, float]) -> dict[str, float] | None:
    """The IEC 60909 network-feeder family a source placement's stated
    internal impedance respells to, or None where the form states no
    complete family.

    ``stated`` is the placement's live numeric form parameters keyed by
    lowercased spelling, exactly as :func:`source_initial_injection`
    reads them. The three-phase source forms state their fundamental
    internal impedance as ``Z1`` [ohm] with angle ``Phi1`` [deg] under
    their own "Impedance Data Format: Impedance" choice, and the zero
    sequence either as a stated ``Z0``/``Phi0`` pair or through the
    ``ZSeq`` choice's own words -- "Zero Seq. Differs from Positive
    Seq.? 0 = No" -- which is the form stating ``Z0 = Z1``. ``Vm`` is
    the form's own base voltage [kV, phase-to-phase], the ``Un`` of the
    attributes' IEC 60909 definitions.

    The receiving vocabulary states RANGES (600-2 mandates min and max
    of each quantity together), and a form stating one impedance states
    a degenerate range: min equals max on every attribute, which is
    also what makes the restatement invertible --

    * ``maxR1ToX1Ratio`` = ``minR1ToX1Ratio`` = cot(Phi1), inverted by
      ``Phi1 = arccot(ratio)`` on (0, 180);
    * ``maxInitialSymShCCurrent`` = ``minInitialSymShCCurrent`` =
      ``Vm / (sqrt(3) |Z1|)`` in A (the attribute's own ``Ik" =
      Sk"/(sqrt(3) Un)`` with ``Sk" = Un^2/|Z1|``), inverted by
      ``|Z1| = Vm / (sqrt(3) Ik")``;
    * ``maxR0ToX0Ratio`` = ``minR0ToX0Ratio`` = cot(Phi0) and
      ``maxZ0ToZ1Ratio`` = ``minZ0ToZ1Ratio`` = ``|Z0|/|Z1|``, the same
      two inverses in the zero sequence.

    ``ikSecond`` is deliberately not stated: 600-2 declares it a
    BOOLEAN -- whether the currents were calculated by the IEC
    superposition method -- and no such calculation exists here, so the
    honest statement is absence. ``voltageFactor`` likewise: it names
    the ``c`` factor of a calculation nobody ran.

    None wherever any needed spelling is absent (the pair gated off by
    the form, a single-phase form whose ``Vm`` is line-to-ground, an
    RRL-format placement) or the arithmetic has no answer (``Z1`` or
    ``sin Phi`` zero): a partial family is withheld whole, because the
    receiving profile mandates the eight attributes together and a
    half-stated range is an invention.
    """
    if form is None or not form.line_to_line:
        return None
    z1 = stated.get("z1")
    phi1 = stated.get("phi1")
    vm_kv = stated.get("vm")
    if z1 is None or phi1 is None or vm_kv is None or z1 <= 0 or vm_kv <= 0:
        return None
    zseq = stated.get("zseq")
    if zseq:
        z0, phi0 = stated.get("z0"), stated.get("phi0")
    elif zseq is None:
        return None
    else:
        z0, phi0 = z1, phi1
    if z0 is None or phi0 is None or z0 <= 0:
        return None
    sin1 = math.sin(math.radians(phi1))
    sin0 = math.sin(math.radians(phi0))
    if not sin1 or not sin0:
        return None
    r1_to_x1 = math.cos(math.radians(phi1)) / sin1
    r0_to_x0 = math.cos(math.radians(phi0)) / sin0
    ik_a = vm_kv * 1e3 / (math.sqrt(3.0) * z1)
    return {
        "maxR1ToX1Ratio": r1_to_x1,
        "minR1ToX1Ratio": r1_to_x1,
        "maxInitialSymShCCurrent": ik_a,
        "minInitialSymShCCurrent": ik_a,
        "maxR0ToX0Ratio": r0_to_x0,
        "minR0ToX0Ratio": r0_to_x0,
        "maxZ0ToZ1Ratio": z0 / z1,
        "minZ0ToZ1Ratio": z0 / z1,
    }


def magnetizing_admittance(im1_percent: float, nll_pu: float,
                           z_base_ohm: float) -> tuple[float, float]:
    """``(g, b)`` in siemens of the magnetizing branch a transformer
    form states as ``Im1`` (magnetizing current, percent of the
    winding's base current) and ``NLL`` (no-load loss, per unit on the
    transformer's own rating).

    Both fields are per-unit statements on the transformer's own base,
    so one ohmic base converts both -- end 1's, ``Vhigh^2/Tmva``, the
    same base the series impedance is written on: ``g = NLL / Zbase``
    and ``|b| = (Im1/100) / Zbase``. The signs are the admittance
    convention ``Y = G + jB`` of :func:`shunt_admittance`: an absorbing
    branch states positive conductance -- cim2pp reads the core loss
    back as ``g x ratedU^2``, sign included -- and an inductive branch
    negative susceptance. The form's minimum for ``Im1`` is positive,
    so ``b`` is strictly negative wherever the branch is stated at all;
    a stated ``NLL`` of zero is a zero eddy-current loss, not a
    placeholder.
    """
    y_base = 1.0 / z_base_ohm
    return nll_pu * y_base, -(im1_percent / 100.0) * y_base


class MachineForm(NamedTuple):
    """Where a rotating-machine form states its nameplate and its
    initial operating point, and under which selector.

    A machine form does NOT simply carry an MVA field. ``rating_choice``
    ("Rating Specified as") selects between two entries and gates the
    other one off: with ``IorMVA == 0`` (Current) the rating is
    ``Vbase`` x ``Ibase`` and the ``MVA`` field is inactive, holding
    whatever the dialog last had, such as the form default 300.0.
    Reading it would state a rating nobody typed.

    ``line_to_neutral_kv`` is exactly what the form calls it: "Rated RMS
    Line-to-Neutral Voltage". cim:RotatingMachine.ratedU is
    phase-to-phase, so it is sqrt(3) times this -- and unlike the
    single-phase source forms, which are not scaled, the factor is
    sound here for a stated reason: a machine's stator IS one balanced
    three-phase set drawn as one symbol, so its line-to-neutral
    nameplate and its line-to-line nameplate describe the same windings.
    A source1 states one phase that need not be one of three.

    ``coherent_count`` ("Number of Coherent Machines", gated on
    ``scale_choice``) makes one symbol stand for N identical machines,
    so it multiplies the apparent power and not the voltage.

    ``ic_choice`` is "Type of Settings for Initial Condition"
    (0 = None, 1 = Powers, 2 = Currents); ``active_mw``/``reactive_mvar``
    exist only under Powers, and the form states them "Out +" where
    CIM's RotatingMachine.p/q use the LOAD sign convention.
    """

    cim_class: str
    rating_choice: str
    line_to_neutral_kv: str
    line_current_ka: str
    rated_mva: str
    scale_choice: str
    coherent_count: str
    ic_choice: str
    active_mw: str
    reactive_mvar: str


#: How far a DERIVED base-voltage witness may sit from a typed one and
#: still be naming the same voltage. 0.5% -- wide enough for sqrt(3) x
#: 7.967 = 13.7992 against a typed 13.8 (5e-5) and 0.398372 against a
#: typed 0.398 (9e-4), and nowhere near a real disagreement such as
#: 477.8 against 539.0 (11%) or 420 against 230 (45%).
#: It applies ONLY where a derived witness is involved; two
#: typed witnesses must still match to the last digit.
DERIVED_KV_TOLERANCE = 5e-3


#: Read from each form's own parameter descriptions in master.pslx.
MACHINE_FORMS = {
    "sync_machine": MachineForm(
        "SynchronousMachine", "IorMVA", "Vbase", "Ibase", "MVA",
        "Iscl", "NOM", "icTyp", "P0", "Q0"),
}


class MachineRating(NamedTuple):
    """One machine's nameplate in CIM's conventions, or the reason there
    is none. ``rated_s`` is three-phase MVA, ``rated_u`` phase-to-phase
    kV."""

    rated_s: float | None
    rated_u: float | None
    reason: str | None


#: Form-declared parameter types whose value is a NUMBER. "Real" and
#: "Integer" by declaration; "Choice" because a choice is stored as the
#: integer index of the selected entry, and the selectors that gate other
#: fields (a machine's ``IorMVA``, a source's ``Ctrl``) are themselves
#: Choices -- an engine statement that omitted them could not say which
#: of the gated fields it is stating. "Text" and everything else carries
#: names, not quantities.
NUMERIC_FORM_TYPES = frozenset({"Real", "Integer", "Choice"})

#: CIM classes of the mapped components that DRIVE the network -- the
#: population whose live, numeric, selector-respected form parameters the
#: add-on profile states beside the equipment, exactly as it states a
#: mapped passive's R/L/C. The passive population is every mapped kind
#: whose class in MASTER_KIND_TO_CIM is "EquivalentBranch" (the ground
#: roles reclassify some of those placements, but the KIND stays the
#: discriminator); these four are everything else that is both mapped
#: and parameterised. Breaker state is SSH's. A load is here because its
#: rated voltage, composite parts and linear frequency indices have no
#: CGMES home even where its exponents do.
ACTIVE_CIM_CLASSES = frozenset({"ExternalNetworkInjection",
                                "SynchronousMachine", "PowerTransformer",
                                "EnergyConsumer"})


def modeled_cim_class(definition_name: str | None) -> str | None:
    """The CIM class a model type's stated definition name maps to, or
    None.

    This is the inverse-side discriminator: an add-on document's
    ``emt:LibraryModelType.definitionName`` is ``master:resistor``-style,
    and which read-back a ``cim:DetailedModelDynamics.Equipment`` join
    belongs to -- the passive R/L/C one or the active parameter one -- is
    decided by the KIND that name states, read off the document alone.
    """
    if not definition_name:
        return None
    kind = definition_name.partition(":")[2] or definition_name
    return MASTER_KIND_TO_CIM.get(kind)


#: cim:PhaseCode member per 1-based conductor index of a DRAWN THREE-PHASE
#: bundle. The letters are PSCAD's own: a component's per-phase view names
#: the ports of its dim-3 port A/B/C in that order (breaker3's A1/B1/C1
#: against N1, xfmr-3p2w's A1/B1/C1 against N1), so index k of the bundle
#: is phase k of the drawing.
PHASE_LETTERS = {1: "A", 2: "B", 3: "C"}



#: Emitted classes whose two terminals are a per-phase conducting
#: connection. A one-terminal shunt (ExternalNetworkInjection,
#: EnergyConsumer, EquivalentInjection) states nodes and no connection,
#: which is what a shunt is.
#:
#: ``GroundDisconnector`` and ``GroundingImpedance`` are here because they
#: keep both ends: 301 requires exactly two terminals of every Switch, and
#: admits a second on an EarthFaultCompensator precisely when the ground
#: side carries topology worth modeling -- which PSCAD's one shared
#: ground node is.
TWO_TERMINAL = ("ACLineSegment", "EquivalentBranch", "Breaker",
                "Disconnector", "PowerTransformer", "GroundDisconnector",
                "GroundingImpedance")

#: Emitted classes carrying ONE terminal whose absent second end is the
#: earth, so the connection they state is between their own node and the
#: ground node.
#:
#: This is not an inference the reader invents: 301's
#: ``EarthFaultCompensator:numberOfTerminals`` states it for the family in
#: its own words -- "If the second terminal ... is omitted, it is assumed
#: the terminal solidly connects to ground" -- and a ``cim:Ground`` says
#: WHICH node that is. Standard CIM carries the implicit end, so no
#: extension vocabulary is minted for it.
#:
#: The other one-terminal classes are deliberately absent. An
#: ExternalNetworkInjection, EnergyConsumer or EquivalentInjection
#: states an injection at a node, not a conducting path to earth. A
#: source's per-phase LIR edges run through its own internal EMF nodes
#: rather than from its terminal to earth.
IMPLICIT_GROUND = ("LinearShuntCompensator",)


#: PSCAD project Settings parameter -> (emt:SimulationCase attribute,
#: divisor into the profile's SI seconds). The form states the solution
#: and plot steps in microseconds and the run length in seconds. A
#: DIVISOR, not a factor: 50/1e6 is exactly 5e-05 where 50*1e-6 is not.
SETTINGS_SECONDS = {
    "time_step": ("timeStep", 1e6),
    "time_duration": ("duration", 1.0),
    "sample_step": ("plotStep", 1e6),
}


#: PSCAD project Settings parameter -> emt:SimulationCase attribute, value
#: VERBATIM: the string the project states, no conversion and no
#: interpretation. The three SETTINGS_SECONDS keys convert because their
#: unit is stated by the form (microseconds vs seconds) and a reader must
#: never have to know which; these have no unit to normalise, and a
#: decoded reading -- a flag word's bits, an enum's meaning -- would be
#: this repo restating PSCAD's manual instead of the project's own words.
#: These are the engine-facing Settings keys not already exchanged --
#: solver thresholds, the solver flag words, output/snapshot behaviour
#: and the multiple-run study definition. The rest of the paramlist is
#: GUI, compiler and provenance state, which the source document owns.
SETTINGS_VERBATIM = {
    "chatter_threshold": "chatterThreshold",
    "branch_threshold": "branchThreshold",
    "sparsity_threshold": "sparsityThreshold",
    "Advanced": "advancedFlags",
    "Options": "optionsFlags",
    "PlotType": "plotType",
    "output_filename": "outputFilename",
    "snapshot_filename": "snapshotFilename",
    "startup_filename": "startupFilename",
    "MrunType": "multipleRunMode",
    "Mruns": "multipleRunCount",
    "multirun_filename": "multipleRunFilename",
}


#: Master-library kinds whose placements RECORD the study: pgb is the one
#: mechanism behind every EMTDC output channel (the recorded <output>
#: list references pgb placements exclusively),
#: and recorder2_0 writes COMTRADE/playback files of the signals its
#: slot parameters name. Their TEXT form parameters -- a channel's
#: title, group and unit, a recorder's file and slot names -- are what
#: the study records its results AS, so they are engine-facing where
#: every other signal block's text is a label or a wiring clue the
#: graph already resolves.
RECORDING_KINDS = frozenset({"pgb", "recorder2_0"})


"""Every number this project emits, and what independently states it.

A wrong quantity can survive every structural oracle in this suite and
be caught only by an independently-stated value: Kersting's worked
examples against the derived Z1/Z0, a transformer's ratedU against the
source's base voltage, and the demand a load form states against the
demand a solver sees. A demand three times too large is symmetric,
SHACL-clean and importable.

This file records that comparison.

:data:`QUANTITIES` names every numeric attribute the emitted documents
carry, and for each one either the INDEPENDENT statement of that quantity
and the oracle comparing against it, or -- explicitly -- that no
independent statement exists.

"Independent" means a statement of the SAME physical quantity arrived at
by a different route -- not a second reading of the same field. A
transformer's ratedU and a Bus wire's BaseKV are independent because two
people typed them into different forms about the same node. A
detailed-model parameter's value and the form field it came from are NOT:
one is a transcription of the other, and comparing them checks the copy,
not the number.
"""

import os
import pathlib
from collections import namedtuple

import pytest
from conftest import master_available

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)

Quantity = namedtuple("Quantity", "source oracle reason")


def checked(source: str, oracle: str) -> Quantity:
    """An independent statement exists AND an oracle compares against it."""
    return Quantity(source, oracle, None)


def unchecked(source: str) -> Quantity:
    """An independent statement exists and NOTHING compares against it.

    The honest middle: the number could be checked and is not. This is the
    list to shorten.
    """
    return Quantity(source, None, None)


def sole(reason: str) -> Quantity:
    """No independent statement exists, and why.

    Not a lesser kind of checking -- a different fact about the world. A
    transcription has nothing to be checked against, and saying so is
    what stops it from looking like an oversight.
    """
    return Quantity(None, None, reason)


def _prose(quantity: Quantity, registry: dict) -> str:
    """The entry's own words, following an "as <other>" cross-reference.

    Repeating five lines of reasoning for x0 because r0 already carries
    it would make the registry unreadable, and unread is how an inventory
    stops being true. The reference is RESOLVED rather than trusted, so a
    pointer at an entry nobody wrote fails.
    """
    text = quantity.source or quantity.reason or ""
    seen = set()
    while text.startswith("as ") and text[3:] in registry:
        target = text[3:]
        assert target not in seen, f"circular reference at {target}"
        seen.add(target)
        other = registry[target]
        text = other.source or other.reason or ""
    return text


#: Keyed exactly as the emitted predicates are.
QUANTITIES = {
    # ---- line constants -------------------------------------------------
    # The one numeric family with an EXTERNAL cross-check, and the reason
    # the zero sequence was worth emitting: Kersting Examples 4.1-4.2
    # solved with Carson's equations against ours with Deri-Semlyen agree
    # to 0.01% on Z1 and 0.55% on Z0.
    "ACLineSegment.r": checked(
        "Kersting Examples 4.1-4.2 (external), physical invariants, and "
        "pandapower's read-back of the ohms",
        "test_lineconst.py::test_published_carson_benchmark_line_reproduces_its_stated_sequence_z"),
    "ACLineSegment.x": checked(
        "the same Kersting comparison, which fixes R and X together",
        "test_lineconst.py::test_published_carson_benchmark_line_reproduces_its_stated_sequence_z"),
    "ACLineSegment.bch": unchecked(
        "pandapower converts bch to a capacitance and back, unit for unit"),
    "ACLineSegment.r0": checked(
        "Kersting Example 4.2 (external), 0.55%; plus x0 > x1 and r0 > r1 "
        "as invariants",
        "test_lineconst.py::test_published_carson_benchmark_line_reproduces_its_stated_sequence_z"),
    "ACLineSegment.x0": checked(
        "the same comparison, which reports Z0 as well as Z1",
        "test_lineconst.py::test_published_carson_benchmark_line_reproduces_its_stated_sequence_z"),
    "ACLineSegment.b0ch": unchecked(
        "the same Kron reduction re-solved independently and multiplied "
        "by the line's own length, in SI"),
    "ACLineSegment.gch": sole(
        "the right-of-way's own shunt conductance, which the line data "
        "states once and nothing states a second time"),
    "ACLineSegment.g0ch": sole("as ACLineSegment.gch"),
    "ACLineSegment.shortCircuitEndTemperature": sole(
        "a conductor rating rather than circuit data, mandatory in SC and "
        "stated nowhere in PSCAD. Real CGMES files carry 0, 75, 80 and "
        "160 with no convention between them, so 0.0 is written and "
        "counted rather than a plausible number invented"),

    # ---- base voltage ---------------------------------------------------
    # The strongest family in the model, and the only one with FOUR
    # witnesses.
    "BaseVoltage.nominalVoltage": unchecked(
        "four independent witnesses -- a Bus wire's BaseKV, a transformer "
        "winding's ratedU, a three-phase source form's base voltage, and "
        "a machine's line-to-neutral nameplate times sqrt(3)"),

    # ---- demand ---------------------------------------------------------
    # The family that proves the rule: symmetric counts, SHACL, both
    # consumers and a converging power flow all pass with a three-phase
    # load three times too large.
    "EnergyConsumer.p": unchecked(
        "the demand the case's load forms state, read off the forms and "
        "compared against what a solver actually sees"),
    "EnergyConsumer.q": unchecked(
        "the reactive demand a CONSUMER sees, against the Q the case's "
        "load forms state, read off the forms by the same separate "
        "route the active half uses"),
    "LoadResponseCharacteristic.pVoltageExponent": unchecked(
        "the fixed_load form read straight out of the .pscx with lxml and "
        "master's defaults, which predicts each exponent and the power at "
        "the node's nominal voltage"),
    "LoadResponseCharacteristic.qVoltageExponent": unchecked(
        "as LoadResponseCharacteristic.pVoltageExponent"),
    "LoadResponseCharacteristic.pFrequencyExponent": unchecked(
        "as LoadResponseCharacteristic.pVoltageExponent"),
    "LoadResponseCharacteristic.qFrequencyExponent": unchecked(
        "as LoadResponseCharacteristic.pVoltageExponent"),
    "EquivalentInjection.p": unchecked(
        "the same load forms as EnergyConsumer.p, since a capacitive load "
        "is one read the same way"),
    "EquivalentInjection.q": unchecked(
        "the same route as EnergyConsumer.q, which sums pandapower's ward "
        "table beside its load table, so a capacitive load is read the "
        "same way"),

    # ---- sources --------------------------------------------------------
    "RegulatingControl.targetValue": unchecked(
        "the BaseVoltage the source's node resolved to, which on most "
        "islands came from a witness other than this source. "
        "The comparison is a BAND rather than an identity and says so: Es "
        "is a setpoint near nominal by construction, so 0.5-1.5 pu is "
        "what it can be held to -- but a factor of sqrt(3), a kV/V slip "
        "or a line-to-ground reading leaves it"),
    "ExternalNetworkInjection.p": unchecked(
        "the live Pinit/Qinit pair on the form's own stated MVA base, "
        "negated into the load convention the attribute is defined in, "
        "wherever the placement states its initial condition. "
        "Everywhere else 0.0 stays, mandatory in SSH and "
        "counted as cim_source_injection_placeholder: a voltage source "
        "without a stated initial condition has a solver outcome for "
        "an injection, not data"),
    "ExternalNetworkInjection.q": unchecked(
        "as ExternalNetworkInjection.p"),
    "ExternalNetworkInjection.referencePriority": sole(
        "the uniform 1 is a consumer-mandated designation, not a case "
        "quantity: PSCAD has no slack concept, and 0 would demote every "
        "source to a non-reference in cim2pp's routing; counted as "
        "cim_source_reference_priority_designated"),
    "ExternalNetworkInjection.governorSCD": sole(
        "as ExternalNetworkInjection.maxP"),
    "ExternalNetworkInjection.maxP": sole(
        "a 600-2-mandated capability bound no PSCAD source form states: "
        "the symmetric non-binding sentinel of "
        "pscx.rules.SOURCE_CAPABILITY_BOUND_MW, counted as "
        "cim_source_capability_placeholder -- there is nothing in the "
        "case for a check to compare against"),
    "ExternalNetworkInjection.minP": sole(
        "as ExternalNetworkInjection.maxP"),
    "ExternalNetworkInjection.maxQ": sole(
        "as ExternalNetworkInjection.maxP"),
    "ExternalNetworkInjection.minQ": sole(
        "as ExternalNetworkInjection.maxP"),
    "ExternalNetworkInjection.maxR1ToX1Ratio": unchecked(
        "the stated Z1/Phi1 pair itself, through the recorded inverse "
        "Phi1 = arccot(ratio), which a wrong cotangent or a min/max "
        "swap breaks"),
    "ExternalNetworkInjection.minR1ToX1Ratio": unchecked(
        "as ExternalNetworkInjection.maxR1ToX1Ratio"),
    "ExternalNetworkInjection.maxInitialSymShCCurrent": unchecked(
        "the stated Z1 and the form's own base kV, through the recorded "
        "inverse |Z1| = Vm / (sqrt(3) Ik'')"),
    "ExternalNetworkInjection.minInitialSymShCCurrent": unchecked(
        "as ExternalNetworkInjection.maxInitialSymShCCurrent"),
    "ExternalNetworkInjection.maxR0ToX0Ratio": unchecked(
        "the stated (or ZSeq-declared-equal) zero-sequence pair, the "
        "positive sequence's inverse applied in the zero sequence"),
    "ExternalNetworkInjection.minR0ToX0Ratio": unchecked(
        "as ExternalNetworkInjection.maxR0ToX0Ratio"),
    "ExternalNetworkInjection.maxZ0ToZ1Ratio": unchecked(
        "as ExternalNetworkInjection.maxR0ToX0Ratio"),
    "ExternalNetworkInjection.minZ0ToZ1Ratio": unchecked(
        "as ExternalNetworkInjection.maxR0ToX0Ratio"),

    # ---- two-terminal branches -----------------------------------------
    "EquivalentBranch.r": unchecked(
        "cim2pp's read-back, which stores no ohms at all: it keeps a "
        "per-unit value against a base it derives from the bus voltage "
        "and its own reference power, so recovering the ohms exercises "
        "the base and the unit conversion together. The same route "
        "ACLineSegment.bch is checked by"),
    "EquivalentBranch.x": unchecked(
        "the same per-unit recovery, which for a reactance also exercises "
        "the system frequency it was computed at -- the one "
        "input to this number that no other check reaches"),

    # ---- shunts referenced to earth -------------------------------------
    # gPerSection is stated by a genuinely independent route, and the
    # route is a MEASUREMENT that does not use the number.
    "LinearShuntCompensator.gPerSection": unchecked(
        "113.2 MW, derived by an entirely different path: the ohms read "
        "back out of cim2pp's imported rft_pu for a TWO-terminal "
        "transcription of svc_acsystem's resistor, hung as V^2/R at the "
        "bus as a pandapower shunt by hand, and solved. That path involves "
        "no conductance and no shunt class at all. The emitted "
        "gPerSection reproduces it through nomU^2 * g, so a "
        "reciprocal, a sign or a unit error in the impedance-to-admittance "
        "conversion moves the solved power"),
    "LinearShuntCompensator.bPerSection": unchecked(
        "the reactive power a consumer exchanges at nominal voltage, "
        "which cim2pp computes as -b * nomU^2 -- the conductance's own "
        "route applied to the susceptance. A reciprocal, a "
        "sign or a 2*pi*f error in the C-to-b conversion moves it. The "
        "SIGN alone tells a capacitor from a reactor and says nothing "
        "about the magnitude"),
    "ShuntCompensator.nomU": unchecked(
        "the same four witnesses BaseVoltage.nominalVoltage has, since it "
        "is the same resolved node voltage and they cross-check each "
        "other. It matters more here than at the node, because a consumer "
        "SQUARES it to get the power the shunt draws"),
    "ShuntCompensator.sections": sole(
        "one section is a statement about the DRAWING rather than a "
        "measured quantity: PSCAD's capacitor and resistor forms carry no "
        "section count and no switching, so there is one section and "
        "nothing anywhere states it a second time. A case that draws a "
        "genuinely switched bank would make this a real number and this "
        "entry wrong"),
    "ShuntCompensator.normalSections": sole("as ShuntCompensator.sections"),
    "ShuntCompensator.maximumSections": sole(
        "as ShuntCompensator.sections"),

    # ---- neutral grounding ----------------------------------------------
    "EarthFaultCompensator.r": sole(
        "Every other impedance in this registry is checked through a "
        "consumer's read-back, and GroundingImpedance reaches NEITHER "
        "consumer: cim2pp has no converter for it and PowSyBl "
        "builds no bus for a node whose only equipment is one. The Branch "
        "row states R once and nothing states it again, so there is no "
        "second statement to compare against rather than a comparison "
        "nobody has written yet. It "
        "becomes checkable when a consumer reads ShortCircuit data"),
    "GroundingImpedance.x": sole("as EarthFaultCompensator.r"),

    # ---- transformers ---------------------------------------------------
    "PowerTransformerEnd.ratedU": unchecked(
        "it is one of the four base-voltage witnesses, so an island that "
        "also carries a Bus BaseKV, a source base or a machine nameplate "
        "compares them"),
    "PowerTransformerEnd.ratedS": unchecked(
        "cim2pp's read-back, which cannot separate the three: it keeps a "
        "transformer as sn_mva plus a short-circuit voltage pair, so "
        "recovering the ohms needs the rating, the rated voltage and both "
        "impedances at once. A wrong rating moves the recovered ohms even "
        "though the rating itself round-trips. NOTHING states the rating "
        "independently -- a PSCAD case names Tmva once, and a wrong one "
        "scales r and x TOGETHER so it survives the Bus-BaseKV "
        "substitution unchanged. What bounds it is the per-unit band, "
        "which is external evidence about the fields rather than about "
        "this number, and that is the honest limit of what is available"),
    "PowerTransformerEnd.r": unchecked(
        "a Bus wire's own BaseKV substituted for V1 in the ohmic base, "
        "which is the second statement the consumer's read-back cannot "
        "be: cim2pp rebuilds the base out of OUR ratedS and ratedU, so a "
        "base taken from the wrong winding, off by a factor of 278, "
        "round-trips undetected. BaseKV is typed onto the "
        "WIRE, so a base off by the turns ratio returns CuL multiplied "
        "by its square"),
    "PowerTransformerEnd.x": unchecked(
        "the same Bus-BaseKV substitution as PowerTransformerEnd.r, plus "
        "a consumer recovery one step stronger than r's: cim2pp stores "
        "the short-circuit impedance MAGNITUDE and its real part, so x "
        "comes back as sqrt(z^2 - r^2) -- an identity that fails if r "
        "and x were swapped, which reading either one alone would not "
        "catch. The two routes answer different questions: the "
        "substitution says the ohmic BASE is right, the recovery says "
        "the two impedances are not exchanged"),
    "PowerTransformerEnd.b": unchecked(
        "the form's magnetizing current on the series impedance's own "
        "ohmic base -- b = -(Im1/100)/Zbase, the absorbing sign of the "
        "Y = G + jB convention -- wherever the placement declares the "
        "non-ideal model, whose netlist is what contains the branch; "
        "cim2pp reads the admittance magnitude back as i0_percent "
        "through a base it derives itself. Ideal-core and "
        "saturation-cored placements keep 0.0, counted as "
        "cim_xfmr_no_magnetizing"),
    "PowerTransformerEnd.g": unchecked(
        "the NLL half of the same pair on the same base, sign included: "
        "cim2pp reads the core loss back as pfe_kw = g x ratedU^2, so "
        "an absorbing branch mis-signed comes back as a negative loss"),

    # ---- rotating machines ----------------------------------------------
    # The rotating-machine family, which has a second
    # source: 3 x 15.01 kV x 19.82 kA = 892.49 MVA at sqrt(3) x 15.01
    # = 26.00 kV, against the IEEE First Benchmark Model's published
    # 892.4 MVA at 26 kV -- two fields that name neither.
    "RotatingMachine.ratedS": unchecked(
        "the IEEE First Benchmark Model's published nameplate (external, "
        "one case, 0.011%), and the form's own fields re-read through "
        "the selector that gates them"),
    "RotatingMachine.ratedU": unchecked(
        "the same published nameplate (0.008%), and agreement with the "
        "other three base-voltage witnesses where they meet, to within "
        "the rounding of a typed value"),
    "RotatingMachine.p": unchecked(
        "the machine's own NAMEPLATE, which is the second statement the "
        "form comparison lacked: the apparent power of the operating "
        "point cannot exceed ratedS, and ratedS comes from different form "
        "fields entirely. Asserted beside the consumer's "
        "generator table, so the sign convention is checked too"),
    "RotatingMachine.q": unchecked(
        "the same nameplate bound as RotatingMachine.p, which is on the "
        "APPARENT "
        "power, so it is a statement about P and Q together and neither "
        "can be checked without the other"),
    "SynchronousMachine.minQ": sole(
        "+/- ratedS, the apparent-power circle: the form states no "
        "reactive capability and 452 requires limits, so what is written "
        "is the bound the nameplate implies and nothing narrower"),
    "SynchronousMachine.maxQ": sole("as SynchronousMachine.minQ"),
    "GeneratingUnit.minOperatingP": sole(
        "+/- ratedS for the same reason: the unit exists because PowSyBl "
        "builds no generator without one, and the form states no "
        "real-power limits"),
    "GeneratingUnit.maxOperatingP": sole("as GeneratingUnit.minOperatingP"),
    "GeneratingUnit.normalPF": sole(
        "1.0, mandatory in SSH and stated nowhere on the form -- the same "
        "absence as RotatingMachine.ratedPowerFactor, which is omitted "
        "entirely because it is optional"),

    # ---- study settings -------------------------------------------------
    "SimulationCase.timeStep": unchecked(
        "the .pscx re-read with lxml and converted by independent "
        "arithmetic, a differential against build_emt's own path rather "
        "than a second look at its output"),
    "SimulationCase.duration": unchecked(
        "the same differential as SimulationCase.timeStep, and this is "
        "the field that makes it "
        "differential worth running: the run length is already in "
        "seconds while the two step sizes are in microseconds, so a "
        "uniform conversion would be wrong here and only here"),
    "SimulationCase.plotStep": unchecked(
        "as SimulationCase.timeStep"),
    "SimulationCase.snapshotTime": unchecked(
        "the same differential as SimulationCase.timeStep, with the "
        "extra clause that it "
        "reaches the document only under SnapType, so its ABSENCE on a "
        "case that writes no snapshot is part of the statement"),
    "SimulationCase.chatterThreshold": unchecked(
        "the .pscx re-read with lxml, byte-for-byte: the value is "
        "VERBATIM by rule (SETTINGS_VERBATIM), so the differential "
        "checks the copy and the absence, which is all a verbatim key "
        "has to get wrong"),
    "SimulationCase.branchThreshold": unchecked(
        "as SimulationCase.chatterThreshold"),
    "SimulationCase.sparsityThreshold": unchecked(
        "as SimulationCase.chatterThreshold"),
    "SimulationCase.advancedFlags": unchecked(
        "the .pscx re-read with lxml, byte-for-byte -- a flag word is "
        "carried as the tool's own encoding, named not decoded, so "
        "byte-equality is the whole claim"),
    "SimulationCase.optionsFlags": unchecked(
        "as SimulationCase.advancedFlags"),
    "SimulationCase.plotType": unchecked(
        "as SimulationCase.chatterThreshold"),
    "SimulationCase.multipleRunMode": unchecked(
        "as SimulationCase.chatterThreshold"),
    "SimulationCase.multipleRunCount": unchecked(
        "as SimulationCase.chatterThreshold"),

    # ---- transcriptions and structure -----------------------------------
    # Not physical quantities: a count, an index, or a verbatim copy.
    # They are listed so that the registry names every numeric attribute.
    "ParameterValue.value": sole(
        "a TRANSCRIPTION of the form's own text, so there is nothing "
        "independent to compare it against by construction -- comparing "
        "it with its source would check the copy, not the number"),
    "ParameterValue.numericValue": unchecked(
        "for a detailed-model or surface statement this is a transcription "
        "like ParameterValue.value; for a mapped passive's model it is the "
        "R/L/C the LIR states independently in SI"),
    "ParameterDescriptor.typicalValue": sole(
        "a transcription of the DEFINITION's own default text, in the same "
        "sense as ParameterValue.value transcribes the placement's. What "
        "distinguishes it from that field is whose text it is"),
    "ParameterDescriptor.sequenceNumber": sole(
        "the order the form itself declares its parameters in, which "
        "is the thing being transcribed. It is a property of the model "
        "TYPE rather than of a placement, so it is one number per "
        "(definition, parameter) however many placements state it"),
    "ModelTerminal.phase": unchecked(
        "the per-phase network the LIR states, which this field "
        "rebuilds together with ConnectivityNode.phaseCount"),
    "ModelTerminal.sequenceNumber": sole(
        "an ordinal within one model, minted by the writer rather than "
        "read from anywhere"),
    "ConnectivityNode.phaseCount": unchecked(
        "as ModelTerminal.phase"),
    "ModelPort.sequenceNumber": sole(
        "an ordinal within one type's declared port list, minted by the "
        "writer"),
    "RightOfWayRecord.values": unchecked(
        "the evaluated right-of-way record the lineconst path produces "
        "from the same Model-Data grammar the solved EQ projection is "
        "derived from -- and that projection is itself the independent "
        "witness, physics-checked against a published Carson benchmark "
        "in test_lineconst.py"),
    "RightOfWayRecord.sequenceNumber": sole(
        "an ordinal among one row's siblings, minted by the writer"),
    "Line.conductorCount": unchecked(
        "the wire's own Dim declaration, which the right-of-way record "
        "states independently as its circuits' conductor counts"),
    "ModelPort.mode": unchecked(
        "the ComponentDef's own lowered port list, resolved through the "
        "type's stated definitionName: a transcription by construction, "
        "and the only statement of a PSCAD port coding there is"),
    "ModelPort.dimension": unchecked(
        "the declared width in the ComponentDef's lowered port list, as "
        "for ModelPort.mode; where the declaration defers "
        "to a parameter the stated value is that parameter's NAME and "
        "only incidentally numeric"),
    "ModelPort.dataType": unchecked(
        "as ModelPort.mode"),
    "ModelPort.electricalType": unchecked(
        "as ModelPort.mode"),
    "SignalNet.width": unchecked(
        "every width-stating endpoint of the net is an independent vote "
        "on the same quantity -- dim attrs, :suffix names, #OUTPUT dims "
        "(dim-0 inheritance included)"),
    "SignalConnection.width": unchecked(
        "as SignalNet.width"),
    "SignalConnection.offset": unchecked(
        "the datatap's own evaluated Index, which states the tap slice "
        "independently"),
    "SignalConnection.portName": sole(
        "an identifier, not a quantity: a drawn signal's name may be "
        "spelled as digits, and the read-back compares it as the string "
        "it is"),
    "ModelPort.portName": sole(
        "an identifier, not a quantity: a declared port's name may be "
        "spelled as digits (a control type's numbered inputs), and the "
        "declared-interface read-back compares it as the string it is"),
    "IdentifiedObject.name": sole(
        "an identifier, not a quantity: a control block's display name "
        "is user text and may be spelled as digits; identity is the "
        "mRID, and every read-back compares names as strings"),
    "SignalConnection.value": sole(
        "the constant a module form parameter drives its net with, "
        "verbatim from the instance environment -- a transcription with "
        "nothing independent to compare against, exactly like the "
        "ParameterValue.value it mirrors"),
    "ACDCTerminal.sequenceNumber": sole(
        "a within-equipment ordinal; identity-checked rather than "
        "value-checked (test_identity.py)"),
    "TransformerEnd.endNumber": unchecked(
        "301's TransformerEnd.endNumber-unique requires the lowest end "
        "number to carry the maximum ratedU, which is an independent "
        "statement of the ORDER"),
    "SynchronousMachine.referencePriority": sole(
        "0, which is CIM's own way of saying 'not a reference machine'; "
        "PSCAD has no slack concept to state one"),
}


def test_every_registry_entry_states_a_resolvable_source_or_reason():
    # A registry entry that said nothing would defeat the registry's
    # purpose. An "as <other>" cross-reference counts only if
    # it resolves to an entry that does say something.
    for name, quantity in QUANTITIES.items():
        text = (quantity.source or quantity.reason or "")
        if text.startswith("as "):
            assert text[3:] in QUANTITIES, f"{name} points at {text[3:]!r}"
        assert len(_prose(quantity, QUANTITIES).split()) >= 6, name
        if quantity.oracle is not None:
            assert "::" in quantity.oracle, name
            assert quantity.source is not None, (
                f"{name} names an oracle but no independent statement for "
                f"it to compare against")


def test_the_oracle_each_checked_quantity_names_exists():
    # A named oracle that does not exist is worse than an unchecked
    # number: it reads as a claim that the number is verified. Checked by
    # locating the test function, not by trusting the string.
    import re

    for name, quantity in QUANTITIES.items():
        if quantity.oracle is None:
            continue
        module, _, function = quantity.oracle.partition("::")
        path = os.path.join(os.path.dirname(__file__), module)
        assert os.path.exists(path), f"{name} names a missing file: {module}"
        source = pathlib.Path(path).read_text()
        assert re.search(rf"^def {re.escape(function)}\(", source,
                         re.MULTILINE), (
            f"{name} names {function}, which {module} does not define")

"""What the two consumers actually BUILD from each emitted construct.

SHACL says a document is well formed. The import tests say a network can be
built from it. Neither asks the question this file asks: does each construct
mean to a consumer what the emission meant by it?

The question is worth its own file because the answers disagree, and the
disagreements are the findings. A faithful extraction is still misread
when emission assumes a consumer reads PSCAD's topology the way PSCAD
does: PSCAD's ground as an ordinary ConnectivityNode is no earth to a
load flow, a per-phase element as three objects each carrying
the three-phase total triples the demand, and zero-impedance
conduction as a bare TopologicalNode merge is invisible to a
node-breaker consumer. So this sweeps every construct, with
both consumers as witnesses rather than one:

* pandapower cim2pp is NODE-BREAKER -- one bus per ConnectivityNode.
* PowSyBl calculates a bus-breaker view over the same nodes.

Every claim here is a MEASUREMENT of what a consumer built, not a reading
of its source. Where the two disagree, both are recorded; where they agree
and the emission does not, the emission is wrong. The coverage of the sweep
-- what was checked, what could not be, and why -- is asserted at the
bottom, because a sweep that does not state its own reach is a claim about
whatever it happened to look at.
"""

from collections import Counter

import pytest
from conftest import master_available
from pins import coverage

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)


# --------------------------------------------------------------------------
# The sweep's own coverage
# --------------------------------------------------------------------------

#: Every construct the emission writes, and where the question "do both
#: consumers read this as intended?" is answered -- or why it is not.
#:
#: This is the sweep's reach, stated rather than implied. A construct with
#: no entry is the thing this table exists to make impossible.
SWEPT_CONSTRUCTS = {
    # ---- checked, and the answer is yes -------------------------------
    "ConnectivityNode": "both build one bus each: cim2pp by identity, "
                        "PowSyBl by count",
    "EquivalentBranch": "both build a branch, by mRID identity in both "
                        "import files",
    "ACLineSegment": "both build a line, by mRID identity; cim2pp's 1 km "
                     "equivalent and its hardcoded 50 Hz shunt are "
                     "characterised",
    "PowerTransformer": "both build a two-winding transformer, by mRID "
                        "identity; end order is settled by 301",
    "Breaker": "both build a switch, and its SSH open state survives",
    "ExternalNetworkInjection": "both build solving equipment: cim2pp an "
                                "ext_grid routed by referencePriority, "
                                "PowSyBl a regulating Generator whose "
                                "targetV/P/Q arrive from the "
                                "RegulatingControl and SSH "
                                "(test_ieee39_benchmark, both consumers)",
    "RegulatingControl": "both consume it through the injection that "
                         "names it -- cim2pp's vm_pu is targetValue over "
                         "the bus base, PowSyBl's regulating terminal and "
                         "targetV come from the control (the same two "
                         "benchmark tests)",
    "EnergyConsumer": "cim2pp builds a load whose demand is compared "
                      "against the case's own forms",
    "SynchronousMachine": "cim2pp builds a generator only when a "
                          "SynchronousMachine has a GeneratingUnit",
    "LinearShuntCompensator": "both build a shunt from the same g and b",
    "Ground": "both keep the earth node in the network -- a bus in cim2pp, "
              "an iidm ground on a bus in PowSyBl",

    "TopologicalNode merge": "both follow it through the closed "
                             "Disconnector that states the conduction: "
                             "cim2pp builds a zero-ohm bus-bus switch on "
                             "the right pair, and PowSyBl's calculated bus "
                             "view lands on our TopologicalNodes because "
                             "retained=false is what a topology processor "
                             "folds away",

    # ---- checked, and the answer is no -------------------------------
    "GroundDisconnector": "PowSyBl yes, cim2pp NO: an earthing switch "
                          "reaches one consumer only",
    "GroundingImpedance": "NEITHER models it, so a node holding only one "
                          "gets no calculated bus in PowSyBl. Correct "
                          "rather than "
                          "lost: its r/x are ShortCircuit data and a "
                          "positive-sequence load flow has no use for "
                          "either the impedance or the node",
    "Switch across two base voltages": "a switch whose ends sit at two "
                                       "nominal voltages makes its EQ "
                                       "document NON-CONFORMANT, so the "
                                       "question of what a consumer does "
                                       "with them is downstream of a file "
                                       "no consumer should accept",

    # ---- not checked, and why ----------------------------------------
    "Analog / Measurement": "neither consumer imports the OP profile at "
                            "all, so there is nothing to measure. The "
                            "construct is validated by SHACL and by the "
                            "anchoring identity oracle instead",
    "ShortCircuit r0/x0/b0ch/g0ch": "neither consumer reads the SC "
                                    "profile, so the zero sequence has no "
                                    "consumer-side check; its evidence is "
                                    "Kersting and the physical invariants",
    "emt: add-on documents": "graceful ignore is the whole claim and it IS "
                             "tested, in both directions: PowSyBl exports "
                             "a byte-identical network with and without "
                             "them, cim2pp REJECTS them unless told not to",
    "SimulationCase": "study settings describe a study, not a network; no "
                      "network-building consumer has anything to do with "
                      "them",
    "Terminal.phases": "neither consumer exposes a phase code in its "
                       "network model, so the ABC-or-nothing rule is "
                       "answerable only against the profile, which is "
                       "where it is answered",
    "TLine device terminals": "the synthetic keys never reach a "
                              "document -- they are an extraction-side "
                              "identity, and what reaches CIM is the "
                              "ACLineSegment, checked above",
    "xnode boundary joins": "likewise extraction-side: an xnode "
                            "is a name bridge that becomes node identity "
                            "before any document is written, and the "
                            "identity oracle checks it there",
}


#: The constructs the sweep found a consumer reading differently from the
#: emission, each with the question that actually decides it.
#:
#: A disagreement is not by itself a defect. Who differs does not say what
#: to do, and the field that settles that is ``verdict``:
#:
#:   DETERMINED  something outside this repo decides it -- the profile
#:               names the class, or the constraint is about a different
#:               object entirely. There is nothing to weigh, and the
#:               emission either already matches or must be changed to.
#:   BLOCKED     the answer depends on work not yet done, and the pin
#:               naming that work is where the number lives.
#:   JUDGMENT    the spec is genuinely silent and both readings survive
#:               it, so the emission states what BOTH consumers read
#:               and counts the compromise.
#:
#: The precedent for the last is zero-impedance conduction, which is a
#: `cim:Disconnector` rather than the semantically exacter `cim:Jumper`
#: because both fit a permanent zero-ohm bond and only one reaches cim2pp.
#: That precedent is exactly why the two grounding classes below are NOT
#: judgments: 301 names a class for each of them by name, so there is no
#: silence for a consumer's convenience to fill.
DISAGREEMENTS = {
    "GroundDisconnector": (
        "DETERMINED",
        ("A switch whose far end is the earth reference. PowSyBl converts "
        "it; cim2pp's switch converter reads Breaker, Disconnector, "
        "LoadBreakSwitch and Switch by name, so it builds no element -- "
        "the two NODES survive, only the switch between them is dropped. "
        "The tempting fix is to call it a plain Disconnector, which "
        "reaches both. It is refused because the class is not a label: "
        "301 puts GroundDisconnector in the grounding family and gives "
        "that family the PhaseCode.N terminal rule this repo already "
        "conforms to, so renaming the class changes what its "
        "terminals are allowed to state. A consumer's missing converter "
        "for a class its own profile declares is that consumer's gap, and "
        "what is lost is a path to EARTH a positive-sequence load flow "
        "never had a return through.")),
    "GroundingImpedance": (
        "DETERMINED",
        ("An impedance between a transformer star point or a machine "
        "neutral and earth. NEITHER consumer models it, so a node whose "
        "only equipment is one gets no calculated bus in PowSyBl. "
        "Determined by two readings that agree rather than by one: the "
        "profile names the class outright, AND it keeps its r/x in "
        "ShortCircuit -- which is CIM saying a neutral grounding resistor "
        "is fault data. A positive-sequence load flow has no equation "
        "that reads it, so both consumers dropping it is the consumers "
        "agreeing with the profile, not disagreeing with the emission. "
        "The consequence is already carried where it belongs: the "
        "quantity registry classifies both numbers `sole` for this "
        "reason rather than as oracles nobody wrote.")),
    "Switch across two base voltages": (
        "BLOCKED",
        ("Not a class choice at all, which is why no consumer measurement "
        "can settle it: 452's Switch:connection is about the two "
        "ConnectivityNodes, and it fires because one end sits in an "
        "equipotential carrying two declared voltages. The document is "
        "invalid, so what a consumer would do with it is downstream of a "
        "file no consumer should accept. An equipotential with two "
        "voltages is the shape of an unmapped AC/DC bridge.")),
}

#: The six constructs no network-building consumer can reach, named
#: because the property is the finding.
#:
#: Everything else in this file is checked against something OUTSIDE the
#: repo: a consumer builds an object and we compare. These six cannot be,
#: and the reason is structural rather than circumstantial -- both
#: witnesses build a positive-sequence NETWORK, and a network model has
#: no phase code, no measurement, no study settings and no zero sequence
#: to compare against. So they rest on internal evidence only: SHACL, the
#: anchoring identity oracles, the reconstruction, and the quantity
#: registry.
#:
#: That is not a to-do. It is a standing property of the evidence, and
#: the reason to write it down is what it implies for CHANGE: a defect in
#: one of these six cannot be caught by a consumer the way a load's
#: demand, a transformer's end order or a grounding terminal's phase code
#: is, so a change to them earns a control rather than a consumer.
NO_CONSUMER_BLIND_SPOT = coverage(
    6,
    "constructs the emission writes that no network-building consumer "
    "reads at all, so nothing external validates them and their evidence "
    "is entirely internal: the OP profile and its Analogs, the "
    "ShortCircuit sequence data, the two emt: add-ons (whose whole claim "
    "IS being ignored), SimulationCase, Terminal.phases, and the "
    "extraction-side identities of TLine device terminals and xnode "
    "boundary joins",
    permanent="it is a property of what a WITNESS is here, not of what "
    "was checked. Both consumers build a positive-sequence network, and "
    "a network model has no place to put a phase code, a measurement, a "
    "study setting or a zero-sequence impedance -- so no amount of "
    "further work on these two makes any of the six reachable. A "
    "differently shaped consumer (something that reads ShortCircuit "
    "data, say) would not move this number either: it would be a "
    "different sweep with different witnesses, and adding it is a "
    "finding to write down rather than a decrement to apply here")


def test_every_disagreement_is_characterised_and_says_what_decides_it():
    # Who reads a construct differently is half a finding: "PowSyBl yes,
    # cim2pp no" does not say whether the emission should change.
    #
    # So each disagreement names what decides it, and the three verdicts are
    # different claims: DETERMINED means the profile already answered and
    # the only question is whether we match it; BLOCKED means the answer
    # waits on named work and the pin carries the number; JUDGMENT means
    # the emission states what both consumers read and the compromise is
    # counted. Symmetric against
    # SWEPT_CONSTRUCTS in both directions, so a new disagreement cannot be
    # recorded without a verdict and a verdict cannot outlive its
    # disagreement.
    disagreeing = {name for name, why in SWEPT_CONSTRUCTS.items()
                   if why.startswith("NEITHER") or "cim2pp NO" in why
                   or "NON-CONFORMANT" in why}
    assert set(DISAGREEMENTS) == disagreeing, (
        f"characterised {sorted(DISAGREEMENTS)} against "
        f"{sorted(disagreeing)}")
    for name, (verdict, reason) in DISAGREEMENTS.items():
        assert verdict in ("DETERMINED", "BLOCKED", "JUDGMENT"), name
        assert len(reason.split()) >= 40, f"{name} is characterised thinly"

    # And the shape of the answer, so the position is read rather than
    # re-derived: two are settled by the profile naming a
    # class, and exactly one waits on work that has a pin.
    verdicts = Counter(verdict for verdict, _ in DISAGREEMENTS.values())
    assert verdicts == {"DETERMINED": 2, "BLOCKED": 1}, dict(verdicts)


def test_the_no_consumer_blind_spot_pin_counts_the_unchecked_constructs():
    # The number is asserted against the table it summarises, so the two
    # cannot drift; the permanence is checked by test_pins.py. What this
    # adds is the JOIN: the blind spot is exactly the unchecked class of
    # the sweep, not a separate list that happens to have six entries.
    no_consumer = ("neither consumer imports", "neither consumer reads",
                   "neither consumer exposes", "study settings")
    unchecked = {name for name, why in SWEPT_CONSTRUCTS.items()
                 if why.startswith(no_consumer) or "extraction-side" in why}
    assert len(unchecked) == NO_CONSUMER_BLIND_SPOT, sorted(unchecked)
    assert NO_CONSUMER_BLIND_SPOT.permanent


def test_the_sweep_states_its_own_coverage():
    # A sweep that does not say what it did not look at is a claim about
    # whatever it happened to look at. Every construct is classified, every
    # classification says something, and the count of each is reported so a
    # reader sees the shape rather than having to total it.
    no_consumer = ("neither consumer imports", "neither consumer reads",
                   "neither consumer exposes", "study settings")
    unchecked = {name: why for name, why in SWEPT_CONSTRUCTS.items()
                 if why.startswith(no_consumer) or "extraction-side" in why}
    disagreeing = {name for name, why in SWEPT_CONSTRUCTS.items()
                   if why.startswith("NEITHER") or "cim2pp NO" in why
                   or "NON-CONFORMANT" in why}

    for name, why in SWEPT_CONSTRUCTS.items():
        assert len(why.split()) >= 8, name

    assert len(SWEPT_CONSTRUCTS) >= 20
    assert len(disagreeing) == 3, sorted(disagreeing)
    assert len(unchecked) == 6, sorted(unchecked)
    print(f"\n{len(SWEPT_CONSTRUCTS)} constructs swept: "
          f"{len(SWEPT_CONSTRUCTS) - len(disagreeing) - len(unchecked)} read "
          f"as intended by both consumers, {len(disagreeing)} not, "
          f"{len(unchecked)} unreachable from a consumer at all")
    for name in sorted(disagreeing):
        print(f"    DISAGREES: {name}")
    for name in sorted(unchecked):
        print(f"    NO CONSUMER: {name}")

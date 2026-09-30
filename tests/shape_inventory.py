"""Every ENTSO-E shape file, and whether this repo validates against it.

Broadening SHACL past what already passes finds invalidity: transformer
end numbering, negative voltage angles, a switch across the base-voltage
placeholder, and a missing `regulationCapability`. The SHAPE COVERAGE
ITSELF is a surface to examine, and a shape file nothing loads leaves its
constraints unchecked: `C:456:TP:Terminal:switch` sits in a NotSolvedMAS
variant.

So the loaded set is an inventory with a per-file verdict rather than a
list inside a test file. Every file in the ENTSO-E distribution
is either LOADED against named documents or EXCLUDED with the reason,
stated per FILE and not per family -- because "the InverseAssociation
variants are excluded" is a rule, and a rule cannot say which file it
forgot. ``test_shape_coverage.py`` compares this table against the
directory in both directions, so a file that appears, disappears or is
renamed upstream fails until someone has read it.

Two kinds of exclusion are worth telling apart, and the field says which:
a PROFILE decision (we emit no such document, so its shapes have nothing
to say) and a TOOLING one (pyshacl or rdflib cannot evaluate the file
faithfully). The second is a defect somewhere else, not a choice, and
recording it as a choice would hide it.
"""

from __future__ import annotations

import os
from typing import NamedTuple

#: Where the ENTSO-E distribution lives.
SHACL_DIR = os.environ.get("ENTSOE_SHACL", "")


class Loaded(NamedTuple):
    """Validated against the documents named in ``documents``.

    ``documents`` holds the per-document profile keys
    (EQ/TP/SC/SSH/OP/DL), or ``MERGED`` for a file whose constraints join
    two profiles and can
    only fire against the documents together, or ``HEADER`` for one that
    targets md:FullModel rather than payload.
    """

    documents: tuple[str, ...]
    why: str


class Excluded(NamedTuple):
    """Not loaded, and why -- ``kind`` is PROFILE or TOOLING."""

    kind: str
    why: str


def _profile(why: str) -> Excluded:
    return Excluded("PROFILE", why)


def _tooling(why: str) -> Excluded:
    return Excluded("TOOLING", why)


_NO_GL = ("The GeographicalLocation profile carries geo coordinates and "
          "this repo emits no GL document; a PSCAD case states no "
          "position on the earth at all.")
_NO_DY = ("The Dynamics profile carries control-system models and this "
          "repo emits no DY document: PSCAD's controls are carried as "
          "detailed models in the EMT document today, and promoting them "
          "is a "
          "mapping decision nobody has made.")
_NO_SV = ("The StateVariables profile is solved state, and PSCAD gives "
          "EMT time series, never a converged load flow, so no SV "
          "document will ever be emitted.")
_NO_EQBD = ("The EquipmentBoundary profile is a shared boundary set "
            "exchanged between parties, and this repo emits no EQBD "
            "document: a PSCAD case is one model with no boundary to "
            "another party's.")
_SOLVED = ("SolvedMAS constraints apply to a SOLVED merged model "
           "assembly, which needs an SV document this repo never emits. Our "
           "document set is a NotSolvedMAS one by construction.")
_INVERSE = ("The InverseAssociation variant demands the inverse-direction "
            "triples we deliberately never serialize: one "
            "direction per association, so loading it would report a "
            "violation for every reference the model states correctly.")
_IMPLICIT = ("The same constraints as the Explicit CrossProfile variant, "
             "restated with sh:class instead of an sh:in list over a "
             "(property rdf:type) path. sh:class needs the CGMES RDFS "
             "class hierarchy in the graph to know that a VoltageLevel is "
             "a ConnectivityNodeContainer, and an instance document does "
             "not carry it -- so this variant reports a violation for "
             "every containment the Explicit one accepts.")

#: filename -> Loaded | Excluded. Exhaustive over the distribution.
SHAPES: dict[str, Loaded | Excluded] = {
    # ---- Equipment ------------------------------------------------------
    "61970-301_Equipment-AP-Con-Complex-SHACL.ttl": Loaded(
        ("EQ",), "301's Equipment constraints: the phase-consistency rule "
        "the emitted terminal phases rest on, the switch terminal count, "
        "and the attribute "
        "value ranges that reject a negative voltage angle."),
    "61970-301_Equipment-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("EQ",), "301's single EQ constraint for an unsolved assembly, "
        "on an ACLineSegment's BaseVoltage. That BaseVoltage is often a "
        "placeholder, so it is worth having a shape watch it."),
    "61970-452_Equipment-AP-Con-Complex-SHACL.ttl": Loaded(
        ("EQ",), "452's Equipment constraints: the containment list each "
        "class gets separately, the switch voltage equality, "
        "and the transformer end value rules."),
    "61970-452_Equipment-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("EQ",), "452's unsolved-assembly EQ constraint on "
        "TapChangerControl. No focus node yet -- tap changers are "
        "unmapped -- and loaded anyway, so that mapping them cannot land "
        "unvalidated."),
    "61970-600_Equipment-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("EQ",), "600's unsolved-assembly EQ constraints, including the "
        "rule that an EquivalentInjection's regulationCapability is false "
        "outside an HVDC boundary."),
    "61970-600-1_Equipment-AP-Con-Complex-SHACL.ttl": Loaded(
        ("EQ",), "600-1 carries the only shape in the distribution that "
        "targets cim:Terminal, which every piece of equipment we emit "
        "hangs one or two of."),
    "61970-600-2_Equipment-AP-Con-Complex-SHACL.ttl": Loaded(
        ("EQ",), "600-2's complex EQ rules: the SynchronousMachine "
        "reactive-limit pair, the tap-changer neutral voltage, and the "
        "Substation count (a Warning by design -- one Substation per "
        "project is a simplification the shape flags rather than "
        "forbids)."),
    "61970-600-2_Equipment-AP-Con-Simple-SHACL.ttl": Loaded(
        ("EQ",), "The cardinality and datatype backbone: 145 target "
        "classes, and the file the mandatory regulationCapability "
        "lives in."),

    # ---- Topology -------------------------------------------------------
    "61970-301_Topology-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("TP",), "301's phase-consistency rule restated over "
        "TopologicalNode, which is the TP-side half of the constraint "
        "read from the EQ side."),
    "61970-456_Topology-AP-Con-Complex-SHACL.ttl": Loaded(
        ("TP",), "456's Topology constraints that a single document can "
        "answer, as against its NotSolvedMAS file below, whose switch rule "
        "reaches into EQ."),
    "61970-456_Topology-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("MERGED",), "`C:456:TP:Terminal:switch` -- a RETAINED switch's "
        "two terminals shall not share a TopologicalNode. It joins "
        "Terminal.ConductingEquipment in EQ to "
        "ConnectivityNode.TopologicalNode in TP, so no single-document "
        "harness can evaluate it, and it is the whole reason "
        "Switch.retained is false."),
    "61970-456_Topology-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        Loaded(("MERGED",),
               "The TopologicalNode's BaseVoltage and container types, "
               "which resolve only once EQ and TP are read together."),
    "61970-600_Topology-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("MERGED",), "600's Terminal-to-TopologicalNode rule for an "
        "unsolved assembly; cross-profile for the same reason 456's is."),
    "61970-600-2_Topology-AP-Con-Simple-SHACL.ttl": Loaded(
        ("TP",), "The TP cardinality and datatype backbone: every "
        "mandatory attribute a TopologicalNode and a ConnectivityNode "
        "carry, and the datatype of each."),

    # ---- ShortCircuit ---------------------------------------------------
    "61970-301_ShortCircuit-AP-Con-Complex-SHACL.ttl": Loaded(
        ("SC",), "301's ShortCircuit constraints on the transformer end, "
        "which is the only class this project emits into SC besides the "
        "line segment."),
    "61970-301_ShortCircuit-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("SC",), "301's MutualCoupling terminal assignment. No focus node "
        "-- nothing emits a MutualCoupling -- and loaded so that the PI "
        "sections, whose coupling is the reason to map them, cannot "
        "arrive unvalidated."),
    "61970-452_ShortCircuit-AP-Con-Complex-SHACL.ttl": Loaded(
        ("SC",), "452's ShortCircuit value rules for the machine and the "
        "transformer end, both of which this project emits and neither of "
        "whose sequence data any consumer reads."),
    "61970-452_ShortCircuit-AP-Con-Complex-CrossProfile-SHACL.ttl": Loaded(
        ("MERGED",), "MutualCoupling's terminals live in EQ and its "
        "impedances in SC, so the constraint spans the two documents."),
    "61970-600-2_ShortCircuit-AP-Con-Complex-SHACL.ttl": Loaded(
        ("SC",), "600-2's SeriesCompensator varistor rules. No focus node "
        "yet; the series compensators are on the mapping backlog."),
    "61970-600-2_ShortCircuit-AP-Con-Simple-SHACL.ttl": Loaded(
        ("SC",), "The SC cardinality and datatype backbone, including the "
        "mandatory shortCircuitEndTemperature, which the SC document "
        "states as a "
        "counted 0.0."),

    # ---- SteadyStateHypothesis ------------------------------------------
    "61970-301_SteadyStateHypothesis-AP-Con-Complex-SHACL.ttl": Loaded(
        ("SSH",), "301's SSH constraints on the source setpoints and the "
        "shunt section counts, both of which this project states for every "
        "case that has them."),
    "61970-301_SteadyStateHypothesis-AP-Con-Complex-NotSolvedMAS-SHACL.ttl":
        Loaded(("SSH",),
               "301's unsolved-assembly SSH rules, chiefly the shunt "
               "section count against its maximum."),
    "61970-456_SteadyStateHypothesis-AP-Con-Complex-SHACL.ttl": Loaded(
        ("SSH",), "456's SSH rules, including the EnergyConsumer sign "
        "convention that sends a capacitive load to EquivalentInjection."),
    "61970-600-2_SteadyStateHypothesis-AP-Con-Simple-SHACL.ttl": Loaded(
        ("SSH",), "The SSH cardinality and datatype backbone: the "
        "mandatory in-service flag on every piece of equipment and the "
        "connected flag on every terminal."),
    "61970-456_SteadyStateHypothesis-AP-Con-Complex-NotSolvedMAS-SHACL.ttl":
        _tooling(
            "pyshacl 0.40.1 refuses to evaluate this file. Its "
            "`RotatingMachine.p-limits` query contains "
            "`cim:Equipment.inService true`, and pyshacl's federated-query "
            "guard is the regex `[^?$#]S?ERVICE[\\s<]` applied "
            "case-insensitively to the query text -- which matches the "
            "tail of `inService` followed by a space. The whole file then "
            "raises \"A SPARQL Constraint must not contain a federated "
            "query (SERVICE)\" on any case carrying a machine. A validator "
            "defect, not a profile decision: the constraints themselves "
            "(a machine's p and q inside its GeneratingUnit limits) are "
            "ones this repo would want."),

    # ---- Operation ------------------------------------------------------
    "61970-301_Operation-AP-Con-Complex-SHACL.ttl": Loaded(
        ("OP",), "301's Operation constraints. No focus node -- they "
        "target the limit classes -- and loaded so that operational "
        "limits cannot arrive unvalidated."),
    "61970-452_Operation-AP-Con-Complex-SHACL.ttl": Loaded(
        ("OP",), "452 restricts an Analog's measurementType and "
        "unitSymbol to named sets; both of ours are in them."),
    "61970-452_Operation-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("OP",), "452's rule for when a Measurement must carry a "
        "Terminal, which every emitted Analog does."),
    "61970-600-2_Operation-AP-Con-Simple-SHACL.ttl": Loaded(
        ("OP",), "The OP cardinality backbone, and the file that makes "
        "Measurement.unitSymbol and .unitMultiplier mandatory. The emitter "
        "states both on every Analog."),
    "61970-600-2_Operation-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        Loaded(("MERGED",),
               "An Analog's Terminal and PowerSystemResource are EQ "
               "objects referenced from OP, so their types resolve only "
               "across the two documents."),

    # ---- DiagramLayout --------------------------------------------------
    "61970-600-2_DiagramLayout-AP-Con-Simple-SHACL.ttl": Loaded(
        ("DL",), "The DL cardinality and datatype backbone, and the file "
        "that decides three of this profile's shape: Diagram.orientation "
        "is mandatory, DiagramObjectPoint needs both coordinates and its "
        "owning object, and VisibilityLayer.VisibleObjects is 1..n -- "
        "which is why a declared layer no drawn element names is omitted "
        "rather than written empty."),
    "61970-301_DiagramLayout-AP-Con-Complex-SHACL.ttl": Loaded(
        ("DL",), "301's one live DL value rule: "
        "DiagramObjectPoint.sequenceNumber is sh:minExclusive 0, so a "
        "polyline numbered from zero is a Violation and the emitter "
        "numbers from one."),
    "61970-301_DiagramLayout-AP-Con-Complex-NotSolvedMAS-SHACL.ttl": Loaded(
        ("DL",), "301's unsolved-assembly DL rule -- a DiagramObject "
        "shall link to a SynchronousMachine and not to its GeneratingUnit. "
        "No focus node: nothing here states "
        "DiagramObject.IdentifiedObject at all, because a diagram is a "
        "definition's page and every equipment subject is per instance. "
        "Loaded so that linking them cannot land unvalidated."),
    "61970-453_DiagramLayout-AP-Con-Complex-SHACL.ttl": Loaded(
        ("DL",), "453 restricts a cim:DiagramStyle's name to four values "
        "-- node-breaker, bus-branch, hybrid, geoschematic -- none of "
        "which a PSCAD schematic is. No focus node, because no "
        "DiagramStyle is emitted, and loaded so that inventing one would "
        "have to answer this shape."),
    "61970-453_DiagramLayout-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        Loaded(("MERGED",),
               "The WHITELIST on DiagramObject.IdentifiedObject: a long "
               "sh:in list of classes whose rdf:type resolves only once "
               "DL and EQ are read together. It is a whitelist and not "
               "the blacklist 301's commented-out variant reads as, and "
               "cim:DetailedModelDynamics is NOT in it -- so a diagram "
               "object could not point at a detailed model even where one "
               "drawn element meant one of them."),

    # ---- header ---------------------------------------------------------
    "61970-552-Header-AP-Con-Simple-SHACL.ttl": Loaded(
        ("HEADER",), "The md:FullModel cardinalities. Every emitted "
        "document carries a header, and these shapes are what validate "
        "it."),
    "61970-600-1_AllProfiles-AP-Con-Complex-SHACL.ttl": Loaded(
        ("HEADER",), "600-1's header rules: the profile URI must be a "
        "CGMES one, and created/scenarioTime must be UTC ending in Z."),
    "61970-600-1_Prof10-Header-AP-Con-Complex-SHACL.ttl": Loaded(
        ("HEADER",), "PROF10: which document depends on which. Every rule "
        "reads the DEPENDENCY's own Model.profile, so it fires only "
        "against all the headers at once -- the second cross-document "
        "family after 456's switch rule."),
    "61970-600-2_IdentifiedObjectCommon_AP-Con-Complex-SHACL.ttl": Loaded(
        ("EQ", "TP", "SC", "SSH", "OP", "DL"),
        "The IdentifiedObject string-length rules, which belong to no one "
        "profile and apply wherever a named object is written."),

    # ---- excluded: no such document -------------------------------------
    "61968-13_GeographicalLocation-AP-Con-Complex-SHACL.ttl": _profile(_NO_GL),
    "61970-600-2_GeographicalLocation-AP-Con-Complex-SHACL.ttl":
        _profile(_NO_GL),
    "61970-600-2_GeographicalLocation-AP-Con-Simple-SHACL.ttl":
        _profile(_NO_GL),
    "61970-600-2_GeographicalLocation-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        _profile(_NO_GL),
    "61970-302_Dynamics-AP-Con-Complex-SHACL.ttl": _profile(_NO_DY),
    "61970-457_Dynamics-AP-Con-Complex-SHACL.ttl": _profile(_NO_DY),
    "61970-457_Dynamics-AP-Con-Complex-NotSolvedMAS-SHACL.ttl":
        _profile(_NO_DY),
    "61970-457_Dynamics-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        _profile(_NO_DY),
    "61970-600-2_Dynamics-AP-Con-Simple-SHACL.ttl": _profile(_NO_DY),
    "61970-301_StateVariables-AP-Con-Complex-SHACL.ttl": _profile(_NO_SV),
    "61970-456_StateVariables-AP-Con-Complex-SHACL.ttl": _profile(_NO_SV),
    "61970-456_StateVariables-AP-Con-Complex-Explicit-CrossProfile-SHACL.ttl":
        _profile(_NO_SV),
    "61970-600-2_StateVariables-AP-Con-Simple-SHACL.ttl": _profile(_NO_SV),
    "61970-301_EquipmentBoundary-AP-Con-Complex-SHACL.ttl": _profile(_NO_EQBD),
    "61970-301_EquipmentBoundary-AP-Con-Complex-NotSolvedMAS-SHACL.ttl":
        _profile(_NO_EQBD),
    "61970-600-2_EquipmentBoundary-AP-Con-Simple-SHACL.ttl":
        _profile(_NO_EQBD),

    # ---- excluded: a solved merged assembly -----------------------------
    "61970-301_StateVariables-AP-Con-Complex-SolvedMAS-SHACL.ttl":
        _profile(_SOLVED),
    "61970-456_AllProfiles-AP-Con-Complex-SolvedMAS-SHACL.ttl":
        _profile(_SOLVED),
    "61970-456_StateVariables-AP-Con-Complex-SolvedMAS-SHACL.ttl":
        _profile(_SOLVED),
    "61970-600-1_AllProfiles-AP-Con-Complex-SolvedMAS-SHACL.ttl":
        _profile(_SOLVED),
    "61970-600-2_AllProfiles-AP-Con-Complex-SolvedMAS-SHACL.ttl":
        _profile(_SOLVED),

    # ---- excluded: the inverse association direction --------------------
    "61970-600-2_DiagramLayout-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_Dynamics-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_Equipment-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_EquipmentBoundary-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_GeographicalLocation-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_Operation-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_StateVariables-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),
    "61970-600-2_Topology-AP-Con-Complex-InverseAssociation-SHACL.ttl":
        _profile(_INVERSE),

    # ---- excluded: the Implicit restatement of a loaded file ------------
    "61970-453_DiagramLayout-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT),
    "61970-456_StateVariables-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT + " (Also SV, which this repo never emits.)"),
    "61970-456_Topology-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT),
    "61970-457_Dynamics-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT + " (Also DY, which we do not emit.)"),
    "61970-600-2_GeographicalLocation-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT + " (Also GL, which we do not emit.)"),
    "61970-600-2_Operation-AP-Con-Complex-Implicit-CrossProfile-SHACL.ttl":
        _tooling(_IMPLICIT),
}


def _files_for(document: str) -> list[str]:
    return sorted(name for name, verdict in SHAPES.items()
                  if isinstance(verdict, Loaded)
                  and document in verdict.documents)


#: The per-document shape sets, DERIVED from the inventory so the two
#: cannot drift: a file loaded here is loaded there, and a file nobody
#: classified is loaded nowhere.
EQ_SHAPES = _files_for("EQ")
TP_SHAPES = _files_for("TP")
SC_SHAPES = _files_for("SC")
SSH_SHAPES = _files_for("SSH")
OP_SHAPES = _files_for("OP")
DL_SHAPES = _files_for("DL")

#: Constraints that join two profiles: they fire only against the merged
#: graph, and the per-document harness structurally cannot see them.
MERGED_SHAPES = _files_for("MERGED")

#: Constraints on the md:FullModel header, which is written at file
#: emission and is absent from every in-memory profile graph.
HEADER_SHAPES = _files_for("HEADER")

PROFILE_SHAPES = {"EQ": EQ_SHAPES, "TP": TP_SHAPES, "SC": SC_SHAPES,
                  "SSH": SSH_SHAPES, "OP": OP_SHAPES, "DL": DL_SHAPES}


def paths(filenames) -> list[str]:
    """Shape filenames as absolute paths in the distribution."""
    return [os.path.join(SHACL_DIR, name) for name in filenames]

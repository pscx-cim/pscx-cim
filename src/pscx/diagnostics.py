"""Structured diagnostics: what is missing, approximate or wrong.

A METRIC says what the mapping covered (resistors, ammeters); it
lives on the model that produced it and is not reported here. A
DIAGNOSTIC says something is missing, approximate or wrong, and carries
enough to act on it: a severity, a reason, and either a source span or
the provenance of the thing that produced it.

The severity taxonomy is not error/warn/info. It names the four things
this project has to tell apart, ordered by how wrong the
value that reaches a consumer is:

  ERROR   results are wrong and nothing marks which ones. An unresolvable
          master library is the type case: every definition fails to
          resolve, Series_AF's nodes collapse to a few, and the output is
          a plausible-looking network of the wrong circuit.
  GAP     "we don't know": the emitted value is a STAND-IN. A 1.0 kV
          BaseVoltage, x = 0.0, a breaker defaulted closed. Worse than a
          biased number, because computing with fiction yields fiction.
  BIAS    "we know, and it is wrong in a KNOWN direction": a real value
          from a real derivation, off by a bounded amount. Line series R
          is the DC value, low at power frequency because conductor
          skin effect is not modeled. A consumer can correct for this;
          it cannot correct for a stand-in, which is why the two cannot
          share a category.
  DEFECT  a shipped-library bug reproduced faithfully: mmc_FullCell's
          dangling $RTOFF, intermediate.pslx's `$`-less node tokens,
          LightningCoord's RowCanvas references to definitions its
          project does not contain. These are
          EXPECTED and pinned -- their absence is as suspicious as their
          growth -- so they are never a reason to fail a run.

Every code is declared once in CATALOG with its severity and the reason
it exists. Emitting an uncatalogued code raises: the catalog is only a
complete index of what this program can report if it cannot be bypassed,
and a pinned number whose reason lives nowhere is what this design
replaces.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum


class UncataloguedCode(KeyError):
    """A code was emitted that :data:`CATALOG` does not declare.

    A ``KeyError`` so callers that catch one still do, but it says what
    happened and where to fix it: ``KeyError: 'some_code'`` alone tells a
    user nothing about a catalog they have never heard of.
    """

    def __init__(self, code: str) -> None:
        super().__init__(
            f"diagnostic code {code!r} was emitted but is not registered. "
            f"Every code is declared once in CATALOG in "
            f"src/pscx/diagnostics.py with its severity and the reason it "
            f"exists; declare {code!r} there."
        )


class Severity(IntEnum):
    """Ordered by how wrong the value that reaches a consumer is; see the
    module docstring for what each category means."""

    DEFECT = 0
    BIAS = 1
    GAP = 2
    ERROR = 3


@dataclass(frozen=True)
class Span:
    """Where in a source file. ``line`` is lxml's ``sourceline``, which is
    what lxml is used for."""

    file: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file


@dataclass(frozen=True)
class Provenance:
    """Which thing produced it, for a diagnostic with no single source
    line. A pass deep in elaboration is reading a merged view of many
    files; naming an instance and an element is honest where naming a
    line would be invented.

    ``instance`` is the path through the instance tree, and it is not
    redundant with ``canvas``: a module drawn once can be placed many
    times, so the canvas name alone does not say WHICH placement reported
    this (LightningCoord places one right-of-way module repeatedly).
    """

    case: str | None = None
    canvas: str | None = None
    instance: str | None = None
    element: str | None = None

    def __str__(self) -> str:
        return "/".join(p for p in (self.case, self.canvas, self.instance,
                                    self.element) if p)


@dataclass(frozen=True)
class Kind:
    """A catalogued code: its severity and why it exists."""

    severity: Severity
    message: str


@dataclass
class Diagnostic:
    """One finding. ``count`` aggregates repetitions the caller counted
    itself (many dc-only phases are one finding, not one call each)."""

    code: str
    severity: Severity
    message: str
    detail: str | None = None
    span: Span | None = None
    provenance: Provenance | None = None
    count: int = 1

    @property
    def key(self) -> str:
        """The flat counter key: ``code`` alone, or ``code: detail``. This
        is what every pin reads."""
        return f"{self.code}: {self.detail}" if self.detail else self.code

    def __str__(self) -> str:
        where = self.span or self.provenance
        return (f"{self.severity.name} {self.key}"
                + (f" at {where}" if where else "")
                + (f" (x{self.count})" if self.count != 1 else ""))


class Diagnostics:
    """A bus of findings. Records are kept individually -- that is what
    says WHICH element -- and aggregated on demand."""

    def __init__(self) -> None:
        self._records: list[Diagnostic] = []
        self._suppress = 0

    def emit(self, code: str, detail: str | None = None, *,
             span: Span | None = None, provenance: Provenance | None = None,
             count: int = 1) -> None:
        try:
            kind = CATALOG[code]
        except KeyError:
            raise UncataloguedCode(code) from None
        if self._suppress:
            return
        self._records.append(Diagnostic(
            code=code, severity=kind.severity, message=kind.message,
            detail=detail, span=span, provenance=provenance, count=count))

    @property
    def suppressing(self) -> bool:
        """Whether an :meth:`emit` right now would be discarded.

        A caller that dedupes its OWN findings has to ask: a registry of
        "already reported" entries filled while the bus was suppressed
        records a report that never happened, and because the registry is
        permanent for the process no later call re-emits it. The finding
        is then unreachable rather than merely quiet.
        """
        return bool(self._suppress)

    def records(self) -> list[Diagnostic]:
        return list(self._records)

    def counts(self) -> Counter:
        """Keyed by ``code: detail``."""
        totals: Counter = Counter()
        for record in self._records:
            totals[record.key] += record.count
        return totals

    def count(self, key: str) -> int:
        """How many of one flat ``code: detail`` key. Zero for a key never
        emitted -- the pins that assert an absence read through here."""
        return self.counts()[key]

    def by_code(self) -> Counter:
        """Keyed by code alone, so a code whose detail varies per element
        still aggregates to one number."""
        totals: Counter = Counter()
        for record in self._records:
            totals[record.code] += record.count
        return totals

    def above(self, severity: Severity) -> list[Diagnostic]:
        return [r for r in self._records if r.severity > severity]

    def worst(self) -> Severity | None:
        return max((r.severity for r in self._records), default=None)

    def extend(self, other: Diagnostics) -> None:
        if not self._suppress:
            self._records.extend(other._records)

    def clear(self) -> None:
        self._records.clear()

    @contextmanager
    def suppressed(self):
        """Discard everything emitted inside.

        The detailed-model emitter probes every proprietary parameter for a
        numeric value, and a parameter that does not resolve to one is not
        a defect.
        Failing to resolve one says nothing about the case, and reporting
        them would drown the pinned counters.
        """
        self._suppress += 1
        try:
            yield self
        finally:
            self._suppress -= 1

    def __bool__(self) -> bool:
        return bool(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        # bounded on purpose: a failing assertion reprs the frames that
        # hold it, and an unbounded repr of a large run's findings can
        # exhaust memory
        worst = self.worst()
        return (f"<Diagnostics {len(self._records)} records, "
                f"worst={worst.name if worst else 'none'}>")


#: The process-wide bus. Extraction reaches it from everywhere and has no
#: object to hang a per-run bus off -- a definition is loaded once and
#: shared by every case in a session. Emission keeps its own per-model bus
#: and merges into this one, so a caller can ask either "what did this
#: model report" or "what happened in this run".
DIAGNOSTICS = Diagnostics()


def register(code: str, severity: Severity, message: str) -> None:
    """Declare a code. Registering one twice is a name collision, not an
    update -- two call sites reporting different things under one code
    would make the pin on that code meaningless."""
    if code in CATALOG:
        raise KeyError(f"diagnostic code already registered: {code}")
    CATALOG[code] = Kind(severity, message)


#: Every code this program can report. Keyed by code, never by the flat
#: `code: detail` key -- the detail varies per element and is not part of
#: the identity of the finding.
CATALOG: dict[str, Kind] = {}


def _catalog(severity: Severity, entries: dict[str, str]) -> None:
    for code, message in entries.items():
        register(code, severity, message)


# --------------------------------------------------------------------------
# Loading and parsing: the source is not what we expect
# --------------------------------------------------------------------------

_catalog(Severity.ERROR, {
    "unparseable_library": (
        "A component library did not load, so every definition it holds is "
        "unresolvable and every component placed from it silently vanishes. "
        "Series_AF loses nearly all of its electrical nodes this way, and "
        "what is left still looks like a network."),
    "empty_library": (
        "A component library parsed cleanly and declared no definitions at "
        "all. The consequence is `unparseable_library`'s -- every placement "
        "from it is unresolvable -- but no parse error announces it, so the "
        "only signal left is one `unresolved_definition` per placement, and "
        "not one of those names the library as the reason. This is that "
        "sentence, said once, at the load."),
    "unparseable_case": (
        "The case file did not parse; nothing was extracted from it."),
    "unresolved_definition": (
        "A placed component names a definition no loaded library provides, "
        "so its ports and script segments are unknown and it contributes no "
        "topology at all."),
    "recursive_module": (
        "A module instantiates itself, directly or through a cycle; the "
        "instance tree cannot be finite and the branch is abandoned."),
    "station_root_missing": (
        "The StationCanvas names a root module the project does not define, "
        "so the whole instance tree is missing."),
    "station_foreign_root": (
        "The root module resolves outside the project's own namespace, so "
        "the instance tree would be rooted in a definition whose parameters "
        "are not the project's."),
    "component_bad_coords": (
        "A component's placement coordinates are not numbers, so it cannot "
        "be positioned and every wire junction it should form is lost."),
    "port_bad_coords": (
        "A port's coordinates are not numbers, so the port cannot be placed "
        "and the node it belongs to is split."),
    "wire_bad_coords": (
        "A wire's origin is not a number, so its whole geometry is "
        "displaced and the nets it forms are wrong."),
    "vertex_bad_coords": (
        "A wire vertex is not a number; the segment through it cannot be "
        "tested for coincidence, so junctions on it are lost."),
})

# --------------------------------------------------------------------------
# The script preprocessor and its grammars
# --------------------------------------------------------------------------

_catalog(Severity.ERROR, {
    "branch_bad_case": (
        "A #CASE splice does not match the enumerated grammar, so its arms "
        "cannot be decomposed into guarded alternatives."),
    "branch_case_arm_unglued": (
        "A #CASE arm does not glue onto the open logical line it continues; "
        "the resulting Branch declaration would be a fragment."),
    "branch_case_dangling_glue": (
        "A #CASE arm continues a logical line that was never opened."),
    "branch_splice_reopen": (
        "A `~` opens a logical line while one is already open; the splice "
        "grammar admits one at a time."),
    "branch_splice_broken": (
        "A spliced line could not be reassembled from its parts."),
    "branch_splice_unterminated": (
        "A logical line opened with `~` is never closed, so its Branch "
        "declaration is lost entirely."),
    "branch_unparsed_line": (
        "A Branch line does not match the enumerated grammar; every "
        "declaration in master does, so a new shape means the grammar "
        "is incomplete."),
    "branch_unparsed_body": (
        "A Branch body is neither 1-3 positional values nor a "
        "BREAKER/AMMETER/SOURCE form."),
    "script_brace_underflow": (
        "A one-line brace conditional closes a brace that was never opened, "
        "so the guard stack is corrupt from here on."),
    "script_brace_elseif": (
        "An #ELSEIF inside a brace body. The brace grammar has no meaning "
        "for it."),
    "script_output_inside_brace_body": (
        "An #OUTPUT directive inside a brace body. Brace bodies splice "
        "inline text and never declare writers, so a directive here means "
        "the brace was misread as a block opener."),
    "script_unbalanced_elseif": (
        "An #ELSEIF with no open #IF; the guard chain is corrupt and every "
        "later line carries the wrong guards."),
    "script_unbalanced_else": (
        "An #ELSE with no open #IF."),
    "script_unbalanced_endif": (
        "An #ENDIF with no open #IF."),
    "script_guard_parse_failed": (
        "An #IF guard does not parse under the condition grammar, so the "
        "grammar is incomplete and the guarded lines are taken blind."),
    "cond_parse_failed": (
        "A port condition does not parse and is taken as true, which "
        "selects a port set that may not be the one the form intends."),
    "expr_function_call": (
        "An arithmetic expression calls a function. master.pslx contains "
        "no function call anywhere, so the grammar has no evaluation for "
        "one and the value is lost."),
    "expr_parse_failed": (
        "An arithmetic expression does not parse, so the quantity it "
        "states is unavailable."),
    "computation_unparsed": (
        "A Computations line is not `TYPE name = expr`; the results it "
        "defines are missing from every expression that references them."),
    "computation_failed": (
        "A Computations expression parsed but did not evaluate, so its "
        "result is missing from the environment."),
    "param_cycle": (
        "A parameter's value refers to itself through the hierarchy, so "
        "resolution cannot terminate."),
    "branch_node_syntax": (
        "A Branch node token is neither `$port`, `$port(index)` nor a "
        "literal 0; the endpoint cannot be placed."),
    "branch_node_unknown_port": (
        "A Branch node names a port the definition does not declare, so "
        "the branch has an endpoint nowhere on the component."),
    "dim_conflict_on_node": (
        "Two ports on one electrical node declare different phase counts, "
        "and the resolution picked one of them."),
    "unit_unconverted": (
        "A value is written in one unit and declared in another, and no "
        "conversion is known for the pair, so the number passes through "
        "wrong by an unknown factor."),
})

_catalog(Severity.GAP, {
    "hir_unrecognized": (
        "The project states an element or attribute the loader cannot give "
        "a meaning to. It is kept verbatim with its span rather than "
        "dropped, so what reaches a consumer is incomplete in a way the "
        "consumer can see, which is what makes this a gap and not an "
        "error. `memberof`, `layer` and the dead WireBranch attributes are "
        "what this channel makes visible."),
    "script_guard_macro_fail_open": (
        "A `$`-macro in an #IF guard did not resolve, so the guard is "
        "taken as true and both arms' content may be admitted."),
    "substitution_unresolved": (
        "A placed component's parameter reads `$(x)` and the case declares "
        "no `<Sub>` for x, so the reference falls back to the string `0` -- "
        "a frequency or a rating nobody typed, in a field that states one. "
        "Split from `substitution_intrinsic` because this one is a hole in "
        "the drawing: some case DOES declare that global."),
    "substitution_intrinsic": (
        "A placed component's parameter reads `$(x)` for a name PSCAD "
        "substitutes itself at build time, which this program does not "
        "implement, so the reference falls back to `0`. master.pslx is the "
        "witness: it declares no `<Sub>` at all and still places components "
        "whose captions read `$(Name) [$(Rank)]`, which a shipped library "
        "could not do if those came from a user's globals."),
    "param_unresolved_at_root": (
        "A parameter expression reaches the root instance still unresolved; "
        "the root's environment is the form defaults and has nothing "
        "further to resolve against."),
    "param_name_missing": (
        "A parameter named in an expression is declared nowhere in the "
        "instance's hierarchy, so the quantity it states is unknown."),
    "port_dim_unresolved": (
        "A port's `:Name` dimension suffix resolves to no parameter or "
        "Computations result, so its phase count falls back to whatever "
        "it is connected to."),
    "memberof_nonempty": (
        "A `memberof` entry has a value. It is system-written and empty on "
        "every instance in master; canvas-group "
        "membership is the only documented meaning and that reading is "
        "inferred, not proven."),
    "bridge_empty_name": (
        "A datalabel/import/export names nothing, so it bridges its wire "
        "net onto no name and the net is left unjoined."),
    "layer_undeclared": (
        "A component names a drawing layer the project does not declare, "
        "so whether it is compiled cannot be decided."),
    "layer_custom_state": (
        "A drawing layer is in an Advanced Layering custom state rather "
        "than enabled/disabled/invisible; what it does to compilation is "
        "undocumented."),
    "rlc_nonmaster": (
        "An R/L/C-shaped component resolves outside master, so its Branch "
        "rows are not the ones the primitive mapping assumes."),
    "rlc_branch_count": (
        "A two-terminal R/L/C component yields other than one active "
        "Branch row, so which row carries its value is undecidable."),
    "rlc_value_unresolved": (
        "An R/L/C component's value does not resolve to a number, so no "
        "impedance can be stated for it."),
    "rlc_port_missing": (
        "An R/L/C component's Branch row names endpoints that are not "
        "placed ports, so the element cannot be connected."),
})

_catalog(Severity.ERROR, {
    "branch_bad_nodes": (
        "A Branch node token is neither `$name`, a bare `name(index)` nor "
        "a literal 0, so the branch has an endpoint the grammar cannot "
        "place. The known `$`-less tokens are NOT this: they match the "
        "bare form and are tracked as a defect."),
    "branch_bad_value": (
        "A Branch value token is neither a number nor a `$name`, so the "
        "quantity is unreadable. A dangling `$name` is NOT this: it is "
        "syntactically fine and fails later, as a defect."),
})

_catalog(Severity.DEFECT, {
    "script_unreachable_arm": (
        "A conditional arm's guard chain requires one condition to be both "
        "true and false, so NO environment can ever select it. master ships "
        "such arms: `unity` opens `#IF IType == 0` twice, and "
        "`db_xfmr_3p2w`'s Matrix-Fill omits an `#ENDIF`, in both cases "
        "burying the later arms inside the earlier one. Same family as "
        "mmc_FullCell's dangling $RTOFF: the library says something it "
        "cannot mean."),
    "branch_bare_node": (
        "A Branch node token is missing its `$`. intermediate.pslx's "
        "xfmr-3p4w2 writes `{~N2(3) $G2~}` in one #CASE arm of each BRS "
        "row, a typo for `$N2(3)`. The parser strips `$` anyway, so the token "
        "resolves as the author intended."),
    "branch_dangling_value": (
        "A Branch value names a parameter the definition does not "
        "declare. master's mmc_FullCell says `BREAKER $RTOFF` but "
        "declares Roff/RIoff, copied from mmc_HalfCell without the "
        "rename, and a placement reaches it."),
})

# --------------------------------------------------------------------------
# The LIR
# --------------------------------------------------------------------------

_catalog(Severity.ERROR, {
    "lir_phase_mismatch": (
        "A branch's two endpoints expand to different phase counts, so "
        "there is no per-phase pairing between them."),
    "lir_internal_collision": (
        "A device-internal singleton node collided with a real flat node "
        "key. The keys are coordinate-derived and COULD collide by "
        "accident, which would short a device's internals onto the network."),
    "lir_dangling_endpoint": (
        "A branch endpoint resolves to no node, so the branch is connected "
        "at one end only."),
})

# --------------------------------------------------------------------------
# Line constants
# --------------------------------------------------------------------------

_catalog(Severity.ERROR, {
    "modeldata_block_unanchored": (
        "A Model-Data brace body has no `key =` to attach to, so the "
        "record it states is lost."),
    "modeldata_brace_underflow": (
        "A Model-Data segment closes a brace it never opened."),
    "modeldata_inline_brace": (
        "A Model-Data line mixes an inline brace with a value in a shape "
        "the reader has no rule for."),
    "modeldata_unparsed": (
        "A Model-Data line is not `key = value`; the tower record it "
        "belongs to is incomplete."),
    "modeldata_brace_unbalanced": (
        "A Model-Data segment ends with braces still open, so its guard "
        "context never closed."),
    "modeldata_operand_expr": (
        "A Model-Data operand is an expression shape the arithmetic "
        "grammar does not cover."),
})

_catalog(Severity.GAP, {
    "modeldata_operand_missing": (
        "A Model-Data operand names a parameter the component does not "
        "state, so that conductor coordinate is unknown."),
    "modeldata_operand_nonnumeric": (
        "A Model-Data operand does not evaluate to a number, so the "
        "geometry it states is unusable."),
    "modeldata_operand_unit": (
        "A Model-Data operand is written in a unit the conversion table "
        "does not cover, so its magnitude is uncertain."),
    "modeldata_operand_unevaluated": (
        "A parameterised Model-Data operand resolves to no number through "
        "the placement's parameters or the definition's Computations, so "
        "the evaluated right-of-way record carries the raw token instead "
        "of a value an engine can consume."),
    "lineconst_unsupported": (
        "The right-of-way uses a data-entry mode or a construction the "
        "solver does not implement, so the segment keeps its placeholder "
        "impedance rather than a derived one."),
    "lineconst_missing_ground_data": (
        "The right-of-way states no earth resistivity, so the earth-return "
        "impedance cannot be solved."),
    "lineconst_tower_phases": (
        "A tower component states a phase count the geometry reader has no "
        "layout for."),
    "lineconst_tower_position": (
        "A tower conductor position does not resolve to a coordinate."),
    "lineconst_gw_position": (
        "A ground-wire position does not resolve to a coordinate, so it "
        "cannot be Kron-eliminated."),
    "lineconst_no_length": (
        "The line states no length, so per-unit-length constants cannot be "
        "turned into segment impedances."),
    "lineconst_dc_line": (
        "The line declares 0 Hz. A DC line has no power-frequency positive "
        "sequence at all, so there is nothing for ACLineSegment's r/x to "
        "hold; the MMC and TCCSC bipoles are that population."),
    "lineconst_no_frequency": (
        "The line states no frequency, so no reactance can be computed at "
        "one."),
    "lineconst_length_unitless": (
        "A TLine writes Length with no unit. It is read as kilometres, "
        "the unit the line form declares, which is an assumption about "
        "the line and not a statement it makes."),
    "lineconst_manual_incomplete": (
        "A manually-entered Y/Z matrix is missing entries, so the sequence "
        "impedances cannot be extracted from it."),
    "lineconst_manual_no_base": (
        "A manually-entered matrix states no base to interpret its values "
        "against."),
    "lineconst_manual_no_zero_sequence": (
        "A manual entry states no zero sequence: its group is guarded by "
        "`(NCond>=2)&&(Estim==0)`, and a single-conductor entry fails the "
        "conductor-count arm, so the form never shows the group at all."),
})

_catalog(Severity.BIAS, {
    "lineconst_resistance_dc_only": (
        "Series resistance is the conductor's DC value: skin effect is not "
        "modeled, so R is LOW, and by more for larger conductors. "
        "A real derived value wrong in a known "
        "direction, which a consumer can correct for -- unlike a "
        "placeholder."),
})

_catalog(Severity.DEFECT, {
    "cim_param_case_collision": (
        "A placement states one parameter name in two spellings that "
        "differ only in case, with two DIFFERENT values. Case-insensitive "
        "naming makes the "
        "two keys one name and says nothing about which value is meant, "
        "so whichever is read leaves the other stated value unused. "
        "PSCAD's own dualTF_TestCase files ship this on the "
        "duality_*_tf transformers' rL_/rl_ (2.0 beside 2.5), and in some "
        "of them one spelling's value is the parameter's own name. Same "
        "family as mmc_FullCell's dangling $RTOFF: the file says something "
        "it cannot mean."),
    "lineconst_rowdefn_missing": (
        "A TLine names a RowCanvas definition its own project does not "
        "contain. LightningCoord ships `(null):T11-c1` and `(null):T12-11` "
        "this way, a shipped-case dangling "
        "reference in the same family as mmc_FullCell's dangling $RTOFF."),
})

# --------------------------------------------------------------------------
# CIM emission
# --------------------------------------------------------------------------

# Two objects minting one mRID raise `pscx.cim.MridCollision` at the point
# of registration rather than leaving a counted ERROR and an overwritten
# resource behind, so no code for a collision is catalogued. A code the
# program cannot emit does not belong in an index of what it can report,
# and keeping one would let an `== 0` assertion read as evidence.
_catalog(Severity.ERROR, {
    "cim_line_impedance_out_of_range": (
        "A derived line impedance falls outside the physically plausible "
        "range for its class, so the derivation is wrong rather than "
        "merely approximate."),
    "cim_element_self_loop": (
        "Both ends of one drawn two-terminal element land on ONE drawn "
        "node -- the per-phase edge joined two conductors of the same "
        "bundle. A two-terminal CIM object on one ConnectivityNode states "
        "nothing, so the element is dropped rather than emitted as a "
        "self-loop."),
    "cim_element_phase_overlap": (
        "Two per-phase edges of one drawn element occupy the same "
        "conductor pair, so they are PARALLEL elements rather than "
        "conductors of one element and grouping them would merge two "
        "pieces of equipment into one."),
    "cim_line_phase_mismatch": (
        "A line's two ends carry conductor counts that cannot be paired, "
        "so no per-phase correspondence exists between them."),
    "cim_multiple_ground_nodes": (
        "A case presents more than one ground node, which breaks the "
        "reading that licenses an implicit earth: a one-terminal shunt's "
        "absent second end is THE ground node only while there is exactly "
        "one for it to be. Every such shunt would then be attached to an "
        "ambiguous earth with nothing marking which -- hence ERROR rather "
        "than GAP. It is structurally zero: flatten() unions every "
        "instance's ground key into one."),
})

_catalog(Severity.GAP, {
    "cim_basevoltage_unknown": (
        "No witness states this node's nominal voltage, so it carries the "
        "1.0 kV placeholder. Per-unit results computed against it are "
        "meaningless."),
    "cim_basevoltage_node_conflict": (
        "A node's own witnesses state different voltages, so none is "
        "adopted and the placeholder stands."),
    "cim_basevoltage_island_conflict": (
        "A galvanic island holds more than one declared voltage -- usually "
        "an island merged across an unmapped converter, pooling the AC and "
        "DC sides -- so nothing propagates into its unknown nodes."),
    "cim_basevoltage_cross_check_conflict": (
        "Two independent witnesses disagree on one island's voltage. Such "
        "a conflict is real: a source set off nominal, or an island merged "
        "across a converter."),
    "cim_basevoltage_source_single_phase": (
        "A single-phase source states kV line-to-ground where CIM's "
        "nominalVoltage is phase-to-phase, and NEITHER reading of it is "
        "declared. Measured rather than assumed: taking the value as "
        "stated and scaling it by sqrt(3) each resolve a few cases, and "
        "both are corroborated by no independent witness in any island "
        "and contradicted by one -- so either would be inventing a winner."),
    "cim_basevoltage_source_dc": (
        "A source whose own form says DC is not a witness to any AC "
        "nominal voltage, so it declares nothing and the node keeps "
        "whatever the AC side states about it. The field it would have "
        "been read from -- `Vm`, labelled \"Rated Volts (AC:L-G, RMS)\" -- "
        "stays enabled under either source type, so it is readable on a "
        "DC source and means nothing there. This is where base voltage "
        "would cross the AC/DC boundary if anything let it."),
    "cim_source_setpoint_dc": (
        "The source's form says DC, and a voltage regulation target is a "
        "phase-to-phase AC magnitude. The field the target would come "
        "from (`Es`) is the one the form DISABLES under this source "
        "type, so reading it reports a number nobody typed. The DC "
        "magnitude the case does state (`Esd`) has no standard attribute "
        "to go in."),
    "cim_ground_voltage_conflict": (
        "Ground-side switches in one case reach live nodes at DIFFERENT "
        "nominal voltages. The earth is one flat node and so has "
        "one VoltageLevel, and 452 requires a switch's two nodes to share "
        "a nominal voltage, so no container satisfies both switches at "
        "once. The earth keeps the placeholder and the switches are "
        "counted non-conformant rather than one of them being preferred."),
    "cim_tn_kv_conflict": (
        "Nodes joined by zero-impedance equipment are one equipotential "
        "but resolve to different voltages, so the TopologicalNode has no "
        "single base voltage."),
    "cim_equipment_spans_voltage_levels": (
        "One piece of equipment terminates in two voltage levels, so no "
        "single container satisfies the containment rules."),
    "cim_branch_placeholder_reactance": (
        "The case determines no system frequency, so an inductance or "
        "capacitance cannot be turned into a reactance and x = 0.0 stands "
        "in. 60 Hz is never assumed."),
    "cim_line_placeholder_impedance": (
        "The segment's right-of-way yields no derived impedance, so r/x/b "
        "are placeholders rather than line constants."),
    "cim_breaker_state_default": (
        "The breaker's control signal is driven by logic or by simulation "
        "events, so its state at t=0 is undetermined and it is emitted "
        "closed."),
    "cim_source_injection_placeholder": (
        "The source form states no live initial condition, so its P/Q "
        "injection is a solver outcome rather than case data, but SSH "
        "makes both mandatory, so 0.0 is written."),
    "cim_source_setpoint_undetermined": (
        "The source's voltage setpoint could not be resolved, so no "
        "RegulatingControl is stated and controlEnabled is False; the "
        "attributed reason is counted alongside."),
    "cim_source_reference_priority_designated": (
        "ExternalNetworkInjection.referencePriority is mandatory SSH "
        "state and the case states no priority -- PSCAD has no slack "
        "concept, its sources each pin their own reference -- so the "
        "uniform 1 is a consumer-mandated designation: 0 would demote "
        "every source to a non-reference in cim2pp's ext_grid routing."),
    "cim_source_capability_placeholder": (
        "600-2 mandates the injection's capability envelope (maxP/minP, "
        "maxQ/minQ) and governorSCD, and no PSCAD source form states a "
        "capability or a frequency bias at all, so the bounds are the "
        "symmetric non-binding sentinel and the bias is 0.0 -- "
        "fabrications a consumer's limit check must never bind on."),
    "cim_source_angle_addon_only": (
        "The source states a live phase angle (`Ph`) and the injection "
        "vocabulary regulates a magnitude with no phase attribute, so "
        "the angle leaves the standard documents and travels only as "
        "the add-on's cim:ParameterValue, and the loss is reported."),
    "cim_source_setpoint_not_on_form": (
        "The source form states no operating magnitude at all -- source3R "
        "takes it from an input signal -- so there is no setpoint to read."),
    "cim_source_setpoint_external": (
        "The source is under external or automatic control, so its "
        "magnitude is set by the simulation rather than by the case."),
    "cim_source_setpoint_single_phase": (
        "The setpoint is stated line-to-ground and CIM wants "
        "phase-to-phase; withheld rather than scaled."),
    "cim_source_unplaced": (
        "A source's ports are not placed on any node, so it terminates "
        "nowhere and is not emitted."),
    "cim_load_unplaced": (
        "A load's ports are not placed on any node, so it terminates "
        "nowhere and is not emitted."),
    "cim_load_response_withheld": (
        "A fixed load's voltage and frequency dependence has no exact "
        "CGMES LoadResponseCharacteristic, so the standard documents carry "
        "it as constant power at its rated voltage. The detail names the "
        "reason. The form's own parameters still reach the add-on "
        "document."),
    "cim_xfmr_side_unresolved": (
        "A transformer winding's connection nodes cannot be read from its "
        "placed port names, so the end has no terminal."),
    "cim_xfmr_param_missing": (
        "A transformer's ratings are not stated on the instance, so its "
        "end impedances cannot be computed."),
    "cim_xfmr_no_magnetizing": (
        "The placement's model contains no linear magnetizing branch -- "
        "the ideal-core choice, a saturation-supplied core, or an "
        "unresolved pair -- so b and g are written as 0.0 rather than "
        "derived."),
    "cim_measurement_unanchored": (
        "A measured quantity sits at a node whose only other members are "
        "unmapped kinds, so it has neither Terminal nor "
        "PowerSystemResource to anchor to. An Analog with neither carries "
        "no information and is suppressed."),
    "cim_measurement_silent": (
        "Every #OUTPUT of this meter placement is gated off by its own "
        "form selectors, so it measures nothing. A node label with "
        "MeasV = 0 names its node and reads no voltage. There is no "
        "quantity to state, so no Analog is emitted."),
    "cim_measurement_untyped": (
        "The quantity is measured, but the 452 measurementType entry "
        "naming it is not established in OUTPUT_QUANTITY. Guessing one "
        "would produce a valid document stating the wrong quantity, "
        "which no property assertion downstream can catch, so the "
        "measurement is withheld and counted."),
    "cim_measurement_unknown_output": (
        "A meter writes through an #OUTPUT parameter OUTPUT_QUANTITY "
        "does not name and UNTYPED_OUTPUTS does not hold either, so "
        "master states a measurement this mapping has never seen."),
    "cim_unmapped": (
        "Standard CIM has no class for this component kind, so it is "
        "absent from the equipment document. A detailed model in the EMT "
        "document carries it instead; "
        "this is the promotion backlog, not a silent drop."),
    "cim_terminal_phases_unnameable": (
        "The drawn node this terminal touches carries a number of "
        "conductors cim:PhaseCode has no member for -- one unnamed "
        "conductor, a bipole's two, a double circuit's six -- so "
        "Terminal.phases is omitted. The count is stated by the extension "
        "profile instead, which is the only place it can be said."),
    "cim_element_partial_phases": (
        "A drawn element is wired to some conductors of its nodes and not "
        "others, which standard CIM could state only through per-terminal "
        "phase subsets its own consistency rules then forbid."),
    "cim_machine_no_rated_power_factor": (
        "cim:RotatingMachine.ratedPowerFactor is nameplate data for IEC "
        "60909 short-circuit exchange, and the machine form has no field "
        "for it. P0/Q0 are an INITIAL CONDITION, so P0/sqrt(P0^2+Q0^2) "
        "would put a study's operating point where a nameplate belongs -- "
        "the shortCircuitEndTemperature precedent: a plausible "
        "number a consumer could not tell from a stated one."),
    "cim_machine_reactive_limits_from_rating": (
        "SynchronousMachine.minQ/maxQ are +/- ratedS, the apparent-power "
        "circle. 452 requires reactive limits unless an "
        "InitialReactiveCapabilityCurve is present, and the machine form "
        "states no reactive capability at all -- its X1..X10 / Y1..Y10 "
        "curve is OPEN-CIRCUIT SATURATION, flux against field current, "
        "not the Q(P) capability CIM's curve means. The bound written is "
        "true of every machine and specific to none: a consumer reading "
        "it as THIS machine's capability is reading more than the case "
        "says."),
    "cim_machine_operating_limits_from_rating": (
        "cim:GeneratingUnit.min/maxOperatingP are +/- ratedS and "
        "normalPF is 1.0, because the unit is mandatory for a consumer "
        "to build a generator at all (PowSyBl emits none without one) "
        "while the machine form states no real-power limits and no power "
        "factor. |P| <= ratedS is the nameplate circle: true of the "
        "machine, specific to nothing about it, and not the dispatch "
        "range an operator would recognise."),
    "cim_machine_setpoint_undetermined": (
        "The machine's initial condition is not stated as powers "
        "(icTyp 0 = None or 2 = Currents), so RotatingMachine.p/q -- "
        "mandatory 1..1 in SSH -- carry 0.0. The same reading as a "
        "voltage source's P/Q: the injection is a solver "
        "outcome rather than a quantity the case states."),
    "cim_machine_operating_mode_default": (
        "SynchronousMachine.operatingMode is mandatory 1..1 and the case "
        "states no real power to read a direction from, so it says "
        "condenser -- the mode consistent with the 0.0 p that accompanies "
        "it, and still a stand-in rather than a statement."),
    "cim_machine_rating_missing": (
        "The machine form's selected rating entry is blank, so no "
        "ratedS can be stated and RotatingMachine.ratedS is required by "
        "600-2. Nothing is emitted for the placement."),
    "cim_machine_rating_unselected": (
        "The machine form's 'Rating Specified as' selector does not "
        "resolve, so which of the two rating entries is live is unknown "
        "-- and the inactive one holds the form default (300.0 MVA), "
        "which is what reading it blind would state."),
    "cim_machine_multiple_stator_ends": (
        "A rotating machine reached more than one drawn node. CIM's "
        "machine has ONE terminal, so a per-phase-drawn stator would need "
        "three machines each carrying the full rating. Nothing is emitted "
        "rather than the rating tripled."),
    "cim_machine_stator_not_three_phase": (
        "A rotating machine's drawn node does not carry three conductors, "
        "so the sqrt(3) between the form's line-to-neutral nameplate and "
        "cim:RotatingMachine.ratedU is not the relation between them. "
        "This is the single-phase source's line-to-ground boundary, met "
        "on the machine side."),
    "cim_machine_unplaced": (
        "A rotating machine's Branch rows reach no external node, so "
        "there is nothing for its one terminal to attach to."),
    "cim_equipment_terminates_on_ground": (
        "One end of this equipment lands on the ConnectivityNode PSCAD's "
        "ground symbol drew while the equipment is NOT one of the classes "
        "CIM defines against the earth. The drawing says SHUNT and the "
        "emitted two-terminal branch says SERIES: a load flow's reference "
        "is the slack bus, not that node, so nothing returns through it "
        "and the branch carries no current. What this counts is "
        "the residue that has no admittance to state; see "
        "cim_shunt_states_no_impedance."),
    "cim_shunt_states_no_impedance": (
        "An element drawn to the ground symbol states no R, L or C at "
        "all, so it has no admittance to be re-expressed as: 1/0 is not a "
        "number. The faithful two-terminal transcription stands rather "
        "than an infinity being invented."),
    "cim_element_both_ends_ground": (
        "Both ends of one drawn element land on the ground node, so it is "
        "not a shunt and states nothing about the network. It is counted "
        "rather than assumed away."),
    "cim_switch_ends_differ_in_voltage": (
        "A switch's two ConnectivityNodes resolve to different nominal "
        "voltages, so they land in different VoltageLevels and 452's "
        "Switch:connection constraint fires at sh:Violation severity -- the "
        "document is NON-CONFORMANT, not merely imprecise. Where one end "
        "is a resolved voltage and the other the 1.0 kV placeholder, "
        "this is what the base-voltage gap costs where it costs most: "
        "not a wrong per-unit frame but an invalid file. The constraint "
        "targets every Switch subclass, so the switch's class does not "
        "change the verdict."),
    "cim_shunt_placeholder_susceptance": (
        "The case determines no system frequency, so a shunt L or C "
        "cannot be turned into a susceptance and b = 0.0 stands in. The "
        "same placeholder cim_branch_placeholder_reactance is for a "
        "series branch, and 60 Hz is never assumed."),
    "cim_grounding_placeholder_reactance": (
        "The case determines no system frequency, so a neutral grounding "
        "inductance cannot be turned into a reactance and x = 0.0 stands "
        "in. 60 Hz is never assumed."),
    "cim_shunt_nominal_voltage_placeholder": (
        "A shunt compensator's nomU is the voltage its power is rated "
        "against, and this node's base voltage is unknown, so the 1.0 kV "
        "placeholder reaches it. This one matters more than the node's "
        "own: a consumer computes the power drawn as nomU^2 * (g - jb), "
        "so the placeholder is wrong by the SQUARE of the ratio."),
    "cim_grounding_terminal_phases_withheld": (
        "301 admits only PhaseCode.N on the terminals of a grounding "
        "class (the EarthFaultCompensator family, GroundDisconnector, "
        "Ground), so the conductor letters the drawn node would otherwise "
        "have named cannot be stated there. This is the "
        "cim_terminal_phases_unnameable boundary, met from the grounding "
        "side."),
    "cim_line_phase_spread": (
        "The conductors of one right-of-way carry DIFFERENT positive-"
        "sequence constants, so the circuit is not ideally transposed and "
        "one ACLineSegment cannot state them all."),
    "cim_line_no_zero_sequence": (
        "The line states no zero sequence, so the ShortCircuit document "
        "carries no r0/x0/b0ch for it. A manual entry with Estimate Zero "
        "Sequence set leaves PSCAD to derive one from ratios we cannot "
        "reproduce, and x0 exceeds x1 in every real line, so copying the "
        "positive sequence would be an undetectable fabrication."),
    "cim_line_sc_temperature_placeholder": (
        "The ShortCircuit profile makes ACLineSegment."
        "shortCircuitEndTemperature mandatory and PSCAD states nothing "
        "about it -- it is a conductor rating, not circuit data. Real "
        "CGMES files carry 0, 75, 80 and 160 with no convention between "
        "them, so 0.0 is written rather than a plausible number invented."),
    "cim_active_parameter_not_numeric": (
        "A live, numeric-typed form parameter of a mapped source, machine "
        "or transformer does not evaluate to a number for this placement "
        "-- an input signal or an unresolvable name drives it -- so the "
        "engine-parameter statement carries no value for it. Counted "
        "rather than a number invented; the stated text still travels in "
        "the source document."),
    "cim_xfmr_view_mixed": (
        "One winding side of a transformer is drawn single-line and the "
        "other per phase, so which per-phase unit meets which conductor "
        "of the bundle is stated nowhere."),
})

_catalog(Severity.GAP, {
    "emt_terminal_node_absent": (
        "A detailed model's connection point has no ConnectivityNode in the "
        "equipment document. build_cim gives one to every node this "
        "profile terminates on, over the same two definitions read here, "
        "so the sets can differ only if the equipment document was built "
        "from a different project. A reference to an absent node would "
        "resolve to nothing and raise nothing, so the terminal is dropped."),
    "emt_model_unanchored": (
        "A detailed model joins to nothing in the equipment document, "
        "because the placement states no connection point at all: no "
        "Branch row, and no electrical port sitting on a drawn node. "
        "Its parameters are readable; it cannot be placed."),
    "emt_model_no_parameters": (
        "The placement states no parameter values at all, so the model "
        "records what the component is but nothing about how it is "
        "configured."),
    "emt_parameter_not_numeric": (
        "A proprietary parameter's value is not a number -- a signal name, "
        "an enumeration index, a blank -- so it carries its stated text "
        "without a numericValue."),
    "emt_setting_missing": (
        "The project states no value for this study setting. The profile "
        "has no way to say 'unknown', and a fabricated one would be "
        "indistinguishable from a stated one."),
    "emt_line_value_unresolved": (
        "A hosting wire's own length or frequency resolves to no number, "
        "so the wire's statement omits it and an engine cannot scale its "
        "right-of-way record over this wire."),
})

_catalog(Severity.GAP, {
    "surface_definition_unnamed": (
        "A `<Definition>` states no name, so the elements its page draws "
        "have no identity to enter the stated-parameter surface under -- "
        "the same absence that keeps the drawing off a page in DL, "
        "counted per document that loses by it."),
    "surface_element_without_id": (
        "A named or parametered `<Wire>` states no id, so its ordinal "
        "within the page seeds its subject -- the same substitution DL "
        "makes, counted here too because this document makes it "
        "independently and an ordinal shifts when the page is "
        "reordered."),
})

_catalog(Severity.GAP, {
    "dl_definition_unnamed": (
        "A `<Definition>` states no name, so its drawing has no page to "
        "go on: `IdentifiedObject.name` is mandatory on every DL class "
        "that is an IdentifiedObject, and a Diagram named nothing could "
        "not be told from the next one."),
    "dl_element_without_id": (
        "A drawn element states no id, so it has no stable identity to "
        "seed a DiagramObject from and its ordinal within the page is "
        "used instead. An ordinal shifts when the page is reordered, "
        "which is exactly what an id exists to stop, so the substitution "
        "is counted rather than hidden."),
    "dl_mirror_dropped": (
        "A drawn element states an `orient` of 4 or more, which is a "
        "quarter turn AND a mirror in x. `cim:DiagramObject.rotation` is "
        "an angle and an angle has no mirror, DL declares no property for "
        "a reflected symbol, and folding the bit into the angle would "
        "state a rotation nobody drew -- so the turn is carried and the "
        "mirror is not."),
    "dl_bad_coordinates": (
        "A drawn element states a coordinate that is not an integer. "
        "PSCAD coordinates are integral and the geometry rests on exact "
        "integer collinearity, so the element is left out of the "
        "diagram rather than rounded onto a position nobody drew."),
    "dl_wire_origin_offset": (
        "A wire's first `<vertex>` is not (0,0), so its polyline is "
        "stated exactly but the split between the wire's own x/y and its "
        "offsets is not recoverable from the emitted points -- the origin "
        "reads back as the first point."),
    "dl_layer_undeclared": (
        "A drawn element names a visibility layer the project does not "
        "declare, so there is no name or state to write a "
        "cim:VisibilityLayer from."),
    "dl_layer_unused": (
        "A declared layer holds no drawn element. 600-2 puts "
        "`VisibilityLayer.VisibleObjects` at 1..n, so an empty layer is "
        "not merely uninformative -- writing one is a Violation -- and it "
        "is omitted."),
})

_catalog(Severity.GAP, {
    "reader_geometry_missing": (
        "A drawn element the source record contains has no DL geometry "
        "to stand on, so it comes back without a position rather than "
        "on one nobody drew."),
    "reader_study_disagrees": (
        "The study document names a different project than the source "
        "record states; the record's name is used and the disagreement "
        "is counted rather than resolved silently."),
    "reader_divergence_ignored": (
        "The consumer chose source precedence over a divergent set, so "
        "this projection statement is ignored in favor of the statement "
        "of record -- reported, never silent."),
    "reader_expression_burned": (
        "Applying an engine value under interchange precedence replaced "
        "a $() parameterization with a literal; the burned substitution "
        "is named, because the parameterization does not come back."),
})

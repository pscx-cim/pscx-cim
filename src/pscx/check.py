"""Where a document set disagrees with itself, reported and never repaired.

The authority rule:
a field's statement of record is the document the reconstruction
inventory's CARRIED column names, and every other appearance of the same
quantity is a projection the emitter regenerates. An edit made in a
projection never reaches the reconstruction, so after such an
edit the set disagrees with itself: the projection says one thing, the
statement of record another, and nothing fails until a consumer trusts
the wrong one.

**The comparator is the emitter itself.** A second evaluator that read
the stated text and recomputed ``numericValue`` would be a second
implementation of the forward evaluation, and two implementations of one
rule drift. Instead the set is reconstructed
(:func:`pscx.reader.read_documents`), re-emitted through the one real
emission, and the supplied PROJECTION documents -- the standard five,
EMT and EMTSIM -- are compared against the re-emitted ones subject by
subject through the canonical statement form: each subject's
``(predicate, object)`` statements, sorted, headers aside. A supplied
statement that does not match its re-emission is a divergence. This
covers every projection uniformly -- ``numericValue``,
``emt:SimulationCase`` attributes, an EQ impedance -- with zero new
evaluation machinery. The two statements of record (EMTSRC, DL) are not
compared: the re-emission was BUILT from them, an edit there is carried
rather than divergent, and the only drift the comparator would see is
the reader's own canonical ordering.

**The canonical statement form relabels the order-derived identities.**
The reconstruction cannot restore within-page element order (the
reader's recorded limitation), and a handful of emitted identities are
representatives of that order: a ``cim:ConnectivityNode``'s key is a
union-find representative, its unnamed name is a digest of that key,
and the signal graph's nets and connections are keyed the same way. On
the identity path those permute without anything being different, so
each side is relabeled structurally before comparison -- a node by the
least stable terminal that stands on it, a topological node by its
least member node, a signal net by a digest of its members' own
statements -- and the stable mint identities (equipment, placements,
values, descriptors, the study case) are compared as themselves.

Each divergence is attributed and classified:

- ``parameter`` -- a ``cim:ParameterValue`` statement in the interchange
  document, paired to the drawn statement it restates through the
  ``emt:DetailedModelDynamics.IdentifiedObject`` join the source
  document states (:func:`pscx.surface.restated_join`) and the
  deterministic mint identities; the drawn element and the parameter
  spelling are named.
- ``settings`` -- an ``emt:SimulationCase`` attribute in the study
  document, whose stated-parameter inverse is the source document's
  Settings paramlist through the same tables emission projects by
  (:data:`pscx.rules.SETTINGS_SECONDS`, :data:`~pscx.rules.SETTINGS_VERBATIM`,
  the snapshot pair).
- ``unrepresentable`` -- engine values for the flat instances of ONE
  drawn element that disagree with each other. No ``.pscx`` can state
  them: the statement is per drawn element, the instances are many, and
  one literal cannot say two things. Named with the drawn element.
- ``other-projection`` -- everything else (an EQ attribute, a signal
  net, a study name). It has no stated-parameter inverse; overwriting
  it has no home in a ``.pscx``.

The classification is what the reader's precedence flags consume
(:func:`pscx.reader.read_case`): ``parameter`` and ``settings``
divergences carry an :class:`Application` -- the literal that would make
the statement of record agree with the engine -- and the other two
classes carry none, which is why they stay refused under any flag.

Cost: one forward emission per invocation, plus parsing the set twice.
A consumer command and a reporter, not a fixer: nothing
here writes a supplied file, and a divergence is a report line and a
nonzero exit, never a repaired value.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from typing import NamedTuple

import rdflib
from rdflib import RDF

#: The four classifications a divergence can carry.
PARAMETER = "parameter"
SETTINGS = "settings"
UNREPRESENTABLE = "unrepresentable"
OTHER = "other-projection"

#: The projection documents the comparison runs over, in report order.
#: DL and EMTSRC are the statements of record: the re-emission is built
#: from them, so an edit there is carried, never divergent.
PROJECTIONS = ("EQ", "TP", "SC", "SSH", "OP", "EMT", "EMTSIM")

MD_NS = "http://iec.ch/TC57/61970-552/ModelDescription/1#"


class Divergence(NamedTuple):
    """One statement on which the supplied set disagrees with its own
    re-emission.

    ``supplied``/``reemitted`` are the sorted object strings of the
    statement on each side (None: the side states nothing); ``place``
    is the attribution -- the drawn element and spelling for a
    ``parameter``, the attribute for a ``settings``, the subject's
    class for the rest.
    """

    document: str
    subject: str
    predicate: str
    supplied: str | None
    reemitted: str | None
    classification: str
    place: str

    def line(self) -> str:
        stated = ("states nothing" if self.supplied is None
                  else f"states {self.supplied!r}")
        emitted = ("re-emits nothing" if self.reemitted is None
                   else f"re-emits {self.reemitted!r}")
        return (f"{self.document} {self.predicate} of {self.place} "
                f"{stated}; the statement of record {emitted} "
                f"[{self.classification}]")


class Application(NamedTuple):
    """The one edit that would make the statement of record state what
    the engine states: a literal into a drawn element's parameter, or
    into the project's Settings paramlist. Built here, applied only by
    the reader under its explicit interchange precedence -- the check
    itself applies nothing."""

    #: ``("parameter", page, kind, ident, spelling)`` or
    #: ``("settings", parameter_name)``.
    target: tuple
    #: The literal to state, unit included for a parameter.
    text: str
    #: The divergences this application settles.
    settles: tuple


class Report(NamedTuple):
    #: How many statements were compared -- the union of both sides over
    #: all nine documents, headers aside. A coherent verdict over zero
    #: comparisons is not a verdict, so the count travels with the
    #: findings.
    compared: int
    divergences: list


class Diagnosis(NamedTuple):
    """One reconstruction, one re-emission, every disagreement."""

    #: The reconstructed case; the reader proceeds with exactly this.
    project: object
    report: Report
    #: One entry per appliable divergence group (parameter / settings).
    applications: list
    #: The divergences no application settles: the unrepresentable
    #: class, every other-projection, and any parameter or settings
    #: statement without a usable stated-parameter inverse.
    unappliable: list


def _prefixed(iri: str) -> str:
    from pscx.emt import CIM_NS, EMT_NS

    for namespace, prefix in ((CIM_NS, "cim:"), (EMT_NS, "emt:"),
                              (MD_NS, "md:"), (str(RDF), "rdf:")):
        if iri.startswith(namespace):
            return prefix + iri[len(namespace):]
    return iri


#: An unnamed node's fallback name: a digest of the same order-derived
#: key its mRID is minted from, so it is relabeled alongside.
_DIGEST_NAME = re.compile(r"N[0-9a-f]{8}")

#: Enumeration ordinals embedded in names -- a terminal's ``:T2``, a
#: per-end source's ``:1`` -- are order bookkeeping like the
#: sequenceNumbers beside them.
_NAME_ORDINAL = re.compile(r":T?\d+")

#: Equipment whose mRID seed embeds a drawn-node key: the per-end
#: sources, loads and injections, the per-unit transformer, and the
#: ground reference. Their identity is re-derived from class, canonical
#: name and the labeled nodes they stand on.
_KEYED_EQUIPMENT = frozenset({
    "cim:ExternalNetworkInjection", "cim:EnergyConsumer",
    "cim:EquivalentInjection", "cim:PowerTransformer", "cim:Ground"})

#: Subjects keyed by a flattened-graph representative: relabeled by
#: neighborhood refinement among themselves.
_GRAPH_KEYED = frozenset({"emt:SignalNet", "emt:SignalConnection",
                          "emt:RightOfWayRecord"})

#: Order bookkeeping on the relabeled subjects: minted from, or
#: numbering, the same order-derived enumeration, so it says nothing a
#: structural label does not already say.
_BOOKKEEPING = frozenset({
    "cim:IdentifiedObject.mRID", "cim:ACDCTerminal.sequenceNumber",
    "emt:ModelTerminal.sequenceNumber",
    "emt:RightOfWayRecord.sequenceNumber",
    "cim:ParameterDescriptor.sequenceNumber"})


def _canonical_literal(kind: str, predicate: str,
                       value: str) -> str | None:
    """A literal on a relabeled subject, with the order bookkeeping
    folded out: None drops the statement, otherwise the canonical text.
    A stated bus name survives; the digest fallback name and the
    embedded enumeration ordinals do not, because both restate the
    order-derived key the label already canonicalizes. A topological
    node is named after whichever member the union-find elected head,
    and a signal net after whichever case-folded spelling of its label
    the merge saw first -- elections both, folded accordingly."""
    if predicate in _BOOKKEEPING:
        return None
    if predicate == "cim:IdentifiedObject.name":
        if kind == "cim:TopologicalNode":
            return None
        if _DIGEST_NAME.fullmatch(value):
            return "N"
        if kind == "emt:SignalNet":
            return value.lower()
        return _NAME_ORDINAL.sub(":", value)
    return value


def _relabeled(graphs: dict) -> tuple[dict, dict]:
    """``(IRI -> structural label, elected-reference replacements)``
    for the order-derived identities of one document set.

    A ``ConnectivityNode``'s mRID is a union-find representative of
    within-page element order -- the one thing the reconstruction does
    not restore -- and everything minted downstream of a
    node key permutes with it: the node-seeded equipment and their
    per-end names, every terminal's binding ordinal, the flat signal
    graph's nets, the right-of-way rows. Each of those is relabeled by
    what it IS rather than which representative the union-find elected:
    nodes and the node-seeded equipment by mutual refinement (a node is
    "where these named equipments stand", the equipment "the one of
    this class and name on those nodes"), terminals by owner and node,
    a keyed record's model by its equipment, its values by descriptor,
    and the graph-keyed families by neighborhood refinement among
    themselves. Subjects still sharing a label are structurally
    symmetric, and relabeling them alike with a rank is exactly what
    makes the comparison insensitive to the election. Everything else
    mints from element ids and definition names and compares as itself.
    """
    import hashlib

    from pscx.emt import CIM, EMT

    def digest(payload) -> str:
        return hashlib.sha256(repr(payload).encode()).hexdigest()[:16]

    eq, tp, emt = graphs["EQ"], graphs["TP"], graphs["EMT"]
    typed: dict[str, str] = {}
    for document in PROJECTIONS:
        # first document wins: EQ types its subjects concretely, and
        # SSH restates some of them as plain cim:Equipment
        for subject, obj in graphs[document].subject_objects(RDF.type):
            typed.setdefault(str(subject), _prefixed(str(obj)))

    name_of: dict[str, str] = {}
    for graph in (eq, emt):
        for subject, stated in graph.subject_objects(
                CIM["IdentifiedObject.name"]):
            name_of[str(subject)] = str(stated)

    def cname(subject: str) -> str:
        return _canonical_literal(typed.get(subject, "?"),
                                  "cim:IdentifiedObject.name",
                                  name_of.get(subject, "")) or ""

    node_of_terminal = {str(s): str(o) for s, o in eq.subject_objects(
        CIM["Terminal.ConnectivityNode"])}
    equipment_of_terminal = {str(s): str(o) for s, o in eq.subject_objects(
        CIM["Terminal.ConductingEquipment"])}
    node_of_model_terminal = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelTerminal.ConnectivityNode"])}
    model_of_terminal = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelTerminal.DetailedModelDynamics"])}
    phase_of = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelTerminal.phase"])}
    equipment_of_model = {str(s): str(o) for s, o in emt.subject_objects(
        CIM["DetailedModelDynamics.Equipment"])}
    model_of_value = {str(s): str(o) for s, o in emt.subject_objects(
        CIM["ParameterValue.DetailedModelDynamics"])}
    descriptor_of = {str(s): str(o) for s, o in emt.subject_objects(
        CIM["ParameterValue.ParameterDescriptor"])}

    keyed = {s for s, kind in typed.items() if kind in _KEYED_EQUIPMENT}
    keyed_models = {model for model, equipment
                    in equipment_of_model.items() if equipment in keyed}

    # -- nodes and the node-seeded equipment, by mutual refinement:
    # what stands on a node names it, and the labeled nodes then name
    # the keyed equipment standing on them
    incidence: dict[str, list] = {}
    for terminal, node in node_of_terminal.items():
        equipment = equipment_of_terminal.get(terminal, "")
        token = (("KEQ", typed.get(equipment, "?"), cname(equipment))
                 if equipment in keyed else ("EQ", equipment))
        incidence.setdefault(node, []).append(token)
    for terminal, node in node_of_model_terminal.items():
        model = model_of_terminal.get(terminal, "")
        token = (("KDMD", cname(model)) if model in keyed_models
                 else ("DMD", model))
        incidence.setdefault(node, []).append(
            token + (phase_of.get(terminal, ""),))
    own: dict[str, list] = {}
    for node, count in emt.subject_objects(
            EMT["ConnectivityNode.phaseCount"]):
        own.setdefault(str(node), []).append(("phases", str(count)))
    for node, container in eq.subject_objects(
            CIM["ConnectivityNode.ConnectivityNodeContainer"]):
        # the container is kv-seeded and stable, and it distinguishes
        # nodes nothing else reaches
        own.setdefault(str(node), []).append(("container", str(container)))
    for subject, kind in typed.items():
        if kind == "cim:ConnectivityNode":
            own.setdefault(subject, []).append(("name", cname(subject)))

    nodes = [s for s, kind in typed.items()
             if kind == "cim:ConnectivityNode"]
    ends_of = {}
    for equipment in keyed:
        ends_of[equipment] = sorted(
            node_of_terminal[t] for t, e in equipment_of_terminal.items()
            if e == equipment and t in node_of_terminal)
    node_labels = {node: digest((sorted(own.get(node, ())),
                                 sorted(incidence.get(node, ()))))
                   for node in nodes}
    for _round in range(len(nodes) + 1):
        keyed_labels = {
            equipment: digest((typed[equipment], cname(equipment),
                               sorted(node_labels.get(n, n)
                                      for n in ends_of[equipment])))
            for equipment in keyed}
        refined = {}
        for node in nodes:
            tokens = list(incidence.get(node, ()))
            for equipment in keyed:
                tokens.extend(("edge", keyed_labels[equipment])
                              for n in ends_of[equipment] if n == node)
            refined[node] = digest((node_labels[node], sorted(tokens)))
        done = (len(set(refined.values()))
                == len(set(node_labels.values())))
        node_labels = refined
        if done:
            break

    # -- ranks interleave with derivation from here on: a symmetric
    # pair draws arbitrary but side-local ranks, so everything DERIVED
    # from a ranked subject must fold the ranked name in -- otherwise
    # two symmetric nodes' terminals, models and memberships cross
    # sides through whichever member each side elected
    ranks: dict[str, int] = {}
    rename: dict[str, str] = {}

    def assign(subject: str, label: str) -> None:
        rank = ranks.get(label, 0)
        ranks[label] = rank + 1
        rename[subject] = f"{typed.get(subject, '?')}({label}/{rank})"

    def named(subject: str) -> str:
        return rename.get(subject, subject)

    for subject in sorted(node_labels):
        assign(subject, node_labels[subject])
    for subject in sorted(keyed_labels):
        assign(subject, keyed_labels[subject])
    for subject, kind in sorted(typed.items()):
        if kind == "cim:TopologicalNode":
            members = sorted(
                named(str(s))
                for graph in (eq, tp)
                for s in graph.subjects(
                    CIM["ConnectivityNode.TopologicalNode"],
                    rdflib.URIRef(subject)))
            assign(subject, digest(("tn", members)))

    # -- the model types and their descriptors, value-independently: a
    # definition-less carrier's descriptor ORDER is the file's own, the
    # reconstruction returns its entries sorted, and the type's mRID
    # digests that order -- so a type is what it describes (name,
    # definition, the spelling SET) and a descriptor is its spelling
    spellings_of: dict[str, list] = {}
    type_of_descriptor = {
        str(s): str(o) for s, o in emt.subject_objects(
            CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"])}
    for descriptor, model_type in type_of_descriptor.items():
        spellings_of.setdefault(model_type, []).append(cname(descriptor))
    definition_of = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["LibraryModelType.definitionName"])}
    for subject, kind in sorted(typed.items()):
        if kind == "emt:LibraryModelType":
            assign(subject, digest(
                ("type", cname(subject), definition_of.get(subject, ""),
                 tuple(sorted(spellings_of.get(subject, ()))))))
    for descriptor in sorted(type_of_descriptor):
        assign(descriptor, digest(
            ("desc", named(type_of_descriptor[descriptor]),
             cname(descriptor))))
    port_type_of = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelPort.DetailedModelTypeDynamics"])}
    port_name_of = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelPort.portName"])}
    port_sequence_of = {str(s): str(o) for s, o in emt.subject_objects(
        EMT["ModelPort.sequenceNumber"])}
    for port in sorted(port_type_of):
        assign(port, digest(
            ("port", named(port_type_of[port]),
             port_name_of.get(port, ""), port_sequence_of.get(port, ""))))

    # -- everything bound to a node or a keyed equipment, derived
    # value-independently so an edited attribute stays a tidy
    # per-predicate difference on a matching subject
    for terminal in sorted(node_of_terminal):
        equipment = equipment_of_terminal.get(terminal, "")
        assign(terminal, digest(
            ("t", named(equipment), named(node_of_terminal[terminal]))))
    # a RegulatingControl reuses its injection's node-keyed seed and has
    # no terminal of its own, so it permutes with the node election like
    # everything minted downstream of a node key: it is relabeled as
    # "the control of this injection", folding the ranked owner name in
    control_of = {str(s): str(o) for s, o in eq.subject_objects(
        CIM["RegulatingCondEq.RegulatingControl"])}
    for equipment in sorted(control_of):
        if equipment in keyed:
            assign(control_of[equipment],
                   digest(("rc", named(equipment))))
    for model in sorted(keyed_models):
        assign(model, digest(("dmd", named(equipment_of_model[model]))))
    for value in sorted(model_of_value):
        if model_of_value[value] in keyed_models:
            assign(value, digest(
                ("pv", named(model_of_value[value]),
                 named(descriptor_of.get(value, "")))))
    for terminal in sorted(node_of_model_terminal):
        model = model_of_terminal.get(terminal, "")
        assign(terminal, digest(
            ("mt", named(model), named(node_of_model_terminal[terminal]),
             phase_of.get(terminal, ""))))
    for subject, kind in sorted(typed.items()):
        if kind == "cim:PowerTransformerEnd":
            transformer = str(eq.value(
                rdflib.URIRef(subject),
                CIM["PowerTransformerEnd.PowerTransformer"]))
            end = str(eq.value(rdflib.URIRef(subject),
                               CIM["TransformerEnd.endNumber"]))
            assign(subject, digest(("end", named(transformer), end)))

    # -- the graph-keyed families, by neighborhood refinement among
    # themselves, anchored by everything already named
    graph_keyed = {s for s, kind in typed.items() if kind in _GRAPH_KEYED}
    base: dict[str, list] = {}
    edges: dict[str, list] = {}
    for subject, predicate, obj in emt:
        s, o = str(subject), str(obj)
        pname = _prefixed(str(predicate))
        s_in, o_in = s in graph_keyed, (
            isinstance(obj, rdflib.URIRef) and o in graph_keyed)
        if s_in and o_in:
            edges.setdefault(s, []).append((">", pname, o))
            edges.setdefault(o, []).append(("<", pname, s))
        elif s_in:
            value = rename.get(o)
            if value is None:
                value = _canonical_literal(typed.get(s, "?"), pname, o)
            if value is not None:
                base.setdefault(s, []).append((">", pname, value))
        elif o_in:
            base.setdefault(o, []).append(("<", pname, named(s)))
    refine = {s: digest((typed[s], sorted(base.get(s, ()))))
              for s in graph_keyed}
    for _round in range(len(graph_keyed)):
        refined = {
            s: digest((refine[s],
                       sorted((direction, pname, refine[other])
                              for direction, pname, other
                              in edges.get(s, ()))))
            for s in graph_keyed}
        done = len(set(refined.values())) == len(set(refine.values()))
        refine = refined
        if done:
            break
    for subject in sorted(graph_keyed):
        assign(subject, refine[subject])

    # a measurement's Terminal and PowerSystemResource are ELECTED
    # among the terminals and equipment at the measured device's nodes,
    # and the election can even land on either node of a two-node
    # device -- which member the emitter picked is not data, and the
    # measurement's own subject already states the placement, so the
    # references canonicalize to whether a member was picked, not which
    elections: dict[tuple, str] = {}
    for subject, _terminal in graphs["OP"].subject_objects(
            CIM["Measurement.Terminal"]):
        elections[("cim:Measurement.Terminal", str(subject))] = "elected"
        elections[("cim:Measurement.PowerSystemResource",
                   str(subject))] = "elected"
    return rename, elections


def _statements(graph: rdflib.Graph, rename: dict[str, str],
                elections: dict[tuple, str]) -> dict:
    """``subject -> predicate -> sorted object strings``, headers aside,
    the order-derived identities relabeled on both ends of each
    statement, their order bookkeeping folded out, and the elected
    references replaced by what the election was over.

    Plain strings for literals and IRIs alike, sorted per predicate, so
    the comparison is insensitive to store order and to the datatype an
    editing tool stamped on a literal.
    """
    header = set(graph.subjects(RDF.type, rdflib.URIRef(MD_NS + "FullModel")))
    out: dict[str, dict[str, list]] = {}
    for subject, predicate, obj in graph:
        if subject in header:
            continue
        label = rename.get(str(subject), str(subject))
        name = _prefixed(str(predicate))
        elected = elections.get((name, str(subject)))
        if elected is not None:
            value = elected
        else:
            value = rename.get(str(obj), str(obj))
            if label != str(subject) and value == str(obj):
                value = _canonical_literal(label.partition("(")[0], name,
                                           value)
                if value is None:
                    continue
        out.setdefault(label, {}).setdefault(name, []).append(value)
    return {subject: {predicate: tuple(sorted(objects))
                      for predicate, objects in statements.items()}
            for subject, statements in out.items()}


def _reemitted(project, paths: list) -> dict[str, rdflib.Graph]:
    """The nine documents the supplied set OUGHT to be: the
    reconstruction serialized back to a ``.pscx`` and pushed through the
    one real emission, in a scratch directory.

    Any ``.pslx`` library beside the supplied documents is copied beside
    the scratch case: a case
    that references sibling libraries flattens with its ports resolved
    only when they are found beside it.
    """
    from lxml import etree as ET

    from pscx.cim import emit_case
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.reader import _read_graphs
    from pscx.write import write_project

    with tempfile.TemporaryDirectory() as scratch:
        case_dir = os.path.join(scratch, "case")
        os.makedirs(case_dir)
        target = os.path.join(case_dir, f"{project.name}.pscx")
        with open(target, "wb") as handle:
            handle.write(ET.tostring(write_project(project),
                                     xml_declaration=True,
                                     encoding="UTF-8"))
        for directory in {os.path.dirname(os.path.abspath(str(p)))
                          for p in paths}:
            for sibling in sorted(os.listdir(directory)):
                if sibling.endswith(".pslx"):
                    shutil.copy(os.path.join(directory, sibling), case_dir)
        with DIAGNOSTICS.suppressed():
            emitted = emit_case(target, os.path.join(scratch, "docs"))
        return _read_graphs(emitted)


def _settings_of(project) -> dict[str, str]:
    for paramlist in project.params:
        if paramlist.name == "Settings":
            return dict(paramlist.values)
    return {}


def _number(text: str) -> str:
    return f"{float(text):.12g}"


def _settings_application(attribute: str, supplied: str | None,
                          settings: dict) -> tuple | None:
    """``(parameter name, text)`` where the stated-parameter inverse
    exists and re-emitting it would restate the supplied value, else
    None. The inverse of the three projection rules emission applies:
    the second-valued conversions, the snapshot pair, the verbatim
    adoptions."""
    from pscx.rules import SETTINGS_SECONDS, SETTINGS_VERBATIM

    if supplied is None:
        return None
    for parameter, (candidate, divisor) in SETTINGS_SECONDS.items():
        if candidate == attribute:
            try:
                value = float(supplied)
            except ValueError:
                return None
            # the projection emits only a positive value, so only a
            # positive value can be applied and re-emit to agreement
            return (parameter, _number(str(value * divisor))) \
                if value > 0.0 else None
    if attribute == "startFromSnapshot":
        return ("StartType", {"true": "1", "false": "0"}.get(supplied))\
            if supplied in ("true", "false") else None
    if attribute == "snapshotTime":
        # emitted only under a nonzero SnapType: applying SnapTime under
        # SnapType 0 would state a time the projection never restates
        try:
            snap_type = float(settings.get("SnapType") or 0.0)
        except ValueError:
            snap_type = 0.0
        if not snap_type:
            return None
        try:
            return ("SnapTime", _number(supplied))
        except ValueError:
            return None
    for parameter, candidate in SETTINGS_VERBATIM.items():
        if candidate == attribute:
            return (parameter, supplied)
    return None


def diverging(supplied: dict, reemitted: dict, project) -> Diagnosis:
    """Every statement on which the supplied set disagrees with its own
    re-emission, classified, attributed, and paired with the application
    that would settle it where one exists."""
    from pscx.emt import CIM, EMT
    from pscx.surface import restated_join

    ours_rename, ours_elections = _relabeled(supplied)
    theirs_rename, theirs_elections = _relabeled(reemitted)

    # attribution tables per side, keyed the way the comparison keys its
    # subjects -- the mint identities and the item-1 join are the
    # ledger, the relabeling carries the order-derived ones across
    def value_table(graphs: dict, rename: dict) -> dict:
        table: dict[str, tuple] = {}
        equipment = graphs["EMT"]
        drawn_of = {
            rename.get(target, target): key
            for key, targets in restated_join(graphs["EMTSRC"]).items()
            for target in targets}
        for value, owner in equipment.subject_objects(
                CIM["ParameterValue.DetailedModelDynamics"]):
            descriptor = equipment.value(
                value, CIM["ParameterValue.ParameterDescriptor"])
            spelling = unit = None
            if descriptor is not None:
                spelling = str(equipment.value(
                    descriptor, CIM["IdentifiedObject.name"]))
                stated = equipment.value(
                    descriptor, CIM["ParameterDescriptor.engineeringUnit"])
                unit = None if stated is None else str(stated)
            numeric = equipment.value(
                value, EMT["ParameterValue.numericValue"])
            table[rename.get(str(value), str(value))] = (
                drawn_of.get(rename.get(str(owner), str(owner))),
                spelling, unit,
                None if numeric is None else str(numeric))
        return table

    ours_table = value_table(supplied, ours_rename)
    theirs_table = value_table(reemitted, theirs_rename)

    compared = 0
    divergences: list[Divergence] = []
    for document in PROJECTIONS:
        ours = _statements(supplied[document], ours_rename,
                           ours_elections)
        theirs = _statements(reemitted[document], theirs_rename,
                             theirs_elections)
        for subject in sorted(set(ours) | set(theirs)):
            stated = ours.get(subject, {})
            emitted = theirs.get(subject, {})
            for predicate in sorted(set(stated) | set(emitted)):
                compared += 1
                left = stated.get(predicate)
                right = emitted.get(predicate)
                if left == right:
                    continue
                entry = ours_table.get(subject) or theirs_table.get(subject)
                if (document == "EMTSIM"
                        and predicate.startswith("emt:SimulationCase.")):
                    classification = SETTINGS
                    place = predicate[len("emt:SimulationCase."):]
                elif document == "EMT" and entry is not None \
                        and entry[0] is not None:
                    classification = PARAMETER
                    page, kind, ident = entry[0]
                    place = (f"drawn {page}/{kind}/{ident} "
                             f"{entry[1] or '?'}")
                else:
                    classification = OTHER
                    types = (emitted.get("rdf:type")
                             or stated.get("rdf:type") or ("?",))
                    place = _prefixed(types[0])
                divergences.append(Divergence(
                    document, subject, predicate,
                    ", ".join(left) if left else None,
                    ", ".join(right) if right else None,
                    classification, place))

    # parameter groups: one drawn statement is one application, fed by
    # the supplied numericValue of EVERY flat instance -- uniform, or no
    # literal can state them and the group is unrepresentable
    groups: dict[tuple, list[int]] = {}
    for index, divergence in enumerate(divergences):
        if divergence.classification != PARAMETER:
            continue
        entry = (ours_table.get(divergence.subject)
                 or theirs_table.get(divergence.subject))
        groups.setdefault((entry[0], entry[1]), []).append(index)

    applications: list[Application] = []
    unappliable: list[Divergence] = []
    for (drawn, spelling), members in sorted(groups.items(), key=repr):
        siblings = [entry for entry in ours_table.values()
                    if entry[0] == drawn and entry[1] == spelling]
        stated = {entry[3] for entry in siblings}
        rows = tuple(divergences[i] for i in members)
        if not stated or None in stated:
            # a value with no supplied numericValue -- a text statement,
            # or a stated-text copy edited on its own -- has no engine
            # value to apply; only emt:ParameterValue.numericValue is
            # applied
            unappliable.extend(rows)
            continue
        if len(stated) > 1:
            page, kind, ident = drawn
            for index in members:
                divergences[index] = divergences[index]._replace(
                    classification=UNREPRESENTABLE,
                    place=(f"drawn {page}/{kind}/{ident} {spelling}: "
                           f"instances state "
                           f"{sorted(stated, key=repr)}"))
            unappliable.extend(divergences[i] for i in members)
            continue
        (lexical,) = stated
        unit = next((entry[2] for entry in siblings
                     if entry[2] is not None), None)
        text = f"{lexical} [{unit}]" if unit else lexical
        applications.append(Application(
            ("parameter",) + drawn + (spelling,), text, rows))

    settings = _settings_of(project)
    for divergence in divergences:
        if divergence.classification == SETTINGS:
            inverse = _settings_application(
                divergence.place, divergence.supplied, settings)
            if inverse is None:
                unappliable.append(divergence)
            else:
                applications.append(Application(
                    ("settings", inverse[0]), inverse[1], (divergence,)))
        elif divergence.classification == OTHER:
            unappliable.append(divergence)

    return Diagnosis(project, Report(compared, divergences),
                     applications, unappliable)


def diagnose(paths: Iterable, master=None, bus=None) -> Diagnosis:
    """One case's nine documents, reconstructed once, re-emitted once,
    and compared against themselves.

    The input set is the reader's own -- each document recognized by its
    stated profile, a missing, duplicated or unrecognized profile raises
    -- because a diagnosis that quietly accepted a partial set would
    report coherence over the comparisons it could not make. ``master``
    is the reader's, and the re-emission reads it again through the
    ``PSCAD_MASTER`` convention; ``bus`` receives the reconstruction's
    own findings, never the scratch re-emission's.
    """
    from pscx.diagnostics import Diagnostics
    from pscx.reader import _read_graphs, read_documents

    paths = [str(p) for p in paths]
    supplied = _read_graphs(paths)
    project = read_documents(paths, master=master,
                             bus=bus if bus is not None else Diagnostics())
    return diverging(supplied, _reemitted(project, paths), project)


def check_documents(paths: Iterable, master=None) -> Report:
    """The nine documents of one case, checked for self-disagreement.
    The standalone form of the reader's own diagnosis: same comparator,
    same classifications, no precedence and no application."""
    return diagnose(paths, master=master).report

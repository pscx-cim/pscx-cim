"""Rebuild an ``HirProject`` from the emitted documents.

The reverse direction, composed: the nine documents ``emit_files``
writes for one case -- the standard five, DL, EMT, EMTSIM and EMTSRC --
come back in as one ``HirProject``, which ``pscx.write.write_project``
then turns into a ``.pscx``. Nothing here invents a reconstruction.
Every field is filled either by an inverse that already exists and is
proven against its own document (:func:`pscx.record.reconstruct_record`,
:func:`pscx.surface.reconstruct_surface`,
:func:`pscx.dl.reconstruct_drawing`) or by a rule the reconstruction
inventory pins; this module owns only the composition -- which document
each inverse reads, how their outputs join, and the completeness
contract that no HIR field is defaulted silently.

**The convention.**

- A document is identified by its ``md:Model.profile``, never by its
  filename. The input set is exactly the nine documents of one case; a
  missing, duplicated or unrecognized profile raises.
- Graphs stay separate. The DL graph feeds ``reconstruct_drawing`` and
  the source graph feeds ``reconstruct_record``,
  ``reconstruct_surface`` and the payload read; no cross-document merge
  exists, because no join here crosses a document by IRI: every join is
  a NAME both sides state -- the definition name between record and
  drawing, the drawn grammar ``(page, kind, ident)`` between record,
  surface and drawing, the spelling between descriptor and category.
- The standard five are asserted present and read for nothing: the one
  field they carry (the project name) is also stated by the source
  record's ``emt:ModelProject``, and the study document cross-checks
  it. The EMT interchange document is likewise asserted and unread --
  it is engine data, and a reader that needed it would be a partition
  finding, not a convenience.
- ``master.pslx`` is the reader's own copy of the library the exchange
  resolves by name (the lever: a placement carries a definition
  REFERENCE, and nearly every reference resolves into master). The
  reconstruction itself needs nothing from it -- case-local definitions
  come from the source record, and case-local wins where names shadow
  -- so master serves exactly one job here: a master-typed placement's
  paramlist comes back in master's declared form order rather than
  alphabetically.

**What the composition cannot state, and says so.**

- *Within-page element order.* The record states a page's elements as
  containment joins and the DL as diagram objects, and neither carries
  the order the source document drew them in. The reader emits a
  canonical order -- definitions by name, components by element id,
  wires by element id with the id-less ones at their stated ordinals --
  and the case's meaning is order-free, but a comparison keyed on
  instance DFS indices sees the permutation.
- *The held layer rows.* ``HirLayer`` and ``HirProject.layers`` come
  back empty because those rows are held.

Spans are None throughout: a reconstructed HIR has no source document,
and nothing downstream may invent a line number.
"""

from __future__ import annotations

import dataclasses
import enum
import re
from collections.abc import Iterable
from typing import Any, NamedTuple

import rdflib
from lxml import etree as ET
from rdflib import RDF

from pscx.diagnostics import DIAGNOSTICS, Diagnostics
from pscx.dl import PORT, SCHEMATIC, SYMBOL, USER, WIRE, reconstruct_drawing
from pscx.guards import assemble_splices, parse_script
from pscx.hir import (
    HirCanvas,
    HirComponent,
    HirDefinition,
    HirFormCategory,
    HirFormParameter,
    HirParams,
    HirPort,
    HirProject,
    HirSegment,
    HirSubstitution,
    HirSubstitutionList,
    HirSubstitutions,
    HirWire,
    Opaque,
)
from pscx.io import _XML_PARSER
from pscx.record import PORT_KEYS, reconstruct_record
from pscx.surface import (
    DEFINITION,
    LIBRARY,
    _declared_form,
    _resolve,
    reconstruct_surface,
)

#: The order a rebuilt ``<Port>`` paramlist states its keys in. A port's
#: stated key order is not data -- the record carries the seven values
#: against fixed attributes -- so it comes back in this canonical order,
#: the same way a placement's paramlist comes back in the form's order.
PORT_KEY_ORDER = ("name", "internal", "cond", "mode", "datatype",
                  "electype", "dim")

#: Which parent element each retained payload kind is reinserted under.
#: An OPAQUE tag never appears under two different parents -- pinned by
#: the inventory's derivations test -- which is what lets a payload
#: travel with only its index and no parent reference. This map is that
#: rule stated once; a payload kind it does not name is a defect, not a
#: default.
OPAQUE_PARENT_OF = {
    "List": "project",
    "bookmarks": "project",
    "constants": "project",
    "output": "project",
    "references": "Definition",
    "svg": "Definition",
    "Gfx": "graphics",
    "FileCmp": "schematic",
    "Frame": "schematic",
    "Instrument": "schematic",
    "Line": "schematic",
    "Sticky": "schematic",
    "grouping": "schematic",
    "choice": "parameter",
    "error_msg": "parameter",
    "help": "parameter",
    "regex": "parameter",
    "vis": "parameter",
}

# --------------------------------------------------------------------------
# The completeness contract
# --------------------------------------------------------------------------

#: How a field is populated.
FROM_DOCUMENT = "document"
BY_RULE = "rule"
EMPTY = "empty"
HELD = "held"

#: Every field of every dataclass in :mod:`pscx.hir`, and where this
#: reader gets it. The verdicts mirror the reconstruction inventory --
#: CARRIED reads a document, DERIVABLE is a rule or empty by
#: construction, MISSING is held -- and ``tests/test_reader.py`` holds
#: the two tables equal so neither can drift alone. A field this table
#: does not name fails :func:`_assert_covered` by name, never silently.
FIELD_SOURCE: dict[str, str] = {
    "Unrecognized.kind": EMPTY,
    "Unrecognized.owner": EMPTY,
    "Unrecognized.name": EMPTY,
    "Unrecognized.value": EMPTY,
    "Unrecognized.span": EMPTY,
    "Unrecognized.xpath": EMPTY,
    "Opaque.tag": FROM_DOCUMENT,
    "Opaque.element": FROM_DOCUMENT,
    "Opaque.span": EMPTY,
    "HirParams.name": FROM_DOCUMENT,
    "HirParams.values": FROM_DOCUMENT,
    "HirParams.rows": FROM_DOCUMENT,
    "HirParams.span": EMPTY,
    "HirParams.lines": EMPTY,
    "HirSubstitution.id": EMPTY,
    "HirSubstitution.classid": BY_RULE,
    "HirSubstitution.params": FROM_DOCUMENT,
    "HirSubstitution.span": EMPTY,
    "HirSubstitutionList.classid": BY_RULE,
    "HirSubstitutionList.name": BY_RULE,
    "HirSubstitutionList.subs": BY_RULE,
    "HirSubstitutionList.span": EMPTY,
    "HirSubstitutions.tag": BY_RULE,
    "HirSubstitutions.name": BY_RULE,
    "HirSubstitutions.params": BY_RULE,
    "HirSubstitutions.lists": BY_RULE,
    "HirSubstitutions.span": EMPTY,
    "HirPort.id": FROM_DOCUMENT,
    "HirPort.classid": BY_RULE,
    "HirPort.x": FROM_DOCUMENT,
    "HirPort.y": FROM_DOCUMENT,
    "HirPort.params": FROM_DOCUMENT,
    "HirPort.span": EMPTY,
    "HirComponent.classid": BY_RULE,
    "HirComponent.id": FROM_DOCUMENT,
    "HirComponent.defn": FROM_DOCUMENT,
    "HirComponent.x": FROM_DOCUMENT,
    "HirComponent.y": FROM_DOCUMENT,
    "HirComponent.orient": FROM_DOCUMENT,
    "HirComponent.name": FROM_DOCUMENT,
    "HirComponent.disable": BY_RULE,
    "HirComponent.layer": BY_RULE,
    "HirComponent.params": FROM_DOCUMENT,
    "HirComponent.span": EMPTY,
    "HirWire.classid": BY_RULE,
    "HirWire.id": FROM_DOCUMENT,
    "HirWire.name": FROM_DOCUMENT,
    "HirWire.x": FROM_DOCUMENT,
    "HirWire.y": FROM_DOCUMENT,
    "HirWire.orient": FROM_DOCUMENT,
    "HirWire.disable": BY_RULE,
    "HirWire.layer": BY_RULE,
    "HirWire.defn": FROM_DOCUMENT,
    "HirWire.vertices": FROM_DOCUMENT,
    "HirWire.params": FROM_DOCUMENT,
    "HirWire.hosted": FROM_DOCUMENT,
    "HirWire.span": EMPTY,
    "HirCanvas.classid": BY_RULE,
    "HirCanvas.params": FROM_DOCUMENT,
    "HirCanvas.components": FROM_DOCUMENT,
    "HirCanvas.wires": FROM_DOCUMENT,
    "HirCanvas.opaque": FROM_DOCUMENT,
    "HirCanvas.span": EMPTY,
    "HirFormParameter.name": FROM_DOCUMENT,
    "HirFormParameter.type": FROM_DOCUMENT,
    "HirFormParameter.desc": FROM_DOCUMENT,
    "HirFormParameter.group": FROM_DOCUMENT,
    "HirFormParameter.unit": FROM_DOCUMENT,
    "HirFormParameter.intent": FROM_DOCUMENT,
    "HirFormParameter.dim": FROM_DOCUMENT,
    "HirFormParameter.content_type": FROM_DOCUMENT,
    "HirFormParameter.minimum": FROM_DOCUMENT,
    "HirFormParameter.maximum": FROM_DOCUMENT,
    "HirFormParameter.value": FROM_DOCUMENT,
    "HirFormParameter.condition": FROM_DOCUMENT,
    "HirFormParameter.condition_type": BY_RULE,
    "HirFormParameter.opaque": FROM_DOCUMENT,
    "HirFormParameter.span": EMPTY,
    "HirFormCategory.name": FROM_DOCUMENT,
    "HirFormCategory.visible": FROM_DOCUMENT,
    "HirFormCategory.condition": FROM_DOCUMENT,
    "HirFormCategory.condition_type": BY_RULE,
    "HirFormCategory.parameters": FROM_DOCUMENT,
    "HirFormCategory.span": EMPTY,
    "HirSegment.name": FROM_DOCUMENT,
    "HirSegment.id": EMPTY,
    "HirSegment.text": FROM_DOCUMENT,
    "HirSegment.tree": BY_RULE,
    "HirSegment.span": EMPTY,
    "HirDefinition.classid": FROM_DOCUMENT,
    "HirDefinition.name": FROM_DOCUMENT,
    "HirDefinition.id": EMPTY,
    "HirDefinition.group": FROM_DOCUMENT,
    "HirDefinition.params": FROM_DOCUMENT,
    "HirDefinition.form_name": FROM_DOCUMENT,
    "HirDefinition.form": FROM_DOCUMENT,
    "HirDefinition.ports": FROM_DOCUMENT,
    "HirDefinition.segments": FROM_DOCUMENT,
    "HirDefinition.canvas": FROM_DOCUMENT,
    "HirDefinition.opaque": FROM_DOCUMENT,
    "HirDefinition.span": EMPTY,
    "HirLayer.name": HELD,
    "HirLayer.state": HELD,
    "HirLayer.id": EMPTY,
    "HirLayer.span": EMPTY,
    "HirProject.path": BY_RULE,
    "HirProject.name": FROM_DOCUMENT,
    "HirProject.version": FROM_DOCUMENT,
    "HirProject.target": FROM_DOCUMENT,
    "HirProject.schema": BY_RULE,
    "HirProject.params": FROM_DOCUMENT,
    "HirProject.definitions": FROM_DOCUMENT,
    "HirProject.layers": HELD,
    "HirProject.substitutions": FROM_DOCUMENT,
    "HirProject.settings": EMPTY,
    "HirProject.opaque": FROM_DOCUMENT,
    "HirProject.unrecognized": EMPTY,
}


def _assert_covered() -> None:
    """Every HIR field is named above, and nothing named has gone.

    The same closure the inventory holds over its own table: a new HIR
    field arrives here as a failure by name, never as a silently
    defaulted attribute on every reconstructed project.
    """
    import inspect

    from pscx import hir

    fields = {f"{name}.{field.name}"
              for name, cls in vars(hir).items()
              if inspect.isclass(cls) and dataclasses.is_dataclass(cls)
              and cls.__module__ == "pscx.hir"
              for field in dataclasses.fields(cls)}
    if fields != set(FIELD_SOURCE):
        raise AssertionError(
            f"unnamed HIR fields: {sorted(fields - set(FIELD_SOURCE))}; "
            f"named and gone: {sorted(set(FIELD_SOURCE) - fields)}")


# --------------------------------------------------------------------------
# The input set
# --------------------------------------------------------------------------


def _profile_table() -> dict[str, str]:
    from pycgmes.utils.profile import Profile

    from pscx.emt import EMT_PROFILE_URI, EMT_SIMULATION_PROFILE_URI
    from pscx.surface import EMT_SOURCE_PROFILE_URI

    table = {Profile.DL.uris[0]: "DL",
             EMT_PROFILE_URI: "EMT",
             EMT_SIMULATION_PROFILE_URI: "EMTSIM",
             EMT_SOURCE_PROFILE_URI: "EMTSRC"}
    for profile in (Profile.EQ, Profile.TP, Profile.SC, Profile.SSH,
                    Profile.OP):
        table[profile.uris[0]] = profile.name
    return table


def _read_graphs(paths: Iterable) -> dict[str, rdflib.Graph]:
    """Parse the input set and key each graph by its stated profile."""
    from pscx.cimxml import MD_NS

    profile_of = _profile_table()
    md_profile = rdflib.URIRef(MD_NS + "Model.profile")
    graphs: dict[str, rdflib.Graph] = {}
    for path in paths:
        graph = rdflib.Graph()
        graph.parse(str(path), format="xml")
        stated = [str(uri) for uri in graph.objects(None, md_profile)]
        if len(stated) != 1:
            raise ValueError(f"{path} states {len(stated)} Model.profile "
                             f"headers")
        name = profile_of.get(stated[0])
        if name is None:
            raise ValueError(f"{path} states a profile outside the input "
                             f"set: {stated[0]}")
        if name in graphs:
            raise ValueError(f"two documents state the {name} profile")
        graphs[name] = graph
    missing = set(_profile_table().values()) - set(graphs)
    if missing:
        raise ValueError(f"the input set is not whole; missing: "
                         f"{sorted(missing)}")
    return graphs


# --------------------------------------------------------------------------
# The payload channel: the one read the wrapped inverses do not serve
# --------------------------------------------------------------------------


def _payloads(graph: rdflib.Graph) -> dict[tuple, list]:
    """Every ``emt:ToolPayload``, its stated content VERBATIM.

    ``reconstruct_record`` reads the same subjects and returns their
    content canonicalized, which is the shape a comparison wants; a
    writer reinserts the stated bytes, so this read keeps them. The
    owner resolution is the record's own: the ModelProject, a
    ModelDefinition by name, or a declared descriptor by
    ``(definition, spelling)``.
    """
    from urllib.parse import unquote

    from pscx.emt import CIM, EMT

    name_of = CIM["IdentifiedObject.name"]
    project_subject = next(
        iter(graph.subjects(RDF.type, EMT["ModelProject"])), None)
    of_subject: dict[Any, tuple] = {}
    for subject in graph.subjects(RDF.type, EMT["ModelDefinition"]):
        name = graph.value(subject, EMT["ModelDefinition.definitionName"])
        of_subject[subject] = ("definition", str(name))
    for descriptor in graph.subjects(RDF.type, CIM["ParameterDescriptor"]):
        owner = graph.value(
            descriptor,
            CIM["DetailedModelDescriptor.DetailedModelTypeDynamics"])
        type_name = str(graph.value(owner, name_of) or "")
        parts = type_name.split("/")
        if len(parts) == 3 and unquote(parts[1]) == DEFINITION:
            of_subject[descriptor] = ("parameter", unquote(parts[2]),
                                      str(graph.value(descriptor, name_of)))

    out: dict[tuple, list] = {}
    for subject in graph.subjects(RDF.type, EMT["ToolPayload"]):
        owner = graph.value(subject, EMT["ToolPayload.IdentifiedObject"])
        if owner == project_subject:
            key = ("project",)
        elif owner in of_subject:
            key = of_subject[owner]
        else:
            raise ValueError("a payload joins an owner the document does "
                             "not state")
        out.setdefault(key, []).append((
            str(graph.value(subject, EMT["ToolPayload.payloadKind"])),
            int(graph.value(subject, EMT["ToolPayload.sequenceNumber"])),
            str(graph.value(subject, EMT["ToolPayload.content"])),
        ))
    for rows in out.values():
        rows.sort(key=lambda row: (OPAQUE_PARENT_OF.get(row[0], ""),
                                   row[1], row[0]))
    return out


def _retained(kind: str, sequence: int, content: str) -> Opaque:
    """One payload as the :class:`Opaque` the writer knows how to place.

    ``write_project`` reads two things off a retained element: its
    parent's tag, to pick which written element it goes back under, and
    its index among the parent's children, to insert rather than
    append. A rebuilt element has neither, so it is mounted on a
    scaffold: a parent of the derivable tag, padded so the element
    sits at its stated index. The scaffold never enters any output --
    the writer deep-copies the element out of it.
    """
    element = ET.fromstring(content, _XML_PARSER)
    parent = ET.Element(OPAQUE_PARENT_OF[kind])
    for _ in range(max(sequence - 1, 0)):
        ET.SubElement(parent, "pad")
    parent.append(element)
    return Opaque(kind, element, None)


def _opaque_of(payloads: dict, key: tuple, parents: frozenset) -> list:
    return [_retained(kind, sequence, content)
            for kind, sequence, content in payloads.get(key, ())
            if OPAQUE_PARENT_OF[kind] in parents]


# --------------------------------------------------------------------------
# Field assembly
# --------------------------------------------------------------------------


def _params_of(stated: tuple) -> list[HirParams]:
    """``emt:SourceParameterList`` rows back as paramlists, verbatim."""
    return [HirParams(name, dict(entries)) for name, entries in stated]


def _placement_params(lists: tuple, order: list | None, stated_rows: dict,
                      element_key: tuple) -> list[HirParams]:
    """A drawn element's paramlists, keys in the type's canonical order.

    The stated key order of a placement paramlist is not carried -- the
    surface states values against descriptors -- so it comes back as
    the form's: declared spellings first in form order, everything else
    sorted. The name is the pinned rule's: ``global_address`` for the
    single-key list holding ``gaddress``, blank for every other one.
    A matrix parameter's rows come back verbatim from the surface's
    ``emt:MatrixRow`` statements.
    """
    out = []
    for n, values in enumerate(lists):
        head = [s for s in order or () if s in values]
        keys = head + sorted(set(values) - set(head))
        name = "global_address" if set(values) == {"gaddress"} else ""
        out.append(HirParams(
            name, {key: values[key] for key in keys},
            rows={key: stated_rows[element_key + (n, key)] for key in keys
                  if element_key + (n, key) in stated_rows}))
    return out


def _orient(rotation: float | None, mirrored: bool) -> str | None:
    """The stated ``orient`` back from its two carried halves: the
    quarter turn DL keeps as an angle, and the mirror bit the source
    subject states."""
    if rotation is None:
        return None
    return str(int(rotation) // 90 % 4 + (4 if mirrored else 0))


def _ports(defn_name: str, rows: tuple, symbol: dict) -> list[HirPort]:
    """A definition's ports: geometry from DL, declarations from the
    record, joined by the stated ``sourceIdentifier``.

    The join is stated, never guessed: each record row names the element
    id its DL DiagramObject is named by, and a row that states none is
    an id-less port, which both documents identify by its declaration
    ordinal. Element ids are draw-order accidents, so a document set
    that omits the join underdetermines the pairing -- the reader raises
    rather than picking one.
    """
    if len(rows) != len(symbol):
        raise ValueError(
            f"definition {defn_name!r} declares {len(rows)} ports and "
            f"draws {len(symbol)}")

    ports = []
    for index, (identifier, row) in enumerate(rows):
        drawn = symbol.get((PORT, identifier if identifier is not None
                            else f"#{index}"))
        if drawn is None:
            raise ValueError(
                f"definition {defn_name!r} declares a port whose stated "
                f"identifier {identifier!r} names no drawn symbol port: "
                f"the documents do not state the geometry join")
        values = {key: value
                  for (key, _attribute), value in zip(PORT_KEYS, row)
                  if value is not None}
        ports.append(HirPort(
            id=identifier, classid="Port", x=str(drawn.x), y=str(drawn.y),
            params=HirParams(None, {key: values[key]
                                    for key in PORT_KEY_ORDER
                                    if key in values})))
    return ports


def _form(defn_name: str, record_defn: dict, surface_forms: dict,
          payloads: dict) -> tuple[str | None, list[HirFormCategory]]:
    form_name, declared = surface_forms.get(defn_name, (None, ()))
    detail = {spelling: (desc, unit, value)
              for spelling, desc, unit, value in declared}
    categories = []
    for name, visible, condition, spellings in record_defn["categories"]:
        parameters = []
        for spelling in spellings:
            (type_, group, intent, dim, content_type, minimum, maximum,
             parameter_condition) = record_defn["parameters"][spelling]
            desc, unit, value = detail[spelling]
            parameters.append(HirFormParameter(
                name=spelling, type=type_, desc=desc, group=group,
                unit=unit, intent=intent, dim=dim,
                content_type=content_type, minimum=minimum,
                maximum=maximum, value=value,
                condition=parameter_condition, condition_type=None,
                opaque=_opaque_of(payloads,
                                  ("parameter", defn_name, spelling),
                                  frozenset({"parameter"}))))
        categories.append(HirFormCategory(
            name=name, visible=visible, condition=condition,
            condition_type=None, parameters=parameters))
    return form_name, categories


def _wire_classid(hosted: list[HirComponent], params: list,
                  record: dict) -> str:
    """The wire rule, read off the record's own statements: what a wire
    hosts decides its class, and a hosted row definition's page decides
    line against cable."""
    if not hosted:
        return "Bus" if params else "WireOrthogonal"
    name = (hosted[0].defn or "").rpartition(":")[2]
    info = record["definitions"].get(name)
    if info is None or info["sourceClass"] != "RowDefn":
        return "WireBranch"
    drawn = {record["references"].get(key) or ""
             for key, page in record["containment"].items()
             if page == name and key[1] == USER
             and key not in record["hosting"]}
    return "Cable" if any("Cable_" in defn for defn in drawn) else "TLine"


def _canvas(defn_name: str, defn_classid: str | None, record: dict,
            surface: dict, drawn_page: dict, payloads: dict,
            order_of, bus: Diagnostics) -> HirCanvas:
    containment = record["containment"]
    references = record["references"]
    hosting = record["hosting"]
    mirrored = record["mirrored"]

    def build_component(ident: str) -> HirComponent:
        key = (defn_name, USER, ident)
        stated_name, lists = surface["elements"].get(key, (None, ()))
        drawn = drawn_page.get((USER, ident))
        if drawn is None:
            bus.emit("reader_geometry_missing", f"{defn_name}/{ident}")
        defn = references.get(key)
        return HirComponent(
            classid="UserCmp", id=ident, defn=defn,
            x=None if drawn is None else str(drawn.x),
            y=None if drawn is None else str(drawn.y),
            orient=None if drawn is None else _orient(
                drawn.rotation, mirrored.get(key, False)),
            name=stated_name, disable="false", layer="",
            params=_placement_params(lists, order_of(defn),
                                     surface["rows"], key))

    users = sorted(
        (key[2] for key, page in containment.items()
         if page == defn_name and key[1] == USER),
        key=lambda ident: (0, int(ident)) if ident.isdigit() else (1, ident))
    components = {ident: build_component(ident) for ident in users}

    wire_idents = {key[2] for key, page in containment.items()
                   if page == defn_name and key[1] == WIRE}
    wire_idents |= {ident for kind, ident in drawn_page if kind == WIRE}
    numbered = sorted((ident for ident in wire_idents
                       if not ident.startswith("#")),
                      key=lambda i: int(i) if i.isdigit() else 0)
    wires: list[HirWire] = []
    hosted_of: dict[str, list] = {}
    for hosted_key, wire_key in hosting.items():
        if wire_key[0] == defn_name:
            hosted_of.setdefault(wire_key[2], []).append(hosted_key[2])

    def build_wire(ident: str) -> HirWire:
        key = (defn_name, WIRE, ident)
        stated_name, lists = surface["elements"].get(key, (None, ()))
        drawn = drawn_page.get((WIRE, ident))
        if drawn is None:
            bus.emit("reader_geometry_missing", f"{defn_name}/{ident}")
        hosted = [components.pop(h) for h in sorted(
            hosted_of.get(ident, ()),
            key=lambda i: int(i) if i.isdigit() else 0)]
        params = _placement_params(lists, None, surface["rows"], key)
        return HirWire(
            classid=_wire_classid(hosted, params, record),
            id=None if ident.startswith("#") else ident,
            name=stated_name or "",
            x=None if drawn is None else str(drawn.x),
            y=None if drawn is None else str(drawn.y),
            orient=None if drawn is None else _orient(
                drawn.rotation, mirrored.get(key, False)),
            disable="false", layer="",
            defn=references.get(key),
            vertices=[] if drawn is None else [
                (str(dx), str(dy)) for dx, dy in drawn.vertices],
            params=params, hosted=hosted)

    for ident in numbered:
        wires.append(build_wire(ident))
    for ordinal in sorted(int(ident[1:]) for ident in wire_idents
                          if ident.startswith("#")):
        wires.insert(min(ordinal, len(wires)), build_wire(f"#{ordinal}"))

    classid = {"StationDefn": "StationCanvas", "UserCmpDefn": "UserCanvas",
               "RowDefn": "RowCanvas"}.get(defn_classid or "")
    return HirCanvas(
        classid=classid,
        params=_params_of(record["definitions"][defn_name]
                          ["canvas_paramlists"]),
        components=list(components.values()),
        wires=wires,
        opaque=_opaque_of(payloads, ("definition", defn_name),
                          frozenset({"schematic"})))


def _substitutions(version: str | None,
                   stated: tuple) -> list[HirSubstitutions]:
    """The container back from its pinned shape: a 5.0-era project
    states exactly one, of fixed form, and a 4.5-era one states none.
    The Subs are the stated rows in table order; a stated ``cat`` is
    the empty string, restated beside the carried group."""
    if not (version or "").startswith("5."):
        return []
    subs = []
    for name, value, group in stated:
        values = {"name": name, "value": value}
        if group is not None:
            values.update({"group": group, "cat": ""})
        subs.append(HirSubstitution(
            id=None, classid="GlobalSubstitution",
            params=[HirParams(None, values)]))
    return [HirSubstitutions(
        tag="GlobalSubstitutions", name="Default",
        params=[HirParams(None, {"Current": ""})],
        lists=[HirSubstitutionList(classid="Sub", subs=subs),
               HirSubstitutionList(classid="ValueSet")])]


# --------------------------------------------------------------------------
# The reader
# --------------------------------------------------------------------------


def read_documents(paths: Iterable, master=None,
                   bus: Diagnostics | None = None) -> HirProject:
    """The nine documents of one case, back as an ``HirProject``.

    ``paths`` is the input set in any order; each document is
    recognized by its stated profile. ``master`` is the reader's own
    ``master.pslx`` -- a path or an already-loaded ``HirProject`` --
    and may be None, in which case master-typed placements' paramlists
    come back sorted rather than in master's form order.
    """
    from pscx.emt import CIM, EMT
    from pscx.hir import load_project

    bus = DIAGNOSTICS if bus is None else bus
    _assert_covered()
    graphs = _read_graphs(paths)

    record = reconstruct_record(graphs["EMTSRC"])
    surface = reconstruct_surface(graphs["EMTSRC"])
    drawing = reconstruct_drawing([graphs["DL"]])
    payloads = _payloads(graphs["EMTSRC"])

    name = record["project"]["name"]
    version = record["project"]["version"]
    study_names = {
        str(stated)
        for subject in graphs["EMTSIM"].subjects(RDF.type,
                                                 EMT["SimulationCase"])
        for stated in graphs["EMTSIM"].objects(
            subject, CIM["IdentifiedObject.name"])}
    if name not in study_names:
        bus.emit("reader_study_disagrees",
                 f"{name!r} not among {sorted(study_names)}")

    master_orders: dict[str, list] = {}
    if master is not None:
        if not isinstance(master, HirProject):
            master = load_project(str(master), bus=Diagnostics())
        for definition in master.definitions:
            if definition.name and definition.name not in master_orders:
                master_orders[definition.name] = [
                    spelling for spelling, _p
                    in _declared_form(definition)]
    local = set(record["definitions"])
    case_orders = {
        defn_name: [spelling for spelling, _d, _u, _v in declared]
        for defn_name, (_title, declared) in surface["forms"].items()}

    def order_of(defn: str | None) -> list | None:
        key = _resolve(defn, name, local)
        if key is None:
            return None
        if key[0] == DEFINITION:
            return case_orders.get(key[1])
        if key[0] == LIBRARY and key[1].startswith("master:"):
            return master_orders.get(key[1][len("master:"):])
        return None

    definitions = []
    for defn_name in sorted(record["definitions"]):
        info = record["definitions"][defn_name]
        form_name, categories = _form(defn_name, info, surface["forms"],
                                      payloads)
        segments = []
        for segment_name, text in info["scripts"]:
            tree = parse_script(text)
            if segment_name == "Branch":
                tree = assemble_splices(tree, defn_name)
            segments.append(HirSegment(name=segment_name, id=None,
                                       text=text, tree=tree))
        canvas = None
        if (defn_name, SCHEMATIC) in drawing:
            canvas = _canvas(defn_name, info["sourceClass"], record,
                             surface, drawing[(defn_name, SCHEMATIC)],
                             payloads, order_of, bus)
        definitions.append(HirDefinition(
            classid=info["sourceClass"], name=defn_name, id=None,
            group=info["group"],
            params=_params_of(info["paramlists"]),
            form_name=form_name, form=categories,
            ports=_ports(defn_name, info["ports"],
                         drawing.get((defn_name, SYMBOL), {})),
            segments=segments, canvas=canvas,
            opaque=_opaque_of(payloads, ("definition", defn_name),
                              frozenset({"Definition", "graphics"}))))

    project = HirProject(
        path=f"{name}.pscx",
        name=name,
        version=version,
        target=record["project"]["target"],
        schema=None if version is None
        else ("0" if version.startswith("4.") else ""),
        params=_params_of(record["project_paramlists"]),
        definitions=definitions,
        layers=[],
        substitutions=_substitutions(version, record["substitutions"]),
        settings=[],
        opaque=_opaque_of(payloads, ("project",), frozenset({"project"})),
        unrecognized=[])
    return project


# --------------------------------------------------------------------------
# The consumer entry point: precedence over a divergent set
# --------------------------------------------------------------------------


class Precedence(enum.Enum):
    """The consumer's resolved decision over a divergent set.

    The default REFUSES: divergence is a question, and the tool does not
    answer questions the consumer did not ask. SOURCE proceeds with the
    statement of record and reports what was ignored -- the identity
    behavior, made loud. INTERCHANGE applies the engine values into the
    reconstruction wherever a stated-parameter inverse exists, and
    refuses by name wherever none does. This is the resolved decision
    the API takes; the flag strings live on the CLI alone.
    """

    REFUSE = "refuse"
    SOURCE = "source"
    INTERCHANGE = "interchange"


class DivergentSet(Exception):
    """A supplied set that disagrees with its own re-emission, refused.

    Carries the full report; ``refused`` names the rows that forced the
    refusal -- every divergence under REFUSE, the unappliable and
    unrepresentable rows under INTERCHANGE. Deliberately not a
    ``ValueError``: a malformed input set is exit 2, a coherently
    malformed CASE is exit 1, and the two must not be catchable as one.
    """

    def __init__(self, report, refused) -> None:
        lines = "\n".join(d.line() for d in refused)
        super().__init__(
            f"the supplied set disagrees with its own re-emission on "
            f"{len(refused)} statement(s):\n{lines}")
        self.report = report
        self.refused = refused


class Burn(NamedTuple):
    """One ``$()`` parameterization replaced by a literal, by name."""

    names: tuple
    place: str
    was: str
    text: str

    def line(self) -> str:
        names = ", ".join(self.names)
        return (f"burned $({names}): {self.place} stated {self.was!r}, "
                f"now states {self.text!r}")


def _apply_parameter(project: HirProject, page: str, kind: str, ident: str,
                     spelling: str, text: str) -> list[tuple[str, str]]:
    """State ``text`` on the drawn element's parameter, verbatim into
    every paramlist entry whose spelling case-folds to the engine's --
    two spellings of one name are one parameter to the solver (the
    detailed-model rule), and the engine value overrides them both.

    A WIRE identity applies into the hosted component's paramlists: a
    hosted device is identified by the hosting wire's id and its own
    paramlist is the whole statement of what it is. A spelling
    the element states nowhere is a form DEFAULT the engine overrode --
    the placement's environment merges the form's defaults under its
    stated entries -- and stating it explicitly is exactly what a
    consumer editing the ``.pscx`` would do, so it is added to the last
    paramlist, where the environment merge lets it win. Returns the
    ``(spelling, previous text)`` pairs replaced ('' for a default made
    explicit); an element with no paramlist at all raises.
    """
    for definition in project.definitions:
        if definition.name != page or definition.canvas is None:
            continue
        paramlists = None
        if kind == USER:
            for component in definition.canvas.components:
                if str(component.id) == ident:
                    paramlists = component.params
        else:
            for wire in definition.canvas.wires:
                if str(wire.id) == ident:
                    if not wire.hosted:
                        raise ValueError(
                            f"the engine restates wire {ident} on page "
                            f"{page!r} but it hosts no device to state "
                            f"a parameter on")
                    paramlists = wire.hosted[0].params
        if paramlists is None:
            continue
        replaced = []
        for paramlist in paramlists:
            for name, value in list(paramlist.values.items()):
                if name.lower() == spelling.lower():
                    paramlist.values[name] = text
                    replaced.append((name, value))
        if replaced:
            return replaced
        if paramlists:
            paramlists[-1].values[spelling] = text
            return [(spelling, "")]
        raise ValueError(
            f"the engine restates {spelling!r} of {kind} {ident} on "
            f"page {page!r}, which states no paramlist to carry it")
    raise ValueError(
        f"the engine restates {spelling!r} of {kind} {ident} on page "
        f"{page!r}, which the reconstruction does not state")


def _apply_setting(project: HirProject, parameter: str, text: str) -> str:
    """State ``text`` on the project's Settings paramlist, creating the
    entry where the projection's inverse names one the source never
    stated. Returns the previous text ('' for none)."""
    for paramlist in project.params:
        if paramlist.name == "Settings":
            previous = paramlist.values.get(parameter, "")
            paramlist.values[parameter] = text
            return previous
    raise ValueError("the reconstruction states no Settings paramlist "
                     "to apply an engine value into")


def read_case(paths: Iterable, master=None, bus: Diagnostics | None = None,
              precedence: Precedence = Precedence.REFUSE
              ) -> tuple[HirProject, list[Burn]]:
    """The consumer's reader: the nine documents of one case, diagnosed
    against their own re-emission, then read under an explicit
    precedence.

    A COHERENT set reads exactly as :func:`read_documents` reads it --
    the diagnosis reconstructs once and this returns that same project.
    A divergent set is refused by default (:class:`DivergentSet`, the
    report attached); under ``Precedence.SOURCE`` the statement of
    record proceeds and every ignored divergence is reported on the
    bus; under ``Precedence.INTERCHANGE`` the engine values are applied
    INTO the reconstruction -- the stated text becomes the literal in
    its declared unit -- and every ``$()`` parameterization that
    replaces is a :class:`Burn`, reported by name. What no flag reaches:
    a projection with no stated-parameter inverse (an EQ impedance) and
    the unrepresentable class (instances of one drawn element whose
    engine values disagree with each other) refuse by name even under
    INTERCHANGE, because overwriting them has no home in a ``.pscx``.

    Nothing is ever preferred silently: the identity path aside, every
    outcome is a refusal, a report, or both.
    """
    from pscx.check import UNREPRESENTABLE, diagnose

    bus = DIAGNOSTICS if bus is None else bus
    diagnosis = diagnose(paths, master=master, bus=bus)
    project, report = diagnosis.project, diagnosis.report
    if not report.divergences:
        return project, []
    if precedence is Precedence.REFUSE:
        raise DivergentSet(report, report.divergences)
    unrepresentable = [divergence for divergence in report.divergences
                       if divergence.classification == UNREPRESENTABLE]
    if unrepresentable:
        # no side reaches this class: the set claims per-instance engine
        # values no .pscx can state, so blessing it under EITHER flag
        # would return a case pretending to stand for it
        raise DivergentSet(report, unrepresentable)
    if precedence is Precedence.SOURCE:
        for divergence in report.divergences:
            bus.emit("reader_divergence_ignored", divergence.line())
        return project, []
    if diagnosis.unappliable:
        raise DivergentSet(report, diagnosis.unappliable)
    burns: list[Burn] = []
    for application in diagnosis.applications:
        if application.target[0] == "parameter":
            _kind, page, kind, ident, spelling = application.target
            place = f"drawn {page}/{kind}/{ident} {spelling}"
            replaced = _apply_parameter(project, page, kind, ident,
                                        spelling, application.text)
        else:
            parameter = application.target[1]
            place = f"Settings {parameter}"
            replaced = [(parameter, _apply_setting(project, parameter,
                                                   application.text))]
        for _spelling, previous in replaced:
            names = tuple(re.findall(r"\$\((\w+)\)", previous))
            if names:
                burn = Burn(names, place, previous, application.text)
                burns.append(burn)
                bus.emit("reader_expression_burned", burn.line())
    return project, burns

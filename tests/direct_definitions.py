"""The direct-XML definition reader, kept so there is something to compare
``pscx.io.load_definitions`` against.

``load_definitions`` builds every ``ComponentDef`` from the HIR. The
reader below walks the element tree itself, with ``iter`` where the HIR
uses ``find``, and it is the ONLY thing in the repo that reads a
``<Definition>`` without going through ``pscx.hir.load_project``. It
exists to be compared with, and for no other reason. Nothing on the
extraction path imports it.

Delete it and ``test_definition_lowering.py`` compares an
HIR-derived ``ComponentDef`` against an HIR-derived ``ComponentDef``: green,
and proving nothing. A gate that compares a thing to itself is worse than
one that passes over zero cases, because it does not even look empty.

It carries none of ``load_definitions``' diagnostics. ``load_definitions``
is a diagnostic pass (it names a library that will not parse, one that
declares nothing, and a port whose coordinates are not numbers), and
``test_spans.py`` holds that contract against hand-numbered fixtures. An
oracle carrying a second copy of it would be a second thing to keep true
and nothing would compare the two. So this one is about CONTENT: it
raises on a file it cannot read, and it hands back the ports it dropped
instead of reporting them.

It reads ``intent="Output"`` parameters inside ``<form>/<category>``
only. A ``<parameter>`` outside any category is a shape no definition in
master states, and a second walk that read one without
repeating a category's would have to tell the two apart by ``id()`` of
an lxml element proxy. lxml frees a proxy as soon as nothing references
it (``category.findall("parameter")`` drops its proxies at the end of
each category), so whether that walk sees the same ``id()`` is whether
CPython hands back the same address. When it does not, the walk appends
a duplicate writer directive for a parameter the category walk already
read, and loading one file repeatedly gives different answers. An
oracle that answers differently on the same bytes cannot say whether
anything else changed.
"""

import os

from lxml import etree as ET

from pscx.common import _WRITER_SEGMENTS
from pscx.guards import assemble_splices, parse_script
from pscx.io import _XML_PARSER
from pscx.model import ComponentDef, FormParameterDef, PortDef
from pscx.preproc import (
    BranchDecl,
    _branch_decls,
    _writer_directives_of,
)


def _params(element: ET.Element) -> dict[str, str]:
    plist = element.find("paramlist")
    return {p.get("name"): p.get("value") for p in plist} if plist is not None else {}


def load_definitions_directly(
        path: str, registry: dict[tuple[str, str], ComponentDef],
        dropped_ports: list | None = None) -> None:
    """Load every ``<Definition>`` from a .pslx/.pscx into ``registry``.

    Keyed by ``(namespace, name)`` so a project-local ``resistor`` cannot
    shadow ``master:resistor``. Names of ports whose coordinates do not
    parse are appended to ``dropped_ports``: a port both readers discard
    is agreement reached by both sides throwing the same thing away, and a
    comparison that cannot see it would call that a match.
    """
    dropped = [] if dropped_ports is None else dropped_ports
    root = ET.parse(path, _XML_PARSER).getroot()
    namespace = root.get("name") or os.path.splitext(os.path.basename(path))[0]
    for definition in root.findall(".//Definition"):
        ports = []
        for port in definition.iter("Port"):
            attrs = _params(port)
            try:
                px, py = int(port.get("x")), int(port.get("y"))
            except (TypeError, ValueError):
                dropped.append(
                    f"{definition.get('name')}.{attrs.get('name') or '?'}")
                continue
            raw_name = attrs.get("name") or ""
            base_name, _, suffix = raw_name.partition(":")
            raw_dim = attrs.get("dim", "1")
            dim_name = suffix.strip() or None
            if dim_name is None and raw_dim and not raw_dim.lstrip("-").isdigit():
                dim_name, raw_dim = raw_dim, "0"  # conjugate-style dim attr
            ports.append(
                PortDef(
                    name=base_name,
                    x=px,
                    y=py,
                    condition=attrs.get("cond", "true"),
                    mode=attrs.get("mode", "0"),
                    electype=attrs.get("electype", "0"),
                    dim=raw_dim,
                    internal=(attrs.get("internal", "false") == "true"),
                    dim_name=dim_name,
                    datatype=attrs.get("datatype", "0"),
                )
            )
        defaults = {}
        units: dict[str, str] = {}
        for parameter in definition.iter("parameter"):
            value = parameter.find("value")
            defaults[parameter.get("name")] = (
                (value.text or "").strip() if value is not None else ""
            )
            unit = (parameter.get("unit") or "").strip()
            if unit:
                units[(parameter.get("name") or "").lower()] = unit
        writer_directives: list[tuple[str, int | None, tuple]] = []
        branch_decls: list[BranchDecl] = []
        computations_text = ""
        model_data_text = ""
        guard_trees: list[tuple[str, tuple]] = []
        for segment in definition.iter("segment"):
            seg_name = segment.get("name") or "?"
            text = segment.text or ""
            # parsed once and kept: every reader below walks this tree, and
            # the arm-coverage sweep walks the ones no reader consumes
            tree = parse_script(text)
            if seg_name == "Branch":
                tree = assemble_splices(tree, definition.get("name") or "?")
            guard_trees.append((seg_name, tree))
            if seg_name in _WRITER_SEGMENTS:
                writer_directives.extend(_writer_directives_of(tree))
            elif seg_name == "Branch":
                branch_decls.extend(_branch_decls(
                    tree, definition.get("name") or "?"))
            elif seg_name == "Computations":
                computations_text += text + "\n"
            elif seg_name == "Model-Data":
                model_data_text += text + "\n"
        # Second writer mechanism: form parameters declared intent="Output"
        # (always Real/content_type="Variable", never also an #OUTPUT
        # param). The script references them as ``$Param`` output
        # arguments directly -- e.g. intermediate.pslx duality transformers
        # writing FLUX_*/IMAG_* monitoring signals. Their form enable-conds
        # (category and parameter level) gate whether the write is active.
        form_parameters: list[FormParameterDef] = []
        for category in definition.iter("category"):
            ccond = category.find("cond")
            cguard = (ccond.text or "").strip() if ccond is not None else ""
            category_condition = ((ccond.text or "").strip()
                                  if ccond is not None else None)
            for parameter in category.findall("parameter"):
                pcond = parameter.find("cond")
                ptext = (pcond.text or "").strip() if pcond is not None else ""
                if parameter.get("name"):
                    unit = (parameter.get("unit") or "").strip()
                    form_parameters.append(FormParameterDef(
                        name=parameter.get("name"),
                        type=parameter.get("type"),
                        unit=unit or None,
                        condition=(ptext if pcond is not None else None),
                        category_condition=category_condition,
                    ))
                if parameter.get("intent") != "Output":
                    continue
                guards: list[tuple[str, bool]] = []
                if cguard:
                    guards.append((cguard, True))
                if ptext:
                    guards.append((ptext, True))
                writer_directives.append((parameter.get("name"), 1, tuple(guards)))
        registry[(namespace, definition.get("name"))] = ComponentDef(
            namespace=namespace,
            name=definition.get("name"),
            ports=tuple(ports),
            defaults=defaults,
            writer_directives=tuple(writer_directives),
            branch_decls=tuple(branch_decls),
            computations_text=computations_text,
            model_data_text=model_data_text,
            units=units,
            guard_trees=tuple(guard_trees),
            form_parameters=tuple(form_parameters),
        )

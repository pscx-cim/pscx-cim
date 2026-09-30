"""Emit a .pscx / .pslx document back out of the HIR.

This exists to CHECK a claim, not to serve a consumer: :mod:`pscx.hir` says
an OPAQUE subtree is "retained, so a writer can put it back unchanged", and
until something puts one back that sentence is untested. Nothing in the
package imports this module; its only caller is the round-trip test.

The writer emits exactly what the HIR carries and never consults the source
tree for anything the HIR does not hold. The one place it reads a source
element is :func:`_opaque_into`, and that is the mechanism under test: an
:class:`~pscx.hir.Opaque` RETAINS its element, so the element's own parent
tag is part of what was retained.

An UNRECOGNIZED attribute goes back on the one element that carried it:
the bank records the element's XPath, and :func:`_put_back` resolves it
against the written tree -- tag-positional steps, which is what the writer
preserves -- and sets the value where it lands. What still cannot come
back, and why the round trip is therefore lossy:

  - A banked attribute whose xpath names an element the writer never
    emits -- a discarded ``<Layer>`` or ``<graphics>`` paramlist, an empty
    ``<graphics>`` -- has its value and its identity and nowhere to stand.
  - An UNRECOGNIZED element is banked as a TAG only -- ``value`` is None by
    construction -- so its content is not merely unplaceable, it is gone.
  - Document order among children of different kinds is not recorded.
    ``HirCanvas`` keeps components and wires in two lists, and a canvas
    that draws ``User, Wire, User`` states an order neither list holds.

Whitespace and attribute order are not the claim; comparison against a
source document goes through :func:`canonicalize`.
"""

from __future__ import annotations

import copy

from lxml import etree as ET

from pscx.hir import (
    HirCanvas,
    HirComponent,
    HirDefinition,
    HirParams,
    HirProject,
    HirSubstitutions,
    HirWire,
    Opaque,
    Unrecognized,
)


def canonicalize(element: ET._Element) -> bytes:
    """Canonical XML for one element, whitespace-insensitive.

    C14N settles attribute order and namespace declarations; ``strip_text``
    settles indentation. Neither is what a losslessness claim is about, and
    comparing without them would report every file as different for reasons
    no reader cares about.
    """
    return ET.tostring(element, method="c14n2", strip_text=True)


def _set(element: ET._Element, **attrs: str | None) -> None:
    """Set the attributes the HIR holds a value for, in the order given.

    A field the HIR holds as None was ABSENT from the source, not empty:
    the loader keeps only attributes ``element.get`` returned, so writing
    ``name=""`` for a missing name would state something the file did not.
    """
    for name, value in attrs.items():
        if value is not None:
            element.set(name, value)


def _opaque_into(parents: dict[str, ET._Element], opaque: list[Opaque],
                 default: ET._Element) -> None:
    """Put each retained subtree back under the parent it was read from,
    at the position it was read from.

    Both come off the retained element itself, which is what "retained"
    buys: ``HirDefinition.opaque`` merges the definition's own opaque
    children with its ``graphics``'s into one list, so presence alone
    cannot say where an element belongs, and appending states an order the
    file did not -- a ``<parameter>`` writing ``value, regex, error_msg,
    cond`` where the form wrote ``value, regex, error_msg, cond, help``
    has put every child back and still says something else.

    The index is clamped, because the modeled siblings around it need not
    all have come back. Deep-copied, so the written tree never shares nodes
    with the source one and a caller may mutate either.
    """
    for item in opaque:
        parent = item.element.getparent()
        target = parents.get(parent.tag, default) if parent is not None \
            else default
        index = parent.index(item.element) if parent is not None else len(target)
        target.insert(min(index, len(target)), copy.deepcopy(item.element))


def _step(step: str) -> tuple[str, int]:
    """``"User[18]" -> ("User", 18)``; a step with no index is the first."""
    if step.endswith("]"):
        tag, _, index = step[:-1].partition("[")
        return tag, int(index)
    return step, 1


def _nth(kids: dict, parent: ET._Element, tag: str,
         index: int) -> ET._Element | None:
    """The ``index``-th ``tag`` child of ``parent``, 1-based, or None.

    ``kids`` caches each parent's children grouped by tag, so resolving
    every banked attribute of a document stays linear in its size rather
    than rescanning one canvas's siblings per lookup.
    """
    table = kids.get(parent)
    if table is None:
        table = {}
        for child in parent:
            if isinstance(child.tag, str):
                table.setdefault(child.tag, []).append(child)
        kids[parent] = table
    siblings = table.get(tag)
    if siblings is None or index > len(siblings):
        return None
    return siblings[index - 1]


def _resolve(root: ET._Element, xpath: str, memo: dict,
             kids: dict) -> ET._Element | None:
    """The element ``xpath`` names in the WRITTEN tree, or None.

    The path was recorded against the source document, and tag-positional
    steps are exactly what survives the writer: it emits each tag's
    occurrences under a parent in source order, so ``User[18]`` is the
    same User even where the interleaving with other tags is not held.
    None -- rather than a guess -- when a step names something the writer
    did not emit.
    """
    if xpath in memo:
        return memo[xpath]
    head, _, step = xpath.rpartition("/")
    tag, index = _step(step)
    if head:
        parent = _resolve(root, head, memo, kids)
        element = None if parent is None else _nth(kids, parent, tag, index)
    else:
        element = root if root.tag == tag and index == 1 else None
    memo[xpath] = element
    return element


def _put_back(root: ET._Element, unrecognized: list[Unrecognized]) -> None:
    """Set each banked attribute on the element its xpath names.

    Only attributes: a banked ELEMENT holds no content to put back. Only
    where the path resolves to an element of the owner's tag -- a path
    into a subtree the writer never emitted stays lost, and the round-trip
    triage counts it. An attribute already present is left alone: an
    element inside a retained OPAQUE subtree came back verbatim, values
    included, and the bank must not overwrite what retention put there.
    """
    memo: dict = {}
    kids: dict = {}
    for item in unrecognized:
        if item.kind != "attribute" or item.xpath is None \
                or item.value is None:
            continue
        target = _resolve(root, item.xpath, memo, kids)
        if target is None or target.tag != item.owner:
            continue
        if target.get(item.name) is None:
            target.set(item.name, item.value)


def _write_params(parent: ET._Element, params: HirParams) -> ET._Element:
    element = ET.SubElement(parent, "paramlist")
    _set(element, name=params.name)
    for name, value in params.values.items():
        child = ET.SubElement(element, "param")
        _set(child, name=name, value=value)
        # a matrix parameter's rows, verbatim and in row order, in the
        # CDATA form the source tool writes them in
        for text in params.rows.get(name, ()):
            ET.SubElement(child, "row").text = ET.CDATA(text)
    return element


def _write_component(parent: ET._Element, comp: HirComponent) -> ET._Element:
    element = ET.SubElement(parent, "User")
    _set(element, classid=comp.classid, id=comp.id, defn=comp.defn,
         x=comp.x, y=comp.y, orient=comp.orient, name=comp.name,
         disable=comp.disable, layer=comp.layer)
    for params in comp.params:
        _write_params(element, params)
    return element


def _write_wire(parent: ET._Element, wire: HirWire) -> ET._Element:
    element = ET.SubElement(parent, "Wire")
    _set(element, classid=wire.classid, id=wire.id, name=wire.name,
         x=wire.x, y=wire.y, orient=wire.orient, disable=wire.disable,
         layer=wire.layer, defn=wire.defn)
    for x, y in wire.vertices:
        _set(ET.SubElement(element, "vertex"), x=x, y=y)
    for params in wire.params:
        _write_params(element, params)
    for hosted in wire.hosted:
        _write_component(element, hosted)
    return element


def _write_canvas(parent: ET._Element, canvas: HirCanvas) -> ET._Element:
    element = ET.SubElement(parent, "schematic")
    _set(element, classid=canvas.classid)
    for params in canvas.params:
        _write_params(element, params)
    for comp in canvas.components:
        _write_component(element, comp)
    for wire in canvas.wires:
        _write_wire(element, wire)
    _opaque_into({}, canvas.opaque, element)
    return element


def _write_cond(parent: ET._Element, text: str | None,
                type_: str | None) -> None:
    if text is None:
        return
    cond = ET.SubElement(parent, "cond")
    _set(cond, type=type_)
    cond.text = text


def _write_form(parent: ET._Element, defn: HirDefinition) -> ET._Element:
    element = ET.SubElement(parent, "form")
    _set(element, name=defn.form_name)
    for category in defn.form:
        node = ET.SubElement(element, "category")
        _set(node, name=category.name, visible=category.visible)
        _write_cond(node, category.condition, category.condition_type)
        for param in category.parameters:
            child = ET.SubElement(node, "parameter")
            _set(child, name=param.name, type=param.type, desc=param.desc,
                 group=param.group, unit=param.unit, intent=param.intent,
                 dim=param.dim, content_type=param.content_type,
                 min=param.minimum, max=param.maximum)
            if param.value is not None:
                ET.SubElement(child, "value").text = param.value
            _write_cond(child, param.condition, param.condition_type)
            _opaque_into({}, param.opaque, child)
    return element


def _write_definition(parent: ET._Element,
                      defn: HirDefinition) -> ET._Element:
    element = ET.SubElement(parent, "Definition")
    _set(element, classid=defn.classid, name=defn.name, id=defn.id,
         group=defn.group)
    for params in defn.params:
        _write_params(element, params)
    # `form_name` is the form element's own attribute, so a definition
    # with neither categories nor a title states an empty <form> at
    # most -- and a UserCmpDefn states exactly that. Every shipped
    # UserCmpDefn carries a <form>; PSCAD null-references loading one
    # that does not, and its own save keeps the bare <form/> -- so the
    # empty form is a rule of the class, not a carried value. No other
    # definition class states one.
    if defn.form or defn.form_name is not None \
            or defn.classid == "UserCmpDefn":
        _write_form(element, defn)
    if defn.segments:
        script = ET.SubElement(element, "script")
        for segment in defn.segments:
            node = ET.SubElement(script, "segment")
            _set(node, name=segment.name, id=segment.id)
            node.text = segment.text
    if defn.canvas is not None:
        _write_canvas(element, defn.canvas)
    graphics = ET.SubElement(element, "graphics")
    for port in defn.ports:
        node = ET.SubElement(graphics, "Port")
        _set(node, id=port.id, classid=port.classid, x=port.x, y=port.y)
        if port.params is not None:
            _write_params(node, port.params)
    _opaque_into({"graphics": graphics}, defn.opaque, element)
    if len(graphics) == 0:
        element.remove(graphics)
    return element


def _write_substitutions(parent: ET._Element,
                         container: HirSubstitutions) -> ET._Element:
    """Emit a ``<GlobalSubstitutions>`` at the three depths it was read at.

    Depth is the whole point. ``pscx.nets`` resolves ``$(NAME)`` from
    ``root.iter("Sub")``, which is recursive, so a ``<Sub>`` put back at
    the wrong level still answers the reference while the document says
    something the file did not -- a fidelity check on values alone cannot
    see the difference, and the element census can.

    The container's own paramlists go LAST, after the lists, because that
    is where every file states them and because they are a different
    thing from the paramlist inside a ``<Sub>``.
    """
    element = ET.SubElement(parent, container.tag)
    _set(element, name=container.name)
    for group in container.lists:
        node = ET.SubElement(element, "List")
        _set(node, classid=group.classid, name=group.name)
        for sub in group.subs:
            child = ET.SubElement(node, "Sub")
            _set(child, id=sub.id, classid=sub.classid)
            for params in sub.params:
                _write_params(child, params)
    for params in container.params:
        _write_params(element, params)
    return element


def write_project(project: HirProject) -> ET._Element:
    """A ``<project>`` tree holding everything the HIR kept of one file."""
    root = ET.Element("project")
    _set(root, name=project.name, version=project.version,
         schema=project.schema, Target=project.target)
    for params in project.params:
        _write_params(root, params)
    if project.definitions:
        definitions = ET.SubElement(root, "definitions")
        for defn in project.definitions:
            _write_definition(definitions, defn)
    if project.layers:
        layers = ET.SubElement(root, "Layers")
        for layer in project.layers:
            _set(ET.SubElement(layers, "Layer"), name=layer.name,
                 state=layer.state, id=layer.id)
    for container in project.substitutions:
        _write_substitutions(root, container)
    # `project.settings` is deliberately NOT written: a case states its
    # Settings inside a ROOT-level <List>, which is OPAQUE and therefore
    # already returned whole. Writing them again would emit each twice.
    # The <List> inside a substitution container is a different element
    # with the same tag, and is modeled rather than retained.
    _opaque_into({}, project.opaque, root)
    _put_back(root, project.unrecognized)
    return root

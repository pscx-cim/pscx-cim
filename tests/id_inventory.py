"""Every ``id()`` in ``src/pscx/``, and what holds the object it addresses.

``id()`` returns an address, and an address is an identity only while
something holds the object. CPython hands the same address back for a new
object as soon as the old one is freed, so a set, dict or cache keyed on
one is keyed on a value the runtime is free to reuse.

An lxml element proxy is the one kind of object whose address is wrong
by construction: the library frees the proxy when the last reference
goes, so a guard such as ``id(parameter) not in seen`` over one can give
two different answers for the same bytes.
No call in the package takes the address of one, and this inventory is
what says so.

Every call is correct, and correct for a reason nothing in the
source stated: THE OBJECT STAYS REACHABLE FOR AS LONG AS ITS ADDRESS IS
USED AS A KEY. That is an invariant about the OWNERSHIP GRAPH, not about
``id()`` -- a ``Component``, an ``Instance`` and a ``Netlist`` are
dataclasses the ``FlatProject`` holds for its whole life, and every
``id()``-keyed map in the package is built and read inside that lifetime.
So the classification here is not "safe / unsafe" but "held by WHAT", and
``holder`` names it per site.

Why it is written down rather than reasoned about again: an ``id()``-keyed
map that stops being backed by a live object FAILS OPEN. A module instance
simply stops being recognised as a module instance and the result is a
plausible netlist, not an error, the same shape of failure as an empty
registry. Nothing else in this repo would notice.

``test_id_inventory.py`` compares this table against the source in both
directions, so a call that appears, moves to another function or changes
its argument fails until someone has classified it. It is keyed on
``(module, qualname, expression)`` and not on a line number, so ordinary
edits above a site do not churn the table while a genuinely new site
still lands here for review.
"""

from __future__ import annotations

import ast
import os
from typing import NamedTuple

PACKAGE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "pscx"
)

#: The object hangs off the flattened project -- ``flat.instances``,
#: ``flat.netlists``, ``flat.signal_nets`` -- and outlives every map keyed
#: on its address, all of which are built and read inside one ``flatten``
#: result's lifetime.
FLAT_PROJECT = "FlatProject"

#: The object is held by a list or dict local to the function that takes
#: its address, and the address never leaves that frame.
LOCAL = "a local collection"

#: The mapping keyed on the address also holds the object as its value, so
#: it cannot be freed while the key is meaningful. The strongest of the
#: three: it needs no argument about anyone else's lifetime.
SELF = "the mapping itself"

HOLDERS = frozenset({FLAT_PROJECT, LOCAL, SELF})


class Held(NamedTuple):
    """An object something keeps reachable. ``holder`` says what.

    ``sites`` is how many calls this (module, function, expression) has,
    so a second copy of an existing call is a change to this table too.
    """

    sites: int
    holder: str
    why: str


class Proxy(NamedTuple):
    """An lxml element proxy, whose address lxml is free to recycle.

    EMPTY, and defined anyway. lxml builds a proxy when Python first
    reaches an element and frees it when the last reference goes, which
    makes ``id()`` of one a property of the allocator rather than of the
    document. The class exists so that a call of this kind arrives
    classified rather than assumed, and
    ``test_id_inventory.py`` asserts the set stays empty rather than
    trusting that nobody will write one.
    """

    sites: int
    why: str


_PLACEMENT = (
    "The emission key is ``(instance index, component identity)`` and not "
    "the component alone: instances of one canvas share their Netlist and "
    "therefore their Component objects, so the address pools every "
    "placement of the canvas into one and the instance index separates "
    "them again."
)

#: (module, qualname, expression) -> verdict. Exhaustive over the package.
ID_SITES: dict[tuple[str, str, str], Held | Proxy] = {
    # ---- audit.py -------------------------------------------------------
    ("audit.py", "dc_map", "id(comp)"): Held(
        2, FLAT_PROJECT,
        "The DC source set and the touched-node map, both keyed on "
        "components read off ``flat.instances`` and off the LIR's edge "
        "data -- and the LIR's ``component`` attribute is the same object "
        "the netlist holds, not a copy."),

    # ---- cim.py ---------------------------------------------------------
    ("cim.py", "build_cim.external_ends", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "Reads the placement table ``component_ends`` built from the same "
        "LIR earlier in the same call. " + _PLACEMENT),
    ("cim.py", "build_cim", "id(unmapped_comp)"): Held(
        1, FLAT_PROJECT,
        "Mints a ConnectivityNode for every point a detailed model "
        "connects to, looked up in ``placement_ends``. Both sides read "
        "the same ``flat``, so the address that goes in and the address "
        "that comes out address the same object."),
    ("cim.py", "build_cim.breaker_open_state", "id(entry[2])"): Held(
        1, SELF,
        "De-duplicates the drivers of one signal net down to the distinct "
        "component objects. The dict's VALUE holds the component, so the "
        "key cannot outlive it -- and the count is then read as \"exactly "
        "one driver\", which a recycled address would answer wrongly in "
        "the direction of a confident answer."),
    ("cim.py", "build_cim", "id(comp)"): Held(
        2, FLAT_PROJECT,
        "One membership test against ``inst.module_component_ids``, "
        "skipping zero-impedance kinds that are module placements rather "
        "than equipment, and one ``comp_ends`` lookup for a meter's "
        "drawn nodes. The meter's own membership test belongs to "
        "``measurement_placements``."),
    ("cim.py", "measurement_placements", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "The module-placement arm of the one definition of the meter "
        "set. A placement that is not recognised as one would be stated as "
        "a measurement of a page, and the quantity identity it feeds "
        "would move with it rather than catching the drop."),
    ("cim.py", "absorbed_components", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "The module-placement arm of the one definition of the absorbed "
        "set. This is the failure that would be silent: a placement not "
        "recognised as one becomes an absorbed model of a page, "
        "emitted and validated like any other."),

    # ---- dc.py ----------------------------------------------------------
    ("dc.py", "dc_subsystem", "id(c.component)"): Held(
        1, LOCAL,
        "The converter placements, from ``converter_placements`` and held "
        "by the ``converters`` list for the whole function."),
    ("dc.py", "dc_subsystem", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "The placement key for every LIR edge, matched against the "
        "converter set above and recorded in ``classable``. " + _PLACEMENT),
    ("dc.py", "_kind_of", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "The only site that goes the OTHER WAY: it takes an address "
        "recorded earlier and scans ``flat`` for the component that "
        "answers to it. Resolving an address back to an object is exactly "
        "what a recycled one breaks, and it is safe here because the same "
        "``flat`` held the component throughout."),

    # ---- elaborate.py ---------------------------------------------------
    ("elaborate.py", "pair_radio_ends", "id(tx)"): Held(
        2, LOCAL,
        "Which transmitters got paired, so the unpaired ones can be "
        "counted. The ends are held by the ``tx_ends`` argument across "
        "both the add and the count."),
    ("elaborate.py", "Instance.module_component_ids",
     "id(child.component)"): Held(
        1, FLAT_PROJECT,
        "Where ``module_component_ids`` is derived, on every read, from "
        "the children the Instance holds, each of which holds its placing "
        "component. Nothing stores the addresses, so a copied or "
        "unpickled project answers with its own components' addresses."),
    ("elaborate.py", "flatten", "id(comp)"): Held(
        2, FLAT_PROJECT,
        "The two readers of ``module_component_ids`` inside flattening: a "
        "module port is a join into a child canvas rather than a pin, on "
        "the electrical side and on the signal side."),
    ("elaborate.py", "flatten._electrical_dim", "id(e_inst.netlist)"): Held(
        3, FLAT_PROJECT,
        "A per-Netlist node-dim table, memoised because instances of one "
        "canvas share the Netlist and would otherwise rebuild it per "
        "placement. Keyed on the Netlist rather than the Instance for the "
        "same reason: the sharing IS the saving."),

    # ---- emission.py ----------------------------------------------------
    ("emission.py", "component_ends", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "Builds the placement -> LIR endpoint table every downstream "
        "reader keys into. " + _PLACEMENT),
    ("emission.py", "placement_ends", "id(comp)"): Held(
        2, FLAT_PROJECT,
        "Tests whether a component already got ends from its Branch rows "
        "and falls back to its ports' nodes if not, both under the same "
        "placement key as ``component_ends`` -- one definition, read by "
        "the equipment document and by the add-on profile alike."),
    ("emission.py", "drawn_elements", "id(component)"): Held(
        1, FLAT_PROJECT,
        "Groups the LIR's parallel conductors into one drawn element. The "
        "placement is part of the identity, so a module drawn four times "
        "does not collapse into one element with four conductors on the "
        "same pair."),

    # ---- emt.py ---------------------------------------------------------
    ("emt.py", "build_emt", "id(comp)"): Held(
        1, FLAT_PROJECT,
        "Reads each absorbed placement's connection points out of "
        "``placement_ends``, over components ``absorbed_components`` "
        "yielded from the same ``flat``."),

    # ---- lower.py -------------------------------------------------------
    ("lower.py", "resolve_numeric", "id(inst)"): Held(
        2, LOCAL,
        "The ``(instance, parameter)`` pairs already being resolved, "
        "which is how a parameter that refers to itself through its "
        "parents is caught rather than recursed forever. Each Instance on "
        "the path is held by the frame that passed it and by the "
        "``parent`` chain the walk follows."),
    ("lower.py", "port_dim", "id(comp)"): Held(
        3, FLAT_PROJECT,
        "Memoises ``computations_env`` per component. The cache is a "
        "local of ``flatten``, and every component it keys is held by a "
        "Netlist that outlives the call -- so the cache cannot be handed "
        "an address whose object has gone."),
}


def scan(package: str = PACKAGE) -> dict[tuple[str, str, str], int]:
    """``(module, qualname, expression) -> count`` over the package source.

    Parsed rather than grepped: ``id()`` inside a docstring is prose about
    the mechanism and not a use of it, and ``component_ends`` has one of
    each within ten lines.
    """
    found: dict[tuple[str, str, str], int] = {}
    for name in sorted(os.listdir(package)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(package, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=name)
        _visit(tree, name, (), found)
    return found


def _visit(node: ast.AST, module: str, scope: tuple[str, ...],
           found: dict[tuple[str, str, str], int]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            _visit(child, module, scope + (child.name,), found)
            continue
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                and child.func.id == "id"):
            key = (module, ".".join(scope), ast.unparse(child))
            found[key] = found.get(key, 0) + 1
        _visit(child, module, scope, found)

"""Every dataclass's string annotations must resolve at runtime.

pydantic (via pycgmes) calls ``typing.get_type_hints`` on classes it
touches; an unresolvable
``from __future__ import annotations`` string then raises NameError at a
distance from the real defect. This fails if a module annotates a field
with a type it never imports (e.g. ``Netlist.dsu: "DisjointSet | None"``
while model.py does not import DisjointSet).
"""

import dataclasses
import typing

import pytest

import pscx.elaborate
import pscx.model


def _module_dataclasses(mod):
    return [
        obj
        for obj in vars(mod).values()
        if isinstance(obj, type)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == mod.__name__
    ]


@pytest.mark.parametrize(
    "cls",
    _module_dataclasses(pscx.model) + _module_dataclasses(pscx.elaborate),
    ids=lambda c: f"{c.__module__}.{c.__qualname__}",
)
def test_every_dataclass_annotation_resolves(cls):
    typing.get_type_hints(cls)

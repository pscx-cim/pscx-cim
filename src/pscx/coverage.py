"""Which arms of a library's guard trees anything ever selects.

Concrete expansion cannot answer this. It walks a tree under one
environment and descends only into the arms that hold, so the arms it
never reaches are exactly the ones it never reports -- whether they are a
library feature no placement uses or a guard we read wrongly. The
symbolic interpreter names every arm without an environment; folding in
one placement at a time then says which names were ever chosen.

Three findings, kept apart because they call for different things:

  UNEXERCISED   the alternation is reached and this arm is never taken.
                A library option no observed placement uses.
  UNREACHABLE   the alternation itself is never reached, because it sits
                inside an arm nothing takes. Dead by inheritance; fixing
                the enclosing arm is what would expose it.
  UNSATISFIABLE the arm's guard chain requires one condition to be both
                true and false, so NO environment can select it. This one
                needs no placements at all, and it is what turned up
                master's `#END` blocks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pscx.diagnostics import DIAGNOSTICS
from pscx.guards import ArmRef, contradiction, enumerate_arms

#: A definition's segment, by position and name. The position is part of
#: the identity because a name repeats: Computations and Model-Data are
#: each written across several segments of one definition.
Segment = tuple[int, str]

ArmKey = tuple[str, Segment, str, tuple[int, ...], int]
AltKey = tuple[str, Segment, str, tuple[int, ...]]


@dataclass(frozen=True)
class Site:
    """One arm, and where in the library it is written."""

    definition: str
    segment: Segment
    arm: ArmRef

    @property
    def key(self) -> ArmKey:
        return (self.definition, self.segment, *self.arm.key)

    @property
    def alternation(self) -> AltKey:
        return (self.definition, self.segment, *self.arm.alternation)

    def __str__(self) -> str:
        where = f"{self.definition}/{self.segment[1]}"
        return f"{where} {self.arm.kind} arm {self.arm.index} " \
               f"{self.arm.condition or '#ELSE'}"


class ArmCoverage:
    """Every arm of every indexed definition, and what selected it."""

    def __init__(self) -> None:
        self._sites: dict[ArmKey, Site] = {}
        self._by_definition: dict[str, list[Site]] = {}
        self._selected: set[ArmKey] = set()
        self._reached: set[AltKey] = set()
        self._placed: set[str] = set()

    # -- indexing ----------------------------------------------------------

    def index(self, definition) -> None:
        """Name every arm of one definition's guard trees."""
        if definition.name in self._by_definition:
            return
        sites: list[Site] = []
        for position, (name, tree) in enumerate(definition.guard_trees):
            for arm in enumerate_arms(tree):
                site = Site(definition.name, (position, name), arm)
                sites.append(site)
                if contradiction(arm.guards) is not None:
                    DIAGNOSTICS.emit("script_unreachable_arm", str(site))
        self._by_definition[definition.name] = sites
        for site in sites:
            self._sites[site.key] = site

    def index_all(self, definitions: Iterable) -> None:
        for definition in definitions:
            self.index(definition)

    # -- observation -------------------------------------------------------

    def observe(self, definition, env: dict[str, Any],
                dim_resolver: Callable[[str], int | None] | None = None
                ) -> None:
        """Fold in one placement: which arms it selects, and which
        alternations it reaches at all."""
        from pscx.guards import _holds

        self.index(definition)
        self._placed.add(definition.name)
        for site in self._by_definition[definition.name]:
            if not _holds(site.arm.prefix, env, dim_resolver):
                continue
            self._reached.add(site.alternation)
            if _holds(site.arm.own, env, dim_resolver):
                self._selected.add(site.key)

    # -- what it found -----------------------------------------------------

    @property
    def placed(self) -> set[str]:
        """Definitions at least one placement was observed for. The
        reports below are scoped to these: a definition that is never
        placed has every arm dead for a reason that says nothing."""
        return set(self._placed)

    def sites(self) -> list[Site]:
        return [s for s in self._sites.values() if s.definition in self._placed]

    def selected(self) -> list[Site]:
        return [s for s in self.sites() if s.key in self._selected]

    def unexercised(self) -> list[Site]:
        return [s for s in self.sites()
                if s.key not in self._selected
                and s.alternation in self._reached]

    def unreachable(self) -> list[Site]:
        return [s for s in self.sites()
                if s.key not in self._selected
                and s.alternation not in self._reached]

    def unsatisfiable(self) -> list[tuple[Site, str]]:
        """Arms no environment can select, with the condition the chain
        demands two ways. Needs no observation at all."""
        found = []
        for site in self._sites.values():
            expr = contradiction(site.arm.guards)
            if expr is not None:
                found.append((site, expr))
        return found

    def contradictions(self) -> list[AltKey]:
        """Alternations that are REACHED, carry an ``#ELSE`` and yet select
        no arm. A chain whose arms are exhaustive must take one, so this
        is empty unless arms and their negations disagree."""
        by_alternation: dict[AltKey, list[Site]] = {}
        for site in self.sites():
            by_alternation.setdefault(site.alternation, []).append(site)
        return [alternation for alternation, sites in by_alternation.items()
                if alternation in self._reached
                and not any(s.key in self._selected for s in sites)
                and any(s.arm.condition is None for s in sites)]

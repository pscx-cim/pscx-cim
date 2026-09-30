"""Pinned numbers that carry why they are that number.

A bare pinned integer is a REGRESSION DETECTOR: it says "this was 840 and
still is", which catches a change but explains nothing and cannot say
whether 840 is right. The pins here declare what KIND of claim they make,
so a reader can tell them apart at the point of use, and
``test_pins.py`` checks each kind against the diagnostic catalog rather
than trusting the prose.

  DEFECT    tracks a shipped-library bug we reproduce faithfully. An
            EXPECTATION, not a tolerance: its absence is as suspicious as
            its growth, because it would mean the library changed under
            us or we stopped reading the part that is broken.
  GAP       counts what is missing -- placeholders, undetermined states,
            components standard CIM has no class for. A fall is coverage
            landing and needs re-pinning; a rise is coverage lost.
  BIAS      counts values that are real but wrong in a known direction.
  COVERAGE  a metric: what the mapping reached. Not a diagnostic, and no
            threshold applies to it.
  EVIDENCE  the strong kind. Checked against an INDEPENDENTLY computed
            quantity in both directions, so it says the number is right
            and not merely unchanged. ``check`` names the identity.

Orthogonally, a pin may be PERMANENT: a claim that this number can never
move, because the mechanism that would move it does not exist. A pin that
can never move is a different claim from one that should, and without the
distinction a backlog reads as a to-do list containing items nobody will
ever do. Permanence is not a KIND -- ``test_pins.py`` joins each kind to
the catalog's severity for that code, and a permanent gap is still
catalogued a gap -- so it is a separate field, and the field IS the
reason: ``permanent="..."`` says why, and there is no way to mark a pin
permanent without saying it. A permanent pin that moves anyway is a
finding to write down, not a number to re-pin.

A pin may instead be BLOCKED ON named work: it can move, and the thing
that would move it is known. This is the opposite claim from permanence
and the two must not be confused, because they read the same in prose --
"no witness can fix these" and "this can never be fixed" are one sentence
apart, and the first is a dependency while the second is a full stop.
``blocked_on="..."`` names the work, and ``test_pins.py`` refuses a pin
that claims both. Its value: three separate residuals in this repo turn
out to have ONE trigger, and stating it on each of them is what makes
that trigger legible as unblocking three measurements rather than as
adding coverage.

Each is an ``int``, so a pin is compared like the literal it replaces.
"""

from __future__ import annotations


class Pin(int):
    """An expected number, its kind, and the reason it is expected."""

    kind: str
    why: str
    code: str | None
    check: str | None
    permanent: str | None
    blocked_on: str | None

    def __new__(cls, value: int, kind: str, why: str,
                code: str | None = None, check: str | None = None,
                permanent: str | None = None,
                blocked_on: str | None = None):
        pin = super().__new__(cls, value)
        pin.kind, pin.why, pin.code, pin.check = kind, why, code, check
        pin.permanent, pin.blocked_on = permanent, blocked_on
        return pin

    def __repr__(self) -> str:
        mark = " PERMANENT" if self.permanent else (
            f" BLOCKED ON {self.blocked_on}" if self.blocked_on else "")
        return (f"{self.kind}{mark} pin {int(self)} "
                f"({self.code or self.check}): {self.why}")


def defect(value: int, code: str, why: str,
           permanent: str | None = None,
           blocked_on: str | None = None) -> Pin:
    """A shipped-library bug we reproduce. ``code`` names its diagnostic."""
    return Pin(value, "DEFECT", why, code=code, permanent=permanent,
               blocked_on=blocked_on)


def gap(value: int, code: str, why: str,
        permanent: str | None = None,
        blocked_on: str | None = None) -> Pin:
    """Something missing. ``code`` names its diagnostic."""
    return Pin(value, "GAP", why, code=code, permanent=permanent,
               blocked_on=blocked_on)


def bias(value: int, code: str, why: str,
         permanent: str | None = None,
         blocked_on: str | None = None) -> Pin:
    """A real value wrong in a known direction."""
    return Pin(value, "BIAS", why, code=code, permanent=permanent,
               blocked_on=blocked_on)


def coverage(value: int, why: str, permanent: str | None = None,
             blocked_on: str | None = None) -> Pin:
    """What the mapping reached. A metric, never a diagnostic."""
    return Pin(value, "COVERAGE", why, permanent=permanent,
               blocked_on=blocked_on)


def evidence(value: int, check: str, why: str,
             permanent: str | None = None,
             blocked_on: str | None = None) -> Pin:
    """A number confirmed against an independently computed one.

    ``check`` states the identity the test asserts in BOTH directions --
    that is what makes this evidence rather than a regression detector.
    """
    return Pin(value, "EVIDENCE", why, check=check,
               permanent=permanent, blocked_on=blocked_on)


def all_pins(module) -> dict[str, Pin]:
    """Every ``Pin`` a test module declares, by name."""
    return {name: value for name, value in vars(module).items()
            if isinstance(value, Pin)}


def pin_modules() -> list[str]:
    """Every test module in this directory, by import name.

    DISCOVERED, never listed. A hand-maintained list is the same defect
    the permanence field exists to prevent: it stops being complete
    silently, and the pins it drops are exactly the ones nothing then
    checks against the catalog.
    """
    import pathlib

    here = pathlib.Path(__file__).parent
    return sorted(path.stem for path in here.glob("test_*.py"))

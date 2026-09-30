"""Unit tables and declared-unit conversion."""

from __future__ import annotations

import re

from pscx.diagnostics import DIAGNOSTICS


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------
#
# A parameter *declaration* carries a target unit (``unit="m"``); the value
# written on an instance may use another (``"100.0 [km]"``). PSCAD converts;
# so must we, or derived values are silently off by scale factors
# (pi_section2's per-METER line constants times a length written in km).
# The table below covers the known (written, declared) pairs and fails
# loud on anything new. Values are therefore always expressed in the
# DECLARED unit, which is what the component's script arithmetic assumes.

_UNIT_ALIASES = {"sec": "s", "secs": "s", "ohms": "ohm", "mhos": "mho",
                 "p.u.": "pu", "hertz": "Hz"}

_SI_PREFIXES = {"T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "c": 1e-2,
                "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12}

_SPECIAL_UNIT_FACTORS = {  # canonical (written, declared) -> multiplier
    ("Hz", "rad/s"): 2.0 * 3.141592653589793,
    ("deg", "rad"): 3.141592653589793 / 180.0,
    ("rpm", "rad/s"): 2.0 * 3.141592653589793 / 60.0,
}


def _canon_unit(unit: str) -> str:
    parts = re.split(r"([*/])", unit.strip())
    out = []
    for part in parts:
        p = part.strip()
        out.append(_UNIT_ALIASES.get(p.lower(), p))
    return "".join(out)


def _simple_unit_factor(written: str, declared: str) -> float | None:
    """Factor for one non-compound unit pair, trying SI-prefix splits."""
    if written.lower() == declared.lower():
        return 1.0
    def splits(u):
        # alias the base both before and after the prefix split, so that
        # ``usec`` -> ('u', 'sec'->'s') matches ``us`` -> ('u', 's')
        yield 1.0, _UNIT_ALIASES.get(u.lower(), u)
        for prefix, factor in _SI_PREFIXES.items():
            if u.startswith(prefix) and len(u) > len(prefix):
                base = u[len(prefix):]
                yield factor, _UNIT_ALIASES.get(base.lower(), base)
    for fw, bw in splits(written):
        for fd, bd in splits(declared):
            if bw.lower() == bd.lower():
                return fw / fd
    return None


def unit_factor(written: str, declared: str) -> float | None:
    """Multiplier taking a value in ``written`` units to ``declared`` units,
    or None (loud at the caller) when the pair is not understood."""
    w, d = _canon_unit(written), _canon_unit(declared)
    if w.lower() == d.lower():
        return 1.0
    for (sw, sd), factor in _SPECIAL_UNIT_FACTORS.items():
        if (w.lower(), d.lower()) == (sw.lower(), sd.lower()):
            return factor
        if (d.lower(), w.lower()) == (sw.lower(), sd.lower()):
            return 1.0 / factor
    wparts, dparts = re.split(r"([*/])", w), re.split(r"([*/])", d)
    if len(wparts) != len(dparts):
        return None
    factor, sign = 1.0, 1
    for wp, dp in zip(wparts, dparts):
        wp, dp = wp.strip(), dp.strip()
        if wp in "*/" or dp in "*/":
            if wp != dp:
                return None
            sign = -1 if wp == "/" else 1
            continue
        f = _simple_unit_factor(wp, dp)
        if f is None:
            return None
        factor *= f ** sign
    return factor


# --------------------------------------------------------------------------
# Declared unit -> SI (CIM emission)
# --------------------------------------------------------------------------
#
# Extracted values are in the DECLARED unit of the script that produced them
# (master's capacitor C is [uF], inductance [H]); CIM attributes are SI.
# The table names the SI target per declared unit; the factor is computed by
# the same unit_factor machinery that handles written->declared, so the
# special physical pairs (Hz->rad/s = 2*pi etc.) stay in one place. Grown
# case-by-case; an unknown declared unit RAISES, because a silently
# unconverted quantity would flow into emitted CIM as corrupt data.

SI_UNITS: dict[str, str] = {
    "": "",
    "pu": "pu",
    "ohm": "ohm",
    "mho": "mho",
    "H": "H",
    "F": "F",
    "uF": "F",
    "m": "m",
    "km": "m",
    "V": "V",
    "kV": "V",
    "A": "A",
    "kA": "A",
    "s": "s",
    "us": "s",
    "ms": "s",
    "W": "W",
    "MW": "W",
    "VA": "VA",
    "MVA": "VA",
    "Hz": "rad/s",
    "rad": "rad",
    "deg": "rad",
    "rad/s": "rad/s",
    "rpm": "rad/s",
}


class UnknownUnitError(ValueError):
    """A declared unit with no SI target; the value must not be emitted."""


def si_factor(declared: str) -> tuple[float, str]:
    """``(multiplier, SI unit)`` taking a declared-unit value to SI."""
    d = _canon_unit(declared or "")
    for key, si in SI_UNITS.items():
        if key.lower() == d.lower():
            factor = unit_factor(d, si) if d else 1.0
            if factor is None:  # table names a target the machinery lacks
                raise UnknownUnitError(f"no factor [{declared}] -> [{si}]")
            return factor, si
    raise UnknownUnitError(f"no SI target for declared unit [{declared}]")


_VALUE_WITH_UNIT = re.compile(
    r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?:\[([^\]]*)\])?\s*$"
)


def value_in_declared_unit(raw: str, declared: str | None) -> float | None:
    """Numeric value of ``"100.0 [km]"`` expressed in the declared unit."""
    match = _VALUE_WITH_UNIT.match(raw or "")
    if match is None:
        return None
    value = float(match.group(1))
    written = (match.group(2) or "").strip()
    declared = (declared or "").strip()
    if not written or not declared or written == declared:
        return value
    factor = unit_factor(written, declared)
    if factor is None:
        DIAGNOSTICS.emit("unit_unconverted", f"[{written}] -> [{declared}]")
        return value
    return value * factor

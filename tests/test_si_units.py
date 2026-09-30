"""Declared-unit -> SI conversion for CIM emission.

Extracted branch values are in the script's DECLARED units (master's
capacitor C is [uF], inductance [H]); CIM attributes are SI. The factors
here are hand-derived, never read back from the code under test.
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pscx.units import (
    SI_UNITS,
    UnknownUnitError,
    si_factor,
    unit_factor,
    value_in_declared_unit,
)


@pytest.mark.parametrize(
    ("declared", "expected_factor", "expected_si"),
    [
        # this block fails if a declared unit stops converting to the
        # hand-derived SI factor (silently mis-scaled CIM values)
        ("uF", 1e-6, "F"),
        ("km", 1e3, "m"),
        ("kV", 1e3, "V"),
        ("Hz", 2.0 * math.pi, "rad/s"),
        ("deg", math.pi / 180.0, "rad"),
        ("rpm", 2.0 * math.pi / 60.0, "rad/s"),
        ("ohm", 1.0, "ohm"),
        ("H", 1.0, "H"),
        ("MVA", 1e6, "VA"),
        ("us", 1e-6, "s"),
    ],
)
def test_si_factor_hand_derived(declared, expected_factor, expected_si):
    factor, si = si_factor(declared)
    assert si == expected_si
    assert factor == pytest.approx(expected_factor, rel=1e-12)


def test_unknown_declared_unit_refuses():
    # Fails if an unknown unit passes through silently: once emitted CIM
    # carries physical values, an unconverted quantity is corrupt data.
    with pytest.raises(UnknownUnitError):
        si_factor("furlong")


def test_dimensionless_and_pu_pass_with_factor_one():
    # Fails if unitless parameters start being scaled or rejected.
    assert si_factor("")[0] == 1.0
    assert si_factor("pu")[0] == 1.0


@given(
    declared=st.sampled_from(sorted(SI_UNITS)),
    value=st.floats(
        min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False
    ),
)
def test_si_round_trip_is_identity(declared, value):
    # Two independent paths must agree: the SI factor from the table and
    # the inverse factor computed by the validated unit_factor machinery.
    # Fails if the special-pair table (Hz/deg/rpm) or a prefix split is
    # asymmetric.
    factor, si = si_factor(declared)
    inverse = unit_factor(si, declared) if declared else 1.0
    assert inverse is not None
    back = (value * factor) * inverse
    assert back == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize(
    ("raw", "declared", "expected"),
    [
        # the whole point of the rule: the WRITTEN unit differs from the
        # DECLARED one and the magnitude moves. Deleting the multiply in
        # value_in_declared_unit passes every other test in this file --
        # each of those exercises si_factor/unit_factor, a different
        # function serving CIM emission -- and fails here.
        ("100.0 [km]", "m", 100_000.0),
        ("0.5 [m]", "km", 5e-4),
        ("3.342 [uF]", "F", 3.342e-6),
        ("60.0 [Hz]", "rad/s", 2.0 * math.pi * 60.0),
        ("-45.0 [deg]", "rad", -math.pi / 4.0),
        ("2.0 [MVA]", "VA", 2e6),
        # ...and the three ways the conversion is correctly skipped: no
        # written unit, no declared unit, and the two agreeing.
        ("12.5", "km", 12.5),
        ("12.5 [km]", None, 12.5),
        ("12.5 [km]", "km", 12.5),
    ],
)
def test_a_written_unit_converts_into_the_declared_one(raw, declared,
                                                       expected):
    assert value_in_declared_unit(raw, declared) == pytest.approx(
        expected, rel=1e-12)


def test_an_unconvertible_pair_is_counted_and_the_value_stands():
    # The loud half, on a pair the machinery has no factor for: the
    # number is returned unscaled -- there is nothing better to return --
    # and `unit_unconverted` says so rather than the value passing as
    # converted. It is Severity.ERROR, so the threshold gate holds
    # it at zero and this is the only place the branch is exercised.
    from pscx.diagnostics import DIAGNOSTICS

    before = DIAGNOSTICS.by_code()["unit_unconverted"]
    assert value_in_declared_unit("7.0 [ohm]", "F") == 7.0
    assert DIAGNOSTICS.by_code()["unit_unconverted"] == before + 1

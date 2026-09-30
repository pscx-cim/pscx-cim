"""Line constants: the physics and the sequence reduction."""

import cmath
import math

import pytest
from conftest import master_available

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)


def test_bundle_gmr_matches_the_closed_form_two_conductor_value():
    # A 2-conductor symmetric bundle sits on a circle of radius s/2, so its
    # GMR reduces to sqrt(GMR_c * s) exactly -- the textbook two-bundle
    # result. Fails if the general n-conductor geometric mean is wrong in
    # its self terms (D_ii = GMR_c) or its normalisation (1/n^2).
    from pscx.lineconst import bundle_gmr

    gmr_c, spacing = 0.0203454 * math.exp(-0.25), 0.4572
    assert bundle_gmr(gmr_c, 2, spacing) == pytest.approx(
        math.sqrt(gmr_c * spacing), rel=1e-12)
    # a 3-conductor bundle on a circle of radius A = s/(2 sin(pi/3)) has
    # all three pairwise distances equal to s, so GMR = (GMR_c*s^2)^(1/3)
    assert bundle_gmr(gmr_c, 3, spacing) == pytest.approx(
        (gmr_c * spacing ** 2) ** (1 / 3), rel=1e-12)
    assert bundle_gmr(gmr_c, 1, spacing) == gmr_c


def test_deri_self_and_mutual_impedance_hand_derived():
    # Hand-derived from Z_ii = R + j*w*mu0/(2pi)*ln(2(h+p)/GMR) and
    # Z_ij = j*w*mu0/(2pi)*ln(D'/d) with the Deri-Semlyen complex depth
    # p = sqrt(rho/(j*w*mu0)). Fails on a wrong factor (mu0/2pi vs mu0/8),
    # a wrong image distance, or a real-valued depth.
    from pscx.lineconst import MU0, complex_depth, mutual_impedance, self_impedance

    rho, w = 100.0, 2 * math.pi * 60.0
    p = complex_depth(rho, w, 1.0)
    assert p == pytest.approx(cmath.sqrt(rho / (1j * w * MU0)), rel=1e-14)

    h, gmr, r_dc = 23.333333333333332, 0.02, 3.206e-5
    expected = r_dc + 1j * w * MU0 / (2 * math.pi) * cmath.log(
        2 * (h + p) / gmr)
    assert self_impedance(h, gmr, r_dc, p, w) == pytest.approx(
        expected, rel=1e-14)

    hi, hj, dx = 23.3333, 23.3333, 10.0
    d = math.hypot(hi - hj, dx)
    dimg = cmath.sqrt((hi + hj + 2 * p) ** 2 + dx ** 2)
    assert mutual_impedance(hi, hj, dx, p, w) == pytest.approx(
        1j * w * MU0 / (2 * math.pi) * cmath.log(dimg / d), rel=1e-14)


def test_sequence_projection_reduces_to_the_textbook_identities():
    # For a perfectly balanced 3x3 matrix the projection must give exactly
    # Z1 = Zs - Zm and Z0 = Zs + 2Zm. Fails if the rotation direction, the
    # conjugation or the 1/n normalisation is wrong. A 2-conductor system
    # is the differential mode Zs - Zm; a 1-conductor system is Zs itself.
    from pscx.lineconst import sequence_component

    zs, zm = 0.1 + 1.0j, 0.05 + 0.4j
    z3 = [[zs if i == j else zm for j in range(3)] for i in range(3)]
    assert sequence_component(z3, 1) == pytest.approx(zs - zm, rel=1e-12)
    assert sequence_component(z3, 0) == pytest.approx(zs + 2 * zm, rel=1e-12)
    z2 = [[zs, zm], [zm, zs]]
    assert sequence_component(z2, 1) == pytest.approx(zs - zm, rel=1e-12)
    assert sequence_component(z2, 0) == pytest.approx(zs + zm, rel=1e-12)
    assert sequence_component([[zs]], 1) == pytest.approx(zs, rel=1e-12)


def test_single_conductor_line_has_closed_form_constants():
    # End-to-end through the whole pipeline on the one geometry whose
    # answer is closed-form: one conductor over earth, no ground wires, so
    # the matrices are 1x1 and Z1 is exactly the self impedance while
    # b1 = w / P11 with P11 = ln(2h/r)/(2 pi eps0). Hand-derived at
    # h=20 m, r=0.02 m, r_dc=1e-4 ohm/m, rho=100 ohm*m, f=60 Hz:
    #   r1 = 1.5696675298174984e-4 ohm/m   (R_dc plus the earth-return
    #        resistance the complex depth contributes)
    #   x1 = 8.305825189820397e-4 ohm/m
    #   b1 = 2.7592722775883842e-9 S/m
    # Fails on a wrong GMR (r*exp(-mu_r/4)), a real depth, a missing 2h in
    # the potential coefficient, or a sequence projection that is not the
    # identity at n=1.
    from pscx.lineconst import Conductor, RightOfWay, sequence_constants

    row = RightOfWay(
        conductors=[Conductor(x=0.0, y=20.0, sag=0.0, radius=0.02,
                              r_dc=1e-4, mu_r=1.0, phase=1)],
        ground_wires=[], circuits=[(0,)],
        earth_resistivity=100.0, earth_mu_r=1.0,
    )
    constants = sequence_constants(row, 60.0)[1]
    assert constants.r1 == pytest.approx(1.5696675298174984e-4, rel=1e-12)
    assert constants.x1 == pytest.approx(8.305825189820397e-4, rel=1e-12)
    assert constants.b1 == pytest.approx(2.7592722775883842e-9, rel=1e-12)
    # a single conductor has no other sequence: zero == positive
    assert constants.r0 == pytest.approx(constants.r1, rel=1e-12)
    assert constants.x0 == pytest.approx(constants.x1, rel=1e-12)


def test_ground_wire_elimination_lowers_reactance_and_raises_resistance():
    # Differential, both directions: a Kron-eliminated ground wire carries
    # induced current that opposes the phase flux, so the SAME tower with
    # the ground wire removed must show a strictly higher x0 and a strictly
    # lower r0. Fails if elimination is skipped, or applied with the wrong
    # sign. Positive sequence is nearly unaffected (the balanced set
    # induces almost nothing) -- asserted as a bound, not an equality.
    from pscx.lineconst import Conductor, RightOfWay, sequence_constants

    def tower(with_gw):
        phases = [Conductor(x=x, y=30.0, sag=10.0, radius=0.02, r_dc=6e-5,
                            mu_r=1.0, phase=i + 1)
                  for i, x in enumerate((-10.0, 0.0, 10.0))]
        wires = [Conductor(x=0.0, y=38.0, sag=10.0, radius=0.005,
                           r_dc=2.9e-3, mu_r=1.0)] if with_gw else []
        return RightOfWay(phases, wires, [(0, 1, 2)], 100.0, 1.0)

    with_gw = sequence_constants(tower(True), 60.0)[1]
    without = sequence_constants(tower(False), 60.0)[1]
    assert with_gw.x0 < without.x0
    assert with_gw.r0 > without.r0
    assert abs(with_gw.x1 - without.x1) < 0.02 * without.x1


def test_kron_reduction_matches_a_hand_solved_two_by_two():
    # Eliminating conductor 1 from [[a, b], [c, d]] must leave [a - b*d^-1*c]
    # exactly. Fails if the partition, the inverse or the sign is wrong.
    from pscx.lineconst import kron_reduce

    m = [[2 + 1j, 1 + 0j], [1 + 0j, 4 - 2j]]
    reduced = kron_reduce(m, 1)
    assert len(reduced) == 1
    assert reduced[0][0] == pytest.approx(
        (2 + 1j) - (1 + 0j) * (1 / (4 - 2j)) * (1 + 0j), rel=1e-14)
    # eliminating nothing is the identity
    assert kron_reduce(m, 2) == m


def test_published_carson_benchmark_line_reproduces_its_stated_sequence_z():
    # The one EXTERNAL check on the physics: every other line-constants
    # test compares the code against its own closed forms or against broad
    # published ranges, which catches scale and unit errors but not a
    # 10-30% modeling error.
    #
    # Reference: Kersting, "Distribution System Modeling and Analysis",
    # Examples 4.1-4.2 -- a four-wire 60 Hz line over 100 ohm*m earth.
    # Phases 336,400 26/7 ACSR (GMR 0.0244 ft, 0.306 ohm/mile) at
    # (0, 29), (2.5, 29), (7, 29) ft; neutral 4/0 6/1 ACSR (GMR 0.00814 ft,
    # 0.5920 ohm/mile) at (4, 25) ft, Kron-eliminated. The text solves it
    # with Carson's modified equations and publishes
    #   Z1 = 0.3061 + j0.6270 ohm/mile,  Z0 = 0.7735 + j1.9373 ohm/mile.
    # This module solves it a DIFFERENT way (Deri-Semlyen complex depth),
    # so agreement is evidence about the physics, not about the code
    # reproducing itself.
    #
    # Tolerances are direction-aware, not a single fudge: the positive
    # sequence is a balanced set whose return is the other two phases, so
    # the earth-return term nearly cancels and the two formulations must
    # agree to well under 0.1%. The zero sequence returns THROUGH the
    # earth, which is exactly where Deri's analytical approximation and
    # Carson's series differ, so 1% is the honest bound there. Both are
    # far tighter than any modeling error worth catching.
    from pscx.lineconst import Conductor, RightOfWay, sequence_constants

    ft, mile = 0.3048, 1609.344

    def conductor(x_ft, y_ft, gmr_ft, r_ohm_per_mile, phase=None):
        # the module derives GMR as radius*exp(-mu_r/4); entering the
        # published GMR means undoing that, which is exact at mu_r=1. Only
        # the series impedance is compared -- the potential coefficients
        # use the physical radius, which the reference does not state.
        return Conductor(x=x_ft * ft, y=y_ft * ft, sag=0.0,
                         radius=gmr_ft * ft * math.exp(0.25),
                         r_dc=r_ohm_per_mile / mile, mu_r=1.0, phase=phase)

    row = RightOfWay(
        conductors=[conductor(0.0, 29.0, 0.0244, 0.306, 1),
                    conductor(2.5, 29.0, 0.0244, 0.306, 2),
                    conductor(7.0, 29.0, 0.0244, 0.306, 3)],
        ground_wires=[conductor(4.0, 25.0, 0.00814, 0.5920)],
        circuits=[(0, 1, 2)], earth_resistivity=100.0, earth_mu_r=1.0,
    )
    constants = sequence_constants(row, 60.0)[1]
    z1 = complex(constants.r1, constants.x1) * mile
    z0 = complex(constants.r0, constants.x0) * mile
    assert z1 == pytest.approx(complex(0.3061, 0.6270), rel=1e-3)
    assert z0 == pytest.approx(complex(0.7735, 1.9373), rel=1e-2)


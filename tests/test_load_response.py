"""The rule that states a fixed load's voltage and frequency dependence
in CGMES wherever CGMES can state it exactly.

PSCAD's Fixed Load help states the model as ``P = Scale*PO*(1 + KPF*dF)
*{KA*(V/V0)^NPA + ...}``. CGMES's exponent model is ``p*(V/Vn)^pVoltage
Exponent*(f/fn)^pFrequencyExponent`` with ``p`` at the node's nominal
voltage. The rule is held here against that identity.
"""

import math

import pytest

from pscx.rules import LoadResponse, fixed_load_response

#: One single-part fixed load, keyed the way the emitter keys a form.
SINGLE_PART = {"parts": 1.0, "pqdef": 0.0, "np": 2.0, "nq": 1.0,
               "kpf": 1.0, "kqf": 0.0, "vbo": 38.105, "vpu": 1.0}


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kpf", [0.0, 1.0])
def test_the_cgmes_statement_draws_what_the_pscad_load_draws(kpf):
    # The rescaled nominal power, raised to the CGMES exponents, must give
    # PSCAD's own power at every voltage and frequency inside the model's
    # band. Fails if the rescale runs the wrong way, if an exponent is
    # swapped, or if a frequency index outside {0, 1} were admitted.
    stated = dict(SINGLE_PART, kpf=kpf)
    response = fixed_load_response(stated)
    assert isinstance(response, LoadResponse)
    assert response.rated_kv == pytest.approx(math.sqrt(3.0) * 38.105)
    rated_mw, nominal_kv = 15.0, 66.0
    nominal_mw = rated_mw * (nominal_kv / response.rated_kv) \
        ** response.p_voltage
    for per_unit_v in (0.85, 1.0, 1.15):
        for d_f in (-0.05, 0.0, 0.05):
            volts = per_unit_v * nominal_kv
            pscad = rated_mw * (1.0 + stated["kpf"] * d_f) \
                * (volts / response.rated_kv) ** stated["np"]
            cgmes = nominal_mw * (volts / nominal_kv) ** response.p_voltage \
                * (1.0 + d_f) ** response.p_frequency
            assert cgmes == pytest.approx(pscad, rel=1e-12)


def test_power_stated_at_the_initial_voltage_is_rated_there():
    response = fixed_load_response(dict(SINGLE_PART, pqdef=1.0, vpu=1.05))
    assert isinstance(response, LoadResponse)
    assert response.rated_kv == pytest.approx(
        math.sqrt(3.0) * 38.105 * 1.05)


@pytest.mark.parametrize("change, reason", [
    ({"parts": 2.0}, "composite"),
    ({"kqf": -1.0}, "KQF = -1"),
    ({"kpf": 0.5}, "KPF = 0.5"),
    ({"vbo": None}, "not numeric: vbo"),
    ({"vbo": 0.0}, "not positive"),
])
def test_what_cgmes_cannot_state_exactly_is_refused(change, reason):
    response = fixed_load_response(dict(SINGLE_PART, **change))
    assert isinstance(response, str)
    assert reason in response

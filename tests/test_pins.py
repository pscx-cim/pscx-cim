"""Every pinned number declares what kind of claim it makes.

No pinned number is left without a reason attached. These checks are
what stop that from decaying into prose: a pin that names a diagnostic
code must agree with the catalog's severity for it, so a GAP pin cannot
quietly come to track a shipped defect (or the reverse) while its
comment still says otherwise.
"""

import pins
import pytest


def _declared():
    import importlib

    found = {}
    for name in pins.pin_modules():
        module = importlib.import_module(name)
        for pin_name, pin in pins.all_pins(module).items():
            found[f"{name}.{pin_name}"] = pin
    return found


def test_the_suite_declares_pins():
    # Guards every other check here: they all iterate the declared pins,
    # so an import that silently yielded none would make the file vacuous.
    declared = _declared()
    assert len(declared) >= 10, sorted(declared)


def test_every_module_that_declares_a_pin_is_reached():
    # The control on the discovery itself. A module missing from the
    # discovery fails silently, because the checks below iterate whatever
    # they are given, and its pins are then joined to nothing in the
    # catalog. So the modules are globbed, and this asserts the glob
    # reaches the ones known to declare pins.
    modules = set(pins.pin_modules())
    assert {"test_coverage", "test_consumer_semantics", "test_id_inventory",
            "test_shape_coverage"} <= modules
    reached = {name.split(".")[0] for name in _declared()}
    assert "test_coverage" in reached and "test_id_inventory" in reached


def test_each_pin_states_a_reason():
    for name, pin in _declared().items():
        assert pin.kind in {"DEFECT", "GAP", "BIAS", "COVERAGE", "EVIDENCE"}, name
        assert len(pin.why.split()) >= 5, f"{name}: {pin.why!r}"


def test_a_pins_kind_agrees_with_the_catalog_severity():
    # The join that keeps the two classifications one classification.
    # Fails if a pin claims to track a shipped defect while the catalog
    # calls that code a gap -- which would make "its absence is as
    # suspicious as its growth" false of a number a reader trusts.
    from pscx.diagnostics import CATALOG

    for name, pin in _declared().items():
        if pin.code is None:
            continue
        assert pin.code in CATALOG, f"{name} names an uncatalogued code"
        assert CATALOG[pin.code].severity.name == pin.kind, (
            f"{name} is a {pin.kind} pin but {pin.code} is catalogued "
            f"{CATALOG[pin.code].severity.name}")


def test_a_coverage_or_evidence_pin_names_no_diagnostic_code():
    # A metric has no severity, so a COVERAGE pin that named a diagnostic
    # code would be claiming one. An EVIDENCE pin's whole point is the
    # identity it checks, so it states that instead.
    for name, pin in _declared().items():
        if pin.kind == "COVERAGE":
            assert pin.code is None and pin.check is None, name
        if pin.kind == "EVIDENCE":
            assert pin.code is None, name
            assert pin.check and "==" in pin.check, f"{name}: {pin.check!r}"


def test_every_defect_pin_is_one_of_the_known_shipped_bugs():
    # Exhaustive on purpose. A new DEFECT pin means another shipped bug
    # was found, which is a finding to write down rather than a number to
    # add quietly. ``cim_param_case_collision`` is a bug in a shipped case,
    # and the other known codes are bugs in a shipped library.
    known = {"branch_dangling_value", "branch_bare_node",
             "lineconst_rowdefn_missing", "script_unreachable_arm",
             "cim_param_case_collision"}
    codes = {p.code for p in _declared().values() if p.kind == "DEFECT"}
    assert codes <= known, sorted(codes - known)


def test_a_permanent_pin_states_why_it_can_never_move():
    # The field IS the reason, so this cannot fail by construction --
    # which is the point of making it a string rather than a flag. What
    # it does check is that the reason is a reason: a word or two ("by
    # design", "n/a") would put the backlog back where it started.
    permanent = {name: pin for name, pin in _declared().items()
                 if pin.permanent}
    assert permanent, "the distinction is theoretical without one"
    for name, pin in permanent.items():
        assert len(pin.permanent.split()) >= 8, f"{name}: {pin.permanent!r}"


@pytest.mark.parametrize("kind", ["DEFECT", "GAP", "EVIDENCE"])
def test_each_required_pin_kind_has_a_pin(kind):
    # The negative control for the taxonomy: three kinds that must each
    # have at least one pin, or the distinction they draw is theoretical.
    assert any(p.kind == kind for p in _declared().values()), kind

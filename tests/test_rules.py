"""The mapping rules as a file: what it may depend on, and that the
declarative half really is declarative.
"""

import ast
import os

from conftest import REPO_ROOT

RULES = os.path.join(REPO_ROOT, "src", "pscx", "rules.py")

#: Everything `pscx.rules` is allowed to import. Standard library plus
#: `pscx.diagnostics`, and the reason is a cost the whole front end pays:
#: `pscx.diagnostics` is imported by most of the package, while `pycgmes`
#: and `rdflib` are the CIM stack that `pscx.cli` deliberately defers so
#: an extraction-only run never loads it.
PERMITTED_PSCX = {"pscx.diagnostics"}
FORBIDDEN = {"pycgmes", "rdflib"}


def _imported_modules(path):
    """Every module name the file imports, at any nesting depth."""
    with open(path) as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_rules_import_nothing_that_would_pull_in_the_cim_stack():
    # Fails if a rule ever needs pycgmes or rdflib -- which is what would
    # happen the first time someone writes the CIM class as a pycgmes
    # type rather than as its name. The tables state class NAMES for
    # exactly that reason, and nothing here can enforce that by reading
    # the strings, so the import list is what holds the line.
    #
    # Walks the whole tree, not just module level: a function-local
    # import is how the cim.py <-> emt.py cycle is broken today, so it is
    # the form a violation would most plausibly take.
    imported = _imported_modules(RULES)
    assert not (imported & FORBIDDEN)
    pscx_imports = {name for name in imported if name.split(".")[0] == "pscx"}
    assert pscx_imports <= PERMITTED_PSCX, sorted(pscx_imports - PERMITTED_PSCX)


def test_importing_the_rules_does_not_import_the_cim_stack():
    # The negative control for the reading above, and the claim that
    # actually matters: the AST says what this FILE names, and this says
    # what the interpreter ends up with -- a permitted import whose own
    # dependencies pull rdflib in would pass the first test and fail
    # here. Run in a fresh interpreter because the test session has the
    # CIM stack loaded already.
    import subprocess
    import sys

    probe = (
        "import sys; import pscx.rules; "
        "print(sorted(m for m in sys.modules if m in ('pycgmes', 'rdflib')))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, check=True,
                         env={**os.environ, "PYTHONPATH":
                              os.path.join(REPO_ROOT, "src")})
    assert out.stdout.strip() == "[]", out.stdout


def test_the_rules_are_pure_and_hold_no_emitter_state():
    # What makes this file the DECLARATIVE half rather than a grab bag.
    # Every module-level name is either a literal table, a record TYPE,
    # or a function whose arguments are floats and strings; a function
    # that read a placement would need pscx.lower and would drag the
    # placement reader into a near-leaf module. `source_is_dc` and
    # `machine_rating` stayed in pscx.cim for exactly that reason -- they
    # APPLY rules, they are not rules -- and this fails if one comes back.
    import inspect

    from pscx import rules

    for name, value in vars(rules).items():
        if name.startswith("_") or not inspect.isfunction(value):
            continue
        if value.__module__ != "pscx.rules":
            continue
        parameters = inspect.signature(value).parameters
        assert "inst" not in parameters and "comp" not in parameters, name


def test_the_forward_choice_and_its_inverse_are_stated_together():
    # A consumer-friendly forward choice owes a stated inverse, written
    # where the choice is made. `short` becomes a
    # cim:Disconnector to suit two consumers, and TWO_TERMINAL is what
    # reconstruct() reads to invert it -- so the two must be readable on
    # one page. Fails if either half moves out from under the other,
    # which is how the obligation would quietly become undischarged.
    from pscx import rules

    assert "Disconnector" in rules.TWO_TERMINAL
    assert rules.MASTER_KIND_TO_CIM.get("short") is None
    assert "short" in rules.ZERO_IMPEDANCE_KINDS
    # ...and the one-terminal shunt's inverse, whose second end is stated
    # nowhere and comes back from the cim:Ground
    assert rules.IMPLICIT_GROUND == ("LinearShuntCompensator",)
    assert "LinearShuntCompensator" not in rules.TWO_TERMINAL

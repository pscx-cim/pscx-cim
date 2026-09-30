"""Generate the guide's mapping tables from the code's own rules.

A hand-written mapping table rots the day a rule moves. These pages are
projections of :mod:`pscx.rules` and the reconstruction inventory's
frozen document partition, written into ``guide/generated/`` and pinned
fresh by ``tests/test_guide.py`` -- the EMT.md pattern: committed output,
staleness is a test failure, and the fix is always to rerun this tool.

    python tools/gen_guide_tables.py            # rewrite the pages
    python tools/gen_guide_tables.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "guide" / "generated"

sys.path.insert(0, str(REPO_ROOT / "tests"))


def _table(out, headers, rows) -> None:
    out.write("| " + " | ".join(headers) + " |\n")
    out.write("|" + "|".join("---" for _ in headers) + "|\n")
    for row in rows:
        out.write("| " + " | ".join(row) + " |\n")
    out.write("\n")


def kind_to_cim() -> str:
    from pscx.rules import (
        DEVICE_KIND_TO_CIM,
        GROUND_REFERENCE_CLASS,
        GROUND_ROLES,
        IMPLICIT_GROUND_ROLES,
        MASTER_KIND_TO_CIM,
        OUTPUT_QUANTITY,
        STRUCTURAL_KINDS,
        UNTYPED_OUTPUTS,
        ZERO_IMPEDANCE_KINDS,
    )

    out = io.StringIO()
    out.write(
        "# Kind to standard class\n\n"
        "Generated from `pscx.rules` by `tools/gen_guide_tables.py`; do "
        "not edit by hand.\n\n"
        "Every drawn placement travels in the EMT document as a "
        "`cim:DetailedModelDynamics` naming its library type; the kinds "
        "below are ADDITIONALLY projected into the standard five as the "
        "class named here.\n\n")
    out.write("## Placed components\n\n")
    _table(out, ("master kind", "CIM class"),
           [(kind, f"`cim:{MASTER_KIND_TO_CIM[kind]}`")
            for kind in sorted(MASTER_KIND_TO_CIM)])
    out.write(
        "The classes above are the SERIES reading: an R, L or C drawn "
        "between two live nodes is a `cim:EquivalentBranch`. The same "
        "kinds drawn to the ground symbol play a role the drawing "
        "itself selects -- see *Drawn to ground* below.\n\n")
    out.write("## Hosting wires\n\n")
    _table(out, ("wire classid", "CIM class"),
           [(kind, f"`cim:{DEVICE_KIND_TO_CIM[kind]}`")
            for kind in sorted(DEVICE_KIND_TO_CIM)])
    out.write(
        "## Drawn to ground\n\n"
        "An element between a live node and the ground symbol is one "
        "of these roles. The DRAWING selects the role -- a winding "
        "star point or brought-out machine neutral versus a phase "
        "conductor, and which quantities the form states -- and each "
        "role has its own class, with the reason recorded beside the "
        "rule:\n\n")
    for role in sorted(GROUND_ROLES):
        entry = GROUND_ROLES[role]
        terminals = ("one terminal, the ground side implicit"
                     if role in IMPLICIT_GROUND_ROLES
                     else "two terminals")
        out.write(f"- **{role}** -> `cim:{entry.cim_class}` "
                  f"({terminals}): {entry.reason}\n")
    out.write(
        f"\nThe earth reference itself is stated once per case as "
        f"`cim:{GROUND_REFERENCE_CLASS}`, which is what gives a "
        "one-terminal shunt's implicit second end an address.\n\n")
    out.write(
        "## Meters\n\n"
        "A meter is a measurement point, never conducting equipment: it "
        "becomes a `cim:Analog` in the OP document, typed and unit-bound "
        "as the standard's enumerations require. One Analog per quantity "
        "the placement's own form selectors leave active, so a meter "
        "reading nothing states nothing and a meter reading three states "
        "three.\n\n")
    _table(out, ("master kind", "#OUTPUT", "measurementType", "unitSymbol"),
           [(kind, param, *OUTPUT_QUANTITY[(kind, param)])
            for kind, param in sorted(OUTPUT_QUANTITY)])
    out.write(
        "\nMeasured, but not yet stated: "
        + ", ".join(f"`{kind}.{param}`"
                    for kind, param in sorted(UNTYPED_OUTPUTS))
        + ". The 452 `measurementType` entry naming each has to be read "
        "off that profile's own enumeration, and a guessed entry would "
        "be a valid document stating the wrong quantity. They are "
        "withheld and counted as `cim_measurement_untyped`.\n\n")
    out.write(
        "## Topology roles\n\n"
        "Kinds the graph itself represents -- no equipment is emitted "
        "for them.\n\n"
        "- structural: "
        + ", ".join(sorted(STRUCTURAL_KINDS)) + "\n"
        "- ideal zero-impedance connections (a shared "
        "`cim:TopologicalNode` in TP): "
        + ", ".join(sorted(ZERO_IMPEDANCE_KINDS)) + "\n")
    return out.getvalue()


def statement_of_record() -> str:
    import test_reconstruction_inventory as inventory

    documents = {
        inventory.SOURCE_DOCUMENT: (
            "EMTSRC (source)",
            ("the stated case: parameters verbatim, scripts, structure, "
             "forms, substitutions, retained payloads"),
        ),
        inventory.DL_DOCUMENT: (
            "DL (diagram layout)",
            "geometry: positions, vertices, rotations, layers",
        ),
        inventory.EMT_DOCUMENT: (
            "EMT (interchange)",
            ("the declared port interface -- the only engine-facing "
             "statements of record"),
        ),
        inventory.EMTSIM_DOCUMENT: (
            "EMTSIM (study)",
            "the solver settings projection",
        ),
        inventory.STANDARD_FIVE: (
            "standard five (EQ/TP/SC/SSH/OP)",
            "projections only -- no field's statement of record",
        ),
    }
    out = io.StringIO()
    out.write(
        "# Where each source quantity is stated\n\n"
        "Generated from the reconstruction inventory "
        "(`tests/test_reconstruction_inventory.py`) by "
        "`tools/gen_guide_tables.py`; do not edit by hand.\n\n"
        "Each row is a field of the source file's model and the ONE "
        "document that is its statement of record -- the document where "
        "an edit survives the trip back to `.pscx` (see the edit "
        "contract chapter). Everything else that repeats the quantity "
        "is a projection the emitter regenerates.\n\n")
    for document in (inventory.SOURCE_DOCUMENT, inventory.DL_DOCUMENT,
                     inventory.EMT_DOCUMENT, inventory.EMTSIM_DOCUMENT):
        title, blurb = documents[document]
        fields = sorted(field for field, home in inventory.DOCUMENT_OF.items()
                        if home == document)
        out.write(f"## {title}\n\n{blurb}.\n\n")
        rows = []
        for field in fields:
            entry = inventory.INVENTORY[field]
            terms = ", ".join(f"`{term}`" for term in entry.terms) or "--"
            rows.append((f"`{field}`", terms))
        _table(out, ("source model field", "stated as"), rows)
    return out.getvalue()


PAGES = {
    "kind-to-cim.md": kind_to_cim,
    "statement-of-record.md": statement_of_record,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the guide's mapping tables")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any committed page is stale")
    args = parser.parse_args(argv)

    stale = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in PAGES.items():
        path = OUT_DIR / name
        text = build()
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
            print(f"wrote {path}")
    if stale:
        print(f"stale: {', '.join(stale)}; run "
              f"python tools/gen_guide_tables.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

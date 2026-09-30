"""Command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING, Sequence

from pscx.diagnostics import DIAGNOSTICS, Severity
from pscx.model import Netlist

if TYPE_CHECKING:
    from pscx.elaborate import FlatProject


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

#: --fail-on choices. "none" runs the report without ever failing, for a
#: caller that wants the findings and decides for itself.
_THRESHOLDS = {s.name.lower(): s for s in Severity} | {"none": None}


def _report(threshold: Severity | None) -> int:
    """Report what the run found on STDERR and say whether it stands.

    stdout carries the netlist -- a data stream someone may pipe or diff --
    so a findings summary written there would change the data on the runs
    that have something to report. stderr reaches a terminal just as
    plainly and leaves the stream alone.

    Grouped by severity rather than by code, because that is the order a
    reader needs: an ERROR means the output is of the wrong circuit and
    nothing else matters until it is fixed, while GAP and DEFECT counts
    are informational.
    """
    if not DIAGNOSTICS:
        return 0
    counts = DIAGNOSTICS.counts()
    worst_of: dict[str, Severity] = {}
    where: dict[str, str] = {}
    for record in DIAGNOSTICS.records():
        worst_of[record.key] = record.severity
        if record.key not in where and (record.span or record.provenance):
            where[record.key] = str(record.span or record.provenance)
    print("\nDIAGNOSTICS", file=sys.stderr)
    for severity in sorted(Severity, reverse=True):
        keys = sorted(k for k, s in worst_of.items() if s is severity)
        if not keys:
            continue
        print(f"  {severity.name} ({sum(counts[k] for k in keys)})",
              file=sys.stderr)
        for key in keys:
            at = f"  at {where[key]}" if key in where else ""
            print(f"    {counts[key]:6d}  {key}{at}", file=sys.stderr)
    if threshold is None:
        return 0
    over = DIAGNOSTICS.above(threshold)
    if not over:
        return 0
    print(f"\n{len(over)} diagnostic(s) above {threshold.name}; "
          f"{over[0].message}", file=sys.stderr)
    return 1


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace", action="append", default=None, metavar="FILE.pswx",
        help="a workspace whose projects are loaded with the case. Each "
             "filepath is opened the same way as a sibling library. The "
             "case remains the root of the instance tree",
    )


def _add_fail_on(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fail-on", choices=sorted(_THRESHOLDS), default="gap",
        help="exit non-zero when a diagnostic is STRICTLY above this "
             "severity (default: gap, i.e. fail on ERROR only). GAP and "
             "DEFECT findings are informational",
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _print_netlist(netlist: Netlist) -> None:
    print(
        f"canvas {netlist.canvas!r}: {len(netlist.components)} components, "
        f"{len(netlist.electrical_ports)} electrical ports, "
        f"{len(netlist.nodes)} nodes "
        f"({netlist.phase_expanded_node_count} phase-expanded), "
        f"{len(netlist.devices)} devices"
    )
    for index, node in enumerate(
        sorted(netlist.nodes, key=lambda n: -len(n.ports)), start=1
    ):
        members = ", ".join(f"{c.kind}.{p.name}" for c, p in node.ports)
        tag = " [GND]" if node.is_ground else ""
        print(f"  N{index} dim={node.dim}{tag}: {members}")
    for device in netlist.devices:
        print(f"  device {device.kind} {device.name!r} dim={device.dim}")

    interesting = [n for n in netlist.signal_nets if n.ports or n.names]
    print(f"  signal nets: {len(interesting)}")
    for index, net in enumerate(
        sorted(interesting, key=lambda n: -(len(n.ports) + len(n.names))), start=1
    ):
        names = "/".join(sorted(net.names))
        members = ", ".join(f"{c.kind}.{p.name}" for c, p in net.ports)
        drivers = ", ".join(
            f"{d[1].kind}.{d[2].name}" if d[0] == "port"
            else f"{d[1].kind}:{d[2]}={d[3]}" if d[0] == "writer"
            else f"{d[0]}({d[1]})"
            for d in net.drivers
        )
        label = f" [{names}]" if names else ""
        print(f"  S{index}{label}: {members or '(no pins)'}  <- {drivers or 'UNDRIVEN'}")


def _print_flat(fp: FlatProject) -> None:
    print(f"instance tree ({len(fp.instances)} instances, "
          f"{len(fp.orphan_canvases)} orphan canvases):")
    for inst in fp.instances:
        depth = inst.path.count("/")
        print(f"  {'  ' * depth}{inst.path}  [{inst.canvas}]")
    if fp.orphan_canvases:
        print("  orphan canvases:", ", ".join(fp.orphan_canvases))
    print(f"flat: {len(fp.nodes)} electrical nodes, "
          f"{len(fp.signal_nets)} signal nets, "
          f"{len(fp.radio_pairs)} radio pairs")
    for tx, rx in fp.radio_pairs:
        print(f"  radio {tx.name!r}: {tx.instance.path} -> {rx.instance.path}")
    print("stats:", dict(fp.stats))


def _emit_main(argv: Sequence[str]) -> int:
    # imported lazily: emission pulls in pycgmes/rdflib, which the
    # extraction-only invocations never need
    from pscx.cim import (
        DEFAULT_MODELING_AUTHORITY_SET,
        DEFAULT_SCENARIO_TIME,
        emit_case,
    )

    parser = argparse.ArgumentParser(
        prog="pscx emit",
        description="write one CIMXML document per profile "
                    "(EQ, TP, SC, SSH, OP)",
    )
    parser.add_argument("case", help=".pscx case file")
    parser.add_argument("--out", required=True, help="output directory")
    _add_workspace(parser)
    parser.add_argument(
        "--scenario-time", default=DEFAULT_SCENARIO_TIME,
        help="md:Model.scenarioTime (default: a fixed sentinel, so output "
             "stays byte-reproducible)",
    )
    parser.add_argument(
        "--modeling-authority-set", default=DEFAULT_MODELING_AUTHORITY_SET,
        help="md:Model.modelingAuthoritySet on every document header: the "
             "authority the model set belongs to, stated as an absolute "
             "URI (default: this project's own identity base). The one "
             "header field a downstream organization legitimately "
             "restamps; it never varies per document",
    )
    parser.add_argument("--model-version", default="1",
                        help="md:Model.version")
    _add_fail_on(parser)
    parser.add_argument(
        "--no-add-on", dest="add_on", action="store_false",
        help="write only the standard CGMES documents. pandapower cim2pp "
             "rejects the emt: profile URI unless built with "
             "ignore_errors=True; this is the answer for a "
             "consumer that will not pass it",
    )
    parser.add_argument(
        "--no-source", dest="source", action="store_false",
        help="write the interchange documents only, without the source "
             "document. The source document carries the source file's "
             "verbatim stated text; no interchange consumer -- an EMT "
             "engine included -- needs anything it states",
    )
    args = parser.parse_args(argv)
    for path in emit_case(args.case, args.out,
                          scenario_time=args.scenario_time,
                          modeling_authority_set=args.modeling_authority_set,
                          version=args.model_version,
                          add_on=args.add_on, source=args.source,
                          workspaces=args.workspace or ()):
        print(path)
    return _report(_THRESHOLDS[args.fail_on])


def _document_paths(documents: Sequence[str]) -> list[str]:
    paths = list(documents)
    if len(paths) == 1 and os.path.isdir(paths[0]):
        import glob

        paths = sorted(glob.glob(os.path.join(paths[0], "*.xml")))
    return paths


def _master():
    from pscx.io import MASTER_PSLX

    return MASTER_PSLX if MASTER_PSLX and os.path.exists(MASTER_PSLX) \
        else None


def _check_main(argv: Sequence[str]) -> int:
    # imported lazily, same as emission: the checker parses the emitted
    # graphs and the extraction-only invocations never need rdflib
    from pscx.check import check_documents

    parser = argparse.ArgumentParser(
        prog="pscx check",
        description="report where one case's document set disagrees "
                    "with itself. The comparator is the emitter: the "
                    "set is reconstructed, re-emitted, and compared "
                    "against itself subject by subject, so every "
                    "projection -- an engine numericValue, a study "
                    "setting, an EQ impedance -- is checked by the one "
                    "rule that produces it. A reporter, never a fixer "
                    "-- the statement of record is the source document, "
                    "and the fix for a divergence is to edit there and "
                    "re-emit (or read the set back with an explicit "
                    "precedence: pscx read)",
    )
    parser.add_argument(
        "documents", nargs="+",
        help="the nine documents of one case, or one directory "
             "containing exactly them")
    args = parser.parse_args(argv)
    try:
        report = check_documents(_document_paths(args.documents),
                                 master=_master())
    except ValueError as error:
        print(f"not one case's document set: {error}", file=sys.stderr)
        return 2
    for divergence in report.divergences:
        print(divergence.line())
    if report.divergences:
        print(f"{len(report.divergences)} divergence(s) over "
              f"{report.compared} compared statements; the statement "
              f"of record is the source document -- edit there and "
              f"re-emit, or choose a side with pscx read",
              file=sys.stderr)
        return 1
    print(f"coherent: {report.compared} statements agree with their "
          f"own re-emission")
    return 0


def _read_main(argv: Sequence[str]) -> int:
    # imported lazily like the other document-facing subcommands
    from lxml import etree as ET

    from pscx.reader import DivergentSet, Precedence, read_case
    from pscx.write import write_project

    parser = argparse.ArgumentParser(
        prog="pscx read",
        description="reconstruct a .pscx case from the nine documents "
                    "of one case. A divergent set -- one that disagrees "
                    "with its own re-emission -- is refused with the "
                    "report unless a side is chosen explicitly; nothing "
                    "is preferred silently in either direction",
    )
    parser.add_argument(
        "documents", nargs="+",
        help="the nine documents of one case, or one directory "
             "containing exactly them")
    parser.add_argument("--out", required=True,
                        help="the .pscx file to write")
    side = parser.add_mutually_exclusive_group()
    side.add_argument(
        "--prefer-source", action="store_const", dest="precedence",
        const=Precedence.SOURCE, default=Precedence.REFUSE,
        help="proceed with the statement of record and report every "
             "ignored projection statement")
    side.add_argument(
        "--prefer-interchange", action="store_const", dest="precedence",
        const=Precedence.INTERCHANGE,
        help="apply the engine values into the reconstruction where a "
             "stated-parameter inverse exists (ParameterValue."
             "numericValue, the adopted SimulationCase attributes); "
             "every burned $() parameterization is reported by name, "
             "and a projection with no inverse or a drawn element whose "
             "instances disagree with each other still refuses")
    args = parser.parse_args(argv)
    try:
        project, burns = read_case(_document_paths(args.documents),
                                   master=_master(),
                                   precedence=args.precedence)
    except DivergentSet as refusal:
        for divergence in refusal.refused:
            print(divergence.line())
        print(f"refused: {len(refusal.refused)} statement(s); choose a "
              f"side with --prefer-source or --prefer-interchange, or "
              f"edit the statement of record and re-emit",
              file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"not one case's document set: {error}", file=sys.stderr)
        return 2
    for burn in burns:
        print(burn.line())
    with open(args.out, "wb") as handle:
        handle.write(ET.tostring(write_project(project),
                                 xml_declaration=True, encoding="UTF-8"))
    print(args.out)
    return _report(_THRESHOLDS["gap"])


def _catalog_main(argv: Sequence[str]) -> int:
    # imported lazily like the other document-facing subcommands
    from pscx.catalog import (library_surface, undeclared_terms,
                              write_catalog, write_shapes)

    parser = argparse.ArgumentParser(
        prog="pscx catalog",
        description="write one catalog document for a whole library: "
                    "every model the library declares, its parameters, "
                    "categories, choice lists and ports, as CIM instance "
                    "data under the same terms the case documents use",
    )
    parser.add_argument(
        "library", nargs="?", default=None,
        help="the .pslx library to catalog (default: the configured "
             "master.pslx)")
    parser.add_argument("--out", required=True,
                        help="path of the catalog CIMXML to write")
    parser.add_argument("--html", default=None, metavar="PAGE",
                        help="also render the written catalog as one "
                             "browsable HTML page")
    parser.add_argument("--shapes", default=None, metavar="FILE",
                        help="also write SHACL shapes, one per definition, "
                             "that check a case document's stated values "
                             "against the bounds and choices the library "
                             "declares (Turtle; needs SHACL-AF targets)")
    args = parser.parse_args(argv)

    if args.library is None:
        from pscx.io import load_master, master_project

        if _master() is None:
            print("no library given and no master.pslx configured; set "
                  "PSCAD_MASTER or pass a .pslx path", file=sys.stderr)
            return 2
        project = master_project()
        registry = load_master()
    else:
        from pscx.io import load_definitions, read_project

        project = read_project(args.library)
        registry = {}
        load_definitions(args.library, registry)

    catalog = write_catalog(project, registry, args.out)
    classes = sorted(catalog.minted.values())
    print(f"{args.out}: {len(catalog.minted)} subjects "
          f"({classes.count('LibraryModelType')} types, "
          f"{classes.count('ParameterDescriptor')} descriptors, "
          f"{classes.count('ParameterCategory')} categories, "
          f"{classes.count('ModelPort')} ports, "
          f"{classes.count('ToolPayload')} payloads)")
    proposed = undeclared_terms(catalog.graph)
    if proposed:
        # unreachable while builder and vocabulary agree; loud if a term
        # is ever added to one without the other
        for term in proposed:
            print(f"emt:{term} is stated but not declared", file=sys.stderr)
        return 1
    if args.shapes is not None:
        from rdflib.namespace import SH

        shapes = write_shapes(library_surface(project, registry),
                              args.shapes)
        print(f"{args.shapes}: "
              f"{len(set(shapes.subjects(SH.target, None)))} shapes")
    if args.html is not None:
        # rendered from the WRITTEN document, not the in-memory graph,
        # so the page is provably a view of the file just produced
        from pscx.catalog_html import render_catalog_html

        rendered = render_catalog_html(args.out, args.html)
        print(f"{args.html}: {rendered} model types rendered")
    return 0


def main(argv: Sequence[str]) -> int:
    if argv and argv[0] == "emit":
        return _emit_main(list(argv[1:]))
    if argv and argv[0] == "check":
        return _check_main(list(argv[1:]))
    if argv and argv[0] == "read":
        return _read_main(list(argv[1:]))
    if argv and argv[0] == "catalog":
        return _catalog_main(list(argv[1:]))
    parser = argparse.ArgumentParser(
        prog="pscx",
        description="Convert PSCAD cases to CIM and back. This form "
                    "prints a case's extracted netlist (see also: "
                    "pscx emit CASE --out DIR, pscx check DIR, "
                    "pscx read DIR --out CASE.pscx)",
    )
    parser.add_argument("--flatten", action="store_true",
                        help="print the flattened whole-project view")
    parser.add_argument("cases", nargs="*", metavar="CASE.pscx")
    _add_workspace(parser)
    _add_fail_on(parser)
    args = parser.parse_args(argv)
    if not args.cases:
        parser.print_usage(sys.stderr)
        return 2
    # imported here so `pscx catalog` never loads the case pipeline
    from pscx.elaborate import flatten
    from pscx.nets import extract

    if args.flatten:
        for case in args.cases:
            print(f"=== {case}")
            _print_flat(flatten(case, workspaces=args.workspace or ()))
        return _report(_THRESHOLDS[args.fail_on])
    for case in args.cases:
        print(f"=== {case}")
        for netlist in extract(case, workspaces=args.workspace or ()):
            _print_netlist(netlist)
    return _report(_THRESHOLDS[args.fail_on])


def cli() -> int:
    """Console-script entry point (``pscx`` in ``[project.scripts]``)."""
    return main(sys.argv[1:])

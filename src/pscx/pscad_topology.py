"""Extract electrical topology from PSCAD 5.0 ``.pscx`` project files.

A ``.pscx`` contains no netlist. Connectivity is implicit and must be
reconstructed from three separate mechanisms:

1. **Geometry**: components carry ``x``/``y``/``orient``; their port offsets
   live in the master library (``master.pslx``). A node exists wherever a port
   coincides with a wire.
2. **Conditional ports**: most library ports exist only when a boolean
   expression over the instance's parameters holds.
3. **Named signals**: ``datalabel`` components join by name with no wire.
   Handled by the *signal* graph (see :func:`_build_signal_nets`), a second
   graph entirely separate from the electrical one.

Usage
-----
    python pscad_topology.py CASE.pscx          # print the netlist

    from pscad_topology import extract
    for netlist in extract("CASE.pscx"):
        for node in netlist.nodes:
            ...
"""

# This module is a thin facade: the implementation lives in the pscx.*
# modules listed below, and every top-level name is re-exported here so
# consumers of the documented API see one namespace.
# ruff: noqa: F401, I001
from __future__ import annotations

import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

from pscx.diagnostics import DIAGNOSTICS
from pscx.common import (
    CONDUCTIVE_WIRES,
    ELECTRICAL_MODES,
    ELECTYPE_GROUND,
    EVENT_WRITERS,
    GROUND_KEY,
    HOSTING_VERTEX_COUNT,
    HOSTING_WIRES,
    MASTER_KEYED_KINDS,
    MASTER_PSLX,
    NAME_BRIDGES,
    SIGNAL_MODES,
    _NUMBER,
    _SIGNAL_NAME,
    _WRITER_SEGMENTS,
    as_number,
)
from pscx.guards import (
    Alternative,
    Arm,
    ArmRef,
    Case,
    Conditional,
    Splice,
    Text,
    _CASE_ARM,
    _CASE_DIRECTIVE,
    _DIM_MACRO,
    _OUTPUT_DIRECTIVE,
    _guard_holds,
    assemble_splices,
    enumerate_arms,
    expand,
    guarded_lines,
    parse_script,
    selected_arms,
)
from pscx.preproc import (
    BRANCH_BARE_NODES,
    BranchDecl,
    _BARE_NODE_TOKEN,
    _BRANCH_KEYWORDS,
    _NUMERIC_TOKEN,
    _guard_expressions,
    _parse_branch_segment,
    _writer_directives,
    preprocess_script,
)
from pscx.expr import (
    ConditionParser,
    _LITERALS,
    _TOKEN,
    _coerce_pair,
    _eval_arithmetic,
    condition_holds,
)
from pscx.units import (
    _SI_PREFIXES,
    _SPECIAL_UNIT_FACTORS,
    _UNIT_ALIASES,
    _VALUE_WITH_UNIT,
    _canon_unit,
    _simple_unit_factor,
    unit_factor,
    value_in_declared_unit,
)
from pscx.geometry import (
    DisjointSet,
    Point,
    point_on_segment,
    rotate_port,
)
from pscx.model import (
    Component,
    ComponentDef,
    Device,
    Netlist,
    Node,
    Port,
    PortDef,
    SignalNet,
)
from pscx.io import (
    _is_disabled,
    _layer_states,
    _vertices,
    load_definitions,
    load_master,
    read_project,
    register_definitions,
    resolve,
)
from pscx.nets import (
    SIGNAL_NAME_CASEFOLD,
    _bridge_name,
    _build_nodes,
    _build_signal_nets,
    _canon_name,
    _collect_wires,
    _place_components,
    extract,
)
from pscx.lower import (
    EvaluatedBranch,
    _COMPUTATION_LINE,
    _IDENT,
    _NUMBER_WITH_UNIT,
    _resolve_branch_node,
    computations_env,
    evaluate_branches,
    port_dim,
    resolve_numeric,
)
from pscx.elaborate import (
    FlatNode,
    FlatProject,
    FlatSignalNet,
    Instance,
    RadioLinkEnd,
    _station_roots,
    flatten,
)
from pscx.cli import (
    _print_flat,
    _print_netlist,
    cli,
    main,
)


def __getattr__(name: str):
    # MASTER is served lazily by pscx.io (master.pslx is read on first
    # access); re-exporting it by name here would defeat that.
    if name == "MASTER":
        return load_master()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

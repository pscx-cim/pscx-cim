"""Shared configuration and derived constants."""

from __future__ import annotations

import os
import re
from typing import Any


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

#: The PSCAD master library, named by the environment and nothing else: the
#: file can be saved anywhere, so no default would stay right.
#: Empty when PSCAD_MASTER is unset.
MASTER_PSLX = os.environ.get("PSCAD_MASTER", "")

# --------------------------------------------------------------------------
# Derived constants -- see module docstring for provenance
# --------------------------------------------------------------------------

#: ``mode`` values denoting an electrical node rather than a signal pin.
#:
#: mode 1 = input signal, 2 = output signal, 3 = electrical node.
#: mode 4 is *also* electrical: ``multimeter`` declares port ``B`` twice -- as
#: mode 3 when measuring current, mode 4 when not (``MeasP+MeasQ+MeasI+IRMS==0``).
#: Confirmed by an ``Inverter_AC`` in ENI/Cigre_BM.pscx that abuts a mode-4
#: multimeter pin. More precisely, mode 4 is a type-agnostic PASS-THROUGH:
#: ``pin`` uses it as a junction that also joins crossing wires, electrical
#: or data (see the junction step in ``_build_nodes``/``_build_signal_nets``).
ELECTRICAL_MODES = frozenset({"3", "4"})

#: ``mode`` values denoting a data-signal pin: 1 = input (reads its net),
#: 2 = output (drives its net). Signal ports use the same wire geometry as
#: electrical ones but form a SEPARATE graph; the two never share a wire.
SIGNAL_MODES = frozenset({"1", "2"})

#: Components that bridge their wire net onto a canvas-scoped *name* instead
#: of transforming the signal. ``datalabel`` is bidirectional; ``import``
#: reads the named entity onto its wire, ``export`` writes its wire to the
#: name. Their pins are excluded from driver/reader lists -- they are joins.
#:
#: The name resolves against the canvas's namespace, which contains:
#:   - datalabel-bridged wire nets on the same canvas,
#:   - parameters named by ``#OUTPUT`` directives (the value is the name),
#:   - the canvas's own definition's boundary Ports (graphics, mode 1/2),
#:   - the canvas's own definition's form parameters (e.g. every ``import``
#:     on the Chopper canvas names one of Chopper's five form parameters).
#: Matched on the bare definition name.
NAME_BRIDGES = frozenset({"datalabel", "import", "export"})

#: Kinds whose semantics are keyed on MASTER's definitions (name bridges,
#: connectors, slicers). Assumption (b) closed: classification requires the
#: resolved definition to live in the ``master`` namespace -- a project-local
#: definition merely NAMED ``datalabel`` etc. is an ordinary component.
MASTER_KEYED_KINDS = frozenset({
    "datalabel", "import", "export", "xnode", "radiolink",
    "datatap", "datamerge", "pin",
})

#: Script segments in which a ``#OUTPUT REAL <Param> ...`` directive marks
#: ``<Param>`` as a signal-writing parameter (its *value* names the signal a
#: meter writes, e.g. ``multimeter.CurI = "Iload"``). Derived from master:
#: every definition carrying #OUTPUT has it in Dsout/Dsdyn/Fortran segments;
#: an occurrence inside a Comments segment is commentary and is excluded.
#:
#: #OUTPUT directives are frequently guarded by ``#IF``/``#ELSEIF``/``#ELSE``
#: preprocessor blocks (``multimeter`` only writes ``Vrms`` under ``RMS!=0``),
#: so each directive is stored with its guard chain and evaluated against the
#: instance's parameters. Ignoring the guards produces phantom
#: multi-driver nets. ``Sequencer_*`` #OUTPUTs are genuinely
#: unconditional: several timed events writing one variable is the intended
#: idiom, so a multi-writer net made of event writers is NOT an error.
_WRITER_SEGMENTS = frozenset({"Dsout", "Dsdyn", "Fortran"})

#: Definitions whose #OUTPUT sits inside a runtime ``IF ($IState==1)`` event
#: block: several timed events legally write ONE variable (e.g. a breaker
#: opened at t=0.2s and reclosed at t=0.5s by two Sequencer_Breaker events).
#: A net whose multiple drivers are all event writers is not a conflict.
EVENT_WRITERS = frozenset({"Sequencer_SetVar", "Sequencer_Breaker"})

#: Valid EMTDC signal names, per the datalabel form's own regex (a leading
#: letter, then letters/digits/underscore). A writer parameter whose value
#: fails this (for example ``fft.Fout = '\"'``) names nothing.
_SIGNAL_NAME = re.compile(r"[A-Za-z]\w*\Z")

#: ``electype`` is the Component Wizard's "Electrical Type" enum:
#: 0 = Fixed, 1 = Removable, 2 = Switched, 3 = Ground.  Named by the PSCAD 5
#: manual ("Electrical Node Types", Component_Design/The_Graphic_Section/
#: Connections.htm in ol-help.chm) and the Automation Library reference
#: (``add_electrical(..., electype='FIXED')``; wizard.html lists FIXED,
#: SWITCHED, REMOVABLE).  Numeric coding triangulated: docs list the four in
#: the order Fixed, Removable, Switched, Ground on three independent pages;
#: 3 = Ground is anchored empirically (``ground.A``, ``source_1.NB`` under
#: ``Grnd==1``, both legitimately wireless); every electype-1 port in master
#: belongs to a collapsible series-branch element (R/L/C, ammeter, switch,
#: pi-section...) = the manual's "Removable" description verbatim, and every
#: electype-2 port to a frequently-switching-conductance device (thyristor
#: bridges g6p200*, arrester, spark_gap, peswitch, varrlc...) = "Switched".
#: Domain over master is exactly {0,1,2,3}.  Removable/Switched are
#: EMTDC solver hints (branch collapsing, Optimal Node Ordering) -- no
#: topological meaning, so both are treated as ordinary terminals.
ELECTYPE_GROUND = "3"

#: Wire classids conducting along their whole length. ``Bus`` additionally
#: carries ``Name`` and ``BaseKV`` -- useful for a CIM named node.
CONDUCTIVE_WIRES = frozenset({"WireOrthogonal", "Bus"})

#: Wire classids that *host a device* rather than conduct. An instance is
#: drawn with 4 vertices laid out as::
#:
#:     v0 --stub-- v1 ==device body== v2 --stub-- v3
#:
#: The body does not conduct, so such a wire contributes two DISTINCT
#: terminals. ``TLine`` maps onto a CIM ACLineSegment; the hosted ``<User>``
#: child carries ``Name``, ``Length`` and ``Dim``.
HOSTING_WIRES = frozenset({"TLine", "Cable", "WireBranch"})

HOSTING_VERTEX_COUNT = 4

#: Sentinel key for the global ground net.
GROUND_KEY = ("GROUND",)


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------

#: Parameter values carry units (``"100.0 [ohm]"``) and may use scientific
#: notation (``"1.0E6 [ohm]"``). The exponent MUST be kept: a
#: leading-digits match reads 1.0E6 as 1.0.
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def as_number(raw: Any) -> float | None:
    """Leading numeric value of a PSCAD parameter, or ``None``."""
    match = _NUMBER.match(str(raw).strip())
    return float(match.group(0)) if match else None

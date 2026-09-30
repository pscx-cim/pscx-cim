# Kind to standard class

Generated from `pscx.rules` by `tools/gen_guide_tables.py`; do not edit by hand.

Every drawn placement travels in the EMT document as a `cim:DetailedModelDynamics` naming its library type; the kinds below are ADDITIONALLY projected into the standard five as the class named here.

## Placed components

| master kind | CIM class |
|---|---|
| breaker1 | `cim:Breaker` |
| breaker3 | `cim:Breaker` |
| capacitor | `cim:EquivalentBranch` |
| fixed_load | `cim:EnergyConsumer` |
| inductor | `cim:EquivalentBranch` |
| reactive_load | `cim:EnergyConsumer` |
| resistive_load | `cim:EnergyConsumer` |
| resistor | `cim:EquivalentBranch` |
| source1 | `cim:ExternalNetworkInjection` |
| source3 | `cim:ExternalNetworkInjection` |
| source3R | `cim:ExternalNetworkInjection` |
| source_1 | `cim:ExternalNetworkInjection` |
| source_3 | `cim:ExternalNetworkInjection` |
| sync_machine | `cim:SynchronousMachine` |
| xfmr-3p2w | `cim:PowerTransformer` |

The classes above are the SERIES reading: an R, L or C drawn between two live nodes is a `cim:EquivalentBranch`. The same kinds drawn to the ground symbol play a role the drawing itself selects -- see *Drawn to ground* below.

## Hosting wires

| wire classid | CIM class |
|---|---|
| TLine | `cim:ACLineSegment` |

## Drawn to ground

An element between a live node and the ground symbol is one of these roles. The DRAWING selects the role -- a winding star point or brought-out machine neutral versus a phase conductor, and which quantities the form states -- and each role has its own class, with the reason recorded beside the rule:

- **ground_switch** -> `cim:GroundDisconnector` (two terminals): a switch whose far end is the ground reference, which is the class's own definition -- isolating a circuit from Ground. It keeps TWO terminals, because 301 requires exactly two of every Switch
- **neutral_grounding_impedance** -> `cim:GroundingImpedance` (two terminals): an impedance between a transformer winding's star point (or a machine's brought-out neutral) and earth. A neutral grounding resistor is not a shunt admittance: it carries no current when the phases are balanced, and CIM agrees -- EarthFaultCompensator r/x are ShortCircuit data, not EQ
- **shunt_capacitor** -> `cim:LinearShuntCompensator` (one terminal, the ground side implicit): a fixed capacitance from a phase conductor to earth -- a shunt capacitor bank, susceptance +wC. NOT a switchable bank: PSCAD's capacitor form states one capacitance and no section count, so maximumSections is 1 and no switching capability is invented
- **shunt_reactor** -> `cim:LinearShuntCompensator` (one terminal, the ground side implicit): a fixed inductance from a phase conductor to earth -- a shunt reactor, whose susceptance is NEGATIVE (-1/wL) in the sign convention bPerSection uses
- **shunt_resistor** -> `cim:LinearShuntCompensator` (one terminal, the ground side implicit): a fixed resistance from a phase conductor to earth: a discharge, damping or numerical-grounding path. Its conductance is what a load flow needs, so it goes in EQ as gPerSection with bPerSection zero

The earth reference itself is stated once per case as `cim:Ground`, which is what gives a one-terminal shunt's implicit second end an address.

## Meters

A meter is a measurement point, never conducting equipment: it becomes a `cim:Analog` in the OP document, typed and unit-bound as the standard's enumerations require. One Analog per quantity the placement's own form selectors leave active, so a meter reading nothing states nothing and a meter reading three states three.

| master kind | #OUTPUT | measurementType | unitSymbol |
|---|---|---|---|
| ammeter | Name | LineCurrent | A |
| multimeter | Crms | LineCurrent | A |
| multimeter | CurI | LineCurrent | A |
| multimeter | VolI | Voltage | V |
| multimeter | VolILL | Voltage | V |
| multimeter | Vrms | Voltage | V |
| nodelabel | VName | Voltage | V |
| voltmeter | Name | Voltage | V |
| voltmetergnd | Name | Voltage | V |


Measured, but not yet stated: `multimeter.P`, `multimeter.Ph`, `multimeter.Q`. The 452 `measurementType` entry naming each has to be read off that profile's own enumeration, and a guessed entry would be a valid document stating the wrong quantity. They are withheld and counted as `cim_measurement_untyped`.

## Topology roles

Kinds the graph itself represents -- no equipment is emitted for them.

- structural: breakout, breakout3, ground, nodeloop, pin, xnode
- ideal zero-impedance connections (a shared `cim:TopologicalNode` in TP): short

# Walkthrough

This chapter walks one real case through the whole exchange: emit the
documents, read what the mapping produced, prove the set coherent,
and read the documents back into a `.pscx`. The excerpts below are
checked against a fresh run by `tests/test_walkthrough_excerpts.py`.

## The case

The benchmark is PSCAD's own IEEE 39-bus example: ten sources, 34
Bergeron lines carrying the classic R/X/B values as manual line
constants, twelve transformers and nineteen constant-PQ loads, all at
230 kV. The working file is the case resaved under PSCAD 5.0.2:

```
benchmarks/ieee39/ieee_39_bus_v5.pscx
```

The case and everything derived from it are PSCAD's content. They are
not part of this repository, and this chapter quotes only short
structural excerpts. You download the zip from the PSCAD knowledge
base yourself, resave the project under PSCAD 5,
and place it at the path above; the source is the knowledge-base
article "IEEE 39 Bus System". Every step below assumes the file is in
place.

## Emit

```
pscx emit benchmarks/ieee39/ieee_39_bus_v5.pscx --out out/
```

The command prints the nine paths it wrote. The
[documents chapter](documents.md) says what each one is:

```text
out/ieee_39_bus_v5_EQ.xml
out/ieee_39_bus_v5_TP.xml
out/ieee_39_bus_v5_SC.xml
out/ieee_39_bus_v5_SSH.xml
out/ieee_39_bus_v5_OP.xml
out/ieee_39_bus_v5_DL.xml
out/ieee_39_bus_v5_EMT.xml
out/ieee_39_bus_v5_EMTSIM.xml
out/ieee_39_bus_v5_EMTSRC.xml
```

`--no-add-on` writes the standard six alone (EQ, TP, SC, SSH, OP, DL);
that is the set a steady-state consumer such as PowSyBl reads. Alongside the paths,
stderr carries the diagnostics report:

```text
DIAGNOSTICS
  GAP (216)
         1  cim_basevoltage_unknown
        34  cim_line_no_zero_sequence
         1  cim_load_response_withheld: an EquivalentInjection has no load response  at ieee_39_bus_v5/Main/Main/1468690411
        10  cim_measurement_silent: multimeter
        10  cim_source_angle_addon_only
        10  cim_source_capability_placeholder
        10  cim_source_reference_priority_designated
        63  cim_terminal_phases_unnameable
        42  dl_mirror_dropped: User  at ieee_39_bus_v5/Main
         1  emt_setting_missing: multirun_filename
        34  lineconst_manual_no_zero_sequence
```

GAP findings are normal for a real case (see
[known limits](limits.md)). Each one counts something the source did
not state or the standard cannot hold; none is an error. The
taxonomy in `src/pscx/diagnostics.py` explains every code with its
evidence; for this case, in one sentence each:

- `cim_basevoltage_unknown` (1): the one node with no stated nominal
  voltage is the ground reference, which carries the 1.0 kV
  placeholder; every conducting node resolves to 230 kV.
- `cim_line_no_zero_sequence` and `lineconst_manual_no_zero_sequence`
  (34 each): the manual line constants state no zero sequence, so SC
  carries no `r0`/`x0`/`b0ch` for the 34 lines. `pscx` does not derive
  values that the case does not state.
- `cim_load_response_withheld` (1): the one capacitive load is an
  `EquivalentInjection`, which has no load response in CGMES. Its form
  still travels in the add-on. The other 18 loads are constant power
  and state their exponents in a `LoadResponseCharacteristic`.
- `cim_measurement_silent: multimeter` (10): each of the ten
  multimeters runs with every measurement selector on its form turned
  off, so it reads nothing and states no `cim:Analog`. A meter states
  one Analog per quantity its selectors leave active, and ten of ten
  leave none.
- `cim_source_angle_addon_only` (10): a source's angle has no
  attribute in the standard injection vocabulary, so it travels only
  in the add-on. The section "Where the angle lives" below shows it.
- `cim_source_capability_placeholder` (10): the CGMES equipment
  shapes mandate a capability envelope no PSCAD source form states,
  so symmetric non-binding sentinel bounds are written and counted.
- `cim_source_reference_priority_designated` (10): each source states
  `referencePriority` = 1, which consumers require and the case does
  not state.
- `cim_terminal_phases_unnameable` (63): a terminal whose conductor
  count has no matching `cim:PhaseCode` value omits `Terminal.phases`,
  and the extension profile states the count instead.
- `dl_mirror_dropped` (42): a mirrored symbol's quarter turn is
  carried, but DL states rotation as an angle, which cannot express a
  mirror. The mirror is kept in the source record.
- `emt_setting_missing` (1): the project states no value for one
  study setting, and none is fabricated.

## The mapping, by excerpt

Each excerpt below is a few lines of the emitted documents, elided
with `...`, annotated with where the number came from in the case's
own form text. The [mapping chapter](mapping.md) states the rules;
this section shows them landing.

### A busbar

The drawn Bus wire named `Bus31` becomes a `cim:ConnectivityNode` in
EQ, bound to a `cim:TopologicalNode` in TP:

```xml
<cim:ConnectivityNode rdf:about="urn:uuid:ac464a2d-...">
  ...
  <cim:IdentifiedObject.name>Bus31</cim:IdentifiedObject.name>
</cim:ConnectivityNode>
```

```xml
<cim:ConnectivityNode rdf:about="urn:uuid:ac464a2d-...">
  <cim:ConnectivityNode.TopologicalNode rdf:resource="urn:uuid:4eda2cbf-..."/>
  ...
</cim:ConnectivityNode>
```

The 230 kV comes from the Bus wire's own `BaseKV` field. One
`cim:BaseVoltage` subject serves the whole island; every conducting
node resolves to it, including the four junctions the case draws with
no bus of their own:

```xml
<cim:BaseVoltage rdf:about="urn:uuid:35c7ce28-...">
  <cim:BaseVoltage.nominalVoltage>230.0</cim:BaseVoltage.nominalVoltage>
  ...
</cim:BaseVoltage>
```

### A line

The line module `T5_8` states its constants as per-unit totals on the 529 ohm base (230 kV, 100 MVA). The emitted
`cim:ACLineSegment` carries them in ohms and siemens: r = 0.0008 x 529
= 0.4232, x = 0.0112 x 529 = 5.9248, bch = 0.1476 / 529 = 2.79e-4.

```xml
<cim:ACLineSegment rdf:about="urn:uuid:197479e2-...">
  <cim:ACLineSegment.bch>0.0002790170132325142</cim:ACLineSegment.bch>
  <cim:ACLineSegment.gch>0.0</cim:ACLineSegment.gch>
  <cim:ACLineSegment.r>0.4232</cim:ACLineSegment.r>
  <cim:ACLineSegment.x>5.9248</cim:ACLineSegment.x>
  ...
  <cim:IdentifiedObject.name>T5_8</cim:IdentifiedObject.name>
</cim:ACLineSegment>
```

### A transformer

A transformer placement becomes a `cim:PowerTransformer` with two
`cim:PowerTransformerEnd`s. The end below belongs to the transformer
between buses 29 and 38: the form states Xl = 0.0156 pu on Tmva =
100 MVA at 230/230 kV, so x = 0.0156 x 529 = 8.2524 ohm on end 1. The
form declares the non-ideal model, so end 1 also states the
magnetizing pair: b = -(Im1/100)/529 with Im1 = 2 percent, and
g = NLL/529 with NLL = 0.0008 pu. A placement declaring the Ideal
model states b = g = 0.0 behind a counted diagnostic instead.

```xml
<cim:PowerTransformerEnd rdf:about="urn:uuid:0d215533-...">
  ...
  <cim:PowerTransformerEnd.b>-3.780718336483932e-05</cim:PowerTransformerEnd.b>
  ...
  <cim:PowerTransformerEnd.g>1.5122873345935729e-06</cim:PowerTransformerEnd.g>
  <cim:PowerTransformerEnd.r>0.0</cim:PowerTransformerEnd.r>
  <cim:PowerTransformerEnd.ratedS>100.0</cim:PowerTransformerEnd.ratedS>
  <cim:PowerTransformerEnd.ratedU>230.0</cim:PowerTransformerEnd.ratedU>
  <cim:PowerTransformerEnd.x>8.2524</cim:PowerTransformerEnd.x>
  ...
  <cim:TransformerEnd.endNumber>1</cim:TransformerEnd.endNumber>
</cim:PowerTransformerEnd>
```

### A load

A fixed load is a `cim:EnergyConsumer` in EQ, and SSH states its
drawn P and Q. The load at bus 39 states 11.04 pu and 2.5 pu on
100 MVA; SSH carries the form's own evaluated
megawatts, positive in the CIM load convention because a load
consumes:

```xml
<cim:EnergyConsumer rdf:about="urn:uuid:16f26c0d-...">
  <cim:EnergyConsumer.p>1104.0</cim:EnergyConsumer.p>
  <cim:EnergyConsumer.q>249.99989999999997</cim:EnergyConsumer.q>
  ...
</cim:EnergyConsumer>
```

EQ also links the load to a `cim:LoadResponseCharacteristic` stating
its voltage and frequency exponents. Here all four are zero, because the
IEEE 39 loads are constant power. CGMES states `p` and `q` at the node's
nominal voltage, and with zero exponents that is the same power the form
states at its rated voltage.

The one capacitive load, at bus 24, is the exception: a load stating
negative reactive power is outside `cim:EnergyConsumer`'s vocabulary,
so it travels as a `cim:EquivalentInjection`:

```xml
<cim:EquivalentInjection rdf:about="urn:uuid:2fdf48e6-...">
  <cim:Equipment.inService>true</cim:Equipment.inService>
  <cim:EquivalentInjection.p>308.6001</cim:EquivalentInjection.p>
  <cim:EquivalentInjection.q>-92.19999</cim:EquivalentInjection.q>
  ...
</cim:EquivalentInjection>
```

### A source

Each of the ten grid sources emits as a `cim:ExternalNetworkInjection`
package. The excerpts below all belong to one placement, the source at bus 35,
drawn as `Source1`. In EQ, the injection carries the mandated
capability envelope as non-binding sentinels and names its
voltage-mode control:

```xml
<cim:ExternalNetworkInjection rdf:about="urn:uuid:3c6366c4-...">
  ...
  <cim:ExternalNetworkInjection.maxP>1000000000.0</cim:ExternalNetworkInjection.maxP>
  ...
  <cim:IdentifiedObject.name>Source1</cim:IdentifiedObject.name>
  <cim:RegulatingCondEq.RegulatingControl rdf:resource="urn:uuid:aecc1abe-..."/>
</cim:ExternalNetworkInjection>
```

The control regulates voltage at the injection's own terminal, and
its SSH state targets the form's live `Es`, 241.339 kV:

```xml
<cim:RegulatingControl rdf:about="urn:uuid:aecc1abe-...">
  ...
  <cim:RegulatingControl.enabled>true</cim:RegulatingControl.enabled>
  <cim:RegulatingControl.targetValue>241.339</cim:RegulatingControl.targetValue>
  <cim:RegulatingControl.targetValueUnitMultiplier rdf:resource="http://iec.ch/TC57/CIM100#UnitMultiplier.k"/>
</cim:RegulatingControl>
```

SSH states the live dispatch.
The form's initial condition at bus 35 is Pinit = 6.5 pu, Qinit =
1.67 pu on its stated 100 MVA base; the load convention makes an
injection into the network negative, so SSH states p = -650.0 and
q = -167.0:

```xml
<cim:ExternalNetworkInjection rdf:about="urn:uuid:3c6366c4-...">
  ...
  <cim:ExternalNetworkInjection.p>-650.0</cim:ExternalNetworkInjection.p>
  <cim:ExternalNetworkInjection.q>-167.0</cim:ExternalNetworkInjection.q>
  <cim:ExternalNetworkInjection.referencePriority>1</cim:ExternalNetworkInjection.referencePriority>
  ...
</cim:ExternalNetworkInjection>
```

The form's stated internal impedance, 10 ohm at 80 degrees, is
written in SC with the IEC 60909 network-feeder attributes, each as a
range whose minimum equals its maximum: the R/X ratio is cot(80 deg) = 0.17633, the initial
short-circuit current is 230 kV / (sqrt(3) x 10 ohm) = 13279 A, and
the form's "zero sequence equals positive" choice states the Z0/Z1
ratio as 1:

```xml
<cim:ExternalNetworkInjection rdf:about="urn:uuid:3c6366c4-...">
  <cim:ExternalNetworkInjection.maxInitialSymShCCurrent>13279.056191361395</cim:ExternalNetworkInjection.maxInitialSymShCCurrent>
  ...
  <cim:ExternalNetworkInjection.maxR1ToX1Ratio>0.17632698070846506</cim:ExternalNetworkInjection.maxR1ToX1Ratio>
  <cim:ExternalNetworkInjection.maxZ0ToZ1Ratio>1.0</cim:ExternalNetworkInjection.maxZ0ToZ1Ratio>
  ...
</cim:ExternalNetworkInjection>
```

Every document's header states the modeling authority, and the value
matters: PowSyBl reads SSH at all only when the header resolves:

```xml
<md:FullModel rdf:about="urn:uuid:3e0bc2b5-...">
  <md:Model.modelingAuthoritySet>https://w3id.org/pscx-cim/ModelingAuthority</md:Model.modelingAuthoritySet>
  ...
</md:FullModel>
```

### Where the angle lives

The source's live angle `Ph` is the quantity counted as
`cim_source_angle_addon_only` above. The injection vocabulary has no
attribute for it, so it travels only in the add-on EMT document, as a
`cim:ParameterValue` on the placement's `cim:DetailedModelDynamics`.
For the bus 35 source the case states 4.78 degrees:

```xml
<cim:ParameterValue rdf:about="urn:uuid:99c2b63d-...">
  <cim:ParameterValue.DetailedModelDynamics rdf:resource="urn:uuid:6d1ad267-..."/>
  <cim:ParameterValue.ParameterDescriptor rdf:resource="urn:uuid:51064f69-..."/>
  <cim:ParameterValue.value>4.78</cim:ParameterValue.value>
  <emt:ParameterValue.numericValue>4.78</emt:ParameterValue.numericValue>
</cim:ParameterValue>
```

A consumer that needs the ten reference angles reads them here, by
joining `DetailedModelDynamics.Equipment` to each injection's mRID.
Without the angles, all ten references would default to zero, which
describes a different network.

## Check

```
pscx check out/
```

```text
coherent: 11789 statements agree with their own re-emission
```

Coherent means every projection in the set, such as an EQ impedance
or an SSH injection, agrees with what the statements of record
produce. Run it before handing the set to any consumer: a set that has
been edited, or only partly copied, fails here with a report per
statement instead of giving a consumer wrong numbers without warning. The
[edit contract](edits.md) explains the partition behind it.

## Back to pscx

```
pscx read out/ --out roundtrip.pscx
```

The reconstruction rebuilds a `.pscx` from the documents, using your
own `master.pslx`. The case that comes back states the same network,
parameters and geometry as the documents. It is not a byte-identical
copy of the original file.

A coherent set reads back with no flag. A divergent set, for example
one with an edited EMT value as described in the
[edit contract](edits.md), is refused with the full report until you
choose a side. `--prefer-source` keeps the statements of record and
reports every ignored projection. `--prefer-interchange` applies
engine values where they can be traced back and names every burned
parameterization. Values that cannot be traced back, and flattened
instances that contradict each other, are refused under every flag.

As an end-to-end check, open `roundtrip.pscx` in PSCAD and resave it.
The project loads, and the resave reproduces the drawn network the
exchange carried.

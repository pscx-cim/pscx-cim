# EMTIOP and the pscx-cim Representation of PSCAD Models

## Summary

The pscx-cim framework represents a PSCAD case with the same detailed-model mechanism that the EMTIOP profile uses: a placement is a `cim:DetailedModelDynamics`, what a parameter means is a `cim:ParameterDescriptor` on the model type, and what the placement sets it to is a `cim:ParameterValue`. The framework does not, however, adopt the concrete type class that EMTIOP supplies, `NthAmDynamicModel`, because that class describes North American positive-sequence dynamics models and cannot state the identity of a PSCAD library definition. In place of that class, a type class of the framework's own, `emt:LibraryModelType`, is placed in the same slot. The design of EMTIOP is therefore retained, while one class and the serialization conventions are not. A consumer that reads the core CIM detailed-model classes can read the detailed-model statements of both profiles, and a bridge between the two reduces to a type-class mapping and the serialization conversions listed in Section 5.

## 1. The Document Groups

The framework writes a case as groups of documents, each of which answers one question about the case. The groups are listed in order of preference: a concept is written in the most standard group that can express it, and it moves to a later group only when the earlier one has no class for it.

| Group | Question answered | Documents | Vocabulary |
|---|---|---|---|
| Standard network | What is the electrical network? | EQ, TP, SC, SSH, OP | CGMES 3.0 |
| Detailed model | Which library model is placed where, and with what parameter values? | EMT | core CIM detailed-model classes, plus `emt:` where CIM has no class |
| Study and source record | How is the case run, and what did the author write? | EMTSIM, EMTSRC | `emt:` |
| Geometry | Where is each item drawn? | DL | CGMES Diagram Layout |

**The standard network documents** carry every component whose kind has a standard CGMES class, such as lines, transformers, machines and switches. A consumer that knows only CGMES can therefore build and solve the network from these five documents alone.

**The detailed model**, written in the EMT document, carries every drawn placement, including those already written in the standard documents. Each placement is a `cim:DetailedModelDynamics` with its `cim:ParameterValue` statements, typed by an `emt:LibraryModelType`. Where a placement is also standard equipment, `DetailedModelDynamics.Equipment` links the two views of the same object. Where it is not (for example, a control block or a user-defined component), the EMT document is the only place it appears. The EMT document is emitted as an add-on document; a consumer that does not need it may be given the standard documents only (`--no-add-on`).

**The study and source-record documents** record the solver settings (EMTSIM) and the case's own stated text (EMTSRC). The source record is required to rebuild a `.pscx` file, but a simulator does not need it.

**The geometry document** (DL) records positions and rotations, which are needed to redraw the case but not to simulate it.

## 2. The Detailed-Model Mechanism

Core CIM (Grid18 v15) defines a family of classes that describe a model by a type and a list of named parameters, without binding the model to a fixed role such as exciter or governor. The family is used as follows.

```
DetailedModelTypeDynamics   (a profile supplies the concrete subclass)
 ├─ ParameterDescriptor     name, sequenceNumber, engineeringUnit, typicalValue
 │
DetailedModelDynamics       one per placement; refers to its type
 ├─ Equipment               optional link to the standard equipment object
 └─ ParameterValue          value; refers to one DetailedModelDynamics and one ParameterDescriptor
```

`ParameterValue` is not an `IdentifiedObject`. It carries neither an mRID nor a name, because its descriptor already states what the parameter is. CGMES also defines a second mechanism, `ProprietaryParameterDynamics`, which is bound to user-defined exciters, governors and similar slots. A PSCAD component in general occupies none of those slots, so that mechanism does not apply.

## 3. Where EMTIOP Agrees

EMTIOP is the profile of the EMTHub project, which targets electromagnetic transient (EMT) model portability for inverter-based resources under IEEE P3743. In `emtiop.owl`, `NthAmDynamicModel` is declared as a subclass of `DetailedModelTypeDynamics`, and EMTIOP uses `DetailedModelDynamics`, `ParameterDescriptor` and `ParameterValue` with the same attributes listed above. The two representations therefore agree on the following points.

1. A model type is described once, and each placement refers to it.
2. A parameter is described by a descriptor on the type, and a placement states only the value.
3. The core CIM detailed-model family is used, not the slot-bound CGMES mechanism.
4. A profile that exchanges detailed models supplies its own concrete type class.

## 4. Where EMTIOP Does Not Fit

The disagreement lies in the concrete type class. `NthAmDynamicModel` carries four attributes, and none of them can be filled correctly for a general PSCAD definition.

| Attribute | Meaning in EMTIOP | Difficulty for a PSCAD definition |
|---|---|---|
| `nameKind` | The tool whose naming the model follows: `DYR` (PSS/E), `DYD` (PSLF), `AUX` (PowerWorld), `DGS` (PowerFactory) or `Other` | No value names PSCAD, and `Other` is documented for user-code models only. |
| `statusKind` | Whether the model is `allowed`, `deprecated` or `prohibited` for interconnection-wide studies in North America | The approved-model lists do not cover resistors, breakers, meters or control blocks, so any value would be an invented claim. |
| `modelKind` | The role of the model, such as machine, excitation system, governor, stabilizer or renewable resource | Passive elements, measurement and control-library blocks have no applicable value, and the attribute is required. |
| `closestStandardModel` | The nearest positive-sequence model in the CIM StandardModels package | Most PSCAD components have no positive-sequence counterpart, and the attribute is required. |

In addition, `NthAmDynamicModel` has no attribute for the modeling tool, the tool version or the qualified definition name. A placement typed by this class could not be joined back to the `.pslx` definition that it instantiates, and the reconstruction of a `.pscx` file would therefore fail. The model types that EMTHub ships are consistent with this reading: all of them are PSS/E `DYR` controller models.

The second EMTIOP extension for models, `IEEECigreAPI`, describes real-code models compiled against the IEEE/CIGRE DLL interface (interface version, sample time and library location). It is suitable for that subset of models, but it does not describe the PSCAD component library in general.

## 5. Why Not Adopt the EMTIOP Profile as a Whole

The profile target of the framework is CGMES. A change of target would require a second emission path for every document, which is not justified by the difference in one class. The EMTIOP profile also declares no AC switch class, so the switching devices of a PSCAD case would have no home in it. Finally, the serialization conventions differ, and an exchange between the two would require the following conversions:

1. a rewrite from the EMTIOP namespace base (`emtiop01v01`, `grid18v15`) to the `CIM100#` namespace used here;
2. a conversion between Turtle and RDF/XML;
3. a split of one merged file into the separate documents of this framework;
4. a change from bare-UUID subjects to `urn:uuid:` subjects;
5. a change from typed literals to plain literals, which PowSyBl requires.

## References

1. EMTHub repository, https://github.com/temcdrm/emthub
2. EMTHub documentation, https://emthub.readthedocs.io/en/latest/Overview.html
3. EMTIOP profile files: `emtiop.owl`, `Emtiop.xmi`, `profile_attributes.json`, `Dynamics.rst` (EMTHub repository, `emtiop/`)
4. EMTHub model types, `src/emthub/queries/detailed_model_types.json`

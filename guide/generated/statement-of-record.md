# Where each source quantity is stated

Generated from the reconstruction inventory (`tests/test_reconstruction_inventory.py`) by `tools/gen_guide_tables.py`; do not edit by hand.

Each row is a field of the source file's model and the ONE document that is its statement of record -- the document where an edit survives the trip back to `.pscx` (see the edit contract chapter). Everything else that repeats the quantity is a projection the emitter regenerates.

## EMTSRC (source)

the stated case: parameters verbatim, scripts, structure, forms, substitutions, retained payloads.

| source model field | stated as |
|---|---|
| `HirCanvas.components` | `emt:DetailedModelDynamics.ContainingDefinition` |
| `HirCanvas.opaque` | `emt:ToolPayload.IdentifiedObject` |
| `HirCanvas.params` | `emt:SourceParameterList` |
| `HirCanvas.wires` | `emt:DetailedModelDynamics.ContainingDefinition` |
| `HirComponent.defn` | `emt:DetailedModelDynamics.definitionReference` |
| `HirComponent.id` | `cim:IdentifiedObject.name` |
| `HirComponent.name` | `cim:IdentifiedObject.description` |
| `HirComponent.params` | `cim:ParameterValue` |
| `HirDefinition.classid` | `emt:IdentifiedObject.sourceClass` |
| `HirDefinition.form` | `emt:ParameterCategory`, `emt:ParameterCategory.ModelDefinition`, `emt:ParameterCategory.sequenceNumber` |
| `HirDefinition.form_name` | `cim:IdentifiedObject.description` |
| `HirDefinition.group` | `emt:ModelDefinition.group` |
| `HirDefinition.name` | `emt:ModelDefinition`, `emt:ModelDefinition.definitionName` |
| `HirDefinition.opaque` | `emt:ToolPayload`, `emt:ToolPayload.IdentifiedObject` |
| `HirDefinition.params` | `emt:SourceParameterList` |
| `HirDefinition.segments` | `emt:ModelScript.ModelDefinition`, `emt:ModelScript.sequenceNumber` |
| `HirFormCategory.condition` | `emt:ParameterCategory.condition` |
| `HirFormCategory.name` | `cim:IdentifiedObject.name` |
| `HirFormCategory.parameters` | `emt:ParameterDescriptor.ParameterCategory` |
| `HirFormCategory.visible` | `emt:ParameterCategory.visible` |
| `HirFormParameter.condition` | `emt:ParameterDescriptor.condition` |
| `HirFormParameter.content_type` | `emt:ParameterDescriptor.contentType` |
| `HirFormParameter.desc` | `cim:IdentifiedObject.description` |
| `HirFormParameter.dim` | `emt:ParameterDescriptor.dimension` |
| `HirFormParameter.group` | `emt:ParameterDescriptor.group` |
| `HirFormParameter.intent` | `emt:ParameterDescriptor.intent` |
| `HirFormParameter.maximum` | `emt:ParameterDescriptor.maximum` |
| `HirFormParameter.minimum` | `emt:ParameterDescriptor.minimum` |
| `HirFormParameter.name` | `cim:IdentifiedObject.name` |
| `HirFormParameter.opaque` | `emt:ToolPayload.IdentifiedObject` |
| `HirFormParameter.type` | `emt:ParameterDescriptor.parameterType` |
| `HirFormParameter.unit` | `cim:ParameterDescriptor.engineeringUnit` |
| `HirFormParameter.value` | `cim:ParameterDescriptor.typicalValue` |
| `HirLayer.state` | `emt:VisibilityLayer.state` |
| `HirParams.name` | `emt:SourceParameterList.parameterSetName` |
| `HirParams.rows` | `emt:MatrixRow`, `emt:MatrixRow.ParameterValue`, `emt:MatrixRow.sequenceNumber`, `emt:MatrixRow.value` |
| `HirParams.values` | `cim:ParameterValue`, `emt:SourceParameterList`, `emt:SourceParameterList.IdentifiedObject`, `emt:SourceParameterList.scope`, `emt:SourceParameterList.sequenceNumber`, `emt:SourceParameter`, `emt:SourceParameter.SourceParameterList`, `emt:SourceParameter.parameterName`, `emt:SourceParameter.value`, `emt:SourceParameter.sequenceNumber` |
| `HirPort.id` | `emt:ModelPort.sourceIdentifier` |
| `HirProject.definitions` | `emt:ModelDefinition.ModelProject` |
| `HirProject.opaque` | `emt:ToolPayload.IdentifiedObject` |
| `HirProject.substitutions` | `emt:Substitution.ModelProject` |
| `HirProject.target` | `emt:ModelProject.targetPlatform` |
| `HirProject.version` | `emt:ModelProject`, `emt:ModelProject.sourceFormatVersion` |
| `HirSegment.name` | `emt:ModelScript`, `emt:ModelScript.segmentName` |
| `HirSegment.text` | `emt:ModelScript.source` |
| `HirSubstitution.params` | `emt:Substitution`, `emt:Substitution.value`, `emt:Substitution.group`, `emt:Substitution.sequenceNumber` |
| `HirWire.defn` | `emt:DetailedModelDynamics.definitionReference` |
| `HirWire.hosted` | `emt:DetailedModelDynamics.HostingWire` |
| `HirWire.id` | `cim:IdentifiedObject.name` |
| `HirWire.name` | `cim:IdentifiedObject.description` |
| `HirWire.params` | `cim:ParameterValue` |
| `Opaque.element` | `emt:ToolPayload.content`, `emt:ToolPayload.sequenceNumber` |
| `Opaque.tag` | `emt:ToolPayload.payloadKind` |

## DL (diagram layout)

geometry: positions, vertices, rotations, layers.

| source model field | stated as |
|---|---|
| `HirComponent.orient` | `cim:DiagramObject.rotation`, `emt:DetailedModelDynamics.mirrored` |
| `HirComponent.x` | `cim:DiagramObject`, `cim:DiagramObject.Diagram`, `cim:DiagramObjectPoint.DiagramObject` |
| `HirComponent.y` | -- |
| `HirDefinition.canvas` | `cim:Diagram` |
| `HirLayer.name` | `cim:VisibilityLayer`, `cim:IdentifiedObject.name` |
| `HirPort.x` | `cim:Diagram`, `cim:DiagramObject`, `cim:DiagramObjectPoint`, `cim:DiagramObjectPoint.xPosition` |
| `HirPort.y` | `cim:DiagramObjectPoint.yPosition` |
| `HirProject.layers` | `cim:VisibilityLayer` |
| `HirWire.orient` | -- |
| `HirWire.vertices` | `cim:DiagramObjectPoint`, `cim:DiagramObjectPoint.sequenceNumber`, `cim:DiagramObjectPoint.xPosition`, `cim:DiagramObjectPoint.yPosition` |
| `HirWire.x` | -- |
| `HirWire.y` | -- |

## EMT (interchange)

the declared port interface -- the only engine-facing statements of record.

| source model field | stated as |
|---|---|
| `HirDefinition.ports` | `emt:ModelPort`, `emt:ModelPort.DetailedModelTypeDynamics`, `emt:ModelPort.portName`, `emt:ModelPort.sequenceNumber` |
| `HirPort.params` | `emt:ModelPort.internal`, `emt:ModelPort.condition`, `emt:ModelPort.mode`, `emt:ModelPort.dataType`, `emt:ModelPort.electricalType`, `emt:ModelPort.dimension` |

## EMTSIM (study)

the solver settings projection.

| source model field | stated as |
|---|---|
| `HirProject.params` | `emt:SimulationCase`, `emt:SourceParameterList` |


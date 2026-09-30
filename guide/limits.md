# Limits

This page lists everything a `pscx` document set leaves out, and a few
behaviours that can surprise you on real cases. A limit that this page
does not list is a bug; please report it.

The page has three parts: what the exchange never carries, what it
holds back for now, and what to expect on real cases.

## What the exchange never carries

### How a library model works inside

The documents say which library model each component uses, and they
give its parameters, ports, defaults and units. They do not contain the
model's equations or code, which stay in the PSCAD library. A simulator that reads the documents supplies its own
implementation of each model.

This is also why `pscx read` needs your own `master.pslx` to rebuild a
`.pscx` file.

### Radio links between two projects

A radio link joins a sender and a receiver by name, with no wire drawn
between them. When both ends are in the same case, the documents carry
the pair. When the receiver is in another project (`rccon=1` in
PSCAD), the documents of one case cannot describe the other project's
end, so that end is missing.

### What a simulation run produces

Some information exists only after a simulation has run: how the
breakers switch after the starting state, and the order of the
recorded output channels. The documents carry the starting state, in
the SSH document, and nothing after it. The `<output>` list inside a
`.pscx` file is left over from a past run and can disagree with the
drawing next to it, so it is not carried either. Your simulator
produces its own.

## What is held back for now

Each item below is held back until a specific event. The test suite
checks for that event on every run, so this page changes when it
happens.

### Bookkeeping for unused drawing layers

The documents record which drawing layer each element is on, and
whether each layer is enabled. They leave out what PSCAD stores for
layers that nothing uses: the `<Layer>` element's own attributes, the
empty `<layers>` list it saves, and their parameters.

**This changes when** a case uses an enabled or populated layer.

### Measurements with no equipment to attach to

A meter's measurement is written as a standard `cim:Analog`, and an
Analog has to be attached to equipment in the EQ document: "this is
the voltage at that node". Some measured quantities in the example
cases sit at nodes whose only equipment has no standard CIM class
yet. There is nothing in EQ to attach them to, so their Analogs are
left out, and the report counts each one as
`cim_measurement_unanchored`. The signals themselves are still in the
documents, in the signal graph, and every recording is listed.

**This changes when** that equipment maps to a standard class. The
measurements then appear with no other change.

### Power and angle measurements without a type

A multimeter that reports active power, reactive power or phase angle
needs a `measurementType` value for each of the three, taken from the
list that IEC 61970-452 defines. `pscx` does not yet read those values
from the list, and it does not guess them, because a wrong value
passes validation while naming the wrong quantity. These quantities
are left out, and the report counts each one as
`cim_measurement_untyped`. Every other meter output has its Analog.

**This changes when** the three values are read from the standard's
list into `pscx`. The quantities then appear with no other change.

## What to expect on real cases

### A case with only control blocks has an empty EQ document

A case with no electrical equipment has an empty EQ document. If you
write the standard documents only (`pscx emit --no-add-on`), nothing
then carries the project's name. The full set, which includes the EMT
document, still carries everything.

### The `emt:` addresses are names, not web pages

The `emt:` namespace and profile addresses under
`https://w3id.org/pscx-cim/` identify the vocabulary. They do not open
in a browser, because the redirect behind them is not registered yet.
Compare them as text, and do not try to fetch them.

### The output depends on your library version

The documents are determined by the case and your `master.pslx`: the
same case with the same library always gives the same documents. A
different PSCAD version ships a different library, and the documents,
and their digests can then differ.

### Substitution ids can change when PSCAD saves a file

PSCAD is not consistent about the ids of `<Sub>` elements when it
saves a file. One recorded save gave them new ids, and another left
elements without ids as they were. Nothing reads these ids, and the
files `pscx read` writes state none.

### Findings in the report are normal

`pscx emit` prints a report of findings to stderr, and every real case
produces some at the GAP and DEFECT levels. A GAP means the source did
not state something, or the standard cannot hold it, so `pscx` wrote a
stand-in and counted it. A DEFECT is a known flaw in the PSCAD library,
reproduced as it is. Neither is an error. ERROR means the output
describes the wrong circuit, and it is the only level at which
`pscx emit` exits with code 1 under the default `--fail-on gap`.

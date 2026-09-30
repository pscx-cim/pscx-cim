# Edits

Every quantity in a document set has exactly one **statement of
record**: the document where an edit to it survives the trip back to
`.pscx`. Every other copy of that quantity is a **projection**, which
the emitter regenerates from the statement of record. The
[statement-of-record table](generated/statement-of-record.md) lists
the document for every field; this page states the rules that follow
from it.

| document | role | edits that survive |
|---|---|---|
| EMTSRC (source) | statement of record for the case: parameters, scripts, module structure, forms, substitutions, settings and retained payloads | parameter values, script text, settings, structure |
| DL | statement of record for geometry | positions, vertices, rotations |
| EMT (interchange) | projection for engines: evaluated `numericValue`s, model types, the signal graph | a `numericValue`, applied only under `--prefer-interchange`; any other edit is refused or ignored with a report |
| EMTSIM (study) | projection of the solver settings | an adopted attribute, applied only under `--prefer-interchange` |
| standard five | projections of the stated parameters into standard CIM | none; no flag makes an EQ edit take effect |

## What happens to each kind of edit

**An edit to a statement of record** (EMTSRC or DL) is kept. The set
is now *stale*: its projections no longer match what the edited record
would emit. `pscx check` reports this, and `pscx read` refuses the set
by default until you either re-emit or choose a side, so an old
engine value cannot overwrite your source edit.

**An edit to a projection** (an EMT `numericValue` or an EMTSIM
setting) makes the set *divergent*. `pscx read` refuses it by default
and prints the report. With `--prefer-interchange`, the edit is
applied wherever it can be traced back to a stated parameter: the
parameter's text becomes your number, in its declared unit. If that
text was a `$()` parameterization rather than a literal, the reference
is lost. The tool calls this a *burn* and names every one.

**Some edits cannot be carried under any flag:**

- A value in the standard five, such as an EQ impedance. It is
  reported as unappliable and the read is refused, because no
  statement in a `.pscx` holds it.
- Different engine values for the flattened instances of one drawn
  element. A module placed several times is still drawn once, so one
  drawn parameter cannot hold two values. The read is refused and the
  element and its instances are named.

## Summary

Edit the statement of record and your edit survives. A set that
disagrees with itself is refused, with the full report. You then have
three ways forward:

1. keep the statements of record (`--prefer-source`), with every
   ignored projection reported;
2. keep the engine values (`--prefer-interchange`), applied where
   they can be traced back, with every burn and every refusal named;
3. make the edit in the statement of record instead, and re-emit.

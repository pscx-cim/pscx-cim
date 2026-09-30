# Command line

Three subcommands carry a case through the exchange: `emit`, `check`
and `read`. When one fails, it prints a report that says what it needs
from you.

`PSCAD_MASTER` names your library, and [getting started](start.md)
shows how to set it.

## pscx emit: a case into its nine documents

```
pscx emit CASE.pscx --out DIR
```

Prints the nine paths it wrote. Options:

- `--no-add-on` writes the standard CGMES documents only. Use it for a
  consumer that rejects the `emt:` profile URI; pandapower's `cim2pp`
  does, unless it is built with `ignore_errors=True`. You lose
  everything the add-on carries, including the project name of a case
  with no electrical equipment (see [known limits](limits.md)).
- `--no-source` leaves out the source record. The set still serves
  every engine, but it can no longer be read back into a `.pscx`.
- `--fail-on {none,defect,bias,gap,error}` exits with code 1 when a
  diagnostic is *strictly above* this severity. The default is `gap`,
  which fails on ERROR only. GAP and DEFECT findings are normal for
  every real case. They are printed to stderr grouped by severity, and
  each line gives a count and a key that say what was found and where.

Exit codes: `0` emitted; `1` a diagnostic above the `--fail-on`
threshold. The documents are written in both cases, and the report
says what the emitter could not state.

## pscx check: does a set agree with itself?

```
pscx check DIR            # or the nine paths
```

Rebuilds the case from the set, emits it again, and compares every
projection in the set with what the statements of record produce.
Both sides come from the same emitter, so a disagreement means the
documents were changed after emission.

Exit codes:

- `0`: the set is coherent. It prints
  `coherent: N statements agree with their own re-emission`.
- `1`: the set is divergent. It prints one line per statement: the
  document, the predicate, the value in the set, the re-emitted value,
  and a class:
  - `parameter`: a projection of a stated parameter that can be traced
    back;
  - `settings`: an adopted study attribute;
  - `unrepresentable`: flattened instances of one drawn element that
    disagree with each other;
  - `other-projection`: anything else, such as an EQ impedance.

  To fix it, edit the statement of record and re-emit, or choose a
  side with `pscx read`.
- `2`: the paths are not one case's document set. The message names
  what is missing or duplicated.

## pscx read: a set back into a .pscx

```
pscx read DIR --out CASE.pscx
pscx read DIR --out CASE.pscx --prefer-source
pscx read DIR --out CASE.pscx --prefer-interchange
```

Needs your `master.pslx`, because the documents name library models
but do not copy their implementations. A coherent set reads back with
no flag. A divergent set is refused with the same report `pscx check`
prints. You choose which side to keep with a flag:

- `--prefer-source` keeps the statements of record. Every ignored
  projection is reported as `reader_divergence_ignored`.
- `--prefer-interchange` applies the engine values wherever they can
  be traced back to a stated parameter: `emt:ParameterValue.numericValue`
  and the adopted `emt:SimulationCase` attributes. Where the replaced
  text was a `$()` parameterization, the reference is lost; each such
  *burn* is reported by name as `reader_expression_burned`. A value
  that cannot be traced back, such as an EQ impedance, is reported as
  unappliable and the read is still refused, because a `.pscx` has no
  place for it.
- Under every flag, different engine values for the flattened
  instances of one drawn element cannot be represented, since one
  drawn parameter cannot hold two values. The set is refused, and the
  element and its instances are named.

Exit codes: `0` written, with any burns printed first; `1` refused,
with the report; `2` not one case's document set.

## The whole loop

```
pscx emit case.pscx --out out/          # nine documents
pscx check out/                         # coherent: N statements agree ...
# ... edit out/case_EMTSRC.xml (a parameter's stated value) ...
pscx check out/                         # exit 1: the projections disagree
pscx read out/ --out case_back.pscx --prefer-source   # carry your edit
pscx emit case_back.pscx --out out2/    # re-emit; out2/ is coherent again
```

## pscx catalog: your library as a document

```
pscx catalog --out catalog.xml                # your configured master.pslx
pscx catalog LIBRARY.pslx --out catalog.xml   # any other library
pscx catalog --out catalog.xml --html catalog.html   # plus a browsable page
pscx catalog --out catalog.xml --shapes shapes.ttl   # plus per-model SHACL
```

Writes every model your library declares: parameters with their
descriptions, units, defaults, bounds and choice lists; categories
with their enabling conditions; and ports. It uses the same terms as
the case documents and the same join key,
`(modelingTool, toolVersion, definitionName)`. The catalog states what
each model declares, not how it is implemented.

`--html` also renders the catalog as one self-contained page. It lists
every model type with its parameters, choice lists and ports, and
notes what `pscx emit` additionally writes into standard CIM for each
one. The page is built by reading back the catalog file just written,
so it shows what the document contains.

`--shapes` also writes SHACL shapes generated from the library, one per
model that declares a bound or a choice list. Each shape targets the
`cim:ParameterValue`s whose type names that model and checks them
against the declaration of the same name: `emt:ParameterValue.numericValue`
against the declared minimum and maximum of a Real or Integer
parameter, and `cim:ParameterValue.value` against a Choice parameter's
entries. A name the form declares more than once accepts a value that
any of its declarations accepts. The targets are SPARQL targets, so a
validator needs SHACL Advanced Features (pyshacl's `advanced=True`).

Exit codes: `0` written, with a count of subjects; `1` the catalog
used an `emt:` term the published vocabulary does not declare, which
is a defect in the tool rather than in your data (please report it);
`2` no library was given and no `master.pslx` is configured.

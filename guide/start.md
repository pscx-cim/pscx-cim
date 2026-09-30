# Getting started

This chapter installs `pscx`, points it at your PSCAD library, and
converts a first case to CIM and back.

## Install

`pscx` needs Python 3.11 or later. From a clone of this repository,
install it with [uv](https://docs.astral.sh/uv/):

```
uv sync
uv run pscx --help
```

or with pip, into a virtual environment:

```
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # Linux, macOS, WSL
pip install .
pscx --help
```

The rest of this guide writes commands as `pscx`. Under uv, prefix
each one with `uv run`.

## Point pscx at your library

`pscx` reads the PSCAD master library, `master.pslx`, which is
downloadable from the PSCAD website. Set `PSCAD_MASTER` to the downloaded
file.

In PowerShell:

```
$env:PSCAD_MASTER = "C:\path\to\your\PSCAD\master.pslx"
```

In bash:

```
export PSCAD_MASTER=/path/to/your/PSCAD/master.pslx
```

There is no default, because the file can be saved anywhere. A command
that needs the library and cannot find one says so and exits.

## Convert a case

```
pscx emit CASE.pscx --out out/          # the nine CIM documents
pscx check out/                         # do the documents agree with each other?
pscx read out/ --out roundtrip.pscx     # the documents back into a .pscx
```

`pscx emit` prints each document it writes, followed by a report of
what the case leaves unstated. [Documents](documents.md) explains what
each of the nine documents holds.

## Where to go next

- [Documents](documents.md) describes the nine documents `pscx emit`
  writes for a case.
- [Command line](cli.md) states every option and exit code.
- [Walkthrough](walkthrough.md) takes the IEEE 39-bus case through the
  whole exchange.

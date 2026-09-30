# pscx-cim guide

`pscx` converts PSCAD `.pscx` cases into IEC 61970/61968 CIM document
sets, and converts those sets back into `.pscx` files.

PSCAD is a registered trademark of Manitoba Hydro International Ltd.
This project is not affiliated with or endorsed by Manitoba Hydro
International.

CIM+ takes its classes from three sources: the CGMES 3.0 profiles;
core CIM, for classes that CGMES 3.0 does not profile, such as
`ParameterDescriptor`; and the EMT extension profile, for what neither
standard has a class for.

The guide covers:

- installing `pscx` and converting a first case;
- which document to read for what;
- how each PSCAD kind maps into CIM;
- every command, option and exit code;
- a worked example that takes the IEEE 39-bus benchmark from `.pscx`
  to documents and back;
- what happens when you edit a document;
- the complete list of what the exchange does not carry;
- for developers, how the code is organized.

When a command cannot do something, it stops and prints a report
that says why. No command repairs, chooses or discards anything
without reporting it. The [CLI chapter](cli.md) explains each exit
code.

```{toctree}
:maxdepth: 2

start
documents
mapping
cli
walkthrough
edits
limits
developer/index
```

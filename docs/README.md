# docs/

Developer documentation. The repo root's `README.md` states what the project
is and how to run it, and `guide/` is the user guide.

- [architecture.md](architecture.md): the path a library and a case take
  through the code.
- [emtiop.md](emtiop.md): how the document groups pscx-cim writes relate
  to the EMTIOP profile.

## Reading this as a site

```
uv run tools/build_docs.py site
```

(`tools/build_docs.py guide` builds the guide with the gate's `-W -n`
flags; no argument builds both.)

The build is not a gate: it reads
`docs/` and writes only `docs/_build/`, which is ignored.

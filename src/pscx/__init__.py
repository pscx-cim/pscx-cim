"""pscx: PSCAD libraries and cases to CIM+, and back.

The API lives in the submodules, and importing the package imports none
of them, so a caller that reads a library does not pay for the case
pipeline. :mod:`pscx.pscad_topology` gathers the extraction front end
into one namespace.
"""


def __getattr__(name: str):
    # MASTER is served lazily by pscx.io (master.pslx is read on first
    # access, not at import).
    if name == "MASTER":
        from pscx.io import load_master

        return load_master()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MASTER"]

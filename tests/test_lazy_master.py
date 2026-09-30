"""master.pslx must be read on first MASTER access, not at import.

An eager module-scope load costs ~0.7s per process and makes pure-unit
tests of expr/units/preproc require a master.pslx they never use.
The oracle: with PSCAD_MASTER pointed at a nonexistent file, an
(attempted) read reports an ``unparseable_library`` diagnostic — its
presence on the bus timestamps exactly when the load happened.
Subprocesses give each test a fresh interpreter.
"""

import os
import subprocess
import sys

import pytest
from conftest import master_available

_ENV = {**os.environ, "PSCAD_MASTER": "/nonexistent/master.pslx"}


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        env=_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def test_importing_pscx_does_not_read_master():
    # Fails if importing pscx.units / pscx.expr / pscx (or anything they
    # pull in) reads master.pslx at import time.
    proc = _run(
        "import pscx.units, pscx.expr, pscx\n"
        "from pscx.diagnostics import DIAGNOSTICS\n"
        "assert not DIAGNOSTICS, DIAGNOSTICS.counts()\n"
    )
    assert proc.returncode == 0, proc.stderr


def test_master_attribute_resolves_on_first_access():
    # Fails if pscx.MASTER / pscx.io.MASTER stop resolving (AttributeError),
    # or if accessing them does not actually attempt the master load.
    proc = _run(
        "import pscx, pscx.io\n"
        "from pscx.diagnostics import DIAGNOSTICS\n"
        "assert not DIAGNOSTICS, 'load happened at import'\n"
        "m = pscx.MASTER\n"
        "assert DIAGNOSTICS.count('unparseable_library: master.pslx') == 1, \\\n"
        "    'access did not attempt the load'\n"
        "assert m == {}\n"
        "assert pscx.io.MASTER is m, 'accessors disagree'\n"
        "import pscx.pscad_topology as pt\n"
        "assert pt.MASTER is m, 'facade accessor disagrees'\n"
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(not master_available(), reason="PSCAD master.pslx not found")
def test_master_contents_unchanged_by_laziness():
    # Fails if the lazy accessor returns something other than the loaded
    # registry (e.g. an empty dict or a re-load per access).
    import pscx

    assert ("master", "resistor") in pscx.MASTER
    assert pscx.MASTER is pscx.MASTER


def test_the_cache_path_is_built_from_portable_calls(monkeypatch, tmp_path):
    # Fails if the cache path goes back to a POSIX-only call such as
    # os.getuid, which Windows does not have and PSCAD runs on Windows.
    import pscx.io

    library = tmp_path / "master.pslx"
    library.write_text("<project/>")
    monkeypatch.setattr(pscx.io, "MASTER_PSLX", str(library))
    monkeypatch.delenv("PSCX_CACHE", raising=False)
    for name in ("getuid", "geteuid", "getgid"):
        monkeypatch.delattr(os, name, raising=False)
    assert pscx.io._master_cache_path().endswith(".pickle")

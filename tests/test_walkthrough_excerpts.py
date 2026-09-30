"""The walkthrough chapter's excerpts, checked against a fresh run.

The chapter quotes short, elided excerpts of what pscx writes for the
IEEE 39-bus case. These tests re-run the chapter's commands and assert
that every quoted fence still appears in the output. A whole-line ``...``
in a fence elides any number of lines; an inline ``...`` elides the rest
of the matched region on that line. Without the case, every test here
skips with one named reason.
"""

import os
import re

import pytest
from conftest import REPO_ROOT
from test_ieee39_benchmark import CASE

CHAPTER = os.path.join(REPO_ROOT, "guide", "walkthrough.md")

pytestmark = pytest.mark.skipif(
    not os.path.exists(CASE),
    reason=f"IEEE 39 benchmark not present at {os.path.dirname(CASE)}")

#: The chapter quotes exactly this many document excerpts; the pin
#: moves with the chapter, deliberately, so a deleted fence cannot
#: silently shrink the coverage.
QUOTED_EXCERPTS = 13


@pytest.fixture(autouse=True)
def restore_the_global_bus():
    """Give back the process-global DIAGNOSTICS this module borrows.

    The console-transcript test clears the bus so the report it captures
    is one run's own findings. The bus
    also carries the once-per-process library defects that other tests
    read, so what was held is restored either way.
    """
    from pscx.diagnostics import DIAGNOSTICS, Diagnostics

    held = Diagnostics()
    held.extend(DIAGNOSTICS)
    try:
        yield
    finally:
        DIAGNOSTICS.clear()
        DIAGNOSTICS.extend(held)


def _fences(language):
    with open(CHAPTER) as handle:
        text = handle.read()
    return re.findall(rf"```{language}\n(.*?)```", text, re.DOTALL)


def _chunks(fence):
    """The fence's quoted line runs, split on whole-line elisions."""
    chunks, current = [], []
    for line in fence.strip().splitlines():
        if line.strip() == "...":
            if current:
                chunks.append(current)
            current = []
        else:
            current.append(line.strip())
    if current:
        chunks.append(current)
    return chunks


def _pattern(chunk):
    return "\n".join(
        "[^\n]*".join(re.escape(part) for part in line.split("..."))
        for line in chunk)


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    from pscx.cim import emit_files
    from pscx.elaborate import flatten

    out = tmp_path_factory.mktemp("walkthrough")
    return list(emit_files(flatten(CASE), out))


@pytest.mark.slow
def test_every_quoted_excerpt_appears_in_a_fresh_emission(emitted):
    documents = ["\n".join(line.strip() for line in
                           path.read_text().splitlines())
                 for path in emitted]
    fences = _fences("xml")
    assert len(fences) == QUOTED_EXCERPTS
    for fence in fences:
        chunks = _chunks(fence)
        assert any(all(re.search(_pattern(chunk), document)
                       for chunk in chunks)
                   for document in documents), fence.splitlines()[0]


@pytest.mark.slow
def test_the_console_transcripts_are_fresh(tmp_path, capsys):
    from pscx import cli
    from pscx.diagnostics import DIAGNOSTICS

    DIAGNOSTICS.clear()
    out = tmp_path / "out"
    assert cli.main(["emit", CASE, "--out", str(out)]) == 0
    report = capsys.readouterr()

    paths_fence = next(f for f in _fences("text") if "_EQ.xml" in f)
    stated = [line.rsplit("/", 1)[-1]
              for line in paths_fence.strip().splitlines()]
    printed = [os.path.basename(line)
               for line in report.out.strip().splitlines()]
    assert stated == printed

    diagnostics = next(f for f in _fences("text") if "DIAGNOSTICS" in f)
    quoted = "\n".join(line.strip()
                       for line in diagnostics.strip().splitlines())
    fresh = "\n".join(line.strip() for line in report.err.splitlines())
    assert quoted in fresh

    assert cli.main(["check", str(out)]) == 0
    coherent = next(f for f in _fences("text") if "coherent:" in f)
    assert capsys.readouterr().out.strip() == coherent.strip()

    # the chapter's closing claim: a coherent set reads back with no flag
    assert cli.main(["read", str(out),
                     "--out", str(tmp_path / "roundtrip.pscx")]) == 0



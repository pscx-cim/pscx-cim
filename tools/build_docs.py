"""Build the repo's two sphinx trees with one command.

    uv run tools/build_docs.py          # both trees
    uv run tools/build_docs.py site     # docs/  -> docs/_build/html
    uv run tools/build_docs.py guide    # guide/ -> guide/_build/html

``site`` renders ``docs/`` for reading; that build is not a gate.
``guide`` first rewrites ``guide/generated/`` via gen_guide_tables, then
builds with the same ``-W -n`` flags ``tests/test_guide.py`` enforces,
so a green build here is a green gate there.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "tools"))


def _sphinx(source: Path, out: Path, *flags: str) -> int:
    cmd = [sys.executable, "-m", "sphinx", "-b", "html", *flags, str(source), str(out)]
    return subprocess.run(cmd, check=False).returncode


def build_site() -> int:
    return _sphinx(REPO_ROOT / "docs", REPO_ROOT / "docs" / "_build" / "html")


def build_guide() -> int:
    import gen_guide_tables

    rc = gen_guide_tables.main([])
    if rc:
        return rc
    return _sphinx(
        REPO_ROOT / "guide", REPO_ROOT / "guide" / "_build" / "html", "-W", "-n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the docs/ site and the guide."
    )
    parser.add_argument(
        "target", nargs="?", default="all", choices=("site", "guide", "all")
    )
    args = parser.parse_args(argv)
    rc = 0
    if args.target in ("guide", "all"):
        rc = build_guide() or rc
    if args.target in ("site", "all"):
        rc = build_site() or rc
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

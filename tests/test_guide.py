"""The guide is generated where it can rot, and its build is a
gate: stale tables and broken cross-references both fail here, so the
guide the consumer team opens is the guide the code actually implements.
"""

import os
import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))


def test_the_generated_tables_are_fresh():
    import gen_guide_tables

    assert gen_guide_tables.main(["--check"]) == 0, (
        "guide/generated/ is stale; run python tools/gen_guide_tables.py")


def test_the_guide_builds_with_warnings_as_errors(tmp_path):
    # -W turns every warning into an error and -n (nitpicky) flags every
    # reference sphinx cannot resolve, so a renamed chapter or a broken
    # cross-reference fails the suite instead of shipping a dead link.
    result = subprocess.run(
        [sys.executable, "-m", "sphinx", "-b", "html", "-W", "-n",
         os.path.join(REPO_ROOT, "guide"), str(tmp_path / "html")],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"sphinx-build failed:\n{result.stdout}\n{result.stderr}")

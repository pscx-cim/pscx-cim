"""Sphinx configuration for `docs/`.

`_prepare` rewrites a link into `../guide/` before MyST reads it. That link
names a file outside this tree, so it becomes a link to the same page of the
guide built beside this site by `tools/build_docs.py`, and the source link
keeps working on GitHub too.

Build:

    uv run --group docs sphinx-build -b html docs docs/_build/html
"""

from __future__ import annotations

import re

project = "pscx-cim"
extensions = ["myst_parser", "sphinxcontrib.mermaid"]
#: a plain mermaid fence renders here and on GitHub alike
myst_fence_as_directive = ["mermaid"]
#: a diagram draws at its own size, not squeezed into a fixed-height frame
mermaid_height = "auto"

root_doc = "index"
exclude_patterns = ["_build", "README.md"]

html_theme = "pydata_sphinx_theme"
html_title = "pscx-cim"

_GUIDE_LINK = re.compile(r"\[([^\]]*)\]\(\.\./guide/([\w/-]+)\.md\)")


def _prepare(app, docname, source):
    # docs/_build/html/<docname>.html climbs to the repo root, then descends
    # into guide/_build/html.
    up = "../" * (docname.count("/") + 3)

    def guide(match):
        return f'<a href="{up}guide/_build/html/{match.group(2)}.html">{match.group(1)}</a>'

    source[0] = _GUIDE_LINK.sub(guide, source[0])


def setup(app):
    app.connect("source-read", _prepare)

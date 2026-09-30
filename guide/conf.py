"""Sphinx configuration for the guide.

A reader starting from these pages alone can install pscx, emit a case,
check it and read it back. The developer section follows a case through
the code. Built with warnings as errors by
``tests/test_guide.py``, so a broken cross-reference fails a gate rather
than shipping.

    uv run --group docs sphinx-build -b html -W -n guide guide/_build/html
"""

project = "pscx-cim guide"
extensions = ["myst_parser", "sphinxcontrib.mermaid"]
#: a plain mermaid fence renders here and on GitHub alike
myst_fence_as_directive = ["mermaid"]
#: a diagram draws at its own size, not squeezed into a fixed-height frame
mermaid_height = "auto"

root_doc = "index"
exclude_patterns = ["_build"]

html_theme = "pydata_sphinx_theme"
html_title = "pscx-cim guide"
#: every chapter fits in the top bar, so none is folded into "More"
html_theme_options = {"header_links_before_dropdown": 8}

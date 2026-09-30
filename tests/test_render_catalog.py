"""The rendered page is a view of the written catalog document.

``pscx catalog --html`` decodes the written document through
``read_catalog`` and joins the emitter's own rule tables at render
time, so these tests hold the page to exactly what the document and
the rules state: every declared shape of the fixture library surfaces,
condition-gated duplicate declarations stay distinct rows, choice
payloads come back as ``value = label`` entries, help text and visibility
expressions have their own columns, each mapping
treatment annotates as the rules say, and over master.pslx every
definition the catalog states renders.
"""

import html

import pytest
from conftest import master_available
from test_catalog import _fixture_library


def _fixture_page(tmp_path) -> str:
    from pscx.catalog import write_catalog
    from pscx.catalog_html import render_catalog_html

    project, registry = _fixture_library(tmp_path)
    catalog_path = str(tmp_path / "catalog.xml")
    write_catalog(project, registry, catalog_path)
    page_path = tmp_path / "catalog.html"
    assert render_catalog_html(catalog_path, str(page_path)) == 1
    return page_path.read_text()


def test_every_declared_shape_of_the_fixture_surfaces(tmp_path):
    from pscx.catalog import EMT_LIBRARY_PROFILE_URI

    page = _fixture_page(tmp_path)
    # each stated field has a column, and an empty element contributes
    # no text, so the tags themselves never reach the page
    for heading in (
        "choices", "help", "visible when", "group", "content type",
        "dimension", "intent", "pattern", "error", "table",
    ):
        assert f"<th>{heading}</th>" in page
    assert "0 = Plain<br>1 = Fancy" in page
    assert "Which kind of widget" in page
    assert "G == 1" in page
    assert "Variant" in page
    assert "Letters only" in page
    assert "^[A-Z]+$" in page
    for tag in ("help", "vis", "regex", "error_msg", "column", "choice"):
        assert f"&lt;{tag}" not in page
    assert "which kind of widget" in page
    # the bounded Real keeps its unit, bounds and default
    assert "<td>pu</td>" in page
    assert "<td>10</td>" in page
    assert "<td>1.0</td>" in page
    # one name declared twice as condition-gated variants stays two rows
    assert page.count("<td>G</td>") == 2
    assert "Kind == 1" in page
    # the declared port
    assert "<td>A</td>" in page
    # the document's identity and join key
    assert EMT_LIBRARY_PROFILE_URI in page
    assert "definitionName" in page
    # self-contained interactivity: collapsible sections, the filter,
    # and both color schemes, all inline
    assert "<details" in page
    assert "type=search" in page
    assert "prefers-color-scheme" in page
    # the filter key carries parameter names and descriptions (lowered),
    # so a parameter search finds its declaring type
    assert "gain" in page
    # the page uses the window width. A table wider than the window
    # keeps its horizontal bar pinned to the bottom of the screen.
    assert "viewport" in page
    assert "class=scroll-bar" in page
    assert "max-width" not in page
    assert "max-height" not in page


def test_each_mapping_treatment_annotates_as_the_rules_state():
    from pscx.catalog_html import catalog_html

    def bare():
        return {
            "description": None,
            "modelingTool": "PSCAD",
            "toolVersion": "5",
            "categories": [],
            "descriptors": [],
            "ports": [],
        }

    surface = {
        f"master:{kind}": bare()
        for kind in (
            "resistor",
            "breaker1",
            "TLine",
            "ammeter",
            # a meter whose quantities depend on its form selectors, so
            # the annotation has more than one row to state
            "multimeter",
            "ground",
            "short",
            "g6p200_2",
            "widget",
        )
    }
    page = catalog_html(surface)
    # the passives' annotation states the ground-role split, not just
    # the series class, and the breaker states its ground-side class
    assert "cim:LinearShuntCompensator" in page
    assert "cim:GroundingImpedance" in page
    assert "cim:GroundDisconnector" in page
    # the index column carries the short label, so a mapped type is
    # findable without scrolling the sections
    assert "standard projection" in page
    # the labels the guide's mapping chapter defines are glossed on the
    # page itself, so it stands alone
    assert "DETAILED MODEL</b> = the kind is specific to PSCAD" in page
    assert "cim:Analog per active output" in page
    assert "triaged STANDARD: cim:CsConverter" in page
    assert "cim:EquivalentBranch" in page  # standard-five projection
    assert "cim:ACLineSegment" in page  # hosting wire
    # a meter's quantities are named per #OUTPUT, not one per kind: the
    # ammeter states its single one, the multimeter several of differing
    # type, which a kind-keyed annotation could not have shown
    assert "Name as LineCurrent in A" in page
    assert "CurI as LineCurrent in A" in page
    assert "VolI as Voltage in V" in page
    assert "topology role" in page  # structural
    assert "cim:TopologicalNode" in page  # ideal zero impedance
    assert "cim:CsConverter" in page  # triaged, with candidate
    assert "EMT statement alone" in page  # unmapped, untriaged


@pytest.mark.slow
@pytest.mark.skipif(
    not master_available(), reason="PSCAD_MASTER names no master.pslx here"
)
def test_every_definition_the_master_catalog_states_renders(tmp_path):
    from pscx.catalog import read_catalog, write_catalog
    from pscx.catalog_html import render_catalog_html
    from pscx.io import load_master, master_project

    catalog_path = str(tmp_path / "catalog.xml")
    write_catalog(master_project(), load_master(), catalog_path)
    page_path = tmp_path / "catalog.html"
    rendered = render_catalog_html(catalog_path, str(page_path))
    page = page_path.read_text()
    surface = read_catalog(catalog_path)
    assert rendered == len(surface)
    for qualified in surface:
        assert f'id="{html.escape(qualified, quote=True)}"' in page

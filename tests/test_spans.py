"""Loader diagnostics point at a file and a line.

Each fixture below is written out with its lines numbered in a comment, so
the asserted line number is hand-derived from the text beside it rather
than copied from a run.
"""


def _load(tmp_path, name, text):
    """Write a library fixture and load it, returning fresh diagnostics."""
    from pscx.diagnostics import Diagnostics
    from pscx.io import load_definitions

    path = tmp_path / name
    path.write_text(text)
    bus = Diagnostics()
    import pscx.io as io_module

    saved, io_module.DIAGNOSTICS = io_module.DIAGNOSTICS, bus
    try:
        load_definitions(str(path), {})
    finally:
        io_module.DIAGNOSTICS = saved
    return bus


#  1 <?xml version="1.0" encoding="UTF-8"?>
#  2 <project name="broken">
#  3   <Definition name="widget">
#  4     <Port x="0" y="0"/>
#  5   </Definitionn>
#  6 </project>
MALFORMED_LIBRARY = """\
<?xml version="1.0" encoding="UTF-8"?>
<project name="broken">
  <Definition name="widget">
    <Port x="0" y="0"/>
  </Definitionn>
</project>
"""

#: The ports sit under ``<graphics>`` because that is where a definition
#: states them in master.
#: The loader models a definition rather than scraping one, so a
#: ``<Port>`` written anywhere else is a shape it does not read, and a
#: fixture that stated one would be testing a document PSCAD does not
#: write.
#  1 <?xml version="1.0" encoding="UTF-8"?>
#  2 <project name="fixture">
#  3   <Definition name="widget">
#  4     <graphics>
#  5       <Port x="0" y="0">
#  6         <paramlist>
#  7           <param name="name" value="A"/>
#  8         </paramlist>
#  9       </Port>
# 10       <Port x="oops" y="0">
# 11         <paramlist>
# 12           <param name="name" value="B"/>
# 13         </paramlist>
# 14       </Port>
# 15     </graphics>
# 16   </Definition>
# 17 </project>
BAD_PORT_LIBRARY = """\
<?xml version="1.0" encoding="UTF-8"?>
<project name="fixture">
  <Definition name="widget">
    <graphics>
      <Port x="0" y="0">
        <paramlist>
          <param name="name" value="A"/>
        </paramlist>
      </Port>
      <Port x="oops" y="0">
        <paramlist>
          <param name="name" value="B"/>
        </paramlist>
      </Port>
    </graphics>
  </Definition>
</project>
"""


def test_an_unparseable_library_reports_the_line_the_parse_failed_on(tmp_path):
    # The closing tag on line 5 is `</Definitionn>`. Fails if the span is
    # absent, names the wrong file, or reports the start of the document
    # instead of where the parser gave up -- which is the whole difference
    # between "master.pslx is broken" and "master.pslx is broken HERE".
    bus = _load(tmp_path, "broken.xml", MALFORMED_LIBRARY)

    record, = bus.records()
    assert record.code == "unparseable_library"
    assert record.span.file == "broken.xml"
    assert record.span.line == 5
    assert str(record.span) == "broken.xml:5"


def test_a_bad_port_reports_the_line_of_that_port_not_of_the_definition(
        tmp_path):
    # Port B on line 10 has x="oops"; port A on line 5 is fine. Fails if the
    # span points at the enclosing Definition (line 3), at the document, or
    # at the first port -- each of which would be a plausible-looking
    # number that sends a reader to the wrong element.
    bus = _load(tmp_path, "ports.xml", BAD_PORT_LIBRARY)

    record, = bus.records()
    assert record.code == "port_bad_coords"
    assert record.span.line == 10
    assert record.detail == "widget.B"


def test_a_well_formed_library_reports_nothing(tmp_path):
    # The negative control: the fixture above differs from this one by a
    # single attribute, so a span test that passed on both would be
    # measuring nothing.
    good = BAD_PORT_LIBRARY.replace('x="oops"', 'x="10"')
    assert _load(tmp_path, "ports.xml", good).records() == []


def test_a_library_that_declares_nothing_names_the_file_and_no_line(tmp_path):
    # The one loader failure with no element to point at: the document is
    # well-formed and simply holds no Definition, so there is no line that
    # is more the fault than any other and reporting one would fabricate
    # it. The file alone is the honest span. Fails if this state passes
    # silently -- which is what left an empty registry to announce itself
    # only as tens of thousands of `unresolved_definition` records, none
    # of which names the library.
    bus = _load(tmp_path, "hollow.pslx",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<project name="hollow"/>\n')

    record, = bus.records()
    assert record.code == "empty_library"
    assert record.detail == "hollow.pslx"
    assert (record.span.file, record.span.line) == ("hollow.pslx", None)
    assert str(record.span) == "hollow.pslx"


def test_a_malformed_case_reports_where_the_parse_failed(tmp_path, monkeypatch):
    # The same treatment on the case side, where the failure is total: a
    # case that does not parse yields no canvases at all, so the line
    # number is the only thing distinguishing "this file is not a case"
    # from "this file has a typo on line 5".
    from pscx import nets
    from pscx.diagnostics import Diagnostics

    case = tmp_path / "broken.pscx"
    case.write_text(MALFORMED_LIBRARY)
    bus = Diagnostics()
    monkeypatch.setattr(nets, "DIAGNOSTICS", bus)
    assert list(nets.extract(str(case))) == []

    record, = bus.records()
    assert record.code == "unparseable_case"
    assert (record.span.file, record.span.line) == ("broken.pscx", 5)


"""Running pyshacl the way a CIMXML instance file must be run.

Shared by the standard-profile tests (ENTSO-E shapes) and the emt:
extension tests (our own shapes), because the two subtleties it handles
are properties of CIMXML itself, not of either shape set: instance files
carry PLAIN literals, which ``pscx.cimxml.coerce_datatypes`` types, and
conformance means no sh:Violation.
"""

import functools


@functools.cache
def _translated(text: str, base, namespaces):
    """One SPARQL query string, parsed and translated to algebra once.

    Keyed on everything ``SPARQLProcessor.query`` derives the algebra
    from, so a hit is the same translation the uncached path would have
    produced.
    """
    from rdflib.plugins.sparql.algebra import translateQuery
    from rdflib.plugins.sparql.parser import parseQuery

    return translateQuery(parseQuery(text), base,
                          dict(namespaces) if namespaces else None)


@functools.cache
def install_query_cache() -> bool:
    """Make rdflib reuse a parsed SPARQL query instead of reparsing it.

    A SHACL constraint's query is one string evaluated once per focus
    node, and rdflib's processor pyparses it EVERY time. On an EQ
    document the reparsing alone is a third of the run. Reusing a
    translated query across evaluations is rdflib's own `prepareQuery`
    contract -- `evalQuery` reads `query.algebra` and builds a fresh
    context per call -- so this changes what the suite spends, never what
    it concludes: the reports are identical.

    Installed by :func:`validate` rather than at import, so a test that
    only reads shapes leaves rdflib alone. Cached, so it installs once.
    """
    from rdflib.plugins.sparql import processor

    original = processor.SPARQLProcessor.query

    @functools.wraps(original)
    def query(self, strOrQuery, initBindings=None, initNs=None, base=None,
              DEBUG=False):
        if isinstance(strOrQuery, str):
            strOrQuery = _translated(
                strOrQuery, base,
                tuple(sorted(initNs.items())) if initNs else None)
        return original(self, strOrQuery, initBindings, initNs, base, DEBUG)

    processor.SPARQLProcessor.query = query
    return True


@functools.cache
def shapes(filenames: tuple):
    import rdflib

    graph = rdflib.Graph()
    for name in filenames:
        graph.parse(name, format="turtle")
    return graph


def violating_shapes(report: str) -> set:
    """The shapes a report blames at sh:Violation severity, by name.

    Severity has to be read alongside the shape rather than after it: the
    600-2 EQ shapes report a Substation count of one as a WARNING by
    design, and a test that scrapes every "Source Shape:" line sees it
    beside a real violation and cannot tell them apart.
    """
    found, severity = set(), None
    for line in report.splitlines():
        text = line.strip()
        if text.startswith("Severity:"):
            severity = text.split(":", 1)[1].strip()
        elif text.startswith("Source Shape:"):
            if severity == "sh:Violation":
                found.add(text.split(":", 1)[1].strip())
            severity = None
    return found


def validate(data_graph, shape_files):
    """``(conforms, report)``, where conforming means no sh:Violation.

    pyshacl's own conforms flag is false on ANY result, including
    sh:Warning/sh:Info assessment notes -- the 456 shapes
    carry "please assess" Warnings by design, and a document can be
    fully conformant while collecting them.
    """
    import pyshacl
    from rdflib.namespace import SH

    from pscx.cimxml import coerce_datatypes

    install_query_cache()
    shape_graph = shapes(tuple(shape_files))
    _conforms, results, text = pyshacl.validate(
        coerce_datatypes(data_graph, shape_graph), shacl_graph=shape_graph,
        advanced=True,
    )
    if isinstance(results, pyshacl.errors.ValidationFailure):
        # pyshacl RETURNS this where a constraint cannot be evaluated at
        # all rather than raising it, and the report graph it stands in
        # for has no triples to read. A constraint that could not run is
        # not a constraint that passed, so it is non-conformance with the
        # validator's own words as the report -- never a silent skip, and
        # never an AttributeError on the caller's next line.
        return False, f"validation failed: {results}"
    violations = set(results.subjects(SH.resultSeverity, SH.Violation))
    return not violations, text

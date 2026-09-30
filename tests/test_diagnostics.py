"""The diagnostic record and the severity taxonomy.

The taxonomy is not a generic error/warn/info trio: it names the four
things this project has to tell apart, and the
tests below are written against those four meanings rather than against
an enum's arithmetic.
"""

import pytest


def test_a_diagnostic_carries_a_span_and_a_provenance_independently():
    # A loader knows a file and a line; a pass deep in elaboration knows an
    # instance and an element and no single source line. The record must
    # carry either without fabricating the other, so this fails if span and
    # provenance are collapsed into one field or if one is made mandatory.
    from pscx.diagnostics import Diagnostics, Provenance, Severity, Span

    bus = Diagnostics()
    bus.emit("unparseable_library", "master.pslx",
             span=Span("master.pslx", 40195))
    bus.emit("cim_basevoltage_unknown",
             provenance=Provenance(case="Cigre_BM1", canvas="Main",
                                   element="17"))

    loader, deep = bus.records()
    assert loader.code == "unparseable_library"
    assert loader.severity is Severity.ERROR
    assert (loader.span.file, loader.span.line) == ("master.pslx", 40195)
    assert loader.provenance is None

    assert deep.span is None
    assert deep.provenance.canvas == "Main"
    assert deep.provenance.element == "17"
    # the message is the code's registered reason, not the caller's detail
    assert "placeholder" in deep.message.lower()


def test_the_flat_key_joins_code_and_detail():
    # The counter key is code + detail, which is what every existing pin
    # and oracle reads. Fails if the glue changes shape -- e.g. if a code
    # with no detail grows a trailing separator, which would silently
    # rename 60-odd pinned keys at once.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    bus.emit("lineconst_rowdefn_missing", "(null):T11-c1")
    bus.emit("lineconst_resistance_dc_only")
    assert [r.key for r in bus.records()] == [
        "lineconst_rowdefn_missing: (null):T11-c1",
        "lineconst_resistance_dc_only",
    ]


def test_aggregation_by_code_survives_differing_provenance():
    # Two emissions of one code from two elements are ONE finding with a
    # count of two and TWO records: the count is what a pin asserts, and
    # the records say which element. Fails if aggregating
    # discards provenance, or if distinct provenance splits the count.
    from pscx.diagnostics import Diagnostics, Provenance

    bus = Diagnostics()
    for element in ("17", "23", "41"):
        bus.emit("cim_breaker_state_default",
                 provenance=Provenance(case="c", element=element))
    bus.emit("cim_basevoltage_unknown", count=5)

    assert bus.by_code() == {"cim_breaker_state_default": 3,
                             "cim_basevoltage_unknown": 5}
    assert len(bus.records()) == 4
    assert {r.provenance.element for r in bus.records()
            if r.code == "cim_breaker_state_default"} == {"17", "23", "41"}


def test_a_count_above_one_aggregates_into_one_record():
    # lineconst reports 153 dc-only phases as `count=len(per_metre)`, not
    # as 153 calls. Fails if count is ignored (the pin would read 1) or if
    # it is expanded into 153 indistinguishable records.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    bus.emit("lineconst_resistance_dc_only", count=153)
    assert bus.by_code()["lineconst_resistance_dc_only"] == 153
    assert len(bus.records()) == 1
    assert bus.records()[0].count == 153


def test_a_metric_is_not_a_diagnostic():
    # The split the whole design rests on. `cim_mapped: resistor` = 392 says
    # what the mapping COVERED; `cim_basevoltage_unknown` = 100 says a
    # value is missing. Conflated, "warnings empty" is not a usable exit
    # criterion, because it can never reach zero. Fails if a
    # metric can be emitted as a diagnostic, which would put coverage
    # counts back on the severity bus.
    from pscx.diagnostics import CATALOG, Diagnostics

    # `cim_unmapped_device` is here because it is a metric and not a
    # diagnostic: a hosted device with no standard class is carried by a detailed
    # model like any other absorbed kind, so what the counter says is what the
    # mapping covered, not that something is missing.
    for metric in ("cim_mapped", "cim_measurement", "cim_zero_impedance",
                   "emt_electrical_model", "cim_basevoltage_from_island",
                   "cim_unmapped_device"):
        assert metric not in CATALOG, metric
    with pytest.raises(KeyError):
        Diagnostics().emit("cim_mapped", "resistor")


def test_count_reads_one_flat_key():
    # What a pin asks: how many of THIS key. Fails if a key
    # that was never emitted raises instead of reading 0 -- every pin that
    # asserts a count is zero depends on that.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    bus.emit("cim_unmapped", "peswitch", count=251)
    assert bus.count("cim_unmapped: peswitch") == 251
    assert bus.count("cim_unmapped: resistor") == 0
    assert bus.count("cim_basevoltage_unknown") == 0


def test_merging_a_model_bus_into_the_run_keeps_both_readable():
    # Emission builds a per-model bus so a caller can ask what THIS case
    # reported; the run needs the union to decide an exit code. Fails if
    # merging aliases the two, which would let one case's findings appear
    # in the next case's model.
    from pscx.diagnostics import Diagnostics

    run, model = Diagnostics(), Diagnostics()
    run.emit("lineconst_dc_line", "DCTL")
    model.emit("cim_basevoltage_unknown")
    run.extend(model)
    assert run.by_code() == {"lineconst_dc_line": 1,
                             "cim_basevoltage_unknown": 1}
    assert model.by_code() == {"cim_basevoltage_unknown": 1}
    model.emit("cim_breaker_state_default")
    assert run.count("cim_breaker_state_default") == 0


def test_severity_orders_by_how_wrong_the_emitted_value_is():
    # The whole point of a four-way taxonomy: a consumer must be able to
    # tell a stand-in from a biased estimate. Ordering, most severe first:
    # ERROR (results wrong, nothing marks which), GAP (the emitted value is
    # a fabricated stand-in), BIAS (a real value, wrong in a known
    # direction), DEFECT (a shipped-library bug reproduced faithfully --
    # expected and pinned, so never a reason to fail a run).
    from pscx.diagnostics import Severity

    assert (Severity.ERROR > Severity.GAP > Severity.BIAS > Severity.DEFECT)
    assert sorted(Severity, reverse=True) == [
        Severity.ERROR, Severity.GAP, Severity.BIAS, Severity.DEFECT]


def test_bad_token_codes_are_not_catalogued_as_defects():
    # DEFECT is the one severity a run is never failed for, so what earns
    # it has to be a shipped bug this project reproduces faithfully. A
    # token the grammar cannot read at all is not that: the `$`-less node
    # token resolves as its author intended and the dangling `$name` is
    # syntactically valid, while `branch_bad_nodes` and `branch_bad_value`
    # fire where the branch cannot be read. Fails if either bad-token code
    # is catalogued DEFECT, which would make it unfailable.
    from pscx.diagnostics import CATALOG, Severity

    assert CATALOG["branch_bare_node"].severity is Severity.DEFECT
    assert CATALOG["branch_dangling_value"].severity is Severity.DEFECT
    assert CATALOG["branch_bad_nodes"].severity is Severity.ERROR
    assert CATALOG["branch_bad_value"].severity is Severity.ERROR


def test_a_threshold_query_partitions_the_bus_in_both_directions():
    # What the exit code will be driven by. Asserted symmetrically -- too
    # many AND too few -- because a threshold that admitted everything and
    # one that admitted nothing would both pass a one-sided check.
    from pscx.diagnostics import Diagnostics, Severity

    bus = Diagnostics()
    bus.emit("unparseable_library", "master.pslx")     # ERROR
    bus.emit("cim_basevoltage_unknown")                # GAP
    bus.emit("lineconst_resistance_dc_only")           # BIAS
    bus.emit("branch_dangling_value", "mmc_FullCell")  # DEFECT

    assert [r.code for r in bus.above(Severity.GAP)] == ["unparseable_library"]
    assert len(bus.above(Severity.DEFECT)) == 3
    assert len(bus.above(Severity.ERROR)) == 0
    assert bus.worst() is Severity.ERROR

    quiet = Diagnostics()
    quiet.emit("branch_dangling_value", "mmc_FullCell")
    assert quiet.worst() is Severity.DEFECT
    assert quiet.above(Severity.DEFECT) == []


def test_every_code_the_catalog_declares_states_a_reason():
    # A pinned number whose reason lives only in a reviewer's head is
    # exactly what the catalog exists to prevent. Fails if a code is
    # registered with
    # an empty or placeholder message, or with a severity outside the
    # taxonomy.
    from pscx.diagnostics import CATALOG, Severity

    assert len(CATALOG) > 50
    for code, kind in CATALOG.items():
        assert isinstance(kind.severity, Severity), code
        assert len(kind.message.split()) >= 4, code
        assert not kind.message.lower().startswith(("todo", "tbd", "n/a")), code


def test_an_unregistered_code_is_refused():
    # The negative control for the rule above: the catalog is only a
    # complete index of what this program can report if emitting an
    # uncatalogued code is impossible. Fails if emit() silently invents a
    # severity for an unknown code.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    with pytest.raises(KeyError, match="not_a_real_code"):
        bus.emit("not_a_real_code")
    assert bus.records() == []


def test_refusing_an_unregistered_code_says_what_to_do_about_it():
    # `CATALOG[code]` on a miss gives a user `KeyError: 'some_code'` and
    # nothing else -- no statement that a diagnostic code was emitted
    # without being registered, and no pointer to where codes are declared.
    # Fails if the refusal is a bare KeyError, or if it does not name
    # the module a reader has to open to fix it.
    from pscx.diagnostics import Diagnostics, UncataloguedCode

    bus = Diagnostics()
    with pytest.raises(UncataloguedCode) as raised:
        bus.emit("cim_brand_new_finding", "resistor")
    text = str(raised.value)
    assert "cim_brand_new_finding" in text
    assert "pscx/diagnostics.py" in text
    assert "CATALOG" in text
    # still a KeyError, so the call sites that catch one keep working
    assert isinstance(raised.value, KeyError)


def test_a_detail_is_never_mistaken_for_a_code():
    # The keys carry `code: detail` and details contain colons of their own
    # ("(null):T11-c1"). Fails if emit() ever re-splits a key, which would
    # make `lineconst_rowdefn_missing` and its detail two codes.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    bus.emit("lineconst_rowdefn_missing", "(null):T11-c1")
    assert bus.by_code() == {"lineconst_rowdefn_missing": 1}
    assert bus.records()[0].detail == "(null):T11-c1"


def test_suppressed_discards_everything_emitted_inside_it():
    # The detailed-model emitter probes 1,045 non-numeric proprietary
    # parameters, and failing
    # to resolve one says nothing about the case. Fails if the
    # probe's diagnostics leak into the bus, where they would drown every
    # pinned counter, or if suppression discards what was
    # already there, or if it survives an exception raised inside.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    bus.emit("cim_basevoltage_unknown")
    with bus.suppressed():
        bus.emit("param_name_missing", "Main.kV")
    assert bus.by_code() == {"cim_basevoltage_unknown": 1}

    with pytest.raises(ValueError), bus.suppressed():
        bus.emit("param_name_missing", "Main.kV")
        raise ValueError("boom")
    assert bus.by_code() == {"cim_basevoltage_unknown": 1}


def test_the_repr_is_bounded():
    # A failing assertion inside a test reprs every frame, and a
    # 15 GB repr gets pytest OOM-killed so the failure reports NOTHING.
    # Fails if the bus reprs its records.
    from pscx.diagnostics import Diagnostics

    bus = Diagnostics()
    for index in range(5000):
        bus.emit("cim_breaker_state_default", str(index))
    assert len(repr(bus)) < 200

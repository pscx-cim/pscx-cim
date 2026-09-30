"""The projection onto the granularity PSCAD drew.

Hand-derived on inputs countable by hand: the forward rule and the
arithmetic under it.
"""

def test_paired_phases_pairs_equal_widths_and_broadcasts_width_one():
    # Hand-enumerated. Equal widths pair conductor to conductor; a width-1
    # end broadcasts, which is how a three-phase shunt bank drawn as one
    # symbol reaches its single star point or ground; two widths that are
    # neither equal nor 1 have no correspondence and yield nothing rather
    # than a truncated zip.
    from pscx.emission import paired_phases

    assert paired_phases(1, 1) == [(1, 1)]
    assert paired_phases(3, 3) == [(1, 1), (2, 2), (3, 3)]
    assert paired_phases(3, 1) == [(1, 1), (2, 1), (3, 1)]
    assert paired_phases(1, 3) == [(1, 1), (1, 2), (1, 3)]
    assert paired_phases(2, 2) == [(1, 1), (2, 2)]
    assert paired_phases(6, 6) == [(k, k) for k in range(1, 7)]
    assert paired_phases(2, 3) == []
    assert paired_phases(3, 6) == []


def test_the_lir_expands_branch_endpoints_through_that_same_rule():
    # The rule is stated once. If the LIR grew its own copy, the
    # reconstruction could invert a rule the forward path never used and
    # every oracle would still pass.
    from pscx.emission import paired_phases
    from pscx.lir import _phase_pairs

    dims = {"a": 3, "b": 1}
    assert _phase_pairs(("a", None), ("b", None), dims) == [
        (("a", phase_a), ("b", phase_b))
        for phase_a, phase_b in paired_phases(3, 1)
    ]
    # an INDEXED endpoint is one conductor whatever its node's width, which
    # is the case naming a phase explicitly
    assert _phase_pairs(("a", 2), ("b", None), dims) == [(("a", 2), ("b", 1))]


def test_phase_code_names_a_conductor_only_where_cim_can():
    # A drawn THREE-phase bundle has phase letters: index k of it is
    # conductor k of an A/B/C set, which is what PSCAD's own per-phase view
    # of the same component spells out (breaker3's A1/B1/C1 against its
    # dim-3 N1). Any other conductor count has no PhaseCode member --
    # there is none for a bipole's two or a double circuit's six -- and
    # stating one anyway is not merely unhelpful but invalid under 301's
    # own consistency rule.
    from pscx.emission import phase_code

    assert phase_code(3, (1, 2, 3)) == "ABC"
    assert phase_code(3, (1,)) == "A"
    assert phase_code(3, (2,)) == "B"
    assert phase_code(3, (3,)) == "C"
    assert phase_code(3, (1, 3)) == "AC"
    assert phase_code(1, (1,)) is None
    assert phase_code(2, (1, 2)) is None
    assert phase_code(6, tuple(range(1, 7))) is None


def test_a_group_that_is_not_a_clean_phase_expansion_is_refused():
    # The safety net under the grouping, exercised on a hand-built pair of
    # per-phase edges.
    # Two edges of one element on the SAME conductor pair are parallel
    # elements, not conductors, and merging them would silently turn two
    # pieces of equipment into one.
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.emission import drawn_elements

    class _Decl:
        label = "BR"

    class _Branch:
        decl = _Decl()

    class _Instance:
        index = 0
        path = "Main"

    component = object()
    data = {"component": component, "instance": _Instance(),
            "branch": _Branch()}
    duplicate = [(("a", 1), ("b", 1), data), (("a", 1), ("b", 1), data)]
    before = DIAGNOSTICS.count("cim_element_phase_overlap")
    assert drawn_elements(duplicate, {"a": 1, "b": 1}) == []
    assert DIAGNOSTICS.count("cim_element_phase_overlap") == before + 1

    # ...and both ends of one element on ONE drawn node states nothing a
    # two-terminal CIM object can hold
    before = DIAGNOSTICS.count("cim_element_self_loop")
    assert drawn_elements([(("a", 1), ("a", 2), data)], {"a": 3}) == []
    assert DIAGNOSTICS.count("cim_element_self_loop") == before + 1

    # the well-formed case, for contrast: three conductors of one element
    good = [(("a", phase), ("b", phase), data) for phase in (1, 2, 3)]
    elements = drawn_elements(good, {"a": 3, "b": 3})
    assert len(elements) == 1
    assert elements[0].phases == ((1, 1), (2, 2), (3, 3))
    assert elements[0].phases_a == (1, 2, 3)
    assert elements[0].node_a == "a" and elements[0].node_b == "b"


def _overlapping_edges():
    """Two per-phase edges of one element on the SAME conductor pair.

    Parallel elements, not conductors of one, so grouping them is the
    silent loss ``cim_element_phase_overlap`` exists to refuse.
    """
    class _Decl:
        label = "BR"

    class _Branch:
        decl = _Decl()

    class _Instance:
        index = 0
        path = "Main"

    data = {"component": object(), "instance": _Instance(),
            "branch": _Branch()}
    return [(("a", 1), ("b", 1), data), (("a", 1), ("b", 1), data)]


def test_an_element_finding_reaches_the_model_that_produced_it():
    # `assert model.diagnostics.count("cim_element_phase_overlap") == 0`
    # can never fail while drawn_elements writes only to the process-global
    # bus: the count is 0 whatever the emitter does, so the assertion says
    # nothing about the emission. The three codes drawn_elements raises are
    # findings ABOUT one emitted model, so they belong on that model's
    # sink, and a per-model assertion has to be able to see them.
    #
    # This fails if a finding goes to the global bus instead of the sink it
    # was handed. The overlap is the finding easiest to force.
    from pscx.cim import CimModel
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.emission import drawn_elements

    model = CimModel(project="hand-built")
    before = DIAGNOSTICS.count("cim_element_phase_overlap")
    assert drawn_elements(_overlapping_edges(), {"a": 1, "b": 1},
                          diagnostics=model.diagnostics) == []
    assert model.diagnostics.count("cim_element_phase_overlap") == 1
    # ...and not on the global as well: emit_files extends the model's
    # records onto it, so a finding written in both places would be
    # counted twice
    assert DIAGNOSTICS.count("cim_element_phase_overlap") == before


def test_the_default_sink_is_the_global_bus():
    # The negative control for the routing above, and the reason the sink
    # parameter is what does the work: hand no sink and the same finding
    # goes to the process-global bus, where a per-model assertion cannot
    # see it, and a `model.diagnostics.count(...) == 0` on these three
    # codes reads 0 whatever the emitter does.
    from pscx.cim import CimModel
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.emission import drawn_elements

    model = CimModel(project="hand-built")
    before = DIAGNOSTICS.count("cim_element_phase_overlap")
    assert drawn_elements(_overlapping_edges(), {"a": 1, "b": 1}) == []
    assert DIAGNOSTICS.count("cim_element_phase_overlap") == before + 1
    assert model.diagnostics.count("cim_element_phase_overlap") == 0


def test_every_element_finding_reaches_the_sink_it_is_handed():
    # The other two codes drawn_elements raises, on the same routing. A
    # self-loop is forced by putting both ends of one element on one drawn
    # node; a partial expansion by covering one conductor pair of a bundle
    # that has three.
    from pscx.cim import CimModel
    from pscx.emission import drawn_elements

    edges = _overlapping_edges()
    data = edges[0][2]

    model = CimModel(project="hand-built")
    assert drawn_elements([(("a", 1), ("a", 2), data)], {"a": 3},
                          diagnostics=model.diagnostics) == []
    assert model.diagnostics.count("cim_element_self_loop") == 1

    model = CimModel(project="hand-built")
    assert len(drawn_elements([(("a", 1), ("b", 1), data)], {"a": 3, "b": 3},
                              diagnostics=model.diagnostics)) == 1
    assert model.diagnostics.count("cim_element_partial_phases") == 1

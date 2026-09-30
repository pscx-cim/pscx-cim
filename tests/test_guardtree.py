"""The guard tree: what its SHAPE makes true, and what it makes unsayable.

The flat ``(line, guards)`` stream states, as a loop invariant in a
comment, that alternatives accumulate and never compound. In the tree it
is a property of the types: a ``#CASE`` arm is a string, so nothing can
nest inside one, so arm counts can only ever be summed.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

CHAIN = """\
#IF Sat==1
alpha
#ELSEIF Sat==2
beta
#ELSE
gamma
#ENDIF
"""

#: intermediate.pslx xfmr-3p2w, the BRS12 row verbatim: one logical line
#: opened by ``BRS12 = ~``, continued by TWO #CASE lines that sit in
#: mutually exclusive arms of a nested #IF, and closed by ``~ BREAKER 1.0``.
#: Twelve arms each; twelve times twelve would be 144.
BRS12 = """\
BRS12 = ~
  #IF YD1==0
   #CASE (1-YD2)*W2E+YD2*W2O  {~$N2(1) $G2 ~}{~$N2(1) $N2(2) ~}{~$G2 $N2(3) ~}\
{~$N2(3) $N2(2) ~}{~$N2(3) $G2 ~}{~$N2(3) $N2(1) ~}{~$G2 $N2(1) ~}\
{~$N2(2) $N2(1) ~}{~$N2(2) $G2 ~}{~$N2(2) $N2(3) ~}{~$G2 $N2(3) ~}\
{~$N2(1) $N2(3) ~}
  #ELSEIF YD1==1
   #CASE (1-YD2)*W2O+YD2*W2E  {~$N2(1) $N2(2) ~}{~$G2 $N2(2) ~}\
{~$N2(3) $N2(2) ~}{~$N2(3) $G2 ~}{~$N2(3) $N2(1) ~}{~$G2 $N2(1) ~}\
{~$N2(2) $N2(1) ~}{~$N2(2) $G2 ~}{~$N2(2) $N2(3) ~}{~$G2 $N2(3) ~}\
{~$N2(1) $N2(3) ~}{~$N2(1) $G2 ~}
  #ENDIF
~ BREAKER 1.0
"""


def _tree(text, context="test"):
    from pscx.guards import assemble_splices, parse_script

    return assemble_splices(parse_script(text), context)


# --------------------------------------------------------------------------
# Conditionals are n-ary
# --------------------------------------------------------------------------


def test_an_if_elseif_else_chain_is_one_node_with_three_arms():
    # Fails if the chain is desugared into nested binaries -- an #ELSEIF
    # becoming an #IF inside the first arm's else. Under that shape
    # "which arms exclude each other" is something a reader recomputes
    # from nesting depth instead of reading off one node.
    from pscx.guards import Conditional, Text, parse_script

    tree = parse_script(CHAIN)
    assert len(tree) == 1
    chain = tree[0]
    assert isinstance(chain, Conditional)
    assert [arm.condition for arm in chain.arms] == ["Sat==1", "Sat==2", None]
    assert [[n.line for n in arm.body] for arm in chain.arms] == [
        ["alpha"], ["beta"], ["gamma"]]
    assert all(isinstance(n, Text) for arm in chain.arms for n in arm.body)


def test_an_arms_guard_chain_is_its_position_among_its_siblings():
    # Hand-derived: alpha under Sat==1, beta under its negation plus its
    # own condition, gamma under both negations and nothing of its own.
    # Fails if the negations of the earlier arms are dropped (both arms of
    # a chain would then be active at once) or emitted in the wrong order.
    from pscx.guards import guarded_lines, parse_script

    assert guarded_lines(parse_script(CHAIN)) == [
        ("alpha", (("Sat==1", True),)),
        ("beta", (("Sat==1", False), ("Sat==2", True))),
        ("gamma", (("Sat==1", False), ("Sat==2", False))),
    ]


def test_nesting_composes_guard_chains_outermost_first():
    # Two levels, hand-derived. Fails if an inner chain's guards are
    # prefixed rather than appended, or if the outer context is lost when
    # descending -- both of which silently re-attach a body to the wrong
    # arm, which is how a wrong tower branch reaches the impedance
    # derivation.
    from pscx.guards import guarded_lines, parse_script

    text = "#IF A==1\n#IF B==2\nx\n#ELSE\ny\n#ENDIF\n#ENDIF\nz\n"
    assert guarded_lines(parse_script(text)) == [
        ("x", (("A==1", True), ("B==2", True))),
        ("y", (("A==1", True), ("B==2", False))),
        ("z", ()),
    ]


# --------------------------------------------------------------------------
# The brace one-liner is substitution text, not structure
# --------------------------------------------------------------------------

#: Ztrans_fcnZ's shape: a brace body that continues over following lines
#: until the braces balance. The lines inside are the body of a
#: substitution, so none of them is a line of the segment.
MULTILINE_BRACE = """\
#IF Order==2 {
  a11 = $A
  a12 = $B
}
tail = $Z
"""


@pytest.mark.parametrize("text", [
    "#IF A==1 { x }\ntail = $Z\n",
    "#IF A==1 { x } { y }\ntail = $Z\n",
    "#IF A==1 {~ x ~}\ntail = $Z\n",
    MULTILINE_BRACE,
])
def test_a_brace_conditional_opens_no_block(text):
    # The grammar claim itself. A brace one-liner emits no line of its
    # own and does not open a block, so the line AFTER it carries no
    # guard. Reading it as a block-opener leaves the guard stack
    # permanently unbalanced and attaches a garbage chain to every later
    # line of the segment -- which is what happened, masked because the
    # `$`-macro guards of the day failed open.
    from pscx.guards import guarded_lines, parse_script

    lines = guarded_lines(parse_script(text))
    assert lines[-1] == ("tail = $Z", ())
    assert all(guards == () for _line, guards in lines)


def test_a_brace_body_is_text_the_segment_does_not_state():
    # The negative half: the body is substitution text spliced into the
    # surrounding emitted line, so its lines are not lines of the
    # segment. Fails if a body line is emitted as content, which would
    # put a fragment of a conditional into the record unconditionally.
    from pscx.guards import guarded_lines, parse_script

    assert guarded_lines(parse_script(MULTILINE_BRACE)) == [("tail = $Z", ())]


def test_a_brace_condition_is_still_collected_for_evaluation():
    # A brace one-liner opens no block, but its condition is a condition:
    # the guard-grammar oracle parses and evaluates every expression
    # `_guard_expressions` returns, so a brace condition it skipped would
    # be one the oracle never proved parseable. Fails if "not structure"
    # is implemented as "not read".
    from pscx.preproc import _guard_expressions

    assert _guard_expressions("#IF A==1 { x } { y }\ntail\n") == ["A==1"]
    assert _guard_expressions(MULTILINE_BRACE) == ["Order==2"]
    # ...and the body contributes none of its own, however it is spelt
    assert _guard_expressions("#IF A==1 { #IF B==2 }\n") == ["A==1"]


def test_an_output_inside_a_brace_body_is_reported():
    # The premise the writer scan rests on: a brace body never declares a
    # writer, so skipping the body loses none. It is asserted loudly
    # rather than assumed, on both spellings of the body -- one line and
    # several. Fails silently, and invisibly, if the count goes away.
    from pscx.guards import parse_script

    text = "#IF A==1 { #OUTPUT REAL P }\n#IF B==2 {\n#OUTPUT REAL Q\n}\n"
    _tree_out, codes = _emitted(lambda: parse_script(text))
    assert codes["script_output_inside_brace_body"] == 2


# --------------------------------------------------------------------------
# Alternatives accumulate, never compound
# --------------------------------------------------------------------------


def test_the_brs12_shape_is_one_splice_over_two_mutually_exclusive_cases():
    # The shape the tree exists to hold: one open logical line, continued
    # by two #CASE lines that can never both apply. Fails if the splice is
    # parsed as two separate lines, or if the nested #IF is flattened away
    # so the two #CASE lines look like one accumulation of 24 arms from
    # one condition.
    from pscx.guards import Case, Conditional, Splice

    tree = _tree(BRS12)
    assert len(tree) == 1
    splice = tree[0]
    assert isinstance(splice, Splice)
    assert splice.head == "BRS12 ="
    assert splice.tail == "BREAKER 1.0"

    assert len(splice.body) == 1
    chain = splice.body[0]
    assert isinstance(chain, Conditional)
    assert [arm.condition for arm in chain.arms] == ["YD1==0", "YD1==1"]
    for arm in chain.arms:
        assert len(arm.body) == 1
        assert isinstance(arm.body[0], Case)
        assert len(arm.body[0].arms) == 12


def test_the_brs12_arm_count_is_additive():
    # 12 + 12 = 24, and the number that must NOT appear is 12 * 12 = 144.
    # Fails the moment expansion pairs one #CASE's arms with another's --
    # which is what "alternatives accumulate" forbids and what a tree of
    # nested alternations would invite.
    tree = _tree(BRS12)
    alternatives = tree[0].alternatives()
    assert len(alternatives) == 24
    assert len(alternatives) == 12 + 12 != 12 * 12


def test_each_brs12_variant_is_one_whole_branch_row_under_one_arm():
    # The 24 variants, spelled out: head + one middle + tail, each under
    # its own YD1 arm and its own selector equality. Fails if the head or
    # tail is dropped from a variant, if the guard chain loses the
    # enclosing #IF, or if the selector guard names the wrong arm index.
    from pscx.guards import guarded_lines

    lines = guarded_lines(_tree(BRS12))
    assert len(lines) == 24
    assert lines[0] == (
        "BRS12 = $N2(1) $G2 BREAKER 1.0",
        (("YD1==0", True), ("((1-YD2)*W2E+YD2*W2O)==0", True)),
    )
    assert lines[12] == (
        "BRS12 = $N2(1) $N2(2) BREAKER 1.0",
        (("YD1==0", False), ("YD1==1", True),
         ("((1-YD2)*W2O+YD2*W2E)==0", True)),
    )
    assert lines[23][1][-1] == ("((1-YD2)*W2O+YD2*W2E)==11", True)
    assert all(line.startswith("BRS12 = ") and line.endswith(" BREAKER 1.0")
               for line, _guards in lines)


def test_concrete_expansion_picks_the_row_its_selector_names():
    # Hand-derived: YD1=1 takes the second #CASE, whose selector
    # (1-YD2)*W2O+YD2*W2E is (1-0)*1 + 0*3 = 1, so arm 1 -- `$G2 $N2(2)`,
    # the second middle -- glued between the head and the tail. Fails if
    # expansion takes an arm from the wrong #IF branch, or drops the glue.
    from pscx.guards import expand

    env = {"yd1": 1, "yd2": 0, "w2e": 3, "w2o": 1}
    assert expand(_tree(BRS12), env) == ["BRS12 = $G2 $N2(2) BREAKER 1.0"]


def test_a_parenthesised_group_keeps_its_value_for_the_comparison():
    # Hand-derived. A #CASE arm's guard is written
    # `(selector)==k`, so a selector worth 2 names arm 2. Condition
    # semantics coerce a comparison-LESS expression to a truth value;
    # applying that at the parenthesis rather than where a
    # truth value is actually asked for would make `(YD1*Lead)==1` true
    # for every non-zero selector and every arm above the second
    # unreachable.
    from pscx.expr import ConditionParser

    env = {"yd1": 1, "lead": 2}
    assert ConditionParser("YD1*Lead", env, arith=True).parse() == 2
    assert ConditionParser("(YD1*Lead)==2", env).parse() is True
    assert ConditionParser("(YD1*Lead)==1", env).parse() is False
    assert ConditionParser("(YD1*Lead)==0", env).parse() is False
    # unparenthesised, and as an operand of arithmetic, it keeps its value
    assert ConditionParser("YD1*Lead==2", env).parse() is True
    assert ConditionParser("(YD1*Lead)*3==6", env).parse() is True


def test_a_group_is_still_a_truth_value_where_one_is_asked_for():
    # The other half of the coercion rule, and the negative control on the test
    # above: `&&`, `||` and `!` put their operands in a logical context,
    # so a bare group there means "non-zero" and the whole expression is
    # a boolean. Fails if the coercion is missing rather than applied by
    # the operators that want it.
    from pscx.expr import ConditionParser, condition_holds

    env = {"yd1": 1, "lead": 2, "off": 0}
    assert ConditionParser("(YD1*Lead) && YD1", env).parse() is True
    assert ConditionParser("(YD1*Off) || Off", env).parse() is False
    assert ConditionParser("!(YD1*Lead)", env).parse() == 0
    # and a whole condition is still read for its truth by its caller
    assert condition_holds("(YD1*Lead)", env) is True
    assert condition_holds("(YD1*Off)", env) is False


def test_concrete_expansion_picks_an_arm_above_the_second():
    # The same hand-derivation on master's own text, which is where
    # a group's value decides the arm: YD1=1 takes the second #CASE,
    # whose selector (1-YD2)*W2O+YD2*W2E is (1-0)*2 + 0*3 = 2, so arm 2
    # -- `$N2(3) $N2(2)`, the third middle. Coercing the group to a truth
    # value selected arm 1 (`$G2 $N2(2)`) instead: the same node PAIRS
    # over the whole alternation, so no partition moved, but a different
    # labelled breaker bridges a different pair.
    from pscx.guards import expand

    env = {"yd1": 1, "yd2": 0, "w2e": 3, "w2o": 2}
    assert expand(_tree(BRS12), env) == ["BRS12 = $N2(3) $N2(2) BREAKER 1.0"]


def test_the_two_interpreters_agree_line_for_line():
    # The differential that says the tree means one thing. The two walks
    # are genuinely different -- expand tests each arm at its node and
    # never descends into one that fails, guarded_lines carries the whole
    # chain to the leaf and never evaluates anything -- so agreement is
    # evidence rather than a tautology. Fails if either loses the
    # enclosing context when it crosses a splice.
    from pscx.guards import _guard_holds, expand, guarded_lines

    tree = _tree(BRS12)
    for w2o in range(2):
        env = {"yd1": 1, "yd2": 0, "w2e": 0, "w2o": w2o}
        deferred = [line for line, guards in guarded_lines(tree)
                    if all(_guard_holds(e, env, want) for e, want in guards)]
        assert expand(tree, env) == deferred
        assert len(deferred) == 1, w2o


def test_symbolic_enumeration_names_every_arm_with_no_environment():
    # 2 conditional arms plus 12 + 12 #CASE arms, each with a stable
    # identity: re-parsing the same text must name the same arms, or
    # "no case ever selected this one" is a statement about the parse.
    # Fails if arm identity folds in a guard chain (which repeats) or a
    # condition string (which repeats too).
    from pscx.guards import enumerate_arms

    arms = enumerate_arms(_tree(BRS12))
    assert len(arms) == 26
    assert sum(1 for a in arms if a.kind == "conditional") == 2
    assert sum(1 for a in arms if a.kind == "case") == 24
    assert len({a.key for a in arms}) == 26
    assert [a.key for a in arms] == [a.key for a in enumerate_arms(_tree(BRS12))]


def test_symbolic_enumeration_finds_the_arms_concrete_selection_skips():
    # Why the symbolic side exists. Two environments between them take 4
    # of the 26 arms, and the other 22 are exactly what concrete expansion
    # cannot report -- it emits what it chose, so nothing it produces
    # mentions them. Fails if selection is computed over lines rather than
    # over arms, which would lose the #CASE arms of the dead #IF branch.
    from pscx.guards import enumerate_arms, selected_arms

    tree = _tree(BRS12)
    chosen = set()
    for yd1 in (0, 1):
        chosen |= selected_arms(tree, {"yd1": yd1, "yd2": 0, "w2e": 0,
                                       "w2o": 0})
    assert len(chosen) == 4
    assert len({a.key for a in enumerate_arms(tree)} - chosen) == 22


def test_a_case_arm_holds_text_so_nothing_can_nest_inside_one():
    # The structural claim, stated against the types rather than against
    # an expansion: an arm is a string. A string cannot hold another
    # alternation, so there is no shape in which two alternations multiply.
    # Fails if an arm is ever widened to hold nodes.
    from pscx.guards import Case

    tree = _tree(BRS12)
    cases = [n for n in tree[0].body[0].arms[0].body if isinstance(n, Case)]
    assert cases
    for case in cases:
        assert all(isinstance(arm, str) for arm in case.arms)


def test_a_splice_cannot_be_opened_inside_an_open_one():
    # The other half of the same rule: one logical line is open at a time,
    # so a splice inside a splice body would be a second open
    # line and the middles of the two could pair up. Fails if the node
    # type accepts it and leaves the rule to a runtime check somewhere.
    from pscx.guards import Splice

    inner = Splice("a", (), "b")
    with pytest.raises(ValueError, match="splice"):
        Splice("outer", (inner,), "tail")


_ARM_TEXT = st.text("abc$ ", min_size=0, max_size=4)


@st.composite
def _case_node(draw):
    from pscx.guards import Case

    arms = tuple(draw(st.lists(_ARM_TEXT, min_size=1, max_size=4)))
    return Case(selector=draw(st.sampled_from(["A", "B", "(1-X)*W"])),
                arms=arms, glued=(True,) * len(arms))


def _body_strategy(depth):
    from pscx.guards import Arm, Conditional, Text

    leaf = st.one_of(_case_node(), st.builds(Text, st.just("plain")))
    if depth == 0:
        return st.lists(leaf, max_size=3).map(tuple)
    nested = st.builds(
        Conditional,
        st.lists(st.builds(Arm, st.sampled_from(["C==1", "C==2", None]),
                           _body_strategy(depth - 1)),
                 min_size=1, max_size=3).map(tuple))
    return st.lists(st.one_of(leaf, nested), max_size=3).map(tuple)


@settings(max_examples=200, deadline=None)
@given(_body_strategy(2))
def test_a_splice_contributes_the_sum_of_its_arms_over_any_shape(body):
    # The law, over shapes nobody wrote by hand: however the #CASE lines
    # are distributed through the conditionals inside an open line, the
    # number of variants is the SUM of their arm counts. A product would
    # need two alternations on one path, which the types forbid; this is
    # the property that says so for every arrangement rather than for the
    # one BRS12 happens to have.
    from pscx.guards import Case, Splice

    def cases(nodes):
        for node in nodes:
            if isinstance(node, Case):
                yield node
            for arm in getattr(node, "arms", ()):
                if hasattr(arm, "body"):
                    yield from cases(arm.body)

    expected = sum(len(case.arms) for case in cases(body))
    splice = Splice("h", body, "t")
    assert len(splice.alternatives()) == expected


# --------------------------------------------------------------------------
# Negative controls: the shapes the grammar refuses
# --------------------------------------------------------------------------


def test_a_standalone_case_emits_whole_lines_not_middles():
    # master xfmr-3p2w's BR11 form: no `~` glue anywhere, so each arm is a
    # complete declaration under its own selector guard. Fails
    # if a standalone #CASE is skipped as a `#`-directive, which is what
    # silently lost 783 master Branch declarations.
    from pscx.guards import guarded_lines

    text = "#CASE YD1 {BR11=$N1(1) $G1 1.0} {BR11=$N1(1) $N1(2) 1.0}\n"
    assert guarded_lines(_tree(text)) == [
        ("BR11=$N1(1) $G1 1.0", (("(YD1)==0", True),)),
        ("BR11=$N1(1) $N1(2) 1.0", (("(YD1)==1", True),)),
    ]


def _emitted(build):
    """Diagnostic codes raised by ``build()``, isolated from the run's bus."""
    from pscx.diagnostics import DIAGNOSTICS

    before = DIAGNOSTICS.by_code()
    result = build()
    return result, DIAGNOSTICS.by_code() - before


def test_an_unglued_arm_inside_an_open_line_is_reported():
    # An arm that does not glue would leave the reassembled declaration a
    # fragment. Fails if the assembler accepts it silently.
    text = "R1 = ~\n#CASE X {a}{b}\n~ 1.0\n"
    _tree_out, codes = _emitted(lambda: _tree(text))
    assert codes["branch_case_arm_unglued"] == 2


def test_a_glued_arm_with_no_open_line_is_reported():
    # The mirror image: glue that continues a line nobody opened. Fails if
    # the assembler emits the fragment as a declaration.
    _tree_out, codes = _emitted(lambda: _tree("#CASE X {~a~}{~b~}\n"))
    assert codes["branch_case_dangling_glue"] == 2


def test_an_unterminated_open_line_is_reported_and_closed():
    # A `~` that never closes would swallow every later declaration.
    # Fails if the assembler drops the line silently or leaves it open.
    from pscx.guards import guarded_lines

    tree, codes = _emitted(lambda: _tree("R1 = $A $B ~\n"))
    assert codes["branch_splice_unterminated"] == 1
    assert guarded_lines(tree) == [("R1 = $A $B ", ())]

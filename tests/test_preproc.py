"""Script preprocessing: the spelling of a directive vs what it guards."""

#: The same conditional written the two ways master spells it. master
#: indents nested conditionals by spacing the keyword off the hash, so
#: whether those lines are DIRECTIVES or content decides which arm of a
#: record body survives.
SPACED = """\
# IF A==1
alpha = $X
#   ELSE
beta = $Y
#   ENDIF
gamma = $Z
"""
PLAIN = """\
#IF A==1
alpha = $X
#ELSE
beta = $Y
#ENDIF
gamma = $Z
"""


def _guards(text):
    from pscx.preproc import preprocess_script

    return {line.strip().split(" =")[0]: guards
            for line, guards in preprocess_script(text)}


def test_spaced_directives_guard_their_content_exactly_like_unspaced_ones():
    # The differential: both spellings must produce the
    # SAME (line, guard-chain) stream. Hand-derived guard chains pin what
    # "the same" means -- alpha under A==1, beta under its negation, gamma
    # under nothing because #ENDIF closed the block. Without the
    # normalisation every one of those lines comes out unguarded and BOTH
    # arms of the conditional stay active, which is how a wrong tower
    # branch reaches the impedance derivation.
    from pscx.preproc import preprocess_script

    assert preprocess_script(SPACED) == preprocess_script(PLAIN)
    assert _guards(SPACED) == {
        "alpha": (("A==1", True),),
        "beta": (("A==1", False),),
        "gamma": (),
    }


def test_guard_expression_collection_reads_spaced_directives_too():
    # _guard_expressions feeds the guard-grammar oracle; a spaced
    # #IF it cannot see is a condition that is never parsed or evaluated,
    # so the oracle would report success over a subset of the guards.
    from pscx.preproc import _guard_expressions

    assert _guard_expressions(SPACED) == ["A==1"]
    assert _guard_expressions(SPACED) == _guard_expressions(PLAIN)
    assert _guard_expressions("#  ELSEIF B>2\nx\n") == ["B>2"]


def test_a_hash_word_that_is_not_a_directive_stays_content():
    # Negative control for the normalisation: it keys on the four
    # directive keywords at a word boundary, so a comment or an unknown
    # #-word must NOT open a guard block -- otherwise every stray hash
    # line in a script body would swallow the lines after it.
    from pscx.preproc import _guard_expressions, preprocess_script

    text = "# IFACE thing\n# Interesting note\nalpha = $X\n"
    lines = preprocess_script(text)
    assert [guards for _line, guards in lines] == [(), (), ()]
    assert len(lines) == 3
    assert _guard_expressions(text) == []


#: master closes some blocks with `#END` rather than `#ENDIF`, in the same
#: indented spelling the preprocessor normalises. The two must guard identically.
ENDED = """\
#  IF itrfa==1
alpha = $X
#  ELSE
beta = $Y
#  END
gamma = $Z
"""


def test_end_closes_a_block_exactly_like_endif():
    # `#END` is the other half of the spaced-directive rule: normalising `#  IF`
    # into a directive without normalising `#  END` leaves the block
    # OPEN, so gamma inherits the #ELSE arm's guard and every later line
    # of the segment is attached to the wrong arm. Fails if `#END` is read
    # as content, in which case master's transformer #OUTPUT winding
    # currents do not reach the signal graph.
    from pscx.preproc import preprocess_script

    assert preprocess_script(ENDED) == preprocess_script(
        ENDED.replace("#  END", "#  ENDIF"))
    assert _guards(ENDED) == {
        "alpha": (("itrfa==1", True),),
        "beta": (("itrfa==1", False),),
        "gamma": (),
    }


def test_endbegin_is_not_an_end():
    # Negative control: `#BEGIN`/`#ENDBEGIN` is a different pair and closes
    # no conditional. Fails if the rule keys on
    # the prefix rather than on the whole word, which would close every
    # #IF block at the first #ENDBEGIN inside it.
    from pscx.preproc import preprocess_script

    text = "#IF A\n#BEGIN\nx\n#ENDBEGIN\ny\n#ENDIF\nz\n"
    assert [(line, guards) for line, guards in preprocess_script(text)] == [
        ("#BEGIN", (("A", True),)),
        ("x", (("A", True),)),
        ("#ENDBEGIN", (("A", True),)),
        ("y", (("A", True),)),
        ("z", ()),
    ]

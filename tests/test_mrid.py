"""Deterministic mRIDs: uuid5 over (project, kind, id).

The seed contract is pinned here independently of the implementation:
namespace = uuid5(NAMESPACE_DNS, "pscx-cim"), seed = the three parts
percent-quoted (safe="") and joined with "/". Emitted files must be
reproducible across runs and machines, so the exact UUIDs are contract.
"""

import re
import subprocess
import sys
import uuid
from urllib.parse import quote

from hypothesis import given
from hypothesis import strategies as st

from pscx.mrid import mrid

_RFC4122_V5 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _expected(project: str, kind: str, ident: str) -> str:
    ns = uuid.uuid5(uuid.NAMESPACE_DNS, "pscx-cim")
    seed = "/".join(quote(p, safe="") for p in (project, kind, ident))
    return str(uuid.uuid5(ns, seed))


def test_mrid_matches_independently_computed_uuid5():
    # Fails if the namespace, seed encoding, or hash version changes --
    # which would silently re-identify every object in re-emitted files.
    assert mrid("half_rec", "ConnectivityNode", "42") == _expected(
        "half_rec", "ConnectivityNode", "42"
    )


def test_mrid_is_stable_across_processes():
    # Fails if identity ever depends on per-process state (uuid4, hash
    # randomization, iteration order).
    inproc = mrid("half_rec", "Terminal", "7")
    out = subprocess.run(
        [sys.executable, "-c", "from pscx.mrid import mrid; print(mrid('half_rec', 'Terminal', '7'))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == inproc


def test_mrid_is_rfc4122_version5():
    # CGMES mRID lexical form: lowercase hyphenated UUID.
    assert _RFC4122_V5.match(mrid("p", "k", "1"))


@given(
    a=st.tuples(st.text(), st.text(), st.text()),
    b=st.tuples(st.text(), st.text(), st.text()),
)
def test_distinct_identities_get_distinct_mrids(a, b):
    # Fails if the seed encoding is ambiguous -- e.g. a naive "/".join
    # makes ("a/b", "c") collide with ("a", "b/c"), silently merging two
    # CIM objects into one mRID.
    if a != b:
        assert mrid(*a) != mrid(*b)


def test_mrid_accepts_integer_ids():
    # pscx element ids are stable integers; they must identify like their
    # string form, not by object repr.
    assert mrid("p", "k", 42) == mrid("p", "k", "42")

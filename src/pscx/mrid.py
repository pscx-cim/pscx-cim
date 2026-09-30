"""Deterministic CIM mRIDs.

Every emitted CIM object is identified by ``uuid5`` over
``(project, kind, id)`` so re-emitting a case reproduces the same file
byte-for-byte and diffs stay meaningful. pscx element ``id`` attributes
are stable integers, which makes them usable seeds. The three seed parts
are percent-quoted before joining so the encoding is injective (a naive
join would collide ``("a/b", "c")`` with ``("a", "b/c")``).
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

#: Root namespace for every pscx-emitted mRID. Changing it re-identifies
#: every object in every emitted file -- never change it casually.
NAMESPACE_PSCX = uuid.uuid5(uuid.NAMESPACE_DNS, "pscx-cim")


class MridCollision(ValueError):
    """Two objects minted with one identity.

    Raised rather than counted, because the alternative is silent data
    loss and it is silent in both directions: a second registration into
    a dict of resources removes the first from the document, and a second
    subject in an RDF graph merges with the first into one carrying both
    their statements. Either way the run exits 0 at the default
    ``--fail-on`` and the document still parses. It lives beside
    :func:`mrid` because a collision is never anything but a seed used
    twice, and the fix is always in the seed.
    """


def mrid(project: str, kind: str, ident: str | int) -> str:
    """The mRID (lowercase hyphenated UUIDv5) of one CIM object.

    ``project`` scopes ids between cases, ``kind`` between object classes
    within a case (a ConnectivityNode and the Terminal derived from the
    same pscx element must differ), ``ident`` is the stable per-kind seed.
    """
    seed = "/".join(quote(str(p), safe="") for p in (project, kind, ident))
    return str(uuid.uuid5(NAMESPACE_PSCX, seed))

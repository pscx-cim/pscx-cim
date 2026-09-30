"""``id()`` is an identity only while something holds the object.

The check compares ``id_inventory.ID_SITES`` against the package source in
both directions. It says every ``id()`` in ``src/pscx/`` has been read and
classified, and that the one class known to be WRONG, an lxml element
proxy whose address the library recycles, has no members. A new call
lands here before it lands anywhere else.
"""

import id_inventory
import pytest
from conftest import master_available
from id_inventory import HOLDERS, ID_SITES, Held, Proxy
from pins import evidence

pytestmark = pytest.mark.skipif(
    not master_available(), reason="PSCAD master.pslx not found"
)

ID_CALL_SITES = evidence(
    30,
    check="sum(entry.sites for entry in ID_SITES.values()) == the id() "
          "calls an AST walk finds in src/pscx/",
    why="calls in the package that take an object's address, every one of "
        "them classified by what holds the object; the number is what "
        "makes the inventory exhaustive rather than a list of the ones "
        "somebody happened to look at")


# ---------------------------------------------------------------------------
# the inventory against the source
# ---------------------------------------------------------------------------

def test_the_inventory_is_exhaustive_over_the_package():
    # Both directions. A call that appeared, moved to another function or
    # changed its argument fails here until it has been classified, and a
    # table entry for a call that is gone fails too -- a stale exemption
    # is how an inventory stops describing the thing it inventories.
    found = id_inventory.scan()
    assert set(found) == set(ID_SITES), (
        f"unclassified: {sorted(set(found) - set(ID_SITES))}; "
        f"stale: {sorted(set(ID_SITES) - set(found))}")
    differing = {key: (count, ID_SITES[key].sites)
                 for key, count in found.items()
                 if count != ID_SITES[key].sites}
    assert not differing, f"(found, classified) per site: {differing}"


def test_the_scan_reads_the_whole_package():
    # The control on the check above: both sides derive from the same
    # walk, so a walk that read nothing would agree with a table that
    # said nothing. This pins the total and the modules it came from.
    found = id_inventory.scan()
    assert sum(found.values()) == ID_CALL_SITES, sorted(found)
    assert {"cim.py", "elaborate.py", "emission.py", "lower.py"} <= {
        module for module, _qualname, _expression in found}


def test_every_site_names_a_holder_and_says_why():
    for key, verdict in ID_SITES.items():
        assert isinstance(verdict, (Held, Proxy)), key
        assert verdict.sites >= 1, key
        assert len(verdict.why.split()) >= 8, f"{key}: {verdict.why!r}"
        if isinstance(verdict, Held):
            assert verdict.holder in HOLDERS, f"{key}: {verdict.holder!r}"


def test_no_site_takes_the_address_of_an_lxml_proxy():
    # The class that is wrong by construction: lxml builds an element
    # proxy on demand and frees it when the last reference goes, so its
    # address is a fact about the allocator. The package has no such
    # site, and this asserts that and refuses the first one.
    proxies = {key for key, verdict in ID_SITES.items()
               if isinstance(verdict, Proxy)}
    assert not proxies, sorted(proxies)


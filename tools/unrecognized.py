"""Rank everything the HIR loader reads and cannot give a meaning to.

    python tools/unrecognized.py [--top N] [PATH ...]

With no paths, reads master.pslx. One
line per distinct (element, attribute) or (element, child element), with
its count and the first place it was seen -- so an entry can be looked at
rather than argued about.
"""

from __future__ import annotations

import argparse
from collections import Counter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unrecognized")
    parser.add_argument("--top", type=int, default=0,
                        help="show only the N most common (default: all)")
    parser.add_argument("paths", nargs="*", metavar="PATH")
    args = parser.parse_args(argv)

    from pscx.common import MASTER_PSLX
    from pscx.diagnostics import DIAGNOSTICS
    from pscx.hir import load_project

    paths = args.paths or [MASTER_PSLX]
    counts: Counter = Counter()
    first: dict = {}
    with DIAGNOSTICS.suppressed():
        for path in paths:
            for item in load_project(path).unrecognized:
                counts[item.key] += 1
                first.setdefault(item.key, item)

    print(f"files {len(paths)}   distinct {len(counts)}   "
          f"occurrences {sum(counts.values())}")
    ranked = counts.most_common(args.top or None)
    for key, count in ranked:
        item = first[key]
        value = "" if item.value is None else f"  = {item.value[:28]!r}"
        print(f"  {count:8d}  {item.kind:9s} {key:32s} {item.span}{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

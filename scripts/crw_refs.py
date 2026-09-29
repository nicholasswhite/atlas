#!/usr/bin/env python3
"""Atlas — extract referenced ADO IDs from a CRW field (combined-blurb resolution).

A feature's What's New / In-Development blurb is sometimes published under a DIFFERENT work
item's ID — the "combined blurb" / primary-secondary convention (see ado-patterns +
release-process instruction files):

  - The CRW tracking comment names the primary: `<!-- 90001010 wndraft wnready -->` sitting in
    item 90001015's CRW means 90001015's blurb ships under 90001010.
  - Or a secondary points at the primary in prose: "ID draft available in [90001010](url)".

So when Atlas verifies whether item X's blurb is live in a doc, checking only X's own ID marker
gives a FALSE "stale toggle / stranded" reading for a secondary whose content is published under
the primary. This helper returns the OTHER ids referenced in X's CRW, so the doc-verify can also
check those (OR the result into in_wn_doc / in_id_doc).

Pure + deterministic. No network. Synthetic combined-feature examples exercise shared publication records.

CLI:
    crw_refs.py --own 90001015 --crw "<...html...>"
    cat crw.html | crw_refs.py --own 90001015          # CRW on stdin
    crw_refs.py --own 90001015 --crw "<...>" --json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

# ADO work-item ids are 5-9 digits in practice. Bound it so we don't match release numbers
# (4-digit YYMM like 2603) or short counts.
_ID_RE = re.compile(r"\b\d{5,9}\b")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
# "draft available in [90001010](url)" or "...in 90001010" (primary pointer convention)
_POINTER_RE = re.compile(r"draft available in[^\d]{0,20}(\d{5,9})", re.IGNORECASE)


def extract_referenced_ids(crw: str | None, own_id: str | int | None = None) -> list[str]:
    """Return ADO ids referenced in `crw` OTHER than `own_id`, de-duped, in first-seen order.

    Looks in two places: ids inside `<!-- ... -->` tracking comments (after unescaping HTML
    entities and stripping any nested tags), and ids in a "draft available in <id>" pointer.
    """
    if not crw:
        return []
    text = html.unescape(crw)
    own = str(own_id).strip() if own_id is not None else None

    found: list[str] = []

    def _add(i: str) -> None:
        if i and i != own and i not in found:
            found.append(i)

    # 1. ids inside tracking comments (strip nested tags first, e.g. <span> inside the comment)
    for body in _COMMENT_RE.findall(text):
        clean = _TAG_RE.sub(" ", body)
        for m in _ID_RE.findall(clean):
            _add(m)

    # 2. "draft available in <id>" prose pointers (outside comments)
    for m in _POINTER_RE.findall(text):
        _add(m)

    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract referenced ADO ids from a CRW (combined blurb).")
    p.add_argument("--own", required=True, help="The work item's own ADO id (excluded from output).")
    p.add_argument("--crw", default=None, help="CRW HTML. If omitted, read from stdin.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    args = p.parse_args(argv)

    crw = args.crw if args.crw is not None else sys.stdin.read()
    refs = extract_referenced_ids(crw, args.own)

    if args.json:
        sys.stdout.write(json.dumps({"own": str(args.own), "referenced_ids": refs}, indent=2) + "\n")
    else:
        if refs:
            print("\n".join(refs))
    # exit 0 if any referenced ids found, 1 if none (lets a caller branch in bash)
    return 0 if refs else 1


if __name__ == "__main__":
    raise SystemExit(main())

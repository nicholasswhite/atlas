#!/usr/bin/env python3
"""Edge-case regression tests for owner_gate — run: python tests/test_owner_gate.py

Covers front-matter parsing edge cases (BOM, CRLF, quoted/case-variant aliases, missing/empty
front matter), roster membership, and the safe defaults (unresolvable ownership is never
editable). docfx path-glob resolution is exercised live by the sandbox; these are the pure
front-matter + roster cases.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import owner_gate as og  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def mkfile(text):
    fd, p = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


cases = []


def run():
    # BOM + front matter
    p = mkfile("\ufeff---\nms.author: alex\ntitle: x\n---\n# H\n")
    cases.append(p)
    check("BOM front matter -> own", og.decide(p, ["alex"])["decision"] == "own")

    # CRLF line endings, non-roster owner
    p = mkfile("---\r\nms.author: casey\r\ntitle: x\r\n---\r\n# H\r\n")
    cases.append(p)
    check("CRLF non-roster -> handoff", og.decide(p, ["alex"])["decision"] == "handoff")

    # No front matter at all -> unknown (safe), NOT editable
    p = mkfile("# Just a heading\nbody\n")
    cases.append(p)
    r = og.decide(p, ["alex"])
    check("no front matter -> unknown", r["decision"] == "unknown")
    check("no front matter -> not editable", r["editable"] is False)
    check("no front matter -> honest reason", "no YAML front matter" in r["reason"])

    # Front matter without ms.author, no docfx -> unknown
    p = mkfile("---\ntitle: x\nms.topic: how-to\n---\n# H\n")
    cases.append(p)
    check("fm without ms.author -> unknown", og.decide(p, ["alex"])["decision"] == "unknown")

    # Quoted ms.author value
    p = mkfile("---\nms.author: 'alex'\n---\n# H\n")
    cases.append(p)
    check("quoted ms.author -> own", og.decide(p, ["alex"])["decision"] == "own")

    # Case-insensitive alias match
    p = mkfile("---\nms.author: Alex\n---\n# H\n")
    cases.append(p)
    check("case-insensitive alias -> own", og.decide(p, ["alex"])["decision"] == "own")

    # Empty roster -> nothing editable
    p = mkfile("---\nms.author: alex\n---\n# H\n")
    cases.append(p)
    check("empty roster -> handoff", og.decide(p, [])["decision"] == "handoff")

    # Multi-author roster
    p = mkfile("---\nms.author: morgan\n---\n# H\n")
    cases.append(p)
    check("multi-author roster matches", og.decide(p, ["alex", "morgan", "casey"])["decision"] == "own")

    # all_authors test mode: non-roster owner becomes editable
    p = mkfile("---\nms.author: taylor\n---\n# H\n")
    cases.append(p)
    check("test-mode bypass -> own", og.decide(p, ["alex"], all_authors=True)["decision"] == "own")

    # Missing file -> error
    check("missing file -> error", og.decide("/no/such/file.md", ["alex"])["decision"] == "error")


try:
    run()
finally:
    for p in cases:
        try:
            os.unlink(p)
        except OSError:
            pass

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

#!/usr/bin/env python3
"""Tests for crw_refs.extract_referenced_ids — run: python tests/test_crw_refs.py

The combined-blurb resolver. A secondary item's blurb is published under a PRIMARY item's id;
doc-verify must check the primary's presence too, or it false-flags the secondary as a stale
toggle. Synthetic combined-publication case: item 90001015's CRW references primary 90001010
(which is in the archive), so 90001015's pubWN=True is correct, not stale.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from crw_refs import extract_referenced_ids  # noqa: E402

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


# Real shape: referenced primary id inside a comment with nested HTML spans (the actual
# 90001015 CRW form, entity-encoded as ADO stores it).
real = ('#### Soft-deleted groups &lt;!-- 90001010,<span>\xa0</span>'
        '<span style="x">wndraft, <b>wnready</b></span> --&gt; ExampleProduct now...')
check("finds primary inside nested-HTML comment", extract_referenced_ids(real, "90001015") == ["90001010"])

# Own id only -> no foreign refs (genuine conflict, not a combined blurb)
own_only = '### Feature <!-- 90001001 iddraft idready idstaged wndraft --> body text'
check("own-id-only -> empty", extract_referenced_ids(own_only, "90001001") == [])

# Multiple primaries comma-separated (the documented combined-blurb form)
multi = '### Combined <!-- 90001003, 90001007 iddraft idready --> text'
check("multiple primaries extracted", extract_referenced_ids(multi, "90001003") == ["90001007"])

# Prose pointer form: "draft available in [PRIMARY](url)"
pointer = '<p>ID draft available in [90001028](https://work.example.invalid/x/90001028) idready</p>'
check("prose pointer extracted", extract_referenced_ids(pointer, "90001029") == ["90001028"])

# Release numbers (4-digit YYMM) must NOT be mistaken for ids
yymm = '### x <!-- 2603 wndraft --> shipped in 2607'
check("4-digit release numbers ignored", extract_referenced_ids(yymm, "90001038") == [])

# Empty / None inputs
check("None crw -> empty", extract_referenced_ids(None, "1") == [])
check("empty crw -> empty", extract_referenced_ids("", "1") == [])

# De-dup + own-id exclusion together
dup = '<!-- 90001010 wndraft --> ... <!-- 90001010 wnready --> ... <!-- 90001015 -->'
check("dedup + own excluded", extract_referenced_ids(dup, "90001015") == ["90001010"])

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

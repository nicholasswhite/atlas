#!/usr/bin/env python3
"""Unit tests for lens_affected --path-prefix rebasing + base behavior — run:
    python tests/test_lens_affected.py

Covers the Run-B papercut: lens graphs store paths relative to the docset AREA root
(e.g. "configuration/x.md"); the owner gate + DOC edit need repo-root paths. --path-prefix
rebases them. Also checks the no-prefix passthrough (back-compat) and author_filter alongside.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lens_affected as la  # noqa: E402

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


_GRAPH = {
    "nodes": [
        {"id": "c1", "label": "Example Portal", "file_type": "concept"},
        {"id": "d1", "label": "configure-example-portal", "file_type": "document",
         "ms_author": "alex", "source_file": "configuration/configure-example-portal.md"},
        {"id": "d2", "label": "other-area", "file_type": "document",
         "ms_author": "taylor", "source_file": "device-config/other.md"},
    ],
    "edges": [
        {"source": "c1", "target": "d1", "relation": "documents"},
        {"source": "c1", "target": "d2", "relation": "documents"},
    ],
    "bridges": [],
}

_tmp = tempfile.mkdtemp(prefix="atlas_lens_test_")
_gpath = Path(_tmp) / "graph.json"
_gpath.write_text(json.dumps(_GRAPH), encoding="utf-8")
graph = la._load_graph(_gpath)

print("lens_affected — no prefix: area-relative path preserved (back-compat):")
r0 = la.affected_articles(graph, ["Example Portal"])
paths0 = {a["path"] for a in r0["affected_articles"]}
check("emits the area-relative path unchanged",
      "configuration/configure-example-portal.md" in paths0)

print("\nlens_affected — --path-prefix rebases to repo-root (papercut 3):")
r1 = la.affected_articles(graph, ["Example Portal"], path_prefix="docs/app-management")
paths1 = {a["path"] for a in r1["affected_articles"]}
check("path rebased to repo-root",
      "docs/app-management/configuration/configure-example-portal.md" in paths1)
check("no double slash on join", not any("//" in p for p in paths1))

print("\nlens_affected — trailing slash in prefix doesn't double up:")
r2 = la.affected_articles(graph, ["Example Portal"], path_prefix="docs/app-management/")
check("trailing slash handled",
      "docs/app-management/configuration/configure-example-portal.md"
      in {a["path"] for a in r2["affected_articles"]})

print("\nlens_affected — author_filter still works alongside the prefix:")
r3 = la.affected_articles(graph, ["Example Portal"], author_filter="alex",
                          path_prefix="docs/app-management")
owners3 = {a["ms_author"] for a in r3["affected_articles"]}
check("only alex articles returned", owners3 == {"alex"})
check("taylor article filtered out",
      all(a["ms_author"] != "taylor" for a in r3["affected_articles"]))

shutil.rmtree(_tmp, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

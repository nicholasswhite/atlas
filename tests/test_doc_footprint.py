#!/usr/bin/env python3
"""Unit tests for doc_footprint reconciliation + path normalization — run:
    python tests/test_doc_footprint.py

doc_footprint is the deterministic merge of two discovery signals (repo-update SCAN +
lens/graphify GRAPH) into one provenance-tagged, owner-resolved affected_articles set. Covers
path rebasing, the confidence/disposition reconciliation table, cross-validation, the no-veto
rule, dedup across sources, and owner preference.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import doc_footprint as df  # noqa: E402

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


def one(scan, graph, **kw):
    r = df.reconcile(scan, graph, **kw)
    return r[0] if r else None


print("doc_footprint._norm_path — normalize + rebase:")
check("area-relative + prefix -> repo-root",
      df._norm_path("configuration/x.md", "docs/app-management")
      == "docs/app-management/configuration/x.md")
check("backslashes -> forward", df._norm_path("a\\b\\c.md") == "a/b/c.md")
check("strips leading ./ and /", df._norm_path("./a/b.md") == "a/b.md" and df._norm_path("/a/b.md") == "a/b.md")
check("trailing-slash prefix doesn't double", df._norm_path("c/x.md", "docs/app-management/")
      == "docs/app-management/c/x.md")

print("\nconfidence reconciliation:")
e = one([{"path": "docs/a.md", "score": 90, "ms_author": "alex"}], [])
check("scan strong only -> INFERRED / edit", e["confidence"] == "INFERRED" and e["disposition"] == "edit")

e = one([{"path": "docs/a.md", "score": 55, "ms_author": "alex"}], [])
check("scan moderate only -> INFERRED / list-only", e["confidence"] == "INFERRED" and e["disposition"] == "list-only")

e = one([{"path": "docs/a.md", "score": 20, "ms_author": "alex"}], [])
check("scan weak only -> AMBIGUOUS / list-only", e["confidence"] == "AMBIGUOUS" and e["disposition"] == "list-only")

e = one([], [{"path": "a.md", "provenance": "EXTRACTED", "ms_author": "alex"}], path_prefix="docs")
check("graph EXTRACTED only -> EXTRACTED / edit", e["confidence"] == "EXTRACTED" and e["disposition"] == "edit")
check("graph-only path rebased", e["path"] == "docs/a.md")

e = one([], [{"path": "docs/a.md", "blast": "bridge", "ms_author": "alex"}])
check("graph w/o provenance (lens_affected) -> INFERRED ripple / list-only",
      e["confidence"] == "INFERRED" and e["disposition"] == "list-only")
check("blast carried from graph", e["blast"] == "bridge")

print("\ncross-validation (the accuracy win):")
e = one([{"path": "docs/a.md", "score": 88, "ms_author": "alex"}],
        [{"path": "docs/a.md", "provenance": "EXTRACTED"}])
check("scan-strong + graph-EXTRACTED -> EXTRACTED / edit", e["confidence"] == "EXTRACTED" and e["disposition"] == "edit")
check("flagged cross_validated", e.get("cross_validated") is True)
check("both sources recorded", set(e["sources"]) == {"H8:repo-scan", "H8:lens-graph"})

print("\nno-veto rule (a weak graph signal can't sink a strong lexical match):")
e = one([{"path": "docs/a.md", "score": 92, "ms_author": "alex"}],
        [{"path": "docs/a.md", "provenance": "AMBIGUOUS"}])
check("scan-strong + graph-AMBIGUOUS -> still edit (no veto)", e["disposition"] == "edit")

e = one([], [{"path": "docs/a.md", "provenance": "AMBIGUOUS"}])
check("graph-AMBIGUOUS only -> AMBIGUOUS / list-only", e["confidence"] == "AMBIGUOUS" and e["disposition"] == "list-only")

print("\ndedup + owner preference + ordering:")
r = df.reconcile(
    [{"path": "docs/a.md", "score": 80, "ms_author": "alex"},
     {"path": "docs/b.md", "score": 50, "ms_author": "morgan"}],
    [{"path": "docs/a.md", "provenance": "EXTRACTED", "ms_author": "WRONG", "concept": "VPP"}],
)
check("same path scan+graph dedupes to one entry", len([a for a in r if a["path"] == "docs/a.md"]) == 1)
a = next(a for a in r if a["path"] == "docs/a.md")
check("scan ms_author wins over graph", a["ms_author"] == "alex")
check("concepts unioned across sources", "VPP" in a["concepts"])
check("edits sort before list-only", r[0]["disposition"] == "edit")

print("\nPhase 1.1 — agreement elevation (cross-validation fires without explicit provenance):")
e = one([{"path": "docs/a.md", "score": 90, "ms_author": "alex"}],
        [{"path": "docs/a.md", "blast": "direct", "ms_author": "alex"}])  # graph w/o provenance
check("both sources + strong scan -> EXTRACTED (agreement = verified)", e["confidence"] == "EXTRACTED")
check("flagged cross_validated", e.get("cross_validated") is True and e["disposition"] == "edit")
e = one([{"path": "docs/a.md", "score": 50, "ms_author": "alex"}],
        [{"path": "docs/a.md", "blast": "direct"}])
check("both sources + weak scan -> INFERRED but corroborated edit",
      e["confidence"] == "INFERRED" and e.get("cross_validated") and e["disposition"] == "edit")
e = one([{"path": "docs/a.md", "score": 95, "ms_author": "alex"}], [])
check("single-source strong scan is NOT cross_validated", not e.get("cross_validated"))

print("\nPhase 1.1 — structural skip-list:")
r = df.reconcile([{"path": "docs/whats-new/index.md", "score": 100, "ms_author": "jordan"},
                  {"path": "docs/app-management/x.md", "score": 80, "ms_author": "alex"}], [])
paths = {a["path"] for a in r}
check("whats-new/index.md dropped (aggregator, never an edit target)", "docs/whats-new/index.md" not in paths)
check("real article kept", "docs/app-management/x.md" in paths)
check("*.yml dropped", df.reconcile([{"path": "docs/app-management/toc.yml", "score": 90, "ms_author": "alex"}], []) == [])
check("includes/ dropped", df.reconcile([{"path": "docs/includes/snip.md", "score": 90, "ms_author": "alex"}], []) == [])
check("--no-skip keeps whats-new",
      len(df.reconcile([{"path": "docs/whats-new/index.md", "score": 100, "ms_author": "jordan"}], [], skip_enabled=False)) == 1)

print("\nPhase 1.1 — platform scoping (feature = iOS/iPadOS):")
def p1(path, score=100):
    return one([{"path": path, "score": score, "ms_author": "alex"}], [], platforms="iOS/iPadOS")
check("add-lob-ios kept as edit", p1("docs/app-management/deployment/add-lob-ios.md")["disposition"] == "edit")
e = p1("docs/app-management/deployment/add-lob-windows.md")
check("add-lob-windows demoted to list-only", e["disposition"] == "list-only" and e.get("off_platform"))
check("add-lob-android demoted", p1("docs/app-management/deployment/add-lob-android.md").get("off_platform"))
check("add-lob-macos demoted (different Apple platform)",
      p1("docs/app-management/deployment/add-lob-macos.md")["disposition"] == "list-only")
check("manage-vpp-apple kept (apple-generic matches iOS family)",
      p1("docs/app-management/deployment/manage-vpp-apple.md")["disposition"] == "edit")
check("platform-agnostic article kept",
      p1("docs/app-management/configuration/configure-example-portal.md")["disposition"] == "edit")
check("no platforms given -> no demotion",
      one([{"path": "docs/x-windows.md", "score": 100, "ms_author": "alex"}], [])["disposition"] == "edit")

print("\nPhase 2.1 — god-node down-weight (hub/overview/landing pages never auto-edit):")
# ms.topic is authoritative when the scan carries it
e = one([{"path": "docs/app-management/x.md", "score": 95, "ms_author": "alex", "ms_topic": "overview"}], [])
check("ms.topic=overview -> list-only + god_node", e["disposition"] == "list-only" and e.get("god_node"))
check("ms.topic=landing -> god_node",
      one([{"path": "docs/x/index.md", "score": 95, "ms_author": "alex", "ms_topic": "landing"}], []).get("god_node"))
# filename heuristic when ms.topic is absent
check("overview.md filename -> god_node",
      one([{"path": "docs/app-management/overview.md", "score": 90, "ms_author": "alex"}], []).get("god_node"))
check("index.md filename -> god_node",
      one([{"path": "docs/app-management/deployment/index.md", "score": 90, "ms_author": "alex"}], []).get("god_node"))
# ms.topic overrides the filename: an explicit how-to named overview.md is NOT a hub
e = one([{"path": "docs/app-management/overview.md", "score": 90, "ms_author": "alex", "ms_topic": "how-to"}], [])
check("ms.topic=how-to on overview.md -> NOT god_node (topic wins)",
      not e.get("god_node") and e["disposition"] == "edit")
# THE §12 scenario: a cross-validated hub is demoted OUT of auto-edit but stays flagged
e = one([{"path": "docs/app-management/overview.md", "score": 92, "ms_author": "alex"}],
        [{"path": "docs/app-management/overview.md", "blast": "direct", "ms_author": "alex"}])
check("cross-validated god-node -> still cross_validated but list-only (out of auto-edit)",
      e.get("cross_validated") and e.get("god_node") and e["disposition"] == "list-only")
check("god-node note explains the demotion", "god-node" in (e.get("note") or ""))
# escape hatch + no false positives
check("--keep-god-nodes keeps the hub editable",
      one([{"path": "docs/app-management/overview.md", "score": 92, "ms_author": "alex"}], [],
          god_nodes=False)["disposition"] == "edit")
check("a real feature article stays editable (not a hub)",
      one([{"path": "docs/app-management/configuration/configure-example-portal.md", "score": 90, "ms_author": "alex"}], [])["disposition"] == "edit")
# skip-list precedence: whats-new/index.md is DROPPED, never surfaced as a god-node
check("whats-new/index.md dropped by skip-list (not reclassified as god-node)",
      df.reconcile([{"path": "docs/whats-new/index.md", "score": 100, "ms_author": "jordan"}], []) == [])

print("\nempty input is safe:")
check("no candidates -> empty list", df.reconcile([], []) == [])

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

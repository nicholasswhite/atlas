#!/usr/bin/env python3
"""Unit tests for doc_edit_plan routing + batching (Phase 2) — run:
    python tests/test_doc_edit_plan.py

The planner turns Phase 1 discovery (affected_articles) into an accuracy-tiered, owner-gated,
batched edit/handoff plan: auto-edit only the verified (EXTRACTED / cross_validated) owned set;
everything else is a typed handoff (suggested-edit / review / cross-owner / owner-unknown).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import doc_edit_plan as dep  # noqa: E402

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


EDITABLE = {"alex", "morgan", "casey"}


def art(path, conf, disp="edit", owner="alex", xval=False):
    e = {"path": path, "confidence": conf, "disposition": disp, "ms_author": owner}
    if xval:
        e["cross_validated"] = True
    return e


print("routing tiers:")
p = dep.plan([
    art("docs/a.md", "EXTRACTED", xval=True),                  # verified -> auto
    art("docs/b.md", "INFERRED"),                             # owned + inferred -> suggested
    art("docs/c.md", "INFERRED", disp="list-only"),           # list-only -> review
    art("docs/d.md", "EXTRACTED", owner="taylor", xval=True),  # non-roster owner -> cross-owner
    art("docs/e.md", "INFERRED", owner="(unknown)"),          # unresolved -> owner-unknown
], EDITABLE)
check("verified owned -> auto_edit", [a["path"] for a in p["auto_edit"]] == ["docs/a.md"])
check("owned + inferred -> suggested-edit", p["handoff"]["suggested-edit"][0]["path"] == "docs/b.md")
check("list-only -> review", p["handoff"]["review"][0]["path"] == "docs/c.md")
check("non-roster owner -> cross-owner", p["handoff"]["cross-owner"][0]["path"] == "docs/d.md")
check("unresolved owner -> owner-unknown", p["handoff"]["owner-unknown"][0]["path"] == "docs/e.md")
check("counts add up",
      p["counts"]["auto_edit"] == 1 and p["counts"]["suggested_edit"] == 1
      and p["counts"]["cross_owner"] == 1 and p["counts"]["owner_unknown"] == 1)

print("\nFinding 2 — owner triage precedes disposition (synthetic regression):")
p_f2 = dep.plan([
    art("docs/lo-taylor.md", "INFERRED", disp="list-only", owner="taylor"),     # list-only + non-roster
    art("docs/lo-unknown.md", "INFERRED", disp="list-only", owner="(unknown)"),  # list-only + unknown
    art("docs/lo-alex.md", "INFERRED", disp="list-only", owner="alex"),      # list-only + owned -> review
], EDITABLE)
check("list-only non-roster owner -> cross-owner (not review)",
      [a["path"] for a in p_f2["handoff"]["cross-owner"]] == ["docs/lo-taylor.md"])
check("list-only unknown owner -> owner-unknown (not review)",
      [a["path"] for a in p_f2["handoff"]["owner-unknown"]] == ["docs/lo-unknown.md"])
check("list-only owned-by-roster -> review",
      [a["path"] for a in p_f2["handoff"]["review"]] == ["docs/lo-alex.md"])

print("\ncross_validated INFERRED still auto-edits (agreement counts as verification):")
p2 = dep.plan([art("docs/x.md", "INFERRED", xval=True)], EDITABLE)
check("xval INFERRED -> auto_edit", [a["path"] for a in p2["auto_edit"]] == ["docs/x.md"])

print("\nbatching + large-set flag:")
many = [art(f"docs/n{i}.md", "EXTRACTED", xval=True) for i in range(25)]
p3 = dep.plan(many, EDITABLE, max_per_pr=10, large_threshold=12)
check("25 auto-edits -> 3 PR batches of <=10", len(p3["batches"]) == 3 and len(p3["batches"][0]) == 10)
check("large_set flagged", p3["large_set"] is True)
check("small set not flagged",
      dep.plan([art("docs/a.md", "EXTRACTED", xval=True)], EDITABLE, large_threshold=12)["large_set"] is False)

print("\nsort: cross-validated first:")
p5 = dep.plan([art("docs/z.md", "EXTRACTED"), art("docs/a.md", "EXTRACTED", xval=True)], EDITABLE)
check("cross_validated sorts before plain EXTRACTED", p5["auto_edit"][0]["path"] == "docs/a.md")

print("\nroster editable resolution:")
roster = {"writers": [{"alias": "alex", "enabled": True}, {"alias": "taylor", "enabled": False}],
          "retiring_reassign": [{"alias": "jordan"}]}
check("enabled-only by default", dep._editable_from_roster(roster, False) == {"alex"})
check("--all-authors includes disabled + retiring",
      dep._editable_from_roster(roster, True) == {"alex", "taylor", "jordan"})
check("test_mode_all_authors NOT auto-honored (plan shows production triage)",
      dep._editable_from_roster({**roster, "test_mode_all_authors": True}, False) == {"alex"})

print("\nCLI _load accepts a dossier patch or a bare list:")
check("dossier-patch dict -> articles", dep._load(json.dumps({"affected_articles": [art("docs/a.md", "EXTRACTED")]})) [0]["path"] == "docs/a.md")
check("bare list -> articles", dep._load(json.dumps([art("docs/a.md", "EXTRACTED")]))[0]["path"] == "docs/a.md")

print("\nPhase 2.1 — god-node handoff (hubs never auto-edit, even when verified):")
god = {"path": "docs/app-management/overview.md", "confidence": "EXTRACTED",
       "disposition": "list-only", "ms_author": "alex", "cross_validated": True, "god_node": True}
pg = dep.plan([god, art("docs/a.md", "EXTRACTED", xval=True)], EDITABLE)
check("god_node -> god-node handoff", [a["path"] for a in pg["handoff"]["god-node"]] == ["docs/app-management/overview.md"])
check("god-node kept OUT of auto_edit even though EXTRACTED + xval", [a["path"] for a in pg["auto_edit"]] == ["docs/a.md"])
check("god_node count recorded", pg["counts"]["god_node"] == 1)
check("god-node entry carries a no-op reason", "no-op" in pg["handoff"]["god-node"][0]["reason"])
check("_entry surfaces god_node", pg["handoff"]["god-node"][0]["god_node"] is True)
# defense in depth: the flag alone routes to god-node even if doc_footprint didn't demote disposition
g2 = {"path": "docs/x/index.md", "confidence": "EXTRACTED", "disposition": "edit",
      "ms_author": "alex", "cross_validated": True, "god_node": True}
pg2 = dep.plan([g2], EDITABLE)
check("god_node flag routes to god-node even if disposition=edit",
      pg2["counts"]["god_node"] == 1 and pg2["counts"]["auto_edit"] == 0)

print("\nempty input is safe:")
check("empty -> zero plan", dep.plan([], EDITABLE)["counts"]["auto_edit"] == 0)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

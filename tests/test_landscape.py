#!/usr/bin/env python3
"""Unit tests for landscape.py — the Stage -1 release relationship resolver — run:
    python tests/test_landscape.py

Synthetic release fixture: the Plugin combined blurb (1 blurb, N items), the
Sync cluster (4 workstreams, not yet combined), tagless PM-authored content, and a retiring-writer
orphan. Covers family extraction, combined-blurb union, clustering, tagless detection, coverage,
and the per_item consumable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import landscape as ls  # noqa: E402

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


def item(iid, title, writer="Casey Example", area="ExampleProduct\\Mgmt\\Plugin", tags=None, refs=None, content=True):
    return {"id": iid, "title": title, "area_path": area, "comms_writer": writer,
            "crw_tags": tags or [], "crw_refs": refs or [], "crw_has_content": content}


# The Casey-2608-shaped fixture.
Plugin = ["90001021", "90001022", "90001023"]   # the combined-blurb marker (90001023 is external)
ITEMS = [
    item("90001021", "[2608] Add Plugin App for Aurora - Aurora", tags=["wndraft"], refs=Plugin),
    item("90001022", "[2608] Add Plugin App for Meridian - Meridian", tags=["wndraft"], refs=Plugin),
    item("90001024", "[2608] Add Plugin App Cedar Labs - Cedar", tags=["wndraft"], refs=Plugin),
    item("90001018", "[Sync] [Workstream 1] Support expanded sync status", area="ExampleProduct\\Mgmt\\Android", tags=[], refs=[]),
    item("90001019", "[Sync] [Workstream 4] Single and bulk activation", area="ExampleProduct\\Mgmt\\Android", tags=[], refs=[]),
    item("90001020", "[Sync] [Workstream 6] Removal of a sync connection", area="ExampleProduct\\Mgmt\\Android", tags=[], refs=[]),
    item("90001017", "Require a passcode for plugin sessions", area="ExampleProduct\\Mgmt\\Plugin Android", tags=["iddraft", "wndraft"]),
    item("90001011", "[Tunnel] [iOS Plugin] Workspace validation", writer="Jordan Example", area="ExampleProduct\\Mgmt\\iOS Tunnel", tags=[]),
]
L = ls.build_landscape(ITEMS, release="2608", retiring=["taylor", "jordan"])

print("_family — bracket / plugin / area fallback, skipping release + workstream:")
check("first meaningful bracket wins ([Sync] over [Workstream])", ls._family("[Sync] [Workstream 4] X") == "sync")
check("release YYMM bracket skipped -> plugin app pattern", ls._family("[2608] Add Plugin App for Aurora") == "plugin app")
check("area-path leaf fallback", ls._family("No brackets here", "ExampleProduct\\Mgmt\\iOS Tunnel") == "ios tunnel")
check("skips phase/wave too", ls._family("[Phase 2] [SEB] Thing") == "seb")

print("\ncombined blurbs — the Plugin marker (1 blurb, N items):")
cb = L["combined_blurbs"]
check("exactly one combined blurb", len(cb) == 1)
g = cb[0]
check("primary = first id in the marker", g["primary"] == "90001021")
check("present members = the in-slate ones", set(g["present"]) == {"90001021", "90001022", "90001024"})
check("external member captured (referenced, not in slate)", g["external"] == ["90001023"])
check("track inferred wn", g["track"] == ["wn"])

print("\ncombined blurbs — REAL-SLATE mixed markers (regression, Casey 2608): the primary self-lists a")
print("SHORT marker while satellites carry a LONGER marker WITHOUT self-listing -> ONE group, no phantom:")
# 90001021's own marker lists [self, 90001022]; the Cedar/Harbor/... satellites carry
# [90001021, 90001022, 90001023] and do NOT list themselves. Union must merge everything into a
# single group (primary 90001021), NOT split off a phantom [90001021, 90001022] group (the stale-rep
# bug the real slate exposed 2026-07-01).
_SHORT = ["90001021", "90001022"]
_LONG = ["90001021", "90001022", "90001023"]
MIX = [
    item("90001021", "[2608] Add Plugin App for Aurora", tags=["wndraft"], refs=_SHORT),
    item("90001022", "[2608] Add Plugin App for Meridian", tags=["wndraft"], refs=[], content=False),
    item("90001024", "[2608] Add Plugin App Cedar", tags=["wndraft"], refs=_LONG),
    item("90001025", "[2608] Update Plugin App Harbor", tags=["wndraft"], refs=_LONG),
    item("90001026", "[2608] Add Plugin App Summit", tags=["wndraft"], refs=_LONG),
    item("90001027", "[2608] Add Plugin App Meadow", tags=["wndraft"], refs=_LONG),
]
Lm = ls.build_landscape(MIX, release="2608")
cbm = Lm["combined_blurbs"]
check("mixed markers -> exactly ONE combined blurb (no phantom split)", len(cbm) == 1)
gm = cbm[0] if cbm else {"primary": None, "present": [], "external": []}
check("satellite that doesn't self-list still resolves to true primary 90001021", gm["primary"] == "90001021")
check("all 6 in-slate members present (union across short + long markers)",
      set(gm["present"]) == {"90001021", "90001022", "90001024", "90001025", "90001026", "90001027"})
check("external (90001023) captured exactly once", gm["external"] == ["90001023"])
check("every satellite points at the same primary",
      all(Lm["per_item"][x]["combined_primary"] == "90001021" for x in ["90001022", "90001024", "90001027"]))

print("\nper_item roles — primary / secondary / standalone:")
check("primary role", L["per_item"]["90001021"]["role"] == "primary")
check("secondary role + points at primary", L["per_item"]["90001022"]["role"] == "secondary" and L["per_item"]["90001022"]["combined_primary"] == "90001021")
check("secondary note says act on primary", "act on primary" in L["per_item"]["90001024"]["note"])
check("standalone role", L["per_item"]["90001017"]["role"] == "standalone")

print("\nclusters — Sync (suggest combine) vs Plugin (already combined):")
byfam = {c["family"]: c for c in L["clusters"]}
check("Sync family clustered (3)", "sync" in byfam and len(byfam["sync"]["members"]) == 3)
check("Sync SUGGEST combine (not yet one blurb)", byfam["sync"]["suggest_combine"] is True)
check("Plugin family already_combined (shares one primary)", byfam["plugin app"]["already_combined"] is True)
check("Plugin does NOT suggest combine", byfam["plugin app"]["suggest_combine"] is False)
check("singletons don't form a cluster", "ios tunnel" not in byfam and "plugin android" not in byfam)

print("\ntagless content — enrich, don't re-draft:")
check("Sync items are tagless (content, no tag)", set(["90001018", "90001019", "90001020"]).issubset(set(L["tagless_content"])))
check("tagged item NOT tagless (has iddraft/wndraft)", "90001017" not in L["tagless_content"])
check("combined-blurb members NOT tagless (they carry wndraft)", "90001021" not in L["tagless_content"])
check("tagless note surfaces", "ENRICH" in L["per_item"]["90001018"]["note"])
check("cluster note surfaces on an un-combined family member", "combined blurb" in L["per_item"]["90001019"]["note"])
check("no-content item is never tagless",
      "z" not in ls.build_landscape([item("z", "Empty", tags=[], content=False)])["tagless_content"])

print("\ncoverage + orphaned (retiring writers):")
check("Jordan's item is orphaned (retiring)", any(o["id"] == "90001011" for o in L["orphaned"]))
check("orphan reason = retiring writer", next(o for o in L["orphaned"] if o["id"] == "90001011")["reason"] == "retiring writer")
check("Casey item not orphaned", not any(o["id"] == "90001021" for o in L["orphaned"]))
check("coverage groups by writer", "Casey Example" in L["coverage"] and "Jordan Example" in L["coverage"])
check("orphan note surfaces", "needs coverage" in L["per_item"]["90001011"]["note"])
# unassigned writer -> orphaned
Lu = ls.build_landscape([item("u", "X", writer="")], retiring=[])
check("unassigned writer -> orphaned", Lu["orphaned"][0]["reason"] == "no comms writer")

print("\nretiring normalization (alias or display name) + empty:")
check("display name 'Jordan Example' matches alias 'jordan'",
      ls._norm_retiring(["jordan"]) == ls._norm_retiring(["Jordan Example"]))
check("empty items -> zero landscape", ls.build_landscape([])["item_count"] == 0)
check("counts consistent", L["item_count"] == 8)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

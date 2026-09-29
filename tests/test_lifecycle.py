#!/usr/bin/env python3
"""Regression tests for lifecycle.resolve — run: python tests/test_lifecycle.py

Covers the original behavior (release signals absent = unchanged) plus the release-branch
awareness fix that distinguishes "staged on the release branch" (in-flight, correct) from
"published" (in main) and "stale/stranded" (nowhere but toggle says published).

Two synthetic lifecycle cases:
  - 90001013: WN staged on 2604_service_release, not main, toggle True -> staged-release, NO conflict
  - 90001016: WN nowhere (off the release train), toggle True            -> stale/stranded conflict
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lifecycle  # noqa: E402

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


def track(res, which):
    return res[which]


def has_conflict(res, topic_substr):
    return any(topic_substr in c.get("topic", "") for c in res.get("conflicts", []))


# --- Backward compatibility: release signals absent => original behavior -----------------
print("backward-compatibility (no release signals):")

# 1. WN live in main -> published
r = lifecycle.resolve({"ado_id": "1", "wn_required": True, "in_wn_doc": True,
                       "published_wn_toggle": True, "crw_has_content": True})
check("WN in main -> published/none", track(r, "wn")["stage"] == "published"
      and track(r, "wn")["route"] == "none")

# 2. WN nowhere but toggle True -> stale conflict (the ORIGINAL false-positive-prone path,
#    correct when there is no release-branch signal to say otherwise)
r = lifecycle.resolve({"ado_id": "2", "wn_required": True, "in_wn_doc": False,
                       "published_wn_toggle": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"]})
check("WN nowhere + toggle -> doc_vs_toggle conflict", has_conflict(r, "wn_doc_vs_toggle"))

# 3. Required, no CRW -> handoff
r = lifecycle.resolve({"ado_id": "3", "id_required": True, "wn_required": True,
                       "crw_has_content": False, "in_id_doc": False, "in_wn_doc": False})
check("no CRW -> handoff", track(r, "id")["route"] == "handoff")

# 4. Draft tag, no ready -> awaiting_review, routed to the APPROVAL stage (3.5), not straight to
#    notify. This is what makes approval detection reachable after the draft (not before it).
r = lifecycle.resolve({"ado_id": "4", "id_required": True, "crw_has_content": True,
                       "crw_tags": ["iddraft"], "in_id_doc": False})
check("iddraft -> awaiting_review", track(r, "id")["stage"] == "awaiting_review")
check("awaiting_review routes to approval (Stage 3.5)", track(r, "id")["route"] == "approval")

# 5. Ready tag, not staged -> ready_to_stage
r = lifecycle.resolve({"ado_id": "5", "wn_required": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready"], "in_wn_doc": False})
check("wnready -> ready_to_stage", track(r, "wn")["stage"] == "ready_to_stage")

# 6. Staged tag, not in doc, no release signal -> staged (pending merge)
r = lifecycle.resolve({"ado_id": "6", "wn_required": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"], "in_wn_doc": False})
check("wnstaged (no release sig) -> staged", track(r, "wn")["stage"] == "staged")

# 7. Private Preview hold
r = lifecycle.resolve({"ado_id": "7", "wn_required": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready"], "in_wn_doc": False,
                       "private_preview_date": "2026-09-01", "release_date": "2026-08-01"})
check("Private Preview hold -> held", track(r, "wn")["stage"] == "held")

# 8. idready tag but requirement toggle False -> tag_vs_toggle conflict
r = lifecycle.resolve({"ado_id": "8", "id_required": False, "crw_has_content": True,
                       "crw_tags": ["iddraft", "idready"], "in_id_doc": False})
check("idready + required False -> tag_vs_toggle conflict", has_conflict(r, "id_tag_vs_toggle"))

# 9. Live in doc but toggle False -> doc_vs_toggle (stale toggle the other direction)
r = lifecycle.resolve({"ado_id": "9", "wn_required": True, "in_wn_doc": True,
                       "published_wn_toggle": False, "crw_has_content": True})
check("WN in main + toggle False -> doc_vs_toggle conflict", has_conflict(r, "wn_doc_vs_toggle"))

# --- New: release-branch awareness ------------------------------------------------------
print("\nrelease-branch awareness (the fix):")

# 10. ANCHOR 90001013: WN on release branch, not main, toggle True -> staged-release, NO conflict
r = lifecycle.resolve({"ado_id": "90001013", "wn_required": True, "in_wn_doc": False,
                       "in_wn_release": True, "published_wn_toggle": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"]})
check("90001013: on release branch -> staged-release", track(r, "wn")["stage"] == "staged-release")
check("90001013: NO stale conflict", not has_conflict(r, "wn_doc_vs_toggle"))

# 11. ANCHOR 90001016: WN nowhere (not release, not main), toggle True -> stale/stranded conflict
r = lifecycle.resolve({"ado_id": "90001016", "wn_required": True, "in_wn_doc": False,
                       "in_wn_release": False, "published_wn_toggle": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"]})
check("90001016: stranded -> doc_vs_toggle conflict", has_conflict(r, "wn_doc_vs_toggle"))
check("90001016: not staged-release", track(r, "wn")["stage"] != "staged-release")

# 12. Release branch + toggle not yet flipped -> still staged-release, no conflict
r = lifecycle.resolve({"ado_id": "12", "wn_required": True, "in_wn_doc": False,
                       "in_wn_release": True, "published_wn_toggle": False, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"]})
check("release branch + toggle False -> staged-release", track(r, "wn")["stage"] == "staged-release")
check("release branch + toggle False -> no conflict", not has_conflict(r, "wn_doc_vs_toggle"))

# 13. In main wins over release-branch signal (published is terminal)
r = lifecycle.resolve({"ado_id": "13", "wn_required": True, "in_wn_doc": True,
                       "in_wn_release": True, "published_wn_toggle": True, "crw_has_content": True})
check("in main beats release signal -> published", track(r, "wn")["stage"] == "published")

# --- Published-lifecycle false-positive guard (found by Pass 3, 2026-06-12) ----------------
print("\npublished-lifecycle guard (ID moved to WN on ship):")

# 14. ANCHOR 90001008/90001012/90001014: feature shipped — ID gone from in-dev, now in WN,
#     pubID True. This is the normal published lifecycle, NOT a stale id_doc_vs_toggle conflict.
r = lifecycle.resolve({"ado_id": "90001008", "id_required": True, "wn_required": True,
                       "in_id_doc": False, "in_wn_doc": True,
                       "published_id_toggle": True, "published_wn_toggle": True,
                       "crw_has_content": True,
                       "crw_tags": ["iddraft", "idready", "idstaged", "wndraft", "wnready"]})
check("shipped item: ID retired (in WN)", track(r, "id")["stage"] == "retired")
check("shipped item: NO id_doc_vs_toggle conflict", not has_conflict(r, "id_doc_vs_toggle"))

# 15. Genuinely stale ID toggle (not in any doc, NOT in WN) still flags
r = lifecycle.resolve({"ado_id": "15b", "id_required": True, "in_id_doc": False,
                       "in_wn_doc": False, "in_id_release": False,
                       "published_id_toggle": True, "crw_has_content": True,
                       "crw_tags": ["iddraft", "idready", "idstaged"]})
check("stale ID (not in WN either) still flags conflict", has_conflict(r, "id_doc_vs_toggle"))

# --- Archive awareness (found by Round 2, 2026-06-12) --------------------------------------
print("\narchive awareness (published-then-rotated-out):")

# 16. ANCHOR 90001004/90001006/90001009: shipped, then rotated into the WN archive after ~6mo.
#     Absent from current index.md but pubWN True is CORRECT — not a stale toggle.
r = lifecycle.resolve({"ado_id": "90001004", "wn_required": True, "in_wn_doc": False,
                       "in_wn_release": False, "in_wn_archive": True,
                       "published_wn_toggle": True, "crw_has_content": True,
                       "crw_tags": ["wndraft", "wnready", "wnstaged"]})
check("archived WN: stage published", track(r, "wn")["stage"] == "published")
check("archived WN: NO stale conflict", not has_conflict(r, "wn_doc_vs_toggle"))

# 17. ID retired when its WN blurb is in the archive (feature shipped long ago)
r = lifecycle.resolve({"ado_id": "90001009", "id_required": True, "wn_required": True,
                       "in_id_doc": False, "in_wn_doc": False, "in_wn_archive": True,
                       "published_id_toggle": True, "published_wn_toggle": True,
                       "crw_has_content": True,
                       "crw_tags": ["iddraft", "idready", "idstaged", "wndraft", "wnready", "wnstaged"]})
check("archived: ID retired", track(r, "id")["stage"] == "retired")
check("archived: no id_doc_vs_toggle", not has_conflict(r, "id_doc_vs_toggle"))
check("archived: no wn_doc_vs_toggle", not has_conflict(r, "wn_doc_vs_toggle"))

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

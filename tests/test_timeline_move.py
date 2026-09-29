#!/usr/bin/env python3
"""Regression tests for timeline_move.resolve_moves — run:
    python tests/test_timeline_move.py

Covers the cross-release move planner: divergence detection (forward slip + backward pull-in),
the no-move healthy cases (aligned, not-staged), ID-vs-WN independence, custom branch patterns,
year rollover, and the guard rails (missing timeline, non-YYMM values).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import timeline_move as tm  # noqa: E402

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


print("timeline_move — divergence detection:")

# 1. WN slipped: staged on 2606, timeline now 2607 -> forward move.
r = tm.resolve_moves({"ado_id": "90001015", "current_timeline": "2607",
                      "staged_wn_release": "2606"})
check("WN slip 2606->2607 = move required", r["move_required"] is True)
check("WN move is forward", r["wn"]["move"] and r["wn"]["direction"] == "forward")
check("WN from_branch is old release", r["wn"]["from_branch"] == "release-docs-2606")
check("WN to_branch is new release", r["wn"]["to_branch"] == "release-docs-2607")
check("WN plan has 4 steps", len(r["wn"]["steps"]) == 4)
check("WN route is wn-staging", r["wn"]["route"] == "wn-staging")
check("WN move is gated", r["wn"]["gated"] is True)

# 2. Aligned: staged on the same release as the timeline -> no move.
r = tm.resolve_moves({"ado_id": "1", "current_timeline": "2607", "staged_wn_release": "2607"})
check("aligned = no move required", r["move_required"] is False)
check("aligned WN move False", r["wn"]["move"] is False)

# 3. Not staged anywhere -> nothing to move (healthy / the normal ID case).
r = tm.resolve_moves({"ado_id": "2", "current_timeline": "2607",
                      "staged_id_release": None, "staged_wn_release": None})
check("not staged = no move", r["move_required"] is False)
check("not-staged reason mentions nothing to move", "nothing to move" in r["wn"]["reason"])

# 4. Track independence: ID on main (None), WN slipped -> only WN moves.
r = tm.resolve_moves({"ado_id": "3", "current_timeline": "2607",
                      "staged_id_release": None, "staged_wn_release": "2605"})
check("only WN moves", r["move_required"] and r["wn"]["move"] and not r["id"]["move"])
check("moves list has exactly one entry", len(r["moves"]) == 1)

# 5. ID staged on a release branch too -> ID also moves.
r = tm.resolve_moves({"ado_id": "4", "current_timeline": "2607",
                      "staged_id_release": "2606", "staged_wn_release": "2606"})
check("both tracks move", r["id"]["move"] and r["wn"]["move"] and len(r["moves"]) == 2)
check("ID route is id-staging", r["id"]["route"] == "id-staging")

# 6. Backward pull-in: staged on 2608, timeline now 2607 -> backward move.
r = tm.resolve_moves({"ado_id": "5", "current_timeline": "2607", "staged_wn_release": "2608"})
check("backward pull-in detected", r["wn"]["move"] and r["wn"]["direction"] == "backward")

# 7. Year rollover: staged 2512 (Dec), timeline 2601 (Jan) -> forward (numeric, not lexical trap).
r = tm.resolve_moves({"ado_id": "6", "current_timeline": "2601", "staged_wn_release": "2512"})
check("year rollover 2512->2601 = forward", r["wn"]["move"] and r["wn"]["direction"] == "forward")

print("\ntimeline_move — guard rails:")

# 8. Missing current timeline -> can't compare, no move, surfaces need.
r = tm.resolve_moves({"ado_id": "7", "staged_wn_release": "2606"})
check("missing timeline = no move", r["wn"]["move"] is False)
check("missing timeline surfaces need", r["wn"].get("needs") == "current_timeline")

# 9. Non-YYMM staged value -> skip auto-move, surface for a human.
r = tm.resolve_moves({"ado_id": "8", "current_timeline": "2607", "staged_wn_release": "backlog"})
check("non-YYMM staged = no auto-move", r["wn"]["move"] is False)
check("non-YYMM surfaces need", r["wn"].get("needs") == "valid timeline")

# 10. Custom branch pattern respected.
r = tm.resolve_moves({"ado_id": "9", "current_timeline": "2607", "staged_wn_release": "2606",
                      "branch_pattern": "{}_service_release"})
check("custom branch pattern from", r["wn"]["from_branch"] == "2606_service_release")
check("custom branch pattern to", r["wn"]["to_branch"] == "2607_service_release")

# 11. Numeric (non-string) timeline values normalize fine.
r = tm.resolve_moves({"ado_id": "10", "current_timeline": 2607, "staged_wn_release": 2606})
check("numeric inputs normalize + move", r["wn"]["move"] and r["wn"]["to_release"] == "2607")

# 12. Empty signals -> no move, no crash.
r = tm.resolve_moves({})
check("empty signals = no move", r["move_required"] is False)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

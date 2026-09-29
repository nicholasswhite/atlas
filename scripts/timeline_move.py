#!/usr/bin/env python3
"""Atlas — cross-release move planner.

When a feature's CommsTimeline changes *after* its blurb was staged onto a release branch,
the staged content is stranded on the OLD release branch. A fresh lifecycle run re-classifies
against the new timeline and would happily re-stage onto the NEW branch, but nothing removes
the orphaned copy from the old one. This module deterministically detects that divergence and
emits a move plan: pull the blurb off the old release branch, re-stage it on the new one, fix
the tracking tags, and notify the PM.

Pure + deterministic. It does NO git, NO ADO writes, NO sends — it reads signals (which the
orchestrator gathers: the feature's current CommsTimeline, and which release branch each track
is actually staged on) and returns a plan. The skill layer executes it, gated by simulation_only
and the merge gate (the move lands as a PR a human merges; it never auto-merges to a live branch).

Primary case is **What's New**: WN is staged per-release on `release-docs-{YYMM}` and only
merges to main when the release publishes. In-development blurbs live in in-development.md on
main and don't normally move between release branches, so a track only earns a move when it is
genuinely found on a release branch *other* than the current-timeline one (staged_*_release set
and != current_timeline).

Signals schema (all optional; absent = unknown):
    {
      "ado_id": "90001015",
      "current_timeline": "2607",     # WorkItem.CommsTimeline as it reads NOW
      "staged_id_release": null,        # release the ID blurb is currently staged on (or null)
      "staged_wn_release": "2606",      # release the WN blurb is currently staged on (or null)
      "branch_pattern": "release-docs-{}"   # optional; {} is filled with the YYMM
    }

The orchestrator populates staged_*_release by checking the current-timeline release branch AND
a small window of recent prior release branches (e.g. the previous 1-2) for the blurb; the first
branch it's found on is that track's staged_*_release.

CLI:
    timeline_move.py --signals '<json>'
    timeline_move.py --signals-file path.json
    cat signals.json | timeline_move.py
    add --explain for a human-readable summary on stderr
"""
from __future__ import annotations

import argparse
import json
import sys

DEFAULT_BRANCH_PATTERN = "release-docs-{}"

ROUTE_ID_STAGING = "id-staging"
ROUTE_WN_STAGING = "wn-staging"


def _norm(v) -> str | None:
    """Normalize a release/timeline value to a trimmed string, or None if empty."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _is_yymm(v: str | None) -> bool:
    """A 4-digit YYMM release number (e.g. 2607). Year-major, so int and lexicographic
    ordering agree, which makes forward/backward detection unambiguous within a century."""
    return bool(v) and v.isdigit() and len(v) == 4


def plan_track(track: str, current_timeline, staged_release, branch_pattern: str) -> dict:
    """Decide whether one track needs a cross-release move, and emit its plan."""
    is_id = track == "id"
    cur = _norm(current_timeline)
    staged = _norm(staged_release)
    base = {"track": track.upper(), "move": False}

    # Not staged on any release branch → nothing to move (the common, healthy case, and the
    # normal state for ID, which lives in in-development.md on main).
    if not staged:
        return {**base, "reason": "Not staged on a release branch; nothing to move."}
    # Can't compare without the current timeline.
    if not cur:
        return {**base, "reason": "Current CommsTimeline unknown; cannot compare.",
                "needs": "current_timeline"}
    # Non-YYMM values (typo, placeholder) → don't guess a move; surface for a human.
    if not _is_yymm(cur) or not _is_yymm(staged):
        return {**base, "reason": f"Non-YYMM value (staged={staged}, current={cur}); "
                                  "skip auto-move and surface for a human.",
                "needs": "valid timeline"}
    # Aligned → no move.
    if staged == cur:
        return {**base, "reason": f"Aligned — staged on {staged}, timeline is {cur}."}

    # Divergence → move required.
    direction = "forward" if int(cur) > int(staged) else "backward"
    from_branch = branch_pattern.format(staged)
    to_branch = branch_pattern.format(cur)
    article = "in-development" if is_id else "What's New"
    return {
        "track": track.upper(),
        "move": True,
        "direction": direction,        # forward = slipped to a later release; backward = pulled in
        "from_release": staged,
        "to_release": cur,
        "from_branch": from_branch,
        "to_branch": to_branch,
        "route": ROUTE_ID_STAGING if is_id else ROUTE_WN_STAGING,
        "gated": True,                 # execution waits on simulation_only + the merge gate
        "steps": [
            f"Remove the {track.upper()} blurb from {from_branch} (the {staged} {article} release).",
            f"Re-stage the {track.upper()} blurb onto {to_branch} (the {cur} release).",
            f"Update the CRW tracking tags / CommsDocChanges to reflect {cur}.",
            f"Notify the PM that the item moved from {staged} to {cur}.",
        ],
        "reason": f"CommsTimeline is {cur} but the {track.upper()} blurb is staged on the "
                  f"{staged} release branch. Move it to stay in sync with the release.",
    }


def resolve_moves(signals: dict) -> dict:
    """Resolve both tracks and report whether any cross-release move is required."""
    pattern = signals.get("branch_pattern") or DEFAULT_BRANCH_PATTERN
    cur = signals.get("current_timeline")
    id_plan = plan_track("id", cur, signals.get("staged_id_release"), pattern)
    wn_plan = plan_track("wn", cur, signals.get("staged_wn_release"), pattern)
    moves = [p for p in (id_plan, wn_plan) if p.get("move")]
    return {
        "ado_id": signals.get("ado_id"),
        "current_timeline": _norm(cur),
        "id": id_plan,
        "wn": wn_plan,
        "move_required": bool(moves),
        "moves": moves,
    }


def explain(res: dict) -> str:
    lines = [f"Timeline move — {res.get('ado_id') or '(no id)'} "
             f"(current timeline: {res.get('current_timeline') or '?'})"]
    for track in ("id", "wn"):
        t = res[track]
        if t.get("move"):
            lines.append(f"  {t['track']}: MOVE {t['direction']} "
                         f"{t['from_release']} -> {t['to_release']} "
                         f"({t['from_branch']} -> {t['to_branch']})")
            for step in t["steps"]:
                lines.append(f"      - {step}")
        else:
            lines.append(f"  {t['track']}: no move — {t['reason']}")
    lines.append(f"  >> MOVE REQUIRED: {res['move_required']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas cross-release move planner.")
    p.add_argument("--signals", default=None, help="JSON signals string.")
    p.add_argument("--signals-file", default=None, help="Path to a JSON signals file.")
    p.add_argument("--explain", action="store_true", help="Print a human-readable summary to stderr.")
    args = p.parse_args(argv)

    if args.signals:
        signals = json.loads(args.signals)
    elif args.signals_file:
        with open(args.signals_file, encoding="utf-8") as fh:
            signals = json.load(fh)
    else:
        signals = json.loads(sys.stdin.read() or "{}")

    res = resolve_moves(signals)
    if args.explain:
        print(explain(res), file=sys.stderr)
    sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Atlas P3 — release lifecycle resolver.

Given a feature's signals, deterministically computes where each track (In-Development
and What's New) sits in the release lifecycle and what the next action is. This is the
glue that lets the Atlas orchestrator drive DOC -> PUBLISH -> NOTIFY reliably instead of
re-deriving state by hand each run.

It codifies four rules that live in user memory / instructions:
  1. The complete-ai-takeover classification table (drafted / ready / staged / published).
  2. **Verify against the WN and ID docs, not the toggles.** Doc presence is the source of
     truth; ADO toggles (PublishedWhatsNew / PublishedIndevelopment) can be stale. When they
     disagree, the doc wins and the disagreement is surfaced as a conflict.
  3. **idready-tag vs IsIndevelopmentrequired-toggle conflict:** when a ready tag says "go"
     but the toggle says "not required," do NOT auto-include and do NOT auto-skip — surface it.
  4. **Private Preview hold:** if PrivatePreview date > release date, the item is held; keep it
     in in-development and skip WN staging.

Precedence ladder (highest first): doc presence  >  CRW tracking tags  >  ADO toggles.

This module is pure + deterministic. It does NO drafting, NO ADO writes, NO git. It reads
signals (which the orchestrator gathers) and returns a plan. The LLM/skill layer executes it.

CLI:
    lifecycle.py --signals '<json>'          # emit resolution JSON
    lifecycle.py --signals-file path.json    # from file
    cat signals.json | lifecycle.py          # from stdin
    add --explain for a human-readable summary on stderr

Signals schema (all optional; absent = unknown):
    {
      "ado_id": "90001013",
      "id_required": true,            # Custom.IsIndevelopmentrequired
      "wn_required": true,            # WorkItem.CommsWhatsNewRequired
      "published_id_toggle": false,   # Custom.PublishedIndevelopment
      "published_wn_toggle": false,   # WorkItem.PublishedWhatsNew
      "crw_tags": ["iddraft","idready"],  # tracking tags present anywhere in the CRW
      "crw_has_content": true,        # false if empty / template boilerplate / <300 chars
      "in_id_doc": false,             # present in docs/whats-new/in-development.md on MAIN (null=unchecked)
      "in_wn_doc": false,             # present in docs/whats-new/index.md on MAIN (null=unchecked)
      "in_id_release": false,         # present in in-development.md on the active RELEASE BRANCH (null=unchecked)
      "in_wn_release": false,         # present in index.md on the active RELEASE BRANCH (null=unchecked)
      "private_preview_date": null,   # ISO date or null
      "release_date": null            # ISO date or null
    }

Release-branch note: this team stages What's New onto a per-release branch (e.g.
`2604_service_release`) that merges to main only when the release publishes. So "published"
means in main; "staged on the release branch" (in_*_release true, in main false) is the correct
in-flight state, NOT a stale toggle. Only "published toggle true AND in neither main nor the
release branch" is a genuine stale/stranded conflict. Supply the in_*_release signals so the
resolver can tell these apart; without them it falls back to main-only checks.
"""
from __future__ import annotations

import argparse
import json
import sys

ID_TAGS = ("iddraft", "idready", "idstaged")
WN_TAGS = ("wndraft", "wnready", "wnstaged")

# Route constants — what skill/action the orchestrator dispatches for a given next step.
ROUTE_NONE = "none"
ROUTE_DRAFT = "draft-id-wn"
ROUTE_APPROVAL = "approval"      # Stage 3.5: check for the PM's sign-off, advance the ready tag
ROUTE_ID_STAGING = "id-staging"
ROUTE_WN_STAGING = "wn-staging"
ROUTE_PUBLISH = "comms-publish"
ROUTE_NOTIFY = "notify-pm"
ROUTE_HANDOFF = "handoff"

# Priority order for choosing the single "overall next action" across both tracks.
# Lower number = higher priority (do this first).
_ROUTE_PRIORITY = {
    ROUTE_HANDOFF: 0,      # a human decision is needed — surface first
    ROUTE_DRAFT: 1,        # produce content
    ROUTE_APPROVAL: 2,     # then get the draft approved (Stage 3.5)
    ROUTE_ID_STAGING: 3,   # ID stages before WN in the lifecycle
    ROUTE_WN_STAGING: 4,
    ROUTE_PUBLISH: 5,
    ROUTE_NOTIFY: 6,       # follow-ups are lowest urgency
    ROUTE_NONE: 99,
}


def _has(tags, name) -> bool:
    return name in (t.lower() for t in (tags or []))


def _held(signals) -> bool:
    """Private Preview hold: PrivatePreview date strictly after the release date."""
    pp = signals.get("private_preview_date")
    rel = signals.get("release_date")
    if not pp or not rel:
        return False
    # ISO date strings compare lexicographically when zero-padded (YYYY-MM-DD).
    return str(pp)[:10] > str(rel)[:10]


def _resolve_track(track: str, signals: dict) -> dict:
    """Resolve one track ('id' or 'wn'). Returns stage/next_action/route/gated/notes/conflicts."""
    is_id = track == "id"
    tags = signals.get("crw_tags", [])
    required = signals.get("id_required" if is_id else "wn_required")
    published_toggle = signals.get("published_id_toggle" if is_id else "published_wn_toggle")
    in_doc = signals.get("in_id_doc" if is_id else "in_wn_doc")
    in_release = signals.get("in_id_release" if is_id else "in_wn_release")
    in_archive = signals.get("in_id_archive" if is_id else "in_wn_archive")
    crw_has_content = signals.get("crw_has_content")

    draft_tag, ready_tag, staged_tag = (ID_TAGS if is_id else WN_TAGS)
    has_draft = _has(tags, draft_tag)
    has_ready = _has(tags, ready_tag)
    has_staged = _has(tags, staged_tag)

    notes: list[str] = []
    conflicts: list[dict] = []

    def result(stage, action, route, gated=False):
        return {
            "track": track.upper(),
            "stage": stage,
            "next_action": action,
            "route": route,
            "gated": gated,
            "notes": notes,
            "conflicts": conflicts,
        }

    # --- Conflict detection (always evaluated, regardless of stage) -------------------
    # Rule 3: ready/draft tag present but the requirement toggle is explicitly False.
    if required is False and (has_draft or has_ready or has_staged):
        conflicts.append({
            "topic": f"{track}_tag_vs_toggle",
            "detail": f"CRW has a {track.upper()} tracking tag but the requirement toggle is False. "
                      "Do not auto-include or auto-skip — confirm with the PM (the toggle may be the "
                      "more recent/correct signal).",
            "needs": "PM confirmation",
        })
    # Rule 2: doc presence disagrees with the published toggle.
    if in_doc is True and published_toggle is False:
        conflicts.append({
            "topic": f"{track}_doc_vs_toggle",
            "detail": f"Blurb is live in the {track.upper()} doc but PublishedToggle is False. "
                      "The doc is the source of truth; treat as published and fix the stale toggle.",
            "needs": "toggle correction",
        })
    if in_doc is False and published_toggle is True:
        if in_release is True:
            # Staged on the active release branch but not yet merged to main. For a
            # release-branch workflow this is the expected in-flight state, NOT a stale toggle.
            notes.append("Staged on the release branch; awaiting merge to main. The publish "
                         "toggle is early but the content is on the release train (not stale).")
        elif in_archive is True:
            # Published, then rotated out of the current article into the archive after ~6
            # months (WN Article Maintenance rule). Absent from the current doc but it DID ship,
            # so PublishedToggle True is correct. NOT a stale toggle.
            notes.append("Blurb is in the What's New archive (shipped, then rotated out after "
                         "~6 months); the published toggle is correct, not stale.")
        elif is_id and (signals.get("in_wn_doc") is True or signals.get("in_wn_archive") is True):
            # The ID blurb legitimately LEAVES in-development and moves to What's New when the
            # feature ships. ID absent from in-dev + present/archived in WN + PublishedIndevelopment
            # True is the normal published lifecycle, NOT a stale toggle. Do not flag.
            notes.append("ID blurb has moved to What's New (feature shipped); absence from "
                         "in-development is expected, not a stale toggle.")
        else:
            conflicts.append({
                "topic": f"{track}_doc_vs_toggle",
                "detail": f"PublishedToggle is True but the blurb is NOT in the {track.upper()} doc "
                          "(not in main, and not on the active release branch). The doc is the "
                          "source of truth; the toggle is stale, or the blurb is stranded off the "
                          "release train.",
                "needs": "toggle correction, or re-stage the blurb onto the release branch",
            })

    # --- Stage resolution: doc presence first, then tags, then toggles ---------------

    # WN published = present in the What's New doc (or rotated into the archive). Verify-docs rule.
    if not is_id and in_doc is True:
        return result("published", "Done — WN blurb is live in the What's New article.", ROUTE_NONE)
    if not is_id and in_archive is True:
        return result("published", "Done — WN blurb shipped and is now in the What's New archive.",
                      ROUTE_NONE)

    # ID retired: once the feature ships, the ID blurb is removed from in-development and the
    # WN blurb goes live. If WN is in its doc (or rotated into the archive), the ID track is
    # complete (moved on).
    if is_id and (signals.get("in_wn_doc") is True or signals.get("in_wn_archive") is True):
        return result("retired", "Done — feature shipped; ID blurb moved to What's New.", ROUTE_NONE)

    # ID live in the in-development doc = published to in-dev (its live artifact).
    if is_id and in_doc is True:
        notes.append("ID blurb is live in the in-development article.")
        return result("published-indev", "Done for now — ID is live in in-development; "
                      "publish toggle should be True.", ROUTE_NONE)

    # Confirmed staged on the active RELEASE BRANCH but not yet merged to main. This is the
    # correct in-flight state for a release-branch workflow — distinct from "live in main"
    # (published) and from "nowhere" (genuinely stale/stranded). No per-item action: it goes
    # live when the release branch merges. This is the fix for false "stale toggle" flags on
    # everything currently staged for an unshipped release.
    if in_release is True and in_doc is not True:
        notes.append("Blurb is staged on the release branch; it goes live when the release "
                     "branch merges to main.")
        if conflicts:
            return result("staged-release",
                          "Staged on the release branch, but a conflict was flagged — review.",
                          ROUTE_HANDOFF, gated=True)
        return result("staged-release",
                      "Staged on the release branch; awaiting release merge to main.",
                      ROUTE_NONE)

    # Not required → nothing to do (unless a conflict above flagged it).
    if required is False:
        action = "Not required." if not conflicts else "Not required per toggle, BUT a tag conflict exists — confirm."
        return result("not_required", action, ROUTE_HANDOFF if conflicts else ROUTE_NONE,
                      gated=bool(conflicts))

    # Required but no usable CRW yet → waiting on the PM. Handoff.
    if crw_has_content is False:
        notes.append("CRW is empty/boilerplate — waiting on PM input.")
        return result("no_crw", "Waiting on PM for CRW content.", ROUTE_HANDOFF, gated=True)

    # Staged (tag) but not yet live in the doc → pending PR merge.
    if has_staged and in_doc is not True:
        # Hold check applies to WN staging: a held item stays in in-development.
        if not is_id and _held(signals):
            notes.append("Private Preview hold active — keep in in-development; do not publish WN yet.")
            return result("held", "Held (Private Preview > release date). Keep in in-development.",
                          ROUTE_HANDOFF, gated=True)
        return result("staged", "Staged; awaiting PR merge, then flip the publish toggle.",
                      ROUTE_PUBLISH, gated=True)

    # Ready (tag) but not staged → stage it into the article.
    if has_ready and not has_staged:
        if not is_id and _held(signals):
            notes.append("Private Preview hold active — do not stage WN; keep in in-development.")
            return result("held", "Held (Private Preview > release date). Do not stage WN.",
                          ROUTE_HANDOFF, gated=True)
        route = ROUTE_ID_STAGING if is_id else ROUTE_WN_STAGING
        return result("ready_to_stage", f"Stage into the {'in-development' if is_id else 'What''s New'} article.",
                      route, gated=True)

    # Draft (tag) but not ready → awaiting the PM's review. The next action is the APPROVAL stage
    # (3.5): check for the PM's sign-off and advance to ready if found; if still pending, the
    # orchestrator falls through to a notify. (Routes to approval, not straight to notify, so the
    # approval-detection step is actually reached.)
    if has_draft and not has_ready:
        return result("awaiting_review",
                      "Run approval detection (Stage 3.5): check for the PM's sign-off; notify if still pending.",
                      ROUTE_APPROVAL)

    # Required, has content, no draft tag → draft it.
    return result("needs_draft", f"Draft the {track.upper()} blurb.", ROUTE_DRAFT)


def resolve(signals: dict) -> dict:
    """Resolve both tracks and pick the single highest-priority next action."""
    id_res = _resolve_track("id", signals)
    wn_res = _resolve_track("wn", signals)

    # Lifecycle order: a feature gets its ID blurb before its WN blurb. If ID still needs a
    # draft, defer the WN draft (don't draft both at once — matches draft-id-wn's own rule).
    if id_res["route"] == ROUTE_DRAFT and wn_res["route"] == ROUTE_DRAFT:
        wn_res["next_action"] = "Deferred — draft ID first, then WN on a later run."
        wn_res["route"] = ROUTE_NONE
        wn_res["stage"] = "deferred"

    # Aggregate handoffs + conflicts.
    handoff: list[dict] = []
    all_conflicts: list[dict] = []
    for res, kind in ((id_res, "id"), (wn_res, "wn")):
        all_conflicts.extend(res["conflicts"])
        if res["route"] == ROUTE_HANDOFF:
            handoff.append({"track": kind.upper(), "reason": res["next_action"], "stage": res["stage"]})

    # Overall next = highest-priority route across tracks.
    candidates = [id_res, wn_res]
    overall = min(candidates, key=lambda r: _ROUTE_PRIORITY.get(r["route"], 99))
    overall_route = overall["route"]
    overall_action = overall["next_action"] if overall_route != ROUTE_NONE else "No action needed."

    return {
        "ado_id": signals.get("ado_id"),
        "id": id_res,
        "wn": wn_res,
        "overall_next": overall_action,
        "overall_route": overall_route,
        "handoff": handoff,
        "conflicts": all_conflicts,
    }


def explain(res: dict) -> str:
    lines = [f"Lifecycle — {res.get('ado_id') or '(no id)'}"]
    for track in ("id", "wn"):
        t = res[track]
        lines.append(f"  {t['track']}: {t['stage']} -> {t['next_action']}  [route: {t['route']}]")
        for n in t.get("notes", []):
            lines.append(f"      note: {n}")
    if res["conflicts"]:
        lines.append("  CONFLICTS (surface, do not auto-resolve):")
        for c in res["conflicts"]:
            lines.append(f"    - {c['topic']}: {c['detail']}")
    if res["handoff"]:
        lines.append("  HANDOFF:")
        for h in res["handoff"]:
            lines.append(f"    - {h['track']}: {h['reason']}")
    lines.append(f"  >> OVERALL NEXT: {res['overall_next']}  [route: {res['overall_route']}]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas release lifecycle resolver.")
    p.add_argument("--signals", default=None, help="JSON signals string.")
    p.add_argument("--signals-file", default=None, help="Path to a JSON signals file.")
    p.add_argument("--explain", action="store_true", help="Print human-readable summary to stderr.")
    args = p.parse_args(argv)

    if args.signals:
        signals = json.loads(args.signals)
    elif args.signals_file:
        with open(args.signals_file, encoding="utf-8") as fh:
            signals = json.load(fh)
    else:
        signals = json.loads(sys.stdin.read() or "{}")

    res = resolve(signals)
    if args.explain:
        print(explain(res), file=sys.stderr)
    sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

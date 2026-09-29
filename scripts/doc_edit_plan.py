#!/usr/bin/env python3
"""Atlas Stage 4 — doc edit planner (deterministic).

Phase 2 of doc-footprint (design: ../references/doc-footprint-design.md). Phase 1 DISCOVERED the
articles a feature touches (`doc_footprint.py` -> dossier `affected_articles`). This planner turns
that discovery into an actionable, **accuracy-tiered, owner-gated, batched** edit/handoff plan that
Stage 4 executes. It is PURE: it routes + batches; it does NOT read files, edit, branch, PR, or
call repo-update. The actual per-article content edit is a caller-supplied editing adapter (gated by `simulation_only` + the merge gate); this script just says *which* articles
to edit, in *what* batches, and *what* to hand off and why.

Accuracy-first posture (matches the Phase 1.1 stance): Atlas **auto-edits only the verified set** —
owned articles whose discovery confidence is `EXTRACTED` or `cross_validated` (the scan and the
graph agreed). An owned, edit-eligible but only-`INFERRED` article (single-source, semantically
unverified) is NOT auto-edited; it becomes a `suggested-edit` handoff the writer confirms. This
keeps Atlas from opening noisy PRs against tangential articles even though every edit is already
merge-gated. A hub / overview / landing **god-node** (flagged `god_node` by `doc_footprint`) is
pulled out first into a `god-node` handoff: it cross-validates on sheer centrality, not feature
relevance, so `RepoEditor` would no-op it — the planner stops it reaching a batch (Phase 2.1).

Routing (per affected_articles entry, in order):
  god_node (hub/overview/landing)               -> handoff "god-node"      (RepoEditor no-ops hubs)
  owner unresolved                              -> handoff "owner-unknown" (verify before editing)
  owner not in the editable roster              -> handoff "cross-owner"   (incl. retiring areas; any disposition)
  disposition != "edit"                         -> handoff "review"        (owned list-only ripple/moderate)
  owned + EXTRACTED/cross_validated             -> AUTO-EDIT               (the verified set)
  owned + edit-disposition but only INFERRED    -> handoff "suggested-edit"(owned, relevant, unverified)

The owner here is the doc_footprint `ms_author` HINT; Stage 4 re-verifies each AUTO-EDIT article
with `owner_gate.py` against `docfx.json` right before editing (the file is truth, not the hint).

CLI:
    doc_edit_plan.py plan --articles '<json|file|->' --roster <roster.json>
                          [--ado 90001005] [--max-per-pr 10] [--large-threshold 12]
                          [--all-authors] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_AUTO_CONFIDENCE = ("EXTRACTED",)
_UNKNOWN_OWNERS = (None, "", "(unknown)", "(unassigned)")
_CONF_RANK = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}


def _entry(a: dict) -> dict:
    return {
        "path": a.get("path"),
        "ms_author": a.get("ms_author"),
        "confidence": a.get("confidence"),
        "disposition": a.get("disposition"),
        "cross_validated": bool(a.get("cross_validated")),
        "god_node": bool(a.get("god_node")),
        "blast": a.get("blast"),
        "sources": a.get("sources", []),
    }


def plan(articles, editable, auto_confidence=_AUTO_CONFIDENCE, max_per_pr=10,
         large_threshold=12, ado_id=None) -> dict:
    """Route + batch affected_articles into an edit/handoff plan. `editable` = the set of
    ms.author aliases Atlas may edit (the enabled roster; or all known authors with --all-authors)."""
    editable = set(editable or [])
    auto_edit: list[dict] = []
    handoff = {"god-node": [], "suggested-edit": [], "review": [], "cross-owner": [], "owner-unknown": []}

    for a in articles or []:
        e = _entry(a)
        disp = e["disposition"]
        owner = e["ms_author"]
        conf = e["confidence"]
        if e["god_node"]:
            e["reason"] = ("hub/overview/landing page (high graph centrality, low feature-specificity); "
                           "RepoEditor no-ops these — review, don't auto-edit")
            handoff["god-node"].append(e)
        elif owner in _UNKNOWN_OWNERS:
            e["reason"] = "owner unresolved; verify ms.author before editing"
            handoff["owner-unknown"].append(e)
        elif owner not in editable:
            e["reason"] = f"owned by {owner} (outside the go-forward roster)"
            handoff["cross-owner"].append(e)
        elif disp != "edit":
            e["reason"] = a.get("note") or "list-only (review whether it needs updating)"
            handoff["review"].append(e)
        elif e["cross_validated"] or conf in auto_confidence:
            auto_edit.append(e)
        else:
            e["reason"] = f"owned + relevant but {conf} (single-source); confirm before editing"
            handoff["suggested-edit"].append(e)

    auto_edit.sort(key=lambda r: (not r["cross_validated"], _CONF_RANK.get(r["confidence"], 9), r["path"]))
    batches = [auto_edit[i:i + max_per_pr] for i in range(0, len(auto_edit), max_per_pr)]

    return {
        "ado_id": ado_id,
        "auto_edit": auto_edit,
        "handoff": handoff,
        "batches": batches,
        "large_set": len(auto_edit) > large_threshold,
        "counts": {
            "auto_edit": len(auto_edit),
            "god_node": len(handoff["god-node"]),
            "suggested_edit": len(handoff["suggested-edit"]),
            "review": len(handoff["review"]),
            "cross_owner": len(handoff["cross-owner"]),
            "owner_unknown": len(handoff["owner-unknown"]),
            "pr_batches": len(batches),
        },
    }


def _editable_from_roster(roster: dict, all_authors: bool) -> set:
    """Editable ms.author set for the plan = the ENABLED roster writers (production routing). The
    owner_gate's `test_mode_all_authors` is a gate-helper testing bypass and is intentionally NOT
    honored here, so the plan always shows the real ownership triage (cross-owner handoffs); the
    explicit `--all-authors` flag is the only override."""
    writers = roster.get("writers", []) or []
    if all_authors:
        known = {w.get("alias") for w in writers}
        known |= {w.get("alias") for w in roster.get("retiring_reassign", []) or []}
        return {a for a in known if a}
    return {w.get("alias") for w in writers if w.get("enabled") and w.get("alias")}


def _load(arg):
    if arg == "-":
        return json.load(sys.stdin)
    p = Path(arg)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    else:
        data = json.loads(arg)
    # accept either a bare list or a dossier-patch {"affected_articles": [...]}
    if isinstance(data, dict):
        return data.get("affected_articles", [])
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description="Atlas Stage 4 doc edit planner.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("plan", help="route affected_articles into an edit/handoff plan")
    m.add_argument("--articles", required=True, help="affected_articles: JSON string, file, or '-' for stdin")
    m.add_argument("--roster", required=True, help="path to roster.json (editable = enabled writers)")
    m.add_argument("--ado", default=None, help="feature ADO id (stamped on the plan)")
    m.add_argument("--max-per-pr", type=int, default=10, help="max auto-edit articles per PR batch (default 10)")
    m.add_argument("--large-threshold", type=int, default=12, help="flag the plan if auto-edit exceeds this (default 12)")
    m.add_argument("--all-authors", action="store_true", help="treat any known author as editable (testing; default = enabled roster)")
    m.add_argument("--json", action="store_true", help="emit JSON (default human-readable)")
    args = ap.parse_args(argv)

    articles = _load(args.articles)
    roster = json.loads(Path(args.roster).read_text(encoding="utf-8"))
    editable = _editable_from_roster(roster, args.all_authors)
    result = plan(articles, editable, max_per_pr=args.max_per_pr,
                  large_threshold=args.large_threshold, ado_id=args.ado)

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    else:
        c = result["counts"]
        flag = "  *** LARGE EDIT SET — confirm before proceeding ***" if result["large_set"] else ""
        print(f"Edit plan for {args.ado or '(feature)'}: {c['auto_edit']} auto-edit "
              f"({c['pr_batches']} PR batch[es]){flag}")
        print(f"  handoff: {c['suggested_edit']} suggested-edit, {c['god_node']} god-node, "
              f"{c['review']} review, {c['cross_owner']} cross-owner, {c['owner_unknown']} owner-unknown")
        print("\nAUTO-EDIT (verified; owner gate re-checked at edit time):")
        for a in result["auto_edit"]:
            xv = " *xval*" if a["cross_validated"] else ""
            print(f"  {a['confidence']:<9} {a['path']}  (owner {a['ms_author']}){xv}")
        for kind in ("suggested-edit", "god-node", "cross-owner", "review", "owner-unknown"):
            items = result["handoff"][kind]
            if items:
                print(f"\nHANDOFF [{kind}] ({len(items)}):")
                for a in items:
                    print(f"  {a['path']}  (owner {a['ms_author']}) — {a.get('reason','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run Atlas's actual planning/state modules over entirely fictional data.

No model, network, credentials, external repository writes, or messages. The
offline draft uses a visible template; host-agent reasoning is documented in
skills/atlas-pipeline/SKILL.md. Output lives in a new directory only.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import os
import re
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(output: Path, fixture: Path | None = None) -> dict:
    if output.exists():
        raise ValueError("Output must be a new directory; existing files are never replaced.")
    data = json.loads((fixture or ROOT / "examples/aurora-sync.json").read_text(encoding="utf-8"))
    if data.get("fictional") is not True:
        raise ValueError("This offline demo accepts only explicitly fictional fixtures.")
    if not re.fullmatch(r"\d{5,9}", str(data.get("feature", {}).get("id", ""))):
        raise ValueError("Fixture feature ID must contain five to nine digits.")
    if not re.fullmatch(r"\d{4}", str(data["feature"].get("comms_timeline", ""))):
        raise ValueError("Fixture release must use four-digit YYMM notation.")
    for article in data.get("articles", []):
        relative = Path(article["path"])
        if relative.is_absolute() or relative.drive or ".." in relative.parts or "\\" in article["path"]:
            raise ValueError("Fixture article paths must be relative and stay inside the output directory.")
    output.mkdir(parents=True)
    state = output / "state"
    os.environ["ATLAS_STATE_DIR"] = str(state)
    import assignment_log
    import crw_tags
    import doc_edit_plan
    import doc_footprint
    import dossier_store
    import feature_notes
    import handoff
    import landscape
    import lifecycle
    import owner_gate
    from wrap_untrusted import wrap_untrusted

    # Stateful modules bind paths during import; explicit rebind also supports
    # two demos called in the same interpreter without cross-run state sharing.
    dossier_store.DOSSIER_DIR = state / "dossiers"
    assignment_log.LEDGER_PATH = state / "assignments-seen.json"
    feature_notes.NOTES_PATH = state / "feature-notes.json"
    handoff.QUEUE_PATH = state / "handoff-queue.md"
    feature = data["feature"]
    feature_id = feature["id"]
    roster = owner_gate.load_roster(str(ROOT / "config/roster.json"))
    events = []

    def stage(name, detail):
        events.append({"stage": name, "detail": detail})

    worklist = [{"id": feature_id, "title": feature["title"], "comms_writer": feature["comms_writer"],
                 "writer": feature["comms_writer"], "crw_has_content": False, "crw_tags": []}]
    release_map = landscape.build_landscape(worklist, release=feature["comms_timeline"])
    write_json(output / "landscape.json", release_map)
    stage("Intake and relationships", "Resolved a fictional worklist before the per-feature run.")

    fragments = output / "sources"
    fragments.mkdir()
    for index, source in enumerate(data["sources"], 1):
        (fragments / f"{index:02}.xml").write_text(wrap_untrusted(source["id"], source["body"]), encoding="utf-8")
    stage("Gather context", "Wrapped fixture sources as external data; no connected services were called.")

    dossier = dossier_store._skeleton(feature_id, feature)
    patch = copy.deepcopy(data["patch"])
    dossier_store.merge(dossier, patch)
    original_count = len(dossier["facts"])
    dossier_store.merge(dossier, copy.deepcopy(patch))
    assert len(dossier["facts"]) == original_count, "Repeat merge duplicated facts"
    errors = dossier_store.validate(dossier)
    if errors:
        raise ValueError("Invalid fixture dossier: " + "; ".join(errors))
    stage("Maintain dossier", "Merged source-linked fixture claims twice without duplicating facts; unavailable evidence stays explicit.")

    baseline = {"ado_id": feature_id, "id_required": True, "wn_required": True,
                "crw_has_content": False, "crw_tags": [], "in_id_doc": False, "in_wn_doc": False}
    states = {"before_draft": lifecycle.resolve(baseline)}
    facts = {fact["field"]: fact["value"] for fact in dossier["facts"] if fact["confidence"] == "EXTRACTED"}
    draft = (f"# {feature['title']}\n\n"
             f"Aurora Sync lets administrators {facts['capability']}. "
             f"This {facts['availability']} requires {facts['requirement']}.\n\n"
             "Mobile synchronization and general availability are outside this announcement.\n\n"
             "*Offline template draft from fictional source claims. No AI model generated this example.*\n")
    (output / "release-note.md").write_text(draft, encoding="utf-8")
    drafted = {**baseline, "crw_has_content": True, "crw_tags": ["iddraft", "wndraft"]}
    states["awaiting_approval"] = lifecycle.resolve(drafted)
    stage("Draft", "Generated a labeled template draft from extracted fixture facts; the agent-host workflow supplies real drafting.")

    affected = doc_footprint.reconcile(data["scan"], data["graph"])
    dossier["affected_articles"] = affected
    plan = doc_edit_plan.plan(affected, roster, ado_id=feature_id)
    write_json(output / "affected-docs.json", affected)
    write_json(output / "edit-plan.json", plan)
    docs = output / "source-docs"
    for article in data["articles"]:
        path = docs / article["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = f"ms.author: {article['author']}\n" if article["author"] else ""
        path.write_text(f"---\n{metadata}ms.topic: {article['topic']}\n---\n# {article['title']}\n\n{article['body']}\n", encoding="utf-8")
    for entry in plan["auto_edit"]:
        actual = owner_gate.decide(str(docs / entry["path"]), roster)
        if actual["decision"] != "own":
            raise ValueError("Owner recheck disagrees with fixture plan")
        destination = output / "proposed-docs" / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((docs / entry["path"]).read_text(encoding="utf-8")
                               + "\n## Aurora Sync preview\n\n" + draft.split("\n\n", 1)[1], encoding="utf-8")
    for kind, entries in plan["handoff"].items():
        for entry in entries:
            handoff.add(feature_id, entry.get("ms_author") or "unassigned", kind,
                        f"{entry['path']}: {entry['reason']}")
    stage("Plan and prepare documentation", "Rechecked actual fixture ownership; copied only the permitted article into proposed-docs. Other owners, missing owners and overview pages became handoffs.")

    approval = data["approval"]
    valid_approval = (approval.get("approved") is True and approval.get("role") == "product-owner"
                      and approval.get("source") in {s["id"] for s in data["sources"]}
                      and approval.get("scope") == "preview")
    if valid_approval:
        crw = f"<!-- {feature_id} iddraft wndraft -->\n{draft}"
        crw = crw_tags.advance_tag(crw, "id", own_id=feature_id)["crw"]
        crw = crw_tags.advance_tag(crw, "wn", own_id=feature_id)["crw"]
        (output / "approved-field.html").write_text(crw, encoding="utf-8")
        states["approved"] = lifecycle.resolve({**drafted, "crw_tags": ["iddraft", "idready", "wndraft", "wnready"]})
        stage("Approval", "Applied the tag transform after checking explicit fictional product-owner approval; no external field was written.")
        staged = output / "staged" / feature["comms_timeline"] / "release-notes.md"
        staged.parent.mkdir(parents=True)
        staged.write_text(draft, encoding="utf-8")
        states["staged_preview"] = lifecycle.resolve({**drafted, "crw_tags": ["idready", "wnready"],
                                                      "in_id_release": True, "in_wn_release": True})
        stage("Stage for review", "Wrote a local staging preview only. No pull request, branch push, merge, or customer publication occurred.")
    else:
        handoff.add(feature_id, "product-owner", "approval", "No valid fixture approval; draft remains pending.")
        stage("Approval", "No applicable approval; staging is held and a handoff is queued.")

    feature_notes.add(feature_id, "Keep preview wording until general availability is independently established.", "scope", "alex")
    dossier_store.save(dossier)
    (output / "dossier.md").write_text(dossier_store.render_markdown(dossier), encoding="utf-8")
    write_json(output / "dossier.json", dossier)
    write_json(output / "lifecycle.json", states)
    (output / "notification-draft.md").write_text(
        "# Unsent fixture notification\n\nAurora Sync's preview draft and proposed documentation change are ready for review. "
        "Cross-owner and unassigned articles remain in the handoff queue.\n\n"
        "This file is a draft; no message was sent and no sent receipt was recorded.\n", encoding="utf-8")
    stage("Handoff", "Saved review tasks and an unsent notification; preserved the dossier for the next run.")
    report = {"status": "passed", "mode": "offline-fictional-demo", "model_calls": 0, "network_calls": 0,
              "production_writes": False, "valid_fixture_approval": valid_approval,
              "fact_count_after_repeat_merge": len(dossier["facts"]), "edit_counts": plan["counts"], "stages": events}
    write_json(output / "run-report.json", report)
    lines = ["# Atlas: a feature carried through a reviewable workflow", "",
             "All inputs are fictional. Drafting uses an explicit offline template. Planning, state, and routing use the actual Python modules.", ""]
    for event in events:
        lines += [f"## {event['stage']}", "", event["detail"], ""]
    lines += ["## Inspect the result", "", "- [Source-linked dossier](dossier.md)", "- [Draft announcement](release-note.md)",
              "- [Edit and handoff plan](edit-plan.json)", "- [Lifecycle transitions](lifecycle.json)",
              "- [Queued handoffs](state/handoff-queue.md)", "- [Unsent notification](notification-draft.md)", ""]
    (output / "README.md").write_text("\n".join(lines), encoding="utf-8")
    cards = "".join(f"<article><span>{index:02}</span><h2>{html.escape(e['stage'])}</h2><p>{html.escape(e['detail'])}</p></article>"
                    for index, e in enumerate(events, 1))
    page = "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    page += "<title>Atlas workflow demo</title><style>body{font:17px/1.6 system-ui;margin:0;background:#f4f5f7;color:#15253c}main{max-width:1040px;margin:auto;padding:56px 24px}h1{font-size:clamp(2rem,5vw,3.5rem);line-height:1.1;max-width:800px}header p{max-width:720px;color:#43536a}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin-top:36px}article{padding:24px;border:1px solid #d9e0e9;border-radius:16px;background:white}article span{color:#386dbe;font-weight:700}h2{font-size:21px}article p{color:#43536a}a{color:#245da6}footer{margin-top:32px}</style><main><header><p>ATLAS / RUNNABLE PUBLIC EDITION</p><h1>One feature. A workflow you can inspect.</h1><p>This run used fictional sources and a template draft. The Python modules performed the real state updates, ownership checks and routing. No model, network or publication was involved.</p></header><section class='grid'>"
    page += cards + "</section><footer><a href='dossier.md'>Read the dossier</a> · <a href='edit-plan.json'>Inspect the edit plan</a> · <a href='run-report.json'>Run evidence</a></footer></main></html>"
    (output / "index.html").write_text(page, encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="A new directory for retained demo output")
    args = parser.parse_args()
    if args.output is None:
        with tempfile.TemporaryDirectory(prefix="atlas-demo-") as temporary:
            report = run(Path(temporary) / "run")
        print(json.dumps(report, indent=2))
        print("Temporary output removed. Use --output <new-directory> to retain the report and HTML walkthrough.")
    else:
        report = run(args.output.resolve())
        print(json.dumps(report, indent=2))
        print(f"Open {args.output.resolve() / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

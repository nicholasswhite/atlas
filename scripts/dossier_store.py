#!/usr/bin/env python3
"""Feature Dossier store for the Atlas pipeline.

The dossier is the single synthesized artifact Atlas produces per feature: it
merges every reachable source into one provenance-tagged picture, rich enough to
make drafting a formatting step rather than a research hunt.

This module owns the *deterministic* half of the dossier: persistence, the
incremental merge logic, schema invariants, and the human-readable markdown
render. The *reasoning* half — reading sources and extracting claims — is done by
the agent via the `harvest-context` skill, which feeds patches into this store.

Schema: ../references/dossier-schema.md
Storage: ../state/dossiers/{ado_id}.json  (+ {ado_id}.md companion)

Design invariants enforced here (from the schema doc):
  1. Every claim carries at least one source.
  2. Conflicts are never auto-resolved (`resolution` stays null until a human sets it).
  3. Merge, don't overwrite — re-runs union new claims and refresh changed facts.

Confidence vocabulary (borrowed from the lens plugin):
  EXTRACTED  — stated verbatim in a source            (draftable as fact)
  INFERRED   — reasoned from one or more sources       (draftable, with care)
  AMBIGUOUS  — sources disagree, or a source hedged    (NOT draftable; -> open_questions)

CLI:
    dossier_store.py init   --id 12345 [--anchor '<json>']
    dossier_store.py merge  --id 12345 --patch '<json>'   (or --patch-file path / stdin)
    dossier_store.py load   --id 12345
    dossier_store.py render --id 12345            # (re)write the .md companion, print it
    dossier_store.py path   --id 12345            # print json + md paths
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONFIDENCE_VALUES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


def _state_root() -> Path:
    """Root for Atlas runtime state. Honors $ATLAS_STATE_DIR (used by the sandbox to redirect
    all writes to a disposable location); defaults to the plugin's own state/ dir."""
    env = os.environ.get("ATLAS_STATE_DIR")
    return Path(env) if env else (Path(__file__).resolve().parent.parent / "state")


DOSSIER_DIR = _state_root() / "dossiers"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_path(ado_id: str) -> Path:
    return DOSSIER_DIR / f"{_safe_id(ado_id)}.json"


def _md_path(ado_id: str) -> Path:
    return DOSSIER_DIR / f"{_safe_id(ado_id)}.md"


def _safe_id(ado_id) -> str:
    value = str(ado_id)
    if not re.fullmatch(r"[0-9]{1,9}", value):
        raise ValueError("Feature identifiers must contain one to nine digits.")
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _skeleton(ado_id: str, anchor: dict | None) -> dict:
    anchor = anchor or {}
    now = _now()
    return {
        "ado_id": str(ado_id),
        "title": anchor.get("title"),
        "comms_timeline": anchor.get("comms_timeline"),
        "comms_writer": anchor.get("comms_writer"),
        "assigned_to": anchor.get("assigned_to"),
        "area_path": anchor.get("area_path"),
        "oob": anchor.get("oob", False),
        "oob_target_date": anchor.get("oob_target_date"),
        "created": now,
        "updated": now,
        "run_count": 1,
        "scope": {"shipping": [], "out_of_scope": []},
        "facts": [],
        "dates": [],
        "conflicts": [],
        "affected_articles": [],
        "sources": {"reached": [], "unavailable": []},
        "open_questions": [],
        "links": {},
        "drafts": {
            "id": {"status": "pending", "tag": None, "written_to_crw": False},
            "wn": {"status": "pending", "tag": None, "written_to_crw": False},
        },
    }


# ---------------------------------------------------------------------------
# Merge helpers — all additive and idempotent.
# ---------------------------------------------------------------------------

def _union_sources(existing: list, incoming: list) -> list:
    out = list(existing or [])
    for s in incoming or []:
        if s not in out:
            out.append(s)
    return out


def _hashable(value):
    """Return a hashable key component. List/dict values (e.g. a `platforms`
    value of ["Windows"]) are serialized to a stable string so they can key a dict."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value


def _merge_claim_list(existing: list, incoming: list, key_fields: tuple[str, ...]) -> list:
    """Merge lists of claim dicts, deduped by `key_fields`. On a key hit, union the
    `sources` list and take the incoming `confidence` if provided."""
    out = list(existing or [])
    index = {tuple(_hashable(c.get(k)) for k in key_fields): c for c in out}
    for item in incoming or []:
        k = tuple(_hashable(item.get(kf)) for kf in key_fields)
        if k in index:
            tgt = index[k]
            tgt["sources"] = _union_sources(tgt.get("sources", []), item.get("sources", []))
            if item.get("confidence"):
                tgt["confidence"] = item["confidence"]
        else:
            index[k] = item
            out.append(item)
    return out


def _merge_conflicts(existing: list, incoming: list) -> list:
    """Merge conflicts keyed by `topic`. On a hit, union positions (by value) and
    NEVER overwrite a human-set resolution with null."""
    out = list(existing or [])
    index = {c.get("topic"): c for c in out}
    for item in incoming or []:
        topic = item.get("topic")
        if topic in index:
            tgt = index[topic]
            # union positions by value
            pos_index = {p.get("value"): p for p in tgt.get("positions", [])}
            for p in item.get("positions", []):
                if p.get("value") in pos_index:
                    existing_pos = pos_index[p["value"]]
                    existing_pos["sources"] = _union_sources(
                        existing_pos.get("sources", []), p.get("sources", [])
                    )
                else:
                    tgt.setdefault("positions", []).append(p)
                    pos_index[p.get("value")] = p
            # invariant 2: don't clobber a resolution that's already set
            if item.get("resolution") and not tgt.get("resolution"):
                tgt["resolution"] = item["resolution"]
            if item.get("needs"):
                tgt["needs"] = item["needs"]
        else:
            index[topic] = item
            out.append(item)
    return out


def _base_tag(t) -> str:
    """The base harvest-source tag (H1..H10), dropping any ':sub-source' suffix. The dossier
    `sources` block is a coverage summary over the ~10 sources, so it is keyed by base tag
    (`H7`, not `H7:telemetry`) — a source is reached-or-not at that granularity. Per-claim
    provenance is unaffected: each claim's own `sources` list keeps the granular sub-tags
    (e.g. `H1:crw`, `H1:fields`)."""
    return str(t).split(":", 1)[0].strip()


def _norm_unavail(u) -> dict:
    """Accept an `unavailable` source entry as a {'tag','reason'} object OR a plain string
    ('tag' or 'tag:reason') and normalize to the object form with a BASE tag. Lenient on input
    so a caller can't crash the merge by passing strings (the symmetric, forgiving counterpart
    to the plain-string `reached` list). Base-normalizing the tag (`H7:telemetry` -> `H7`) means
    the same source recorded once with a sub-tag and once without dedupes to one coverage entry."""
    if isinstance(u, dict):
        return {"tag": _base_tag(u.get("tag", "")), "reason": u.get("reason", "")}
    s = str(u)
    if ":" in s:
        tag, reason = s.split(":", 1)
        return {"tag": _base_tag(tag), "reason": reason.strip()}
    return {"tag": _base_tag(s), "reason": ""}


def _merge_sources(existing: dict, incoming: dict) -> dict:
    """Merge the `sources` coverage block. Both `reached` and `unavailable` are keyed by BASE
    tag (H1..H10), so a source recorded once as `H1:ado-graph` and again as `H1` collapses to a
    single `H1` entry, and `reached`/`unavailable` can never disagree about the same base source
    (e.g. an old `H7:telemetry` unavailable + a new `H7` reached resolve to reached)."""
    existing = existing or {"reached": [], "unavailable": []}
    reached: list[str] = []

    def _add_reached(tag) -> None:
        if isinstance(tag, dict):           # tolerate a {tag, note} object, mirroring `unavailable`
            tag = tag.get("tag", "")
        b = _base_tag(tag)
        if b and b not in reached:
            reached.append(b)

    for tag in existing.get("reached", []) or []:
        _add_reached(tag)

    unavailable: dict[str, dict] = {}
    for u in existing.get("unavailable", []) or []:
        e = _norm_unavail(u)
        unavailable[e["tag"]] = e

    for tag in incoming.get("reached", []) or []:
        _add_reached(tag)
        # a source that's now reached is no longer unavailable
        unavailable.pop(_base_tag(tag), None)

    for u in incoming.get("unavailable", []) or []:
        e = _norm_unavail(u)
        # don't mark unavailable something we've actually reached
        if e["tag"] in reached:
            continue
        unavailable[e["tag"]] = e

    # Final guard: a base tag can never be both reached and unavailable.
    for r in reached:
        unavailable.pop(r, None)

    return {"reached": reached, "unavailable": list(unavailable.values())}


def _dedupe_str_list(existing: list, incoming: list) -> list:
    out = list(existing or [])
    for s in incoming or []:
        if s not in out:
            out.append(s)
    return out


def merge(dossier: dict, patch: dict, bump_run: bool = False) -> dict:
    """Merge `patch` into `dossier` additively. Returns the mutated dossier."""
    # Scalar anchor fields: fill if missing or explicitly provided. These drift over a feature's
    # life (PM reassignment -> assigned_to, slip/pull-in -> comms_timeline, area moves), so the
    # harvest should RE-SUPPLY them in EVERY merge patch, not just at init. A re-run whose patch
    # omits them keeps the stale value (init is skipped once the dossier exists) — see
    # harvest-context Step 4, which requires the current anchor scalars in each patch.
    for field in (
        "title",
        "comms_timeline",
        "comms_writer",
        "assigned_to",
        "area_path",
        "oob",
        "oob_target_date",
    ):
        if field in patch and patch[field] is not None:
            dossier[field] = patch[field]

    if "scope" in patch:
        sc = patch["scope"]
        dossier.setdefault("scope", {"shipping": [], "out_of_scope": []})
        dossier["scope"]["shipping"] = _merge_claim_list(
            dossier["scope"].get("shipping", []), sc.get("shipping", []), ("claim",)
        )
        dossier["scope"]["out_of_scope"] = _merge_claim_list(
            dossier["scope"].get("out_of_scope", []), sc.get("out_of_scope", []), ("claim",)
        )

    if "facts" in patch:
        dossier["facts"] = _merge_claim_list(
            dossier.get("facts", []), patch["facts"], ("field", "value")
        )

    if "dates" in patch:
        dossier["dates"] = _merge_claim_list(
            dossier.get("dates", []), patch["dates"], ("value",)
        )

    if "conflicts" in patch:
        dossier["conflicts"] = _merge_conflicts(dossier.get("conflicts", []), patch["conflicts"])

    if "affected_articles" in patch:
        dossier["affected_articles"] = _merge_claim_list(
            dossier.get("affected_articles", []), patch["affected_articles"], ("path",)
        )

    if "sources" in patch:
        dossier["sources"] = _merge_sources(dossier.get("sources", {}), patch["sources"])

    if "open_questions" in patch:
        dossier["open_questions"] = _dedupe_str_list(
            dossier.get("open_questions", []), patch["open_questions"]
        )

    if "links" in patch:
        dossier.setdefault("links", {}).update(patch["links"])

    if "drafts" in patch:
        for kind in ("id", "wn"):
            if kind in patch["drafts"]:
                dossier.setdefault("drafts", {}).setdefault(kind, {}).update(patch["drafts"][kind])

    dossier["updated"] = _now()
    if bump_run:
        dossier["run_count"] = int(dossier.get("run_count", 0)) + 1
    return dossier


def validate(dossier: dict) -> list[str]:
    """Return a list of invariant-violation messages (empty = valid)."""
    errors: list[str] = []

    def _check_claims(claims, where, require_source=True):
        for i, c in enumerate(claims or []):
            # facts/dates use `sources` (list); affected_articles use `source`/`sources`. Scope
            # bullets are syntheses over the already-sourced facts, so a per-bullet source is optional.
            if require_source and not (c.get("sources") or c.get("source")):
                errors.append(f"{where}[{i}]: claim has no source (invariant 1)")
            conf = c.get("confidence")
            if conf is not None and conf not in CONFIDENCE_VALUES:
                errors.append(f"{where}[{i}]: invalid confidence '{conf}'")

    _check_claims(dossier.get("facts"), "facts")
    _check_claims(dossier.get("dates"), "dates")
    _check_claims((dossier.get("scope") or {}).get("shipping"), "scope.shipping", require_source=False)
    _check_claims((dossier.get("scope") or {}).get("out_of_scope"), "scope.out_of_scope", require_source=False)
    _check_claims(dossier.get("affected_articles"), "affected_articles")
    return errors


# ---------------------------------------------------------------------------
# Markdown companion render
# ---------------------------------------------------------------------------

def _fmt_sources(sources) -> str:
    """Render a source list to a compact string. Tolerant of a bare string, or entries that are
    plain strings (`H1:crw`) OR objects (`{tag, note}` / `{source}` / `{tag, reason}`)."""
    if not sources:
        return "—"
    if isinstance(sources, str):
        sources = [sources]
    out = []
    for s in sources:
        if isinstance(s, dict):
            tag = s.get("tag") or s.get("source") or ""
            note = s.get("note") or s.get("reason")
            out.append(f"{tag}: {note}" if note else str(tag))
        elif s:
            out.append(str(s))
    return ", ".join(out) or "—"


def render_markdown(d: dict) -> str:
    ado_id = d.get("ado_id", "?")
    title = d.get("title") or "(untitled)"
    lines: list[str] = []
    lines.append(f"# Dossier — {ado_id}: {title}")
    lines.append("")
    lines.append(
        f"*Timeline:* {d.get('comms_timeline') or '—'}  ·  "
        f"*Writer:* {d.get('comms_writer') or '—'}  ·  "
        f"*Run:* {d.get('run_count', 1)}  ·  *Updated:* {d.get('updated', '—')}"
    )
    ado_link = (d.get("links") or {}).get("ado")
    if ado_link:
        lines.append(f"*ADO:* {ado_link}")
    lines.append("")

    # Anchor
    lines.append("## Anchor")
    lines.append("")
    assigned = d.get("assigned_to") or {}
    assigned_name = assigned.get("name") if isinstance(assigned, dict) else assigned
    lines.append(f"- **Area:** {d.get('area_path') or '—'}")
    lines.append(f"- **Assigned to:** {assigned_name or '—'}")
    if d.get("oob"):
        lines.append(f"- **OOB:** yes (target {d.get('oob_target_date') or 'TBD'})")
    lines.append("")

    # Scope
    scope = d.get("scope") or {}
    lines.append("## What's shipping / out of scope")
    lines.append("")
    if scope.get("shipping"):
        lines.append("**Shipping:**")
        lines.append("")
        for c in scope["shipping"]:
            lines.append(f"- {c.get('claim')}  _[{c.get('confidence','?')} · {_fmt_sources(c.get('sources') or c.get('source'))}]_")
        lines.append("")
    if scope.get("out_of_scope"):
        lines.append("**Out of scope:**")
        lines.append("")
        for c in scope["out_of_scope"]:
            lines.append(f"- {c.get('claim')}  _[{c.get('confidence','?')} · {_fmt_sources(c.get('sources') or c.get('source'))}]_")
        lines.append("")
    if not scope.get("shipping") and not scope.get("out_of_scope"):
        lines.append("_No scope claims yet._")
        lines.append("")

    # Key facts
    lines.append("## Key facts")
    lines.append("")
    if d.get("facts"):
        lines.append("| Field | Value | Confidence | Sources |")
        lines.append("|---|---|---|---|")
        for f in d["facts"]:
            val = str(f.get("value")).replace("|", "\\|")
            lines.append(
                f"| {f.get('field')} | {val} | {f.get('confidence','?')} | {_fmt_sources(f.get('sources') or f.get('source'))} |"
            )
    else:
        lines.append("_No facts yet._")
    lines.append("")

    # Dates (call out conflicts)
    lines.append("## Dates")
    lines.append("")
    date_conflicts = [c for c in d.get("conflicts", []) if "date" in (c.get("topic") or "").lower()]
    if d.get("dates"):
        for dt in d["dates"]:
            lines.append(f"- {dt.get('value')}  _[{dt.get('confidence','?')} · {_fmt_sources(dt.get('sources'))}]_")
    else:
        lines.append("_No dates yet._")
    if date_conflicts:
        lines.append("")
        lines.append("> ⚠ **Date conflict — needs a human:**")
        for c in date_conflicts:
            positions = "; ".join(
                f"{p.get('value')} ({_fmt_sources(p.get('sources'))})" for p in c.get("positions", [])
            )
            lines.append(f"> - {positions} — needs: {c.get('needs') or 'PM confirmation'}")
    lines.append("")

    # Affected articles
    lines.append("## Affected articles + owners")
    lines.append("")
    if d.get("affected_articles"):
        lines.append("| Article | Owner (ms.author) | Blast | Confidence | Disposition | Sources |")
        lines.append("|---|---|---|---|---|---|")
        for a in d["affected_articles"]:
            # Prefer the dual-source `sources` list (doc_footprint); fall back to the legacy
            # singular `source` (string or list) for older dossiers.
            srcs = a.get("sources")
            if not isinstance(srcs, list):
                srcs = a.get("source") if isinstance(a.get("source"), list) else (
                    [a.get("source")] if a.get("source") else [])
            lines.append(
                f"| {a.get('path')} | {a.get('ms_author','?')} | {a.get('blast','?')} "
                f"| {a.get('confidence','—')} | {a.get('disposition','—')} | {_fmt_sources(srcs)} |"
            )
    else:
        lines.append("_No affected articles mapped yet (discovery not run / no matches)._")
    lines.append("")

    # Open questions
    lines.append("## Open questions")
    lines.append("")
    if d.get("open_questions"):
        for q in d["open_questions"]:
            if isinstance(q, dict):
                text = q.get("question") or q.get("ask") or q.get("topic") or q.get("q") or ""
                needs = q.get("needs")
                lines.append(f"- [ ] {text}" + (f"  _(needs: {needs})_" if needs else ""))
            else:
                lines.append(f"- [ ] {q}")
    else:
        lines.append("_None._")
    lines.append("")

    # Sources
    sources = d.get("sources") or {}
    lines.append("## Sources reached / unavailable")
    lines.append("")
    lines.append(f"**Reached ({len(sources.get('reached', []))}):** {_fmt_sources(sources.get('reached'))}")
    lines.append("")
    if sources.get("unavailable"):
        lines.append("**Unavailable:**")
        lines.append("")
        for u in sources["unavailable"]:
            lines.append(f"- {u.get('tag')}: {u.get('reason')}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load(ado_id: str) -> dict | None:
    p = _json_path(ado_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(dossier: dict) -> tuple[Path, Path]:
    ado_id = dossier["ado_id"]
    jp = _json_path(ado_id)
    _atomic_write(jp, json.dumps(dossier, indent=2, ensure_ascii=False) + "\n")
    mp = _md_path(ado_id)
    _atomic_write(mp, render_markdown(dossier))
    return jp, mp


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_patch(args) -> dict:
    if args.patch:
        return json.loads(args.patch)
    if args.patch_file:
        return json.loads(Path(args.patch_file).read_text(encoding="utf-8"))
    data = sys.stdin.read()
    if not data.strip():
        return {}
    return json.loads(data)


def main(argv: list[str] | None = None) -> int:
    # The dossier markdown legitimately contains non-cp1252 glyphs (⚠ for conflicts, box-drawing
    # rules). On a Windows cp1252 console, printing them crashes. Force UTF-8 stdout so render/load
    # work regardless of how the script is invoked (sandbox, headless agent run, manual debug).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Atlas Feature Dossier store.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create a new dossier from anchor facts.")
    p_init.add_argument("--id", required=True)
    p_init.add_argument("--anchor", default=None, help="JSON of anchor fields.")
    p_init.add_argument("--force", action="store_true", help="Overwrite if it exists.")

    p_merge = sub.add_parser("merge", help="Merge a JSON patch into the dossier.")
    p_merge.add_argument("--id", required=True)
    p_merge.add_argument("--patch", default=None, help="JSON patch string.")
    p_merge.add_argument("--patch-file", default=None, help="Path to a JSON patch file.")
    p_merge.add_argument("--bump-run", action="store_true", help="Increment run_count.")
    p_merge.add_argument("--init-if-missing", action="store_true",
                         help="Create the dossier first if it doesn't exist.")

    p_load = sub.add_parser("load", help="Print the dossier JSON.")
    p_load.add_argument("--id", required=True)

    p_render = sub.add_parser("render", help="Re-render the markdown companion and print it.")
    p_render.add_argument("--id", required=True)

    p_path = sub.add_parser("path", help="Print the dossier file paths.")
    p_path.add_argument("--id", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "init":
        if _json_path(args.id).exists() and not args.force:
            print(f"Dossier {args.id} already exists. Use --force to overwrite.", file=sys.stderr)
            return 1
        anchor = json.loads(args.anchor) if args.anchor else {}
        d = _skeleton(args.id, anchor)
        jp, mp = save(d)
        print(f"Created {jp}")
        print(f"Created {mp}")
        return 0

    if args.cmd == "merge":
        d = load(args.id)
        if d is None:
            if args.init_if_missing:
                d = _skeleton(args.id, {})
            else:
                print(f"No dossier {args.id}. Use init first, or --init-if-missing.", file=sys.stderr)
                return 1
        patch = _load_patch(args)
        # let the patch seed anchor fields on first merge
        merge(d, patch, bump_run=args.bump_run)
        errors = validate(d)
        jp, mp = save(d)
        print(f"Merged into {jp}")
        if errors:
            print("VALIDATION WARNINGS:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
        return 0

    if args.cmd == "load":
        d = load(args.id)
        if d is None:
            print(f"No dossier {args.id}.", file=sys.stderr)
            return 1
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "render":
        d = load(args.id)
        if d is None:
            print(f"No dossier {args.id}.", file=sys.stderr)
            return 1
        md = render_markdown(d)
        _atomic_write(_md_path(args.id), md)
        print(md)
        return 0

    if args.cmd == "path":
        print(_json_path(args.id))
        print(_md_path(args.id))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Atlas H10 — prior-feature notes log.

A persistent place to jot human context about a specific feature/ADO item — decisions, PM
conversations, hold reasons, timing changes — that would otherwise be lost between sessions.
The harvester reads it as source H10 and folds relevant notes into the dossier, so context
accumulates across the whole life of a release item instead of being re-discovered each run.

Originally the 2026-04-13 "release item notes log" idea: the drafting skills surface any
relevant notes for the item being drafted.

Distinct from the dossier store: the dossier is Atlas's *synthesized output* (auto-built from
all 10 sources, rebuilt/merged each run). This notes log is a *human-authored input* — durable
jottings that persist independently and feed INTO the dossier as one source. A note here is
never overwritten by a harvest; it's appended to by a person (or the agent on a person's behalf).

Storage: state/feature-notes.json  (gitignored — local runtime state; may contain PM-conversation
context, so it's not committed). Keyed by ADO id -> list of dated, kinded note entries.

Pure + deterministic. Manages a local JSON file + a markdown render. No ADO, no git, no sends.

CLI:
    feature_notes.py add  --ado 90001002 --note "PM confirmed Win32-only for 2607" [--kind decision] [--author alex]
    feature_notes.py read --ado 90001002 [--json]     # what harvester H10 calls
    feature_notes.py list [--json]                     # all features that have notes
    feature_notes.py render --ado 90001002             # markdown for human reading

Kinds (free-form; standard set): decision · hold · pm-convo · timing · scope · risk · other
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path


def _state_root() -> Path:
    """Root for Atlas runtime state. Honors $ATLAS_STATE_DIR (used by the sandbox to redirect
    all writes to a disposable location); defaults to the plugin's own state/ dir."""
    env = os.environ.get("ATLAS_STATE_DIR")
    return Path(env) if env else (Path(__file__).resolve().parent.parent / "state")


NOTES_PATH = _state_root() / "feature-notes.json"

STANDARD_KINDS = ("decision", "hold", "pm-convo", "timing", "scope", "risk", "other")


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict:
    if not NOTES_PATH.exists():
        return {"features": {}}
    try:
        return json.loads(NOTES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"features": {}}


def _atomic_write(data: dict) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(NOTES_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, NOTES_PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def add(ado: str, note: str, kind: str = "other", author: str = "alex") -> dict:
    data = _load()
    feat = data["features"].setdefault(str(ado), {"notes": []})
    entry = {
        "date": _today(),
        "kind": kind or "other",
        "author": author or "unknown",
        "note": note,
    }
    # Idempotency guard: skip an identical note (same date + kind + text) already present,
    # so re-running a harvest that auto-logs context doesn't duplicate entries.
    for existing in feat["notes"]:
        if (existing.get("date") == entry["date"] and existing.get("kind") == entry["kind"]
                and existing.get("note") == entry["note"]):
            return {"status": "duplicate", "ado": str(ado), "entry": entry}
    feat["notes"].append(entry)
    _atomic_write(data)
    return {"status": "added", "ado": str(ado), "entry": entry, "count": len(feat["notes"])}


def read(ado: str) -> dict:
    data = _load()
    feat = data["features"].get(str(ado))
    if not feat:
        return {"ado": str(ado), "notes": []}
    return {"ado": str(ado), "notes": feat["notes"]}


def list_features() -> list[dict]:
    data = _load()
    out = []
    for ado, feat in data["features"].items():
        notes = feat.get("notes", [])
        last = max((n.get("date", "") for n in notes), default="")
        out.append({"ado": ado, "count": len(notes), "last": last})
    return sorted(out, key=lambda r: r["last"], reverse=True)


def render(ado: str) -> str:
    feat = read(ado)
    if not feat["notes"]:
        return f"# Feature notes — {ado}\n\n_(no notes)_\n"
    lines = [f"# Feature notes — {ado}", ""]
    for n in feat["notes"]:
        lines.append(f"- **{n['date']}** _[{n['kind']}, {n.get('author','?')}]_ — {n['note']}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas prior-feature notes log (harvest source H10).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add")
    pa.add_argument("--ado", required=True)
    pa.add_argument("--note", required=True)
    pa.add_argument("--kind", default="other")
    pa.add_argument("--author", default="alex")

    prd = sub.add_parser("read")
    prd.add_argument("--ado", required=True)
    prd.add_argument("--json", action="store_true")

    pl = sub.add_parser("list")
    pl.add_argument("--json", action="store_true")

    prn = sub.add_parser("render")
    prn.add_argument("--ado", required=True)

    args = p.parse_args(argv)

    if args.cmd == "add":
        res = add(args.ado, args.note, args.kind, args.author)
        if res["status"] == "duplicate":
            print(f"duplicate — not added again ({args.ado})")
        else:
            print(f"added note to {args.ado} (now {res['count']} note(s))")
    elif args.cmd == "read":
        res = read(args.ado)
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        elif not res["notes"]:
            print(f"no notes for {args.ado}")
        else:
            for n in res["notes"]:
                print(f"{n['date']} [{n['kind']}, {n.get('author','?')}] {n['note']}")
    elif args.cmd == "list":
        rows = list_features()
        if args.json:
            sys.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
        elif not rows:
            print("no feature notes yet")
        else:
            for r in rows:
                print(f"{r['ado']}: {r['count']} note(s), last {r['last']}")
    elif args.cmd == "render":
        sys.stdout.write(render(args.ado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

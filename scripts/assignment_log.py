#!/usr/bin/env python3
"""Atlas — writer-assignment notification ledger.

When a feature lands on a writer's slate (its `WorkItem.CommsWriter` is set to them), the
writer should be told once: "feature X (release YYMM) is now yours." Stage 0 INTAKE is where
Atlas discovers each writer's slate, so it's the natural place to detect *newly* assigned
features and notify. The hard part is "once": without a memory of what's already been announced,
every run would re-notify the whole slate. This ledger is that memory.

It records the (writer, ADO id) pairs Atlas has already NOTIFIED. A feature is "pending
notification" for a writer until it's marked notified. Keying by (writer, id) makes reassignment
work for free: if a feature moves from Alex to Morgan, Morgan's pair isn't on record so she gets
notified; Alex's already is, so he isn't re-pinged.

`notified` is only set after a REAL send. Under `simulation_only` the skill composes + logs the
message but does NOT mark notified, so the item stays pending and the first real run still
announces it. (Local state is the simulation's scratchpad, but we deliberately don't burn the
notification here.)

Pure + deterministic. Manages a local JSON file. No ADO, no Teams/email, no judgement about who
to message or how — the skill layer owns the send + the self-vs-gated routing.

Storage: state/assignments-seen.json (gitignored — local runtime state; holds writer names + ids).

CLI:
    assignment_log.py pending --writer "Morgan Example" --items '[{"ado":"90001013","title":"...","timeline":"2607"}]'
    assignment_log.py mark    --writer "Morgan Example" --ado 90001013 --title "..." --timeline 2607
    assignment_log.py is-notified --writer "Morgan Example" --ado 90001013   # exit 0 = notified, 1 = not
    assignment_log.py reassignments --worklist '[{"ado":"90001013","writer":"Casey Example","title":"...","timeline":"2607"}]'
    assignment_log.py clear   --writer "Morgan Example" --ado 90001013       # after the departure notice
    assignment_log.py list [--writer "Morgan Example"] [--json]
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


LEDGER_PATH = _state_root() / "assignments-seen.json"


def _today() -> str:
    return date.today().isoformat()


def _load() -> dict:
    if not LEDGER_PATH.exists():
        return {"writers": {}}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"writers": {}}


def _atomic_write(data: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(LEDGER_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, LEDGER_PATH)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _writer_key(writer: str) -> str:
    return (writer or "").strip()


def is_notified(writer: str, ado: str | int) -> bool:
    """True if (writer, ado) has already been announced to that writer."""
    data = _load()
    feats = data["writers"].get(_writer_key(writer), {})
    return str(ado) in feats


def pending(writer: str, items: list[dict]) -> list[dict]:
    """From a writer's current slate, return the entries not yet notified.

    `items` is the worklist subset for this writer: dicts with at least `ado` (id) and ideally
    `title` + `timeline`. De-dupes on ado, preserves input order.
    """
    data = _load()
    feats = data["writers"].get(_writer_key(writer), {})
    out: list[dict] = []
    seen_ids: set[str] = set()
    for it in items or []:
        ado = str(it.get("ado") or it.get("id") or "").strip()
        if not ado or ado in seen_ids:
            continue
        seen_ids.add(ado)
        if ado not in feats:
            out.append({"ado": ado, "title": it.get("title", ""), "timeline": it.get("timeline", "")})
    return out


def mark_notified(writer: str, ado: str | int, title: str = "", timeline: str = "") -> dict:
    """Record that the writer was notified about this feature. Idempotent on (writer, ado):
    re-marking refreshes title/timeline metadata but doesn't change the first-notified date."""
    data = _load()
    wkey = _writer_key(writer)
    feats = data["writers"].setdefault(wkey, {})
    ado = str(ado)
    if ado in feats:
        # Keep the original notified date; refresh metadata if newly provided.
        if title:
            feats[ado]["title"] = title
        if timeline:
            feats[ado]["timeline"] = timeline
        return {"status": "already", "writer": wkey, "ado": ado, "entry": feats[ado]}
    feats[ado] = {"notified": _today(), "title": title, "timeline": timeline}
    _atomic_write(data)
    return {"status": "marked", "writer": wkey, "ado": ado, "entry": feats[ado],
            "count": len(feats)}


def detect_reassignments(worklist: list[dict]) -> list[dict]:
    """Given the CURRENT active worklist across ALL writers (each {ado, writer, title, timeline}),
    return reassignment events: features the ledger shows were already notified to one writer but
    are now assigned to a DIFFERENT writer. Each event:
        {ado, title, timeline, from_writer, to_writer}

    Used to tell the PREVIOUS writer a feature left their plate (the new writer is handled by
    `pending`). Conservative on purpose: an event fires only when the feature is positively present
    in the current worklist under a *different* writer. A feature that merely dropped off the
    worklist (closed/cut/removed, or outside this run's timeline window) does NOT generate an event
    — Atlas never infers "it left your plate" from absence, only from a visible reassignment, so a
    feature that simply shipped/closed doesn't trigger a false departure notice.
    """
    data = _load()
    current: dict[str, dict] = {}
    for it in worklist or []:
        ado = str(it.get("ado") or it.get("id") or "").strip()
        w = _writer_key(it.get("writer", ""))
        if ado and w:
            current[ado] = {"writer": w, "title": it.get("title", ""),
                            "timeline": it.get("timeline", "")}
    events: list[dict] = []
    for prev_writer, feats in data["writers"].items():
        for ado, meta in feats.items():
            cur = current.get(ado)
            if cur and cur["writer"] != prev_writer:
                events.append({
                    "ado": ado,
                    "title": cur["title"] or meta.get("title", ""),
                    "timeline": cur["timeline"] or meta.get("timeline", ""),
                    "from_writer": prev_writer,
                    "to_writer": cur["writer"],
                })
    return events


def clear(writer: str, ado: str | int) -> dict:
    """Remove a (writer, id) entry — e.g. after telling the previous writer the feature left their
    plate. If the feature ever returns to them, they get a fresh new-assignment notice. Empty
    writer buckets are dropped to keep the file tidy."""
    data = _load()
    wkey = _writer_key(writer)
    feats = data["writers"].get(wkey, {})
    ado = str(ado)
    if ado not in feats:
        return {"status": "absent", "writer": wkey, "ado": ado}
    del feats[ado]
    if not feats:
        del data["writers"][wkey]
    _atomic_write(data)
    return {"status": "cleared", "writer": wkey, "ado": ado}


def list_for(writer: str | None) -> dict:
    """All notified features for one writer, or a writer->count summary if writer is None."""
    data = _load()
    if writer:
        wkey = _writer_key(writer)
        feats = data["writers"].get(wkey, {})
        return {"writer": wkey, "features": feats}
    return {w: len(feats) for w, feats in data["writers"].items()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas writer-assignment notification ledger.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("pending")
    pp.add_argument("--writer", required=True)
    pp.add_argument("--items", required=True, help="JSON array of {ado,title,timeline}.")
    pp.add_argument("--json", action="store_true")

    pm = sub.add_parser("mark")
    pm.add_argument("--writer", required=True)
    pm.add_argument("--ado", required=True)
    pm.add_argument("--title", default="")
    pm.add_argument("--timeline", default="")

    pi = sub.add_parser("is-notified")
    pi.add_argument("--writer", required=True)
    pi.add_argument("--ado", required=True)

    pr = sub.add_parser("reassignments")
    pr.add_argument("--worklist", required=True,
                    help="JSON array of {ado,writer,title,timeline} for ALL active features this run.")
    pr.add_argument("--json", action="store_true")

    pc = sub.add_parser("clear")
    pc.add_argument("--writer", required=True)
    pc.add_argument("--ado", required=True)

    pl = sub.add_parser("list")
    pl.add_argument("--writer", default=None)
    pl.add_argument("--json", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "pending":
        items = json.loads(args.items or "[]")
        res = pending(args.writer, items)
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        elif not res:
            print(f"no new assignments for {args.writer}")
        else:
            print(f"{len(res)} new assignment(s) for {args.writer}:")
            for r in res:
                print(f"  {r['ado']} [{r['timeline'] or '?'}] {r['title']}")
    elif args.cmd == "mark":
        res = mark_notified(args.writer, args.ado, args.title, args.timeline)
        if res["status"] == "already":
            print(f"already notified: {args.writer} / {args.ado}")
        else:
            print(f"marked notified: {args.writer} / {args.ado} (now {res['count']} on record)")
    elif args.cmd == "is-notified":
        notified = is_notified(args.writer, args.ado)
        print(f"{args.writer} / {args.ado}: {'notified' if notified else 'NOT notified'}")
        return 0 if notified else 1
    elif args.cmd == "reassignments":
        worklist = json.loads(args.worklist or "[]")
        events = detect_reassignments(worklist)
        if args.json:
            sys.stdout.write(json.dumps(events, indent=2, ensure_ascii=False) + "\n")
        elif not events:
            print("no reassignments detected")
        else:
            print(f"{len(events)} reassignment(s):")
            for e in events:
                print(f"  {e['ado']} [{e['timeline'] or '?'}] {e['title']}: "
                      f"{e['from_writer']} -> {e['to_writer']}")
        return 0 if events else 1
    elif args.cmd == "clear":
        res = clear(args.writer, args.ado)
        if res["status"] == "absent":
            print(f"no ledger entry for {args.writer} / {args.ado}")
        else:
            print(f"cleared {args.writer} / {args.ado}")
    elif args.cmd == "list":
        res = list_for(args.writer)
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
        elif args.writer:
            feats = res["features"]
            if not feats:
                print(f"no notified assignments on record for {args.writer}")
            else:
                for ado, meta in feats.items():
                    print(f"  {ado} [{meta.get('timeline') or '?'}] notified {meta.get('notified')} "
                          f"{meta.get('title', '')}")
        elif not res:
            print("no assignments notified yet")
        else:
            for w, n in res.items():
                print(f"{w}: {n} notified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

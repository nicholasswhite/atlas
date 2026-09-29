#!/usr/bin/env python3
"""Atlas P5 — cross-owner handoff queue.

With a two-person crew, "Atlas can't finish this" must never mean "it's dropped." Anything the
pipeline can't complete on its own lands here, owner-tagged, for either writer to drain:

  - features with no CRW yet (waiting on a PM)
  - cross-owner article edits (tagged with the real ms.author)
  - conflicts needing a human decision (tag-vs-toggle, doc-vs-toggle, date mismatch)
  - owner-unknown articles (ms.author set by docfx path-glob — verify before editing)
  - Private Preview holds

Substrate decision (per the design doc): until the team picks between the `agent-inbox` GitHub
Issues queue and a simple file, this writes an owner-tagged markdown file both writers can read,
at `state/handoff-queue.md` (gitignored — local runtime state). The format mirrors the repo's
`backlog.md` checklist convention so it's familiar to scan.

Pure + deterministic. Manages a local markdown file. No ADO, no git, no sends.

CLI:
    handoff.py add --ado 90001013 --owner alex --kind no-crw --reason "Waiting on PM for CRW"
    handoff.py list [--owner alex] [--status open|resolved|all]
    handoff.py resolve --ado 90001013 [--kind no-crw] [--note "PM provided CRW 2026-06-12"]
    handoff.py count            # quick open/resolved tallies (machine-readable)

Kinds (free-form, but these are the standard ones):
    no-crw · cross-owner-edit · owner-unknown · conflict · held · other
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path


def _state_root() -> Path:
    """Root for Atlas runtime state. Honors $ATLAS_STATE_DIR (used by the sandbox to redirect
    all writes to a disposable location); defaults to the plugin's own state/ dir."""
    env = os.environ.get("ATLAS_STATE_DIR")
    return Path(env) if env else (Path(__file__).resolve().parent.parent / "state")


QUEUE_PATH = _state_root() / "handoff-queue.md"

_HEADER = """# Atlas Handoff Queue

Items the Atlas pipeline couldn't finish on its own — for the writers to drain. Owner-tagged.
Each line: `date | ADO_ID | owner:<alias> | kind:<kind> | <reason>`.

Managed by `scripts/handoff.py` (add / list / resolve). Gitignored — local runtime state.
"""

_OPEN_HEADER = "## Open"
_RESOLVED_HEADER = "## Resolved"

# - [ ] 2026-06-10 | 90001013 | owner:alex | kind:no-crw | Waiting on PM for CRW
_ENTRY_RE = re.compile(
    r"^- \[(?P<done>[ x])\] (?P<date>\S+) \| (?P<ado>[^|]+?) \| owner:(?P<owner>[^|]*?) \| "
    r"kind:(?P<kind>[^|]*?) \| (?P<reason>.*)$"
)


def _today() -> str:
    return date.today().isoformat()


def _ensure() -> None:
    if not QUEUE_PATH.exists():
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUEUE_PATH.write_text(
            _HEADER + "\n" + _OPEN_HEADER + "\n\n" + _RESOLVED_HEADER + "\n", encoding="utf-8"
        )


def _parse() -> tuple[list[dict], list[dict]]:
    """Return (open_entries, resolved_entries)."""
    _ensure()
    text = QUEUE_PATH.read_text(encoding="utf-8")
    section = None
    open_e: list[dict] = []
    resolved_e: list[dict] = []
    for line in text.splitlines():
        if line.strip() == _OPEN_HEADER:
            section = "open"
            continue
        if line.strip() == _RESOLVED_HEADER:
            section = "resolved"
            continue
        m = _ENTRY_RE.match(line.strip())
        if not m:
            continue
        entry = {
            "date": m.group("date"),
            "ado": m.group("ado").strip(),
            "owner": m.group("owner").strip(),
            "kind": m.group("kind").strip(),
            "reason": m.group("reason").strip(),
        }
        if section == "open" and m.group("done") == " ":
            open_e.append(entry)
        elif section == "resolved" or m.group("done") == "x":
            resolved_e.append(entry)
    return open_e, resolved_e


def _fmt(entry: dict, done: bool) -> str:
    mark = "x" if done else " "
    return (f"- [{mark}] {entry['date']} | {entry['ado']} | owner:{entry['owner']} | "
            f"kind:{entry['kind']} | {entry['reason']}")


def _write(open_e: list[dict], resolved_e: list[dict]) -> None:
    out = [_HEADER, "", _OPEN_HEADER, ""]
    out.extend(_fmt(e, False) for e in open_e)
    if not open_e:
        out.append("_(empty)_")
    out.extend(["", _RESOLVED_HEADER, ""])
    out.extend(_fmt(e, True) for e in resolved_e)
    if not resolved_e:
        out.append("_(empty)_")
    out.append("")
    QUEUE_PATH.write_text("\n".join(out), encoding="utf-8")


def add(ado: str, owner: str, kind: str, reason: str) -> str:
    open_e, resolved_e = _parse()
    # Dedup: same ado + kind already open = no-op (refresh reason if changed).
    for e in open_e:
        if e["ado"] == ado and e["kind"] == kind:
            if e["reason"] != reason:
                e["reason"] = reason
                _write(open_e, resolved_e)
                return f"updated existing open item {ado} ({kind})"
            return f"already queued: {ado} ({kind}) — no change"
    open_e.append({"date": _today(), "ado": ado, "owner": owner or "unassigned",
                   "kind": kind or "other", "reason": reason})
    _write(open_e, resolved_e)
    return f"added: {ado} | owner:{owner} | kind:{kind}"


def resolve(ado: str, kind: str | None, note: str | None) -> str:
    open_e, resolved_e = _parse()
    moved = []
    remaining = []
    for e in open_e:
        if e["ado"] == ado and (kind is None or e["kind"] == kind):
            if note:
                e["reason"] = f"{e['reason']} (resolved {_today()}: {note})"
            else:
                e["reason"] = f"{e['reason']} (resolved {_today()})"
            moved.append(e)
        else:
            remaining.append(e)
    if not moved:
        return f"no open item matched ado={ado}" + (f" kind={kind}" if kind else "")
    resolved_e.extend(moved)
    _write(remaining, resolved_e)
    return f"resolved {len(moved)} item(s) for {ado}"


def list_items(owner: str | None, status: str) -> str:
    open_e, resolved_e = _parse()
    lines = []
    if status in ("open", "all"):
        sel = [e for e in open_e if not owner or e["owner"].lower() == owner.lower()]
        lines.append(f"OPEN ({len(sel)}):")
        if sel:
            lines.extend("  " + _fmt(e, False) for e in sel)
        else:
            lines.append("  (none)")
    if status in ("resolved", "all"):
        sel = [e for e in resolved_e if not owner or e["owner"].lower() == owner.lower()]
        lines.append(f"RESOLVED ({len(sel)}):")
        if sel:
            lines.extend("  " + _fmt(e, True) for e in sel)
        else:
            lines.append("  (none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas cross-owner handoff queue.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add")
    pa.add_argument("--ado", required=True)
    pa.add_argument("--owner", default="unassigned")
    pa.add_argument("--kind", default="other")
    pa.add_argument("--reason", required=True)

    pl = sub.add_parser("list")
    pl.add_argument("--owner", default=None)
    pl.add_argument("--status", default="open", choices=["open", "resolved", "all"])

    pr = sub.add_parser("resolve")
    pr.add_argument("--ado", required=True)
    pr.add_argument("--kind", default=None)
    pr.add_argument("--note", default=None)

    sub.add_parser("count")

    args = p.parse_args(argv)

    if args.cmd == "add":
        print(add(args.ado, args.owner, args.kind, args.reason))
    elif args.cmd == "list":
        print(list_items(args.owner, args.status))
    elif args.cmd == "resolve":
        print(resolve(args.ado, args.kind, args.note))
    elif args.cmd == "count":
        open_e, resolved_e = _parse()
        print(f"open={len(open_e)} resolved={len(resolved_e)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

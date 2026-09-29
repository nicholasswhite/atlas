#!/usr/bin/env python3
"""Atlas — CRW tracking-tag advancement (the mechanical half of auto-approval).

When Atlas detects that the PM has approved a draft (the *judgement* is made by the skill/LLM
layer reading the PM's words in whatever form they take), this script performs the *mechanical*
half: advance the CRW tracking tag `iddraft -> idready` / `wndraft -> wnready` correctly, so the
existing stage gate (lifecycle.py) then sees a ready item and routes it to staging. That removes
the last routine human touch — the writer no longer hand-sets the ready tag — leaving only the
final PR merge as a human step.

Why a deterministic script and not freehand LLM editing: the CRW tracking comment has exact,
fiddly formatting rules (cumulative tags, exactly ONE bolded current tag via HTML `<b>`, the
`<!-- ids tags -->` comment which is often entity-encoded `&lt;!-- ... --&gt;` for visibility and
can carry nested `<span>`/`&nbsp;` cruft). Malforming it on a live work item is a real risk, so
the transform is codified + tested here. The script is PURE: it transforms a CRW string and
returns the result. It does NO ADO write, NO judgement about whether an approval happened — the
skill layer decides that, then calls this, then PATCHes the field (gated by simulation_only).

Tag ladders (cumulative; only the newest tag is bolded — see ado-patterns / release-process):
    ID:  iddraft -> idready -> idstaged
    WN:  wndraft -> wnready -> wnstaged
The approval hop is draft -> ready. **Setting the ready tag does NOT require the draft tag to be
present.** Not every writer uses iddraft/wndraft, and unless Atlas drafted the item itself (its
Stage 3 adds the draft tag) the marker may be absent entirely. The real gate — a real draft blurb
exists AND the PM approved — is the skill layer's job, not a tag check here. So the ready hop has
no tag precondition: it adds the ready tag to whatever marker exists (preserving any tags that ARE
there), or seeds a fresh marker from --own when there's none. The staged hop (idstaged/wnstaged) is
set by the staging skills and DOES require the ready tag, which Atlas sets first.

Rebuild is canonical: the messy nested-HTML body is replaced with a clean
`{ids} {tag1} {tag2} <b>{current}</b>` form, preserving the delimiter style found (entity-encoded
vs raw) so a previously-visible tag stays visible and a hidden one stays hidden. Across the whole
comment exactly one tag is bolded (the just-advanced one); any prior bold is dropped, satisfying
"bold the current, unbold the previous."

CLI:
    crw_tags.py advance --track wn --crw "<...html...>"            # draft->ready (the approval hop)
    crw_tags.py advance --track id --crw-file path [--own 12345]   # target the comment with this id
    crw_tags.py advance --track wn --to wnstaged --crw "<...>"     # explicit target tag
    cat crw.html | crw_tags.py advance --track id                  # CRW on stdin
    add --json for machine-readable {status, from_tag, to_tag, ids, crw}

Exit codes: 0 advanced or seeded · 3 already at target (no-op) · 1 cannot place (no marker + no --own, or staged-without-ready).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

ID_LADDER = ("iddraft", "idready", "idstaged")
WN_LADDER = ("wndraft", "wnready", "wnstaged")
LADDERS = {"id": ID_LADDER, "wn": WN_LADDER}
ALL_TAGS = ID_LADDER + WN_LADDER

_ID_RE = re.compile(r"\b\d{5,9}\b")          # ADO ids: 5-9 digits (excludes 4-digit YYMM)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# A tracking comment, either raw <!-- ... --> or entity-encoded &lt;!-- ... --&gt;.
_COMMENT_RE = re.compile(r"(&lt;!--.*?--&gt;|<!--.*?-->)", re.DOTALL)


def _clean_body(raw_body: str) -> str:
    """Strip nested HTML tags + unescape entities + normalize whitespace, so the bare tokens
    (ids and tag words) can be read out of a messy comment body."""
    no_tags = _HTML_TAG_RE.sub(" ", raw_body)
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _inner(match_text: str) -> str:
    """Return the body between the comment delimiters (handles both forms)."""
    if match_text.startswith("&lt;!--"):
        return match_text[len("&lt;!--"):-len("--&gt;")]
    return match_text[len("<!--"):-len("-->")]


def _encoded(match_text: str) -> bool:
    return match_text.startswith("&lt;!--")


def _find_target_comment(crw: str, own_id: str | None):
    """Pick the marker to advance. Prefer a comment that already carries a known tag (and, when
    several do, the one whose body contains own_id). Failing that, accept a comment that carries
    own_id even with NO workflow tag (an id-only marker some writers leave) — so we advance it in
    place rather than seeding a duplicate. Returns (start, end, body, encoded) or None."""
    tagged: list = []
    id_only: list = []
    for m in _COMMENT_RE.finditer(crw):
        body = _inner(m.group(0))
        clean = _clean_body(body)
        ids = _ID_RE.findall(clean)
        entry = (m.start(), m.end(), body, _encoded(m.group(0)), ids)
        if any(re.search(rf"\b{t}\b", clean) for t in ALL_TAGS):
            tagged.append(entry)
        elif own_id and str(own_id) in ids:
            id_only.append(entry)
    if tagged:
        if own_id:
            for c in tagged:
                if str(own_id) in c[4]:
                    return c[:4]
        return tagged[0][:4]
    if id_only:
        return id_only[0][:4]
    return None


def _canonical(ids: list[str], present: set[str], new_tag: str, encoded: bool) -> str:
    """Build a clean comment: '{ids} {tags...}' with only new_tag bolded, in ladder order."""
    tags = set(present) | {new_tag}
    ordered = [t for t in ALL_TAGS if t in tags]
    rendered = " ".join(f"<b>{t}</b>" if t == new_tag else t for t in ordered)
    ids_str = ", ".join(ids)
    body = f"{ids_str} {rendered}".strip()
    return f"&lt;!-- {body} --&gt;" if encoded else f"<!-- {body} -->"


def advance_tag(crw: str | None, track: str, to_tag: str | None = None,
                own_id: str | int | None = None) -> dict:
    """Advance a CRW tracking tag for one track. Default hop is draft -> ready (the approval hop).

    Returns: {status, track, from_tag, to_tag, ids, crw}. Statuses:
      advanced     — tag added to an existing marker; crw is the modified field
      seeded       — no marker existed, so a fresh one was created from own_id (+ the tag)
      already      — to_tag already present; crw unchanged (idempotent)
      not_ready    — staged requested but the ready tag isn't present (stage via the staging skill)
      no_tracking  — no marker and no own_id to seed one with; can't place a tag
      invalid      — bad track/to_tag
    """
    track = (track or "").strip().lower()
    if track not in LADDERS:
        return {"status": "invalid", "track": track, "from_tag": None, "to_tag": None,
                "ids": [], "crw": crw, "detail": f"track must be one of {tuple(LADDERS)}"}
    ladder = LADDERS[track]
    target = (to_tag or ladder[1]).strip().lower()   # default = the ready tag
    if target not in ladder:
        return {"status": "invalid", "track": track, "from_tag": None, "to_tag": target,
                "ids": [], "crw": crw, "detail": f"to_tag must be one of {ladder}"}

    oid = str(own_id) if own_id is not None else None
    found = _find_target_comment(crw, oid) if crw else None
    if not found:
        # No usable marker. Writers don't always embed one (and don't always use draft tags). If we
        # have the work-item id, SEED a fresh marker carrying just the requested tag — we never
        # invent a draft tag the writer didn't use. Without an id there's nowhere to anchor it.
        if oid:
            seeded = _canonical([oid], set(), target, encoded=True)
            sep = "" if (not crw or crw.endswith(("\n", ">", " "))) else " "
            return {"status": "seeded", "track": track, "from_tag": None, "to_tag": target,
                    "ids": [oid], "crw": (crw or "") + sep + seeded}
        return {"status": "no_tracking", "track": track, "from_tag": None, "to_tag": target,
                "ids": [], "crw": crw}

    start, end, body, encoded = found
    clean = _clean_body(body)
    present = {t for t in ALL_TAGS if re.search(rf"\b{t}\b", clean)}
    ids = _ID_RE.findall(clean) or ([oid] if oid else [])

    # Idempotent: target already recorded.
    if target in present:
        return {"status": "already", "track": track, "from_tag": target, "to_tag": target,
                "ids": ids, "crw": crw}

    idx = ladder.index(target)
    # The ready hop (idx 1) has NO tag precondition: not every writer sets iddraft/wndraft, so the
    # real gate (a real draft blurb + the PM's approval) is enforced by the skill layer, not here.
    # The staged hop (idx 2) still requires the ready tag, which Atlas itself sets before staging.
    if idx >= 2 and ladder[idx - 1] not in present:
        return {"status": "not_ready", "track": track, "from_tag": None, "to_tag": target,
                "ids": ids, "crw": crw,
                "detail": f"can't set {target}: {ladder[idx - 1]} not present (stage via the staging skill)"}

    from_tag = ladder[idx - 1] if (idx and ladder[idx - 1] in present) else None
    new_comment = _canonical(ids, present, target, encoded)
    new_crw = crw[:start] + new_comment + crw[end:]
    return {"status": "advanced", "track": track, "from_tag": from_tag,
            "to_tag": target, "ids": ids, "crw": new_crw}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Advance a CRW tracking tag (auto-approval mechanics).")
    sub = p.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("advance")
    pa.add_argument("--track", required=True, choices=("id", "wn"))
    pa.add_argument("--to", default=None, help="Target tag (default: the track's ready tag).")
    pa.add_argument("--own", default=None, help="Work item's own id (targets the right comment).")
    pa.add_argument("--crw", default=None, help="CRW HTML. If omitted, read from stdin.")
    pa.add_argument("--crw-file", default=None, help="Path to a file holding the CRW HTML.")
    pa.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.crw is not None:
        crw = args.crw
    elif args.crw_file:
        with open(args.crw_file, encoding="utf-8") as fh:
            crw = fh.read()
    else:
        crw = sys.stdin.read()

    res = advance_tag(crw, args.track, args.to, args.own)
    if args.json:
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    else:
        if res["status"] in ("advanced", "seeded"):
            print(f"{res['status']} {res['track'].upper()}: {res['from_tag']} -> {res['to_tag']}", file=sys.stderr)
            sys.stdout.write(res["crw"])
        elif res["status"] == "already":
            print(f"already at {res['to_tag']} — no change", file=sys.stderr)
        else:
            print(f"cannot place ({res['status']}): {res.get('detail', '')}", file=sys.stderr)
    return {"advanced": 0, "seeded": 0, "already": 3}.get(res["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())

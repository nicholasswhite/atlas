#!/usr/bin/env python3
"""Atlas Stage -1 — release LANDSCAPE resolver (deterministic).

The per-item pipeline kept hitting SLATE-shaped blind spots (exercised by synthetic release testing): combined blurbs (one blurb covering N items, e.g. the Plugin apps), release clusters
(the 4 Sync workstreams), tagless PM-authored content (a rough draft to enrich, not re-draft),
at-creation templates (one item reusing another's blurb), and retiring-writer orphans. None of
these are visible one item at a time.

This resolver is the deterministic core of the "context layer": given a release's comms items, it
computes the RELATIONSHIP GRAPH the per-item runs consume up front — so each item already knows its
combined-blurb primary, its cluster siblings, whether its content is a rough tagless draft, and
whether it's orphaned. The BUILD is deterministic (this); the JUDGEMENT ("should these 4 items be
one blurb?") stays in the agent layer, informed by `suggest_combine`.

Pure + stdlib-only. No network; the agent harvests the items (WIQL + REST) and feeds them in.

Item shape (lenient; extra keys ignored):
    {"id","title","area_path","comms_writer","assigned_to","state",
     "id_required"?,"wn_required"?,
     "crw_tags":[...],            # tracking tags present in the CRW (iddraft/wnready/...)
     "crw_refs":[...],            # ORDERED member ids from a combined-blurb marker (e.g.
                                  #   <!-- wndraft, 90001021, 90001022, 90001023 -->); [] if none
     "crw_has_content": bool}     # CRW has a real blurb (not empty / template boilerplate)

CLI:
    landscape.py build --items '<json|file|->' [--release 2608]
                       [--roster config/roster.json | --retiring taylor,jordan] [--json]

Output: the landscape object — combined_blurbs, clusters, tagless_content, coverage, orphaned, and a
per_item summary (role / combined_primary / clusters / tagless / orphaned / note) keyed by id.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_DRAFT = ("iddraft", "wndraft")
_READY = ("idready", "wnready", "idstaged", "wnstaged")
_ALL_TAGS = _DRAFT + _READY


def _sid(v) -> str:
    return str(v).strip()


def _tags(item) -> list:
    return [str(t).strip().lower() for t in (item.get("crw_tags") or []) if str(t).strip()]


def _refs(item) -> list:
    """Ordered, de-duped combined-blurb member ids from the item's marker."""
    out = []
    for r in item.get("crw_refs") or []:
        s = _sid(r)
        if s and s not in out:
            out.append(s)
    return out


def _has_content(item) -> bool:
    return bool(item.get("crw_has_content"))


# --- family / cluster ---------------------------------------------------------------------------
_SKIP_BRACKET = re.compile(r"(?i)^(?:\d{4}|workstream\b.*|phase\b.*|ws\s*\d+|wave\b.*|part\b.*)$")


def _family(title: str, area_path: str = "") -> str | None:
    """A stable feature-family key for clustering: the first MEANINGFUL bracket tag in the title
    (skipping release YYMM + workstream/phase/wave), else the 'Add/Update Plugin App' pattern, else the
    area-path leaf. e.g. '[Sync] [Workstream 4] ...' -> 'sync'; '[2608] Add Plugin App ...' -> 'plugin app'."""
    title = title or ""
    for b in re.findall(r"\[([^\]]+)\]", title):
        bl = b.strip()
        if bl and not _SKIP_BRACKET.match(bl):
            return bl.lower()
    if re.search(r"(?i)\b(?:add|update|remove)\s+plugin\s+app\b", title):
        return "plugin app"
    leaf = (area_path or "").replace("\\", "/").rstrip("/").split("/")[-1].strip()
    return leaf.lower() or None


# --- combined blurbs (union of the marker member sets) ------------------------------------------
def _combined_blurbs(items: list) -> tuple[list, dict]:
    """Group items that share a combined-blurb marker. The PRIMARY is the id listed FIRST in the
    marker (the primary-secondary convention); satellites that carry the replicated marker but don't
    self-list still resolve to that same primary. Returns (groups, id->primary map). A member id
    referenced in a marker but absent from the item set is recorded as `external`."""
    present = {_sid(it.get("id")) for it in items}
    tags_by_id = {_sid(it.get("id")): _tags(it) for it in items}

    # union-find over ids that co-occur in any marker
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    markers: list = []   # (iid, raw_refs) for every item that carries a marker
    for it in items:
        iid = _sid(it.get("id"))
        refs = _refs(it)
        if not refs:
            continue
        markers.append((iid, refs))
        member_ids = refs if iid in refs else [iid] + refs   # self joins the group even if not listed
        for m in member_ids:
            union(iid, m)

    # group ALL involved ids by their FINAL root (re-keying after every union avoids the stale-rep
    # phantom-group bug: a rep captured mid-union can stop being a root once a later marker merges in)
    groups_by_root: dict[str, set] = {}
    for x in list(parent):
        groups_by_root.setdefault(find(x), set()).add(x)

    groups = []
    id_to_primary: dict[str, str] = {}
    for root, members in groups_by_root.items():
        if len(members) < 2:
            continue
        grp_markers = [refs for (iid, refs) in markers if find(iid) == root]
        # ordered member list = first appearance across the raw markers, then any leftover members
        ordered: list = []
        for refs in grp_markers:
            for x in refs:
                if x not in ordered:
                    ordered.append(x)
        for x in sorted(members):
            if x not in ordered:
                ordered.append(x)
        # primary = the id most often listed FIRST in a raw marker (convention), tie-break by
        # present-then-earliest. Using raw refs[0] (not the self-prepended member list) means a
        # satellite that doesn't self-list still votes for the true primary.
        first_ids = [refs[0] for refs in grp_markers if refs]
        if first_ids:
            cnt = Counter(first_ids)
            top = max(cnt.values())
            cands = [i for i in dict.fromkeys(first_ids) if cnt[i] == top]
            primary = sorted(cands, key=lambda i: (i not in present, ordered.index(i)))[0]
        else:
            primary = sorted(members)[0]
        members_ordered = [primary] + [m for m in ordered if m != primary]
        present_members = [m for m in members_ordered if m in present]
        external = [m for m in members_ordered if m not in present]
        track = set()
        for m in members:
            for t in tags_by_id.get(m, []):
                if t.startswith("wn"):
                    track.add("wn")
                elif t.startswith("id"):
                    track.add("id")
        for m in members:
            id_to_primary[m] = primary
        groups.append({
            "primary": primary,
            "members": members_ordered,
            "present": present_members,
            "external": external,
            "track": sorted(track) or ["wn"],
        })
    groups.sort(key=lambda g: g["primary"])
    return groups, id_to_primary


# --- clusters (feature families) ---------------------------------------------------------------
def _clusters(items: list, id_to_primary: dict) -> list:
    fam: dict[str, list] = {}
    meta: dict[str, dict] = {}
    for it in items:
        f = _family(it.get("title", ""), it.get("area_path", ""))
        if not f:
            continue
        iid = _sid(it.get("id"))
        fam.setdefault(f, []).append(iid)
        meta.setdefault(f, {"areas": set()})
        if it.get("area_path"):
            meta[f]["areas"].add(it["area_path"])
    out = []
    for f, ids in fam.items():
        if len(ids) < 2:
            continue
        # already combined = every member resolves to the SAME combined-blurb primary
        prims = {id_to_primary.get(i) for i in ids}
        already = len(prims) == 1 and None not in prims
        out.append({
            "family": f,
            "members": sorted(ids),
            "areas": sorted(meta[f]["areas"]),
            "already_combined": already,
            "suggest_combine": not already,   # a family that ISN'T one blurb yet -> consider combining
        })
    out.sort(key=lambda c: (-len(c["members"]), c["family"]))
    return out


# --- tagless / coverage ------------------------------------------------------------------------
def _tagless(items: list) -> list:
    """Items with real CRW content but NO tracking tag = a rough draft to ENRICH, not re-draft
    (PM-authored, at-creation template, or the Agency CLI once it ships)."""
    out = []
    for it in items:
        if _has_content(it) and not any(t in _ALL_TAGS for t in _tags(it)):
            out.append(_sid(it.get("id")))
    return sorted(out)


def _coverage(items: list, retiring: set) -> tuple[dict, list]:
    by_writer: dict[str, list] = {}
    orphaned = []
    for it in items:
        w = (it.get("comms_writer") or "").strip() or "(unassigned)"
        by_writer.setdefault(w, []).append(_sid(it.get("id")))
        if w.lower() in retiring or w == "(unassigned)":
            orphaned.append({"id": _sid(it.get("id")), "writer": w,
                             "reason": "retiring writer" if w.lower() in retiring else "no comms writer"})
    for w in by_writer:
        by_writer[w].sort()
    orphaned.sort(key=lambda o: o["id"])
    return by_writer, orphaned


_RETIRING_NAMES = {"taylor example": "taylor", "jordan example": "jordan"}


def _norm_retiring(retiring) -> set:
    """Accept aliases or display names; return a lowercased set covering both forms."""
    out = set()
    for r in retiring or []:
        r = str(r).strip().lower()
        if not r:
            continue
        out.add(r)
        for name, alias in _RETIRING_NAMES.items():
            if r in (name, alias):
                out.add(name)
                out.add(alias)
    return out


def build_landscape(items: list, release=None, retiring=None) -> dict:
    items = [it for it in (items or []) if it.get("id") is not None]
    retiring_set = _norm_retiring(retiring)
    combined, id_to_primary = _combined_blurbs(items)
    clusters = _clusters(items, id_to_primary)
    tagless = set(_tagless(items))
    coverage, orphaned = _coverage(items, retiring_set)
    orphan_ids = {o["id"] for o in orphaned}
    fam_of: dict[str, list] = {}
    for c in clusters:
        for m in c["members"]:
            fam_of.setdefault(m, []).append(c["family"])

    per_item = {}
    for it in items:
        iid = _sid(it.get("id"))
        primary = id_to_primary.get(iid)
        if primary and primary != iid:
            role = "secondary"
        elif primary and primary == iid:
            role = "primary"
        else:
            role = "standalone"
        note = []
        if role == "secondary":
            note.append(f"combined-blurb secondary — act on primary {primary}, don't draft separately")
        if iid in tagless:
            note.append("rough draft (content, no tag) — ENRICH, don't re-draft")
        if iid in fam_of and any(c["suggest_combine"] for c in clusters if c["family"] in fam_of[iid]):
            note.append("in an un-combined family cluster — consider a combined blurb")
        if iid in orphan_ids:
            note.append("orphaned (retiring/unassigned writer) — needs coverage")
        per_item[iid] = {
            "role": role,
            "combined_primary": primary,
            "clusters": fam_of.get(iid, []),
            "tagless": iid in tagless,
            "orphaned": iid in orphan_ids,
            "note": "; ".join(note) or "standalone item, no cross-item relationships",
        }

    return {
        "release": release,
        "item_count": len(items),
        "combined_blurbs": combined,
        "clusters": clusters,
        "tagless_content": sorted(tagless),
        "coverage": coverage,
        "orphaned": orphaned,
        "per_item": per_item,
    }


def _load(arg):
    if arg == "-":
        return json.load(sys.stdin)
    p = Path(arg)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else json.loads(arg)
    if isinstance(data, dict):
        return data.get("items", data.get("value", []))
    return data


def _retiring_from_roster(path: str) -> list:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [w.get("alias") for w in data.get("retiring_reassign", []) or [] if w.get("alias")]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Atlas Stage -1 release landscape resolver.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the release landscape (relationship graph) from the items")
    b.add_argument("--items", required=True, help="items: JSON string, file, or '-' for stdin")
    b.add_argument("--release", default=None, help="release label (e.g. 2608), stamped on the output")
    b.add_argument("--roster", default=None, help="roster.json — read retiring_reassign for orphan detection")
    b.add_argument("--retiring", default=None, help="comma-separated retiring writers (aliases or names)")
    b.add_argument("--json", action="store_true", help="emit JSON (default human-readable)")
    args = ap.parse_args(argv)

    items = _load(args.items)
    retiring = []
    if args.retiring:
        retiring = [r for r in args.retiring.split(",") if r.strip()]
    elif args.roster:
        retiring = _retiring_from_roster(args.roster)
    land = build_landscape(items, release=args.release, retiring=retiring)

    if args.json:
        sys.stdout.write(json.dumps(land, indent=2, ensure_ascii=False) + "\n")
    else:
        print(f"Landscape — release {land['release'] or '(n/a)'}: {land['item_count']} items")
        print(f"  combined blurbs: {len(land['combined_blurbs'])}  |  clusters: {len(land['clusters'])}"
              f"  |  tagless: {len(land['tagless_content'])}  |  orphaned: {len(land['orphaned'])}")
        for g in land["combined_blurbs"]:
            ext = f" (+external {g['external']})" if g["external"] else ""
            print(f"  COMBINED [{'/'.join(g['track'])}] primary {g['primary']} <- {g['present']}{ext}")
        for c in land["clusters"]:
            tag = "already combined" if c["already_combined"] else "SUGGEST COMBINE"
            print(f"  CLUSTER '{c['family']}' ({len(c['members'])}) [{tag}]: {c['members']}")
        if land["orphaned"]:
            print(f"  ORPHANED: {[(o['id'], o['writer']) for o in land['orphaned']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

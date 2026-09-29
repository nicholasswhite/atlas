#!/usr/bin/env python3
"""Atlas H8 — doc-footprint discovery merge (deterministic).

Phase 1 of repo-wide doc-footprint discovery (design: ../references/doc-footprint-design.md).
Atlas's Stage 4 used to find affected articles only from a thin lens-graph slice (one area, a
snapshot) or the articles a human named in the CRW. This script is the deterministic merge that
combines TWO discovery signals into one provenance-tagged, owner-resolved `affected_articles` set
for the dossier:

  - SCAN   — repo-update's live repo-wide keyword/semantic discovery (0-100 confidence score +
             DocFX-resolved ms.author). Breadth / recall, current state, every area.
  - GRAPH  — the lens/graphify graph (blast radius: direct/bridge; optional EXTRACTED/INFERRED/
             AMBIGUOUS provenance). Semantic depth, where a graph is built.

The AGENT runs those two sources (the repo-update skill in simulation + lens_affected.py); this
script is PURE: it normalizes paths, dedups by repo-root path, reconciles a confidence + blast +
disposition per article, and emits a dossier patch. No network, no skill calls, no edits.

Reconciliation (cross-validation is the accuracy win):
  - explicit EXTRACTED provenance + strong scan      -> EXTRACTED, edit  (cross-validated)
  - found by BOTH scan + graph + strong scan         -> EXTRACTED, edit  (agreement = verified)
  - graph EXTRACTED                                  -> EXTRACTED, edit
  - found by BOTH (scan below cutoff)                -> INFERRED, edit   (corroborated)
  - scan score >= edit cutoff (single source)        -> INFERRED, edit   (strong lexical)
  - graph INFERRED only / moderate scan              -> INFERRED, list-only  (review first)
  - graph AMBIGUOUS only / weak scan only            -> AMBIGUOUS, list-only

Three accuracy guards sit on top of the table:
  - **Skip-list:** structural / non-editable pages (`whats-new/`, `includes/`, `media/`, `*.yml`)
    are dropped entirely. The What's New aggregator scores high on every concept but is never an
    edit target. Disable with `skip_enabled=False` / `--no-skip`.
  - **Platform scoping:** given the feature's `platforms`, an article scoped to a DIFFERENT platform
    (e.g. `add-lob-windows.md` for an iOS feature) is demoted to `list-only`, never auto-edited.
    A `/apple/`-generic article matches any Apple-family feature; a platform-agnostic article is kept.
  - **God-node down-weight (Phase 2.1):** a hub / landing / overview page (high graph centrality,
    low feature-specificity) is demoted to `list-only` and flagged `god_node`, never auto-edited.
    These get cross-validated by sheer centrality (everything links to the area overview), so the
    guard stops the planner promoting them into an auto-edit batch — `RepoEditor` no-ops them anyway.
    Identified by `ms.topic` (overview/landing/hub) when the scan carries it, else the canonical hub
    filename (`overview.md` / `index.md`). Disable with `god_nodes=False` / `--keep-god-nodes`.

A graph candidate with NO explicit provenance (e.g. `lens_affected.py` output, which carries a
blast class but not a provenance) is treated as INFERRED: a graph edge is a semantic inference, not
a verified explicit mention. A strong lexical scan is never vetoed by a graph AMBIGUOUS. The
`disposition` is a SUGGESTION; Atlas's Stage 4 owner gate is authoritative for what actually gets
edited, and AMBIGUOUS / list-only never auto-edits.

CLI:
    doc_footprint.py merge --scan '<json>' [--graph '<json>'] [--path-prefix P]
                           [--platforms 'iOS/iPadOS'] [--no-skip] [--keep-god-nodes]
                           [--edit-cutoff 70] [--list-floor 40] [--json]
    (--scan / --graph accept a JSON string or a file path; --scan also accepts '-' for stdin)

Candidate shapes (lenient):
    scan:  [{"path","score"|"score_0_100","ms_author","ms_topic"?,"provenance"?,"concepts"?}]  (repo-root paths)
    graph: [{"path","provenance"?,"blast"?,"ms_author"?,"ms_topic"?,"concept"?|"concepts"?}]   (often area-relative)

Output (a dossier `affected_articles` patch, merges straight into the dossier store):
    {"affected_articles": [{"path","ms_author","sources":[...],"confidence","scan_score","blast",
                            "concepts":[...],"disposition","cross_validated"?,"off_platform"?,
                            "god_node"?,"note"?}]}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PROV = ("EXTRACTED", "INFERRED", "AMBIGUOUS")


def _norm_path(path, prefix=None) -> str:
    """Normalize to a repo-root forward-slash path; optionally prepend an area prefix (for
    area-relative graph paths). Strips leading './' and '/', collapses '/./'."""
    if not path:
        return ""
    p = str(path).strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/\./", "/", p).lstrip("/")
    if prefix:
        p = f"{str(prefix).rstrip('/')}/{p}"
    return p


def _prov(v):
    v = str(v or "").strip().upper()
    return v if v in _PROV else None


def _score(entry):
    s = entry.get("score", entry.get("score_0_100"))
    try:
        return int(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _concepts(entry):
    if entry.get("concepts"):
        return [c for c in entry["concepts"] if c]
    if entry.get("concept"):
        return [entry["concept"]]
    return []


def _ms_topic(entry):
    """The article's Learn `ms.topic`, if the discovery source carries it (the real repo-update
    engine resolves it from front matter / docfx). Used by the god-node guard."""
    return entry.get("ms_topic") or entry.get("ms.topic")


# --- Accuracy refinements (Phase 1.1) -------------------------------------------------------
# Structural / non-editable pages a feature change must never auto-edit, even when they score high
# (they aggregate every concept). The What's New article is the canonical trap (it scored 100 in the
# 2026-06-30 dry-run). The real repo-update engine has richer skip-lists + change budgets; this is
# the deterministic safety net so the merge never emits one regardless of the scan's hygiene.
_DEFAULT_SKIP_SEGMENTS = ("docs/whats-new/", "/includes/", "/media/", "/breadcrumb/")
_SKIP_SUFFIXES = (".yml", ".yaml", ".json")


def _skip(path: str) -> bool:
    p = path or ""
    return any(seg in p for seg in _DEFAULT_SKIP_SEGMENTS) or p.endswith(_SKIP_SUFFIXES)


def _norm_platform_set(values) -> set:
    """Normalize a feature's `platforms` (free text or list) to a canonical platform set."""
    if not values:
        return set()
    s = " ".join(values).lower() if isinstance(values, (list, tuple, set)) else str(values).lower()
    out = set()
    if "ios" in s or "ipados" in s:
        out.add("ios")
    if "macos" in s or "mac os" in s:
        out.add("macos")
    if "android" in s:
        out.add("android")
    if "windows" in s:
        out.add("windows")
    if "tvos" in s:
        out.add("tvos")
    if "visionos" in s:
        out.add("visionos")
    return out


_APPLE_FAMILY = {"ios", "macos", "tvos", "visionos"}


def _article_platforms(path: str) -> set:
    """Infer the platform(s) an article is scoped to, from filename/path tokens. Empty = agnostic."""
    p = (path or "").lower()
    found = set()
    if re.search(r"[/_-]ios(?:[/_.-]|$)", p) or re.search(r"[/_-]ipados", p):
        found.add("ios")
    if re.search(r"[/_-]macos", p) or "/macos/" in p:
        found.add("macos")
    if re.search(r"[/_-]android", p) or "/android/" in p:
        found.add("android")
    if re.search(r"[/_-]windows", p) or "/windows/" in p:
        found.add("windows")
    if re.search(r"[/_-]apple(?:[/_.-]|$)", p) or "/apple/" in p:
        found.add("apple")
    return found


def _off_platform(path: str, feature_plats: set):
    """(is_off, article_platforms). A `/apple/`-generic article matches any Apple-family feature.
    A platform-agnostic article (no token) is never off-platform. Returns is_off=True only when the
    article is clearly scoped to platform(s) the feature does not target."""
    if not feature_plats:
        return False, set()
    art = _article_platforms(path)
    if not art:
        return False, set()
    if "apple" in art and (feature_plats & _APPLE_FAMILY):
        return False, art
    specific = art - {"apple"}
    if specific & feature_plats:
        return False, art
    if specific and not (specific & feature_plats):
        return True, art
    return False, art


# --- God-node down-weight (Phase 2.1) -------------------------------------------------------
# A hub / landing / overview page has very high graph centrality (every article in an area links to
# its overview), so the cross-validation tier elevates it to EXTRACTED even though it is NOT
# feature-specific. The 2026-06-30 dry-run promoted deployment/index.md, app-management/overview.md,
# and protection/overview.md into auto-edit purely on centrality; RepoEditor no-ops them at
# execution, but this guard stops them being PROPOSED — a god-node is demoted to list-only (the
# planner files it as a `god-node` handoff for a human glance, never an empty PR). ms.topic is
# authoritative when the scan carries it; otherwise the canonical hub filename is the signal.
_GODNODE_TOPICS = {"overview", "landing", "hub"}
_GODNODE_FILENAMES = ("overview.md", "index.md")


def _god_node(path: str, ms_topic=None) -> bool:
    topic = str(ms_topic or "").strip().lower()
    if topic:
        return topic in _GODNODE_TOPICS   # ms.topic wins: an explicit how-to/conceptual is NOT a hub
    fname = (path or "").rsplit("/", 1)[-1].lower()
    return fname in _GODNODE_FILENAMES


def reconcile(scan, graph, edit_cutoff=70, list_floor=40, path_prefix=None,
              platforms=None, skip_enabled=True, god_nodes=True):
    """Merge scan + graph candidates into one affected_articles list, deduped by repo-root path.
    Applies the structural skip-list (unless skip_enabled=False), demotes off-platform articles to
    list-only when `platforms` is given, and demotes god-node hub/overview pages to list-only
    (unless god_nodes=False) so a hub is never auto-edited on centrality alone."""
    feature_plats = _norm_platform_set(platforms)
    by_path: dict[str, dict] = {}

    def _slot(path, prefix):
        p = _norm_path(path, prefix)
        if not p or (skip_enabled and _skip(p)):
            return None
        return by_path.setdefault(p, {
            "path": p, "ms_author": None, "scan_score": None, "blast": None, "ms_topic": None,
            "concepts": [], "provs": set(), "has_scan": False, "has_graph": False,
        })

    for s in scan or []:
        e = _slot(s.get("path"), None)   # scan paths are already repo-root
        if e is None:
            continue
        e["has_scan"] = True
        sc = _score(s)
        if sc is not None:
            e["scan_score"] = sc if e["scan_score"] is None else max(e["scan_score"], sc)
        if s.get("ms_author"):
            e["ms_author"] = s["ms_author"]      # repo-update's DocFX owner is authoritative
        if not e["ms_topic"]:
            e["ms_topic"] = _ms_topic(s)
        pv = _prov(s.get("provenance"))
        if pv:
            e["provs"].add(pv)
        for c in _concepts(s):
            if c not in e["concepts"]:
                e["concepts"].append(c)

    for g in graph or []:
        e = _slot(g.get("path"), path_prefix)
        if e is None:
            continue
        e["has_graph"] = True
        if g.get("blast"):
            e["blast"] = g["blast"]
        if g.get("ms_author") and not e["ms_author"]:
            e["ms_author"] = g["ms_author"]
        if not e["ms_topic"]:
            e["ms_topic"] = _ms_topic(g)
        # a graph edge without explicit provenance is a semantic inference -> INFERRED
        e["provs"].add(_prov(g.get("provenance")) or "INFERRED")
        for c in _concepts(g):
            if c not in e["concepts"]:
                e["concepts"].append(c)

    out = [_finalize(e, edit_cutoff, list_floor, feature_plats, god_nodes) for e in by_path.values()]
    rank = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}
    out.sort(key=lambda r: (r["disposition"] != "edit", not r.get("cross_validated"),
                            rank.get(r["confidence"], 9), -(r["scan_score"] or 0), r["path"]))
    return out


def _finalize(e, edit_cutoff, list_floor, feature_plats, god_nodes=True):
    provs = e["provs"]
    score = e["scan_score"]
    has_extracted = "EXTRACTED" in provs
    has_inferred = "INFERRED" in provs
    has_ambiguous = "AMBIGUOUS" in provs
    strong_scan = score is not None and score >= edit_cutoff
    mod_scan = score is not None and list_floor <= score < edit_cutoff
    agree = e["has_scan"] and e["has_graph"]   # found independently by BOTH discovery methods

    note = None
    cross = False
    if has_extracted and strong_scan:
        confidence = "EXTRACTED"
        cross = True
        note = "explicit mention + strong scan"
    elif agree and strong_scan:
        # two independent methods (lexical scan + semantic graph) agree at strength = verified.
        confidence = "EXTRACTED"
        cross = True
        note = "cross-validated (scan + graph agreement)"
    elif has_extracted:
        confidence = "EXTRACTED"
        if e["has_scan"] and not strong_scan:
            note = f"graph-explicit; scan score {score} below cutoff"
    elif agree:
        confidence = "INFERRED"
        cross = True
        note = "corroborated (scan + graph); scan below cutoff"
    elif strong_scan:
        confidence = "INFERRED"
        if has_ambiguous:
            note = "strong lexical match; graph uncertain"
    elif has_inferred:
        confidence = "INFERRED"
        note = "semantic relation (graph); review before editing"
    elif mod_scan:
        confidence = "INFERRED"
        note = f"moderate lexical match (score {score})"
    elif has_ambiguous:
        confidence = "AMBIGUOUS"
        note = "graph marked ambiguous"
    else:
        confidence = "AMBIGUOUS"
        note = f"weak signal (score {score})"

    disposition = "edit" if (confidence == "EXTRACTED" or cross
                             or (confidence == "INFERRED" and strong_scan)) else "list-only"

    # Platform scoping: never auto-edit an article scoped to a platform the feature doesn't target.
    off, art_plats = _off_platform(e["path"], feature_plats)
    if off:
        disposition = "list-only"
        tag = f"off-platform (article {sorted(art_plats)} vs feature {sorted(feature_plats)})"
        note = f"{note}; {tag}" if note else tag

    # God-node down-weight: a hub/overview/landing page is never auto-edited (high centrality, not
    # feature-specific). Demote to list-only; the planner files it as a `god-node` handoff.
    god = god_nodes and _god_node(e["path"], e.get("ms_topic"))
    if god:
        disposition = "list-only"
        tag = "god-node hub/overview (high centrality, low feature-specificity; review, don't auto-edit)"
        note = f"{note}; {tag}" if note else tag

    sources = []
    if e["has_scan"]:
        sources.append("H8:repo-scan")
    if e["has_graph"]:
        sources.append("H8:lens-graph")

    entry = {
        "path": e["path"],
        "ms_author": e["ms_author"] or "(unknown)",
        "sources": sources,
        "confidence": confidence,
        "scan_score": score,
        "blast": e["blast"] or "direct",
        "concepts": e["concepts"],
        "disposition": disposition,
    }
    if cross:
        entry["cross_validated"] = True
    if off:
        entry["off_platform"] = True
    if god:
        entry["god_node"] = True
    if note:
        entry["note"] = note
    return entry


def _load(arg, allow_stdin=False):
    if arg is None:
        return []
    if arg == "-" and allow_stdin:
        return json.load(sys.stdin)
    p = Path(arg)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(arg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Atlas H8 doc-footprint discovery merge.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="merge scan + graph discovery into an affected_articles patch")
    m.add_argument("--scan", required=True, help="repo-update scan candidates: JSON string, file, or '-' for stdin")
    m.add_argument("--graph", default=None, help="lens/graph candidates: JSON string or file (optional)")
    m.add_argument("--path-prefix", default=None, help="repo-root prefix for area-relative graph paths")
    m.add_argument("--edit-cutoff", type=int, default=70, help="scan score >= this is edit-eligible (default 70)")
    m.add_argument("--list-floor", type=int, default=40, help="scan score >= this is list-only-eligible (default 40)")
    m.add_argument("--platforms", default=None, help="feature platforms (e.g. 'iOS/iPadOS'); off-platform articles are demoted to list-only")
    m.add_argument("--no-skip", action="store_true", help="disable the structural skip-list (whats-new/, includes/, media/, *.yml)")
    m.add_argument("--keep-god-nodes", action="store_true", help="disable the god-node down-weight (hub/overview/landing pages stay edit-eligible)")
    m.add_argument("--json", action="store_true", help="emit JSON (default human-readable)")
    args = ap.parse_args(argv)

    scan = _load(args.scan, allow_stdin=True)
    graph = _load(args.graph)
    articles = reconcile(scan, graph, args.edit_cutoff, args.list_floor, args.path_prefix,
                         platforms=args.platforms, skip_enabled=not args.no_skip,
                         god_nodes=not args.keep_god_nodes)
    patch = {"affected_articles": articles}

    if args.json:
        sys.stdout.write(json.dumps(patch, indent=2, ensure_ascii=False) + "\n")
    else:
        edits = sum(1 for a in articles if a["disposition"] == "edit")
        print(f"Affected articles: {len(articles)}  ({edits} edit, {len(articles) - edits} list-only)")
        for a in articles:
            xv = " *xval*" if a.get("cross_validated") else ""
            op = " *off-plat*" if a.get("off_platform") else ""
            gn = " *god-node*" if a.get("god_node") else ""
            print(f"  [{a['disposition']:<9}] {a['confidence']:<9} {a['blast']:<6} "
                  f"score={a['scan_score']} {a['path']}  (owner: {a['ms_author']}){xv}{op}{gn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

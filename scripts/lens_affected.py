#!/usr/bin/env python3
"""Convert an adapter-supplied document/concept graph into affected articles.

Given feature terms, match concept nodes and follow links to document nodes,
returning paths, ownership hints and direct/bridge relationship classifications.
The supported graph shape is exercised in tests/test_lens_affected.py. An external
host may supply a compatible lens graph; its builder and runtime are not bundled.
Missing input stays unavailable. Graph relationships alone are inferred evidence.

Pure dictionary traversal plus local JSON input. No network or document edits.
CLI: python scripts/lens_affected.py --help
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_graph(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in raw.get("nodes", [])}
    adj: dict[str, list[dict]] = {nid: [] for nid in nodes}
    for e in raw.get("edges", []):
        s, t = e.get("source"), e.get("target")
        if s in nodes and t in nodes:
            adj[s].append({"to": t, "relation": e.get("relation"), "confidence": e.get("confidence")})
            adj[t].append({"to": s, "relation": e.get("relation"), "confidence": e.get("confidence")})
    return {"nodes": nodes, "adj": adj, "bridges": set(raw.get("bridges", []) or [])}


def _match_concepts(graph: dict, terms: list[str]) -> tuple[list[str], list[str]]:
    """Return (matched node ids, unmatched terms). Exact label/id match first, then substring."""
    nodes = graph["nodes"]
    matched: list[str] = []
    unmatched: list[str] = []
    for term in terms:
        t = term.strip().casefold()
        if not t:
            continue
        hit = None
        for nid, a in nodes.items():
            if a.get("label", "").casefold() == t or nid.casefold() == t:
                hit = nid
                break
        if not hit:
            for nid, a in nodes.items():
                # only match concept nodes by substring, not document nodes
                if a.get("file_type") != "document" and t in a.get("label", "").casefold():
                    hit = nid
                    break
        if hit:
            if hit not in matched:
                matched.append(hit)
        else:
            unmatched.append(term)
    return matched, unmatched


def affected_articles(graph: dict, terms: list[str], author_filter: str | None = None,
                      bridge_terms: list[str] | None = None, path_prefix: str | None = None) -> dict:
    nodes = graph["nodes"]
    adj = graph["adj"]
    bridges = set(graph["bridges"])

    # Bridges aren't persisted in graph.json — lens computes them on demand (betweenness) via
    # `lens/scripts/query.py bridges`. The caller can pass that list in via bridge_terms
    # (concept labels or ids); we resolve them to node ids and fold them into the bridge set.
    bridge_supplied = bool(bridge_terms)
    if bridge_terms:
        wanted = {b.strip().casefold() for b in bridge_terms if b.strip()}
        for nid, a in nodes.items():
            if nid.casefold() in wanted or a.get("label", "").casefold() in wanted:
                bridges.add(nid)

    matched, unmatched = _match_concepts(graph, terms)

    # Walk from each matched concept to neighboring document nodes.
    by_path: dict[str, dict] = {}
    for cid in matched:
        concept_label = nodes[cid].get("label", cid)
        is_bridge = cid in bridges or nodes[cid].get("is_bridge") is True
        for nb in adj.get(cid, []):
            doc = nodes.get(nb["to"], {})
            if doc.get("file_type") != "document":
                continue
            path = doc.get("source_file") or doc.get("label") or nb["to"]
            # Lens graphs often store paths relative to the docset AREA root (e.g.
            # "configuration/x.md"); the owner gate + DOC edit need repo-root paths, so prepend
            # the caller-supplied area prefix (e.g. "docs/app-management") when given.
            if path_prefix:
                path = f"{path_prefix.rstrip('/')}/{str(path).lstrip('/')}"
            owner = doc.get("ms_author") or "(unassigned)"
            if author_filter and owner.casefold() != author_filter.casefold():
                continue
            blast = "bridge" if is_bridge else "direct"
            existing = by_path.get(path)
            # Keep the strongest blast (bridge > direct) and remember which concept matched.
            if existing is None:
                by_path[path] = {
                    "path": path,
                    "ms_author": owner,
                    "blast": blast,
                    "source": "H8:lens",
                    "concept": concept_label,
                }
            else:
                if blast == "bridge":
                    existing["blast"] = "bridge"
                    existing["concept"] = concept_label

    articles = sorted(by_path.values(), key=lambda r: (r["blast"] != "bridge", r["path"]))
    return {
        "affected_articles": articles,
        "matched_concepts": [nodes[c].get("label", c) for c in matched],
        "unmatched_terms": unmatched,
        "bridge_data": "supplied" if bridge_supplied else "none (all blast=direct; pass --bridges "
                       "from `lens query.py bridges` to classify bridge hotspots)",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas H8 lens docs-graph bridge.")
    p.add_argument("--graph", required=True, help="Path to a built lens out/graph.json.")
    p.add_argument("--terms", default=None, help="Comma-separated feature concept terms.")
    p.add_argument("--terms-file", default=None, help="File with one term per line.")
    p.add_argument("--author-filter", default=None, help="Only return articles owned by this ms.author.")
    p.add_argument("--bridges", default=None,
                   help="Comma-separated bridge concept labels/ids (from `lens query.py bridges`) "
                        "to classify bridge-blast articles. Optional.")
    p.add_argument("--path-prefix", default=None,
                   help="Repo-root prefix to prepend to each emitted article path (e.g. "
                        "'docs/app-management'). Lens graphs often store paths relative to the "
                        "docset area root; the owner gate and DOC edit need repo-root paths.")
    p.add_argument("--json", action="store_true", help="Emit JSON (default human-readable).")
    args = p.parse_args(argv)

    graph_path = Path(args.graph)
    if not graph_path.exists():
        print(
            f"source_unavailable: no lens graph at {graph_path}. "
            "Supply a graph from an explicitly configured adapter, "
            "then re-run. H8 stays unavailable until a graph exists.",
            file=sys.stderr,
        )
        return 2

    if args.terms:
        terms = [t.strip() for t in args.terms.split(",")]
    elif args.terms_file:
        terms = [ln.strip() for ln in Path(args.terms_file).read_text(encoding="utf-8").splitlines()]
    else:
        print("error: provide --terms or --terms-file", file=sys.stderr)
        return 3

    graph = _load_graph(graph_path)
    bridge_terms = [b.strip() for b in args.bridges.split(",")] if args.bridges else None
    result = affected_articles(graph, terms, args.author_filter, bridge_terms, args.path_prefix)

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    else:
        arts = result["affected_articles"]
        print(f"Matched concepts: {', '.join(result['matched_concepts']) or '(none)'}")
        if result["unmatched_terms"]:
            print(f"Unmatched terms:  {', '.join(result['unmatched_terms'])}")
        print(f"Bridge data: {result['bridge_data']}")
        print(f"Affected articles ({len(arts)}):")
        for a in arts:
            print(f"  [{a['blast']}] {a['path']}  (owner: {a['ms_author']}, via: {a['concept']})")
        if not arts:
            print("  (none — no document nodes touch the matched concepts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

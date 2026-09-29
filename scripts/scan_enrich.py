#!/usr/bin/env python3
"""Enrich adapter-supplied document candidates with local title and topic evidence.

Input rows contain path, score and ownership hints. This module reads local
article metadata/H1 text and marks title matches; it does not generate a scan.
A caller supplies the candidate rows from its own authorized discovery adapter.
The resulting rows feed doc_footprint.reconcile. Topic-only mode adds metadata
without upgrading a graph inference to an explicit title match.

CLI: python scripts/scan_enrich.py --help
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_FRONT_MATTER_RE = re.compile(r"^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_MS_TOPIC_RE = re.compile(r"^\s*ms\.topic\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_H1_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)


def _stem(path: str) -> str:
    """The filename without the .md extension (the article 'slug')."""
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.lower().endswith(".md") else name


def _norm_terms(terms, min_len=3):
    """Lowercase, dedupe, and drop too-short terms (so 'os'/'id' can't title-match everything)."""
    if isinstance(terms, str):
        terms = terms.split(",")
    out = []
    for t in terms or []:
        tt = str(t).strip().lower()
        if len(tt) >= min_len and tt not in out:
            out.append(tt)
    return out


def title_match(terms, stem, h1=None, min_len=3):
    """Return the first concept term that matches the article TITLE, else None. A multi-word term
    matches as a substring of the normalized stem or the H1; a single-word term must match a whole
    stem token or an H1 word boundary, so 'ios' does NOT match 'kiosk' and 'app' does not match
    'mapping'. Title-match = the article is *about* the concept, not just mentions it in passing."""
    stem_norm = re.sub(r"[-_/.]+", " ", (stem or "").lower()).strip()
    stem_tokens = set(stem_norm.split())
    h1_norm = (h1 or "").lower()
    for t in _norm_terms(terms, min_len):
        if " " in t:
            if t in stem_norm or (h1_norm and t in h1_norm):
                return t
        elif t in stem_tokens or (h1_norm and re.search(rf"\b{re.escape(t)}\b", h1_norm)):
            return t
    return None


def enrich(candidates, terms, meta_lookup=None, min_len=3, title_match_enabled=True):
    """Pure enrichment over scan candidates. `meta_lookup` (optional) maps a repo-root path to
    (ms_topic, h1); when absent the title match uses the filename stem only and ms.topic is left
    as-is. Sets `provenance=EXTRACTED` + `title_match` on a title match (never downgrades an
    existing EXTRACTED), unions the matched term into `concepts`, and fills `ms_topic` from the
    lookup without clobbering one already present.

    `title_match_enabled=False` fills ONLY ms.topic (no EXTRACTED-on-title) — used to enrich GRAPH
    candidates with ms.topic for the god-node guard without changing their provenance (a graph edge
    stays a semantic inference, not a verified mention)."""
    meta_lookup = meta_lookup or {}
    out = []
    for c in candidates or []:
        e = dict(c)
        path = e.get("path")
        ms_topic, h1 = meta_lookup.get(path, (None, None))
        if ms_topic and not e.get("ms_topic"):
            e["ms_topic"] = ms_topic
        matched = title_match(terms, _stem(path), h1, min_len) if title_match_enabled else None
        if matched:
            if str(e.get("provenance") or "").upper() != "EXTRACTED":
                e["provenance"] = "EXTRACTED"
                e["title_match"] = matched
            concepts = list(e.get("concepts") or [])
            if not any(matched == str(x).lower() for x in concepts):
                concepts.append(matched)
            e["concepts"] = concepts
        out.append(e)
    return out


def read_meta(abspath):
    """(ms_topic, h1) for a product-docs article. ms_topic is lowercased; h1 is raw. (None, None) on any
    read error so a missing/locked file degrades to the filename-only path rather than raising."""
    try:
        text = Path(abspath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    ms_topic, body = None, text
    m = _FRONT_MATTER_RE.match(text)
    if m:
        mt = _MS_TOPIC_RE.search(m.group(1))
        if mt:
            ms_topic = mt.group(1).strip().strip("'\"").strip().lower() or None
        body = text[m.end():]
    h1m = _H1_RE.search(body)
    return ms_topic, (h1m.group(1).strip() if h1m else None)


def build_meta_lookup(candidates, repo_root):
    """Read (ms_topic, h1) once per unique candidate path under `repo_root`."""
    root = Path(repo_root)
    lookup = {}
    for c in candidates or []:
        p = c.get("path")
        if p and p not in lookup:
            lookup[p] = read_meta(root / p)
    return lookup


def _load(arg):
    if arg == "-":
        return json.load(sys.stdin)
    p = Path(arg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else json.loads(arg)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Atlas H8 repo-update scan enrichment (EXTRACTED-on-title + ms.topic).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("enrich", help="add EXTRACTED-on-title + ms.topic to repo-update scan candidates")
    m.add_argument("--scan", required=True, help="candidates: JSON string, file, or '-' for stdin (scan OR graph rows)")
    m.add_argument("--terms", default="", help="comma-separated feature concept terms (from the dossier); ignored with --topic-only")
    m.add_argument("--repo-root", default=None, help="product-docs root; when given, read ms.topic + H1 per article")
    m.add_argument("--min-term-len", type=int, default=3, help="ignore terms shorter than this (default 3)")
    m.add_argument("--topic-only", action="store_true", help="fill ms.topic only (skip EXTRACTED-on-title); use to enrich GRAPH candidates for the god-node guard")
    m.add_argument("--json", action="store_true", help="emit JSON (default human-readable)")
    args = ap.parse_args(argv)

    candidates = _load(args.scan)
    meta = build_meta_lookup(candidates, args.repo_root) if args.repo_root else {}
    enriched = enrich(candidates, args.terms, meta, args.min_term_len, title_match_enabled=not args.topic_only)

    if args.json:
        sys.stdout.write(json.dumps(enriched, indent=2, ensure_ascii=False) + "\n")
    else:
        ext = sum(1 for e in enriched if str(e.get("provenance") or "").upper() == "EXTRACTED")
        topics = sum(1 for e in enriched if e.get("ms_topic"))
        print(f"Enriched {len(enriched)} candidates: {ext} EXTRACTED-on-title, {topics} with ms.topic")
        for e in enriched:
            tm = f"  title:{e['title_match']}" if e.get("title_match") else ""
            mt = f"  ms.topic={e['ms_topic']}" if e.get("ms_topic") else ""
            print(f"  [{str(e.get('provenance') or '-'):<9}] score={e.get('score')} {e.get('path')}{mt}{tm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

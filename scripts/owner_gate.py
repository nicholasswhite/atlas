#!/usr/bin/env python3
"""Atlas P3 — article owner gate.

Before Atlas edits any product-docs article in the DOC stage, it must confirm the article is
owned by a writer on the autopilot roster. Only articles whose `ms.author` is an enabled
roster writer are editable; every other target is routed to the handoff queue tagged with the
real owner. This is the hard ownership rule from the safety guardrails — at Tier 4 autonomy it
is what keeps the autopilot from editing articles outside the crew's ownership.

The roster lives in `config/roster.json` (pass `--roster`): the go-forward crew (alex,
morgan, casey) with their fictional docfx aliases. `--authors a,b` or a single `--author`
also work.

product-docs sets `ms.author` through `docs/docfx.json` fileMetadata path-globs, not article
front matter, so pass `--docfx <docset>/docfx.json` to resolve the real owner by path
(longest-prefix wins). Without it, glob-owned articles return `unknown`.

This helper reads a single article's YAML front matter and returns the ownership decision.

Important caveat (handled here): in product-docs, `ms.author` is usually NOT in the article's
front matter — it's set by a path-glob in `docs/docfx.json` `fileMetadata`. So "no ms.author
in the file" does NOT mean unowned. When `--docfx` is supplied, this helper resolves the owner
from those globs (longest-prefix wins); without it, the helper returns `unknown` rather than a
false own/handoff so the orchestrator surfaces it instead of guessing.

Pure + deterministic. Reads a file, parses front matter, returns a decision. No edits.

CLI:
    owner_gate.py --file <product-docs>/docs/app-management/foo.md --roster config/roster.json --docfx <product-docs>/docs/docfx.json
    owner_gate.py --file <path> --authors alex,morgan,casey --docfx <product-docs>/docs/docfx.json
    owner_gate.py --file <path> [--author alex] --json
Exit codes: 0 own, 1 handoff (other owner), 2 unknown (no owner resolved), 3 error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_FRONT_MATTER_RE = re.compile(r"^\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_MS_AUTHOR_RE = re.compile(r"^\s*ms\.author\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_AUTHOR_RE = re.compile(r"^\s*author\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def parse_front_matter(text: str) -> dict:
    """Return {'ms_author': str|None, 'author': str|None, 'has_front_matter': bool}."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {"ms_author": None, "author": None, "has_front_matter": False}
    block = m.group(1)
    ms = _MS_AUTHOR_RE.search(block)
    au = _AUTHOR_RE.search(block)

    def clean(v):
        if v is None:
            return None
        return v.strip().strip("'\"").strip() or None

    return {
        "ms_author": clean(ms.group(1)) if ms else None,
        "author": clean(au.group(1)) if au else None,
        "has_front_matter": True,
    }


def _normalize_roster(roster) -> list[str]:
    """Accept a list of aliases or a single alias string; return lowercased, de-duped aliases."""
    if roster is None:
        roster = []
    if isinstance(roster, str):
        roster = [roster]
    out: list[str] = []
    for a in roster:
        if a and a.strip():
            v = a.strip().lower()
            if v not in out:
                out.append(v)
    return out


def load_roster(path: str) -> list[str]:
    """Load enabled, non-placeholder writer aliases from a roster JSON file.

    Format: {"writers": [{"alias": "alex", "enabled": true}, ...]}. A writer is included only
    when enabled is true and the alias is real (placeholders beginning with "todo" are skipped),
    so a half-filled roster never accidentally claims ownership.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    aliases: list[str] = []
    for w in data.get("writers", []):
        if not w.get("enabled"):
            continue
        alias = (w.get("alias") or "").strip()
        if not alias or alias.lower().startswith("todo"):
            continue
        aliases.append(alias)
    return aliases


def roster_test_mode(path: str) -> bool:
    """True if the roster's `test_mode_all_authors` flag is set.

    Testing posture only: any article whose owner can be resolved is treated as editable,
    regardless of roster membership. The flag lives in the roster file so normal Atlas runs
    (which already load the roster) pick it up; turn it off to snap back to the enabled crew.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("test_mode_all_authors", False))


def _load_docfx_authors(docfx_path: str) -> dict:
    """Return {folder_prefix: alias} from docfx.json fileMetadata['ms.author'].

    The glob suffix `/**/*` is stripped to a folder prefix. Handles fileMetadata at the top
    level or nested under 'build'.
    """
    data = json.loads(Path(docfx_path).read_text(encoding="utf-8"))
    file_meta = (data.get("build", {}) or {}).get("fileMetadata") or data.get("fileMetadata") or {}
    authors = file_meta.get("ms.author", {}) or {}
    prefixes: dict = {}
    for glob, alias in authors.items():
        prefix = glob.split("/**")[0].rstrip("/")
        if prefix:
            prefixes[prefix] = alias
    return prefixes


def resolve_via_docfx(article_path: str, docfx_path: str | None) -> str | None:
    """Resolve an article's ms.author from docfx.json path-globs (longest prefix wins).

    Returns the owner alias, or None if docfx is missing/unreadable or no glob matches.
    """
    if not docfx_path:
        return None
    try:
        prefixes = _load_docfx_authors(docfx_path)
    except (OSError, ValueError):
        return None
    if not prefixes:
        return None
    root = Path(docfx_path).resolve().parent
    try:
        rel = Path(article_path).resolve().relative_to(root).as_posix().lower()
    except ValueError:
        return None
    best, best_depth = None, -1
    for prefix, alias in prefixes.items():
        pl = prefix.lower()
        if rel == pl or rel.startswith(pl + "/"):
            depth = pl.count("/")
            if depth > best_depth:
                best, best_depth = alias, depth
    return best


def decide(path: str, roster=None, docfx: str | None = None, all_authors: bool = False) -> dict:
    """Decide ownership of an article against a roster of writer aliases.

    roster: a list of ms.author aliases the autopilot may edit (or a single alias string).
            Defaults to no authorized writers when not supplied.
    docfx:  optional path to the docset's docfx.json. product-docs sets ms.author via
            fileMetadata path-globs rather than front matter, so when the article has no
            front-matter ms.author this resolves the owner from the docfx globs
            (longest-prefix wins). Without it, a glob-owned article returns 'unknown'.
    all_authors: TESTING posture. When True, any article whose owner resolves is editable,
            regardless of roster membership. Genuinely unresolved ownership still returns
            'unknown' (we never edit a file whose owner we can't even identify).
    """
    aliases = _normalize_roster(roster)
    p = Path(path)
    if not p.exists():
        return {"path": path, "decision": "error", "reason": f"file not found: {path}",
                "ms_author": None, "editable": False, "roster": aliases, "source": None}
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    fm = parse_front_matter(text)
    ms_author = fm["ms_author"]
    source = "frontmatter" if ms_author else None

    if ms_author is None:
        resolved = resolve_via_docfx(path, docfx)
        if resolved is not None:
            ms_author, source = resolved, "docfx"

    if ms_author is None:
        if not fm["has_front_matter"]:
            # No YAML front matter at all AND no docfx glob matched — the file is malformed or
            # not a real article. Distinct from "has front matter, owner set by docfx" below.
            # Either way it is not editable; surface it for a human.
            reason = "no YAML front matter found"
            if docfx:
                reason += " and no docfx fileMetadata glob matched the path"
            else:
                reason += " (and no --docfx provided to resolve a path-glob owner)"
            return {"path": path, "decision": "unknown", "reason": reason,
                    "ms_author": None, "editable": False, "roster": aliases, "source": None}
        if docfx:
            extra = " and no docfx fileMetadata glob matched the article path"
        else:
            extra = " (pass --docfx to resolve docfx-set owners)"
        return {
            "path": path,
            "decision": "unknown",
            "reason": "ms.author not in front matter — likely set by docfx.json path-glob; "
                      "verify the fileMetadata owner before editing. Do not assume ownership." + extra,
            "ms_author": None,
            "editable": False,
            "roster": aliases,
            "source": None,
        }

    if all_authors:
        return {"path": path, "decision": "own",
                "reason": f"TEST MODE (all authors): ms.author {ms_author} (via {source}) is "
                          f"editable — roster membership bypassed for testing.",
                "ms_author": ms_author, "editable": True, "roster": aliases, "source": source,
                "test_mode": True}

    if ms_author.lower() in aliases:
        return {"path": path, "decision": "own",
                "reason": f"ms.author {ms_author} is on the roster (via {source})",
                "ms_author": ms_author, "editable": True, "roster": aliases, "source": source}

    return {
        "path": path,
        "decision": "handoff",
        "reason": f"ms.author is {ms_author} (via {source}), not on the roster "
                  f"({', '.join(aliases) or 'empty'}) — route to handoff queue tagged "
                  f"with owner {ms_author}.",
        "ms_author": ms_author,
        "editable": False,
        "roster": aliases,
        "source": source,
    }


_EXIT = {"own": 0, "handoff": 1, "unknown": 2, "error": 3}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas article owner gate (roster-scoped).")
    p.add_argument("--file", required=True, help="Path to the product-docs article (.md).")
    p.add_argument("--roster", help="Path to a roster JSON file; auto-edit articles owned by "
                                    "any enabled writer in it.")
    p.add_argument("--authors", help="Comma-separated owner aliases (overrides --roster/--author).")
    p.add_argument("--author", default=None,
                   help="Single target owner alias, used only if --roster/--authors are absent "
                        "(no default writer).")
    p.add_argument("--docfx", help="Path to the docset docfx.json; resolves docfx-set ms.author "
                                   "owners when the article front matter has none (product-docs).")
    p.add_argument("--all-authors", action="store_true",
                   help="TEST MODE: treat any article with a resolvable owner as editable, "
                        "bypassing roster membership. Also enabled by test_mode_all_authors in "
                        "the roster file.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    args = p.parse_args(argv)

    all_authors = args.all_authors
    if args.authors:
        roster = args.authors.split(",")
    elif args.roster:
        try:
            roster = load_roster(args.roster)
            if roster_test_mode(args.roster):
                all_authors = True
        except FileNotFoundError:
            sys.stderr.write(f"roster file not found: {args.roster}\n")
            return 3
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            sys.stderr.write(f"malformed roster file {args.roster}: {e}\n")
            return 3
    else:
        roster = [args.author]

    res = decide(args.file, roster, docfx=args.docfx, all_authors=all_authors)
    if args.json:
        sys.stdout.write(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    else:
        print(f"{res['decision'].upper()}: {res['path']}")
        print(f"  ms.author: {res['ms_author']}")
        print(f"  {res['reason']}")
        print(f"  editable: {res['editable']}")
    return _EXIT.get(res["decision"], 3)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Unit tests for dossier_store source-block merge + anchor-scalar refresh — run:
    python tests/test_dossier_store.py

Covers the two Run-B papercut fixes:
  - sources.reached / unavailable are keyed by BASE H-tag, so `H7` and `H7:telemetry` collapse
    to one coverage entry and reached/unavailable never disagree about the same base source.
  - a re-run merge whose patch carries refreshed anchor scalars updates them (PM reassignment,
    timeline slip) instead of keeping the stale value; a patch WITHOUT them never clobbers.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="atlas_ds_test_")
os.environ["ATLAS_STATE_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import dossier_store as ds  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


print("dossier_store._base_tag — strip the ':sub-source' suffix:")
check("base of H7:telemetry is H7", ds._base_tag("H7:telemetry") == "H7")
check("base of H1 is H1", ds._base_tag("H1") == "H1")
check("base of H1:ado-graph is H1", ds._base_tag("H1:ado-graph") == "H1")
check("base trims whitespace", ds._base_tag(" H8 : x ") == "H8")

print("\n_merge_sources — reached deduped to base tag (papercut 2):")
m = ds._merge_sources(
    {"reached": ["H1:ado-graph", "H1:ado-crw", "H8:doc-verify"], "unavailable": []},
    {"reached": ["H1", "H2", "H8"], "unavailable": []},
)
check("H1 collapses across sub-tags + base (once)", m["reached"].count("H1") == 1)
check("H8 collapses to once", m["reached"].count("H8") == 1)
check("reached is base tags only", set(m["reached"]) == {"H1", "H8", "H2"})

print("\n_merge_sources — unavailable keyed by base; collapse + newer reason wins:")
m2 = ds._merge_sources(
    {"reached": [], "unavailable": [{"tag": "H7:telemetry", "reason": "ADX access not configured"}]},
    {"reached": [], "unavailable": [{"tag": "H7", "reason": "feature_id_unresolved"}]},
)
unavail_tags = [u["tag"] for u in m2["unavailable"]]
check("H7 listed once (not H7 + H7:telemetry)",
      unavail_tags.count("H7") == 1 and "H7:telemetry" not in unavail_tags)
check("newer reason wins on collapse", m2["unavailable"][0]["reason"] == "feature_id_unresolved")

print("\n_merge_sources — a now-reached base source clears the stale unavailable:")
m3 = ds._merge_sources(
    {"reached": [], "unavailable": [{"tag": "H7:telemetry", "reason": "x"}]},
    {"reached": ["H7"], "unavailable": []},
)
check("reached H7 removes H7 from unavailable",
      all(u["tag"] != "H7" for u in m3["unavailable"]) and "H7" in m3["reached"])

print("\n_merge_sources — lenient plain-string entries still accepted:")
m4 = ds._merge_sources(
    {"reached": [], "unavailable": []},
    {"reached": [], "unavailable": ["H5: no spec linked", "H4"]},
)
tags4 = sorted(u["tag"] for u in m4["unavailable"])
check("string 'H5: reason' -> base tag H5", "H5" in tags4)
check("bare string 'H4' -> tag H4", "H4" in tags4)

print("\nmerge — re-run refreshes anchor scalars when the patch carries them (papercut 1):")
d = ds._skeleton("90001031", {"title": "T", "assigned_to": {"name": "Quinn Example"}, "comms_timeline": "2610"})
ds.merge(d, {"assigned_to": {"name": "Riley Example"}, "comms_timeline": "2611"})
check("assigned_to refreshed", (d.get("assigned_to") or {}).get("name") == "Riley Example")
check("comms_timeline refreshed", d.get("comms_timeline") == "2611")
ds.merge(d, {"facts": [{"field": "x", "value": "y", "sources": ["H1"], "confidence": "EXTRACTED"}]})
check("a patch WITHOUT scalars keeps the last value (no clobber to null)",
      (d.get("assigned_to") or {}).get("name") == "Riley Example")

print("\nrender — affected_articles back-compat (legacy `source` + new `sources`):")
d2 = ds._skeleton("90001032", {"title": "T2"})
ds.merge(d2, {"affected_articles": [
    {"path": "docs/legacy.md", "ms_author": "alex", "source": "H8:lens"},
    {"path": "docs/new.md", "ms_author": "alex", "sources": ["H8:repo-scan", "H8:lens-graph"],
     "confidence": "EXTRACTED", "disposition": "edit", "blast": "direct"},
]})
md = ds.render_markdown(d2)
check("legacy `source` string still renders", "docs/legacy.md" in md and "H8:lens" in md)
check("new `sources` list renders", "docs/new.md" in md and "H8:repo-scan" in md)
check("confidence + disposition columns render", "EXTRACTED" in md and "edit" in md)

print("\nFinding 1 — render/validate tolerant of patch shape variants (synthetic regression):")
# facts with a singular `source` string (the natural agent shape) render, not "—"
d3 = ds._skeleton("90001033", {"title": "T3"})
ds.merge(d3, {"facts": [{"field": "what", "value": "does X", "source": "H1:crw", "confidence": "EXTRACTED"}]})
check("fact `source` (string) renders in the Sources column", "H1:crw" in ds.render_markdown(d3))
# scope claims without a source don't warn (scope bullets are syntheses over sourced facts)
d4 = ds._skeleton("90001034", {"title": "T4"})
ds.merge(d4, {"scope": {"shipping": [{"claim": "ship A"}], "out_of_scope": [{"claim": "not B"}]}})
check("scope claim without a source does NOT warn", not any("scope" in e for e in ds.validate(d4)))
check("scope claim renders", "ship A" in ds.render_markdown(d4))
# a fact still REQUIRES a source (the invariant is kept where it matters)
d5 = ds._skeleton("90001035", {"title": "T5"})
ds.merge(d5, {"facts": [{"field": "f", "value": "v"}]})
check("a fact WITHOUT a source still warns (invariant kept)", any("facts" in e for e in ds.validate(d5)))
# open_questions with the {topic, ask, needs} shape render the text
d6 = ds._skeleton("90001036", {"title": "T6"})
ds.merge(d6, {"open_questions": [{"topic": "feature_id", "ask": "What is the owning service?", "needs": "PM"}]})
check("open_question `ask` renders", "What is the owning service?" in ds.render_markdown(d6))
# sources.reached supplied as {tag, note} objects merge to base tags and render cleanly (no raw dict)
d7 = ds._skeleton("90001037", {"title": "T7"})
ds.merge(d7, {"sources": {"reached": [{"tag": "H1", "note": "ado graph"}, {"tag": "H8", "note": "footprint"}], "unavailable": []}})
md7 = ds.render_markdown(d7)
check("reached {tag,note} objects merge to base tags", set(d7["sources"]["reached"]) == {"H1", "H8"})
check("reached renders cleanly (no raw dict leak)", "Reached (2)" in md7 and "{'tag'" not in md7)
check("_fmt_sources(bare string)", ds._fmt_sources("H1:crw") == "H1:crw")
check("_fmt_sources(list of dicts)", ds._fmt_sources([{"tag": "H1", "note": "x"}]) == "H1: x")

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

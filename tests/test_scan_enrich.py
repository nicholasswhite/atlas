#!/usr/bin/env python3
"""Unit tests for scan_enrich — the repo-update -> doc_footprint adapter (thread b) — run:
    python tests/test_scan_enrich.py

Covers the two deterministic enrichments the repo-update tables don't carry: EXTRACTED-on-title
(so genuine integration points outrank tangential same-platform body-mentions) and ms.topic read
from front matter (so the Phase 2.1 god-node guard is authoritative). Pure matching is tested
directly; the file-reading layer is tested against temp files.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import scan_enrich as se  # noqa: E402

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


print("title_match — multi-word vs single-word vs boundary:")
TERMS = ["Example Portal", "VPP", "user-less catalog", "os"]
check("multi-word matches stem", se.title_match(TERMS, "configure-example-portal") == "example portal")
check("single-word matches a stem token", se.title_match(["VPP"], "manage-vpp-apps-ios") == "vpp")
check("single-word NOT a substring across tokens ('ios' !~ 'kiosk')", se.title_match(["iOS"], "kiosk-mode") is None)
check("single-word NOT mid-token ('app' !~ 'mapping')", se.title_match(["app"], "mapping-guide") is None)
check("too-short term ignored ('os' < 3)", se.title_match(["os"], "macos-overview") is None)
check("multi-word matches H1 substring",
      se.title_match(["example portal"], "x", "Configure the Example Portal app") == "example portal")
check("single-word matches H1 word boundary",
      se.title_match(["vpp"], "x", "Manage VPP tokens") == "vpp")
check("single-word no H1 false-positive",
      se.title_match(["vpp"], "x", "Supported apps") is None)
check("no match -> None", se.title_match(["bitlocker"], "configure-example-portal", "Example Portal") is None)
check("empty terms -> None", se.title_match([], "anything") is None)


print("\nenrich — EXTRACTED-on-title + concepts:")
e = se.enrich([{"path": "docs/app-management/configuration/configure-example-portal.md", "score": 88, "ms_author": "alex"}],
              "Example Portal,VPP")[0]
check("title match sets provenance EXTRACTED", e["provenance"] == "EXTRACTED")
check("records the matched term", e["title_match"] == "example portal")
check("unions the matched concept", "example portal" in e["concepts"])

e = se.enrich([{"path": "docs/app-management/monitor/data-warehouse.md", "score": 80, "ms_author": "alex"}],
              "Example Portal,VPP")[0]
check("tangential body-mention (no title match) -> provenance untouched", "provenance" not in e)
check("tangential keeps no title_match", "title_match" not in e)

print("\nenrich — never downgrade, never clobber:")
e = se.enrich([{"path": "docs/x/manage-vpp-apple.md", "score": 70, "provenance": "INFERRED", "ms_author": "alex"}],
              "VPP")[0]
check("INFERRED upgraded to EXTRACTED on title match", e["provenance"] == "EXTRACTED")
e = se.enrich([{"path": "docs/x/manage-vpp-apple.md", "score": 70, "provenance": "EXTRACTED", "concepts": ["VPP"]}],
              "VPP")[0]
check("existing EXTRACTED preserved (no title_match key added)", e["provenance"] == "EXTRACTED" and "title_match" not in e)
check("existing concept not duplicated (case-insensitive)", e["concepts"].count("VPP") == 1 and "vpp" not in e["concepts"])

print("\nenrich — ms.topic from the lookup:")
lookup = {"docs/x/overview.md": ("overview", "App management overview")}
e = se.enrich([{"path": "docs/x/overview.md", "score": 90}], "management", lookup)[0]
check("ms_topic filled from lookup", e["ms_topic"] == "overview")
e = se.enrich([{"path": "docs/x/overview.md", "score": 90, "ms_topic": "how-to"}], "management", lookup)[0]
check("existing ms_topic not clobbered", e["ms_topic"] == "how-to")
check("missing path safe", se.enrich([{"score": 50}], "vpp") == [{"score": 50}])
check("empty input safe", se.enrich([], "vpp") == [])

print("\nFinding 3 — title_match_enabled=False fills ms.topic only (graph candidates):")
lookup2 = {"docs/x/manage-vpp-apple.md": ("how-to", "Manage VPP apps")}
e = se.enrich([{"path": "docs/x/manage-vpp-apple.md", "blast": "direct"}], "VPP", lookup2, title_match_enabled=False)[0]
check("ms.topic still filled in topic-only mode", e["ms_topic"] == "how-to")
check("NO EXTRACTED-on-title in topic-only mode", "provenance" not in e and "title_match" not in e)
e2 = se.enrich([{"path": "docs/x/manage-vpp-apple.md", "blast": "direct"}], "VPP", lookup2)[0]
check("title-match still fires when enabled", e2.get("provenance") == "EXTRACTED")


print("\nread_meta / build_meta_lookup — front matter + H1:")
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "docs" / "app-management").mkdir(parents=True)
    (root / "docs" / "app-management" / "overview.md").write_text(
        "---\ntitle: Overview\nms.topic: overview\nms.date: 06/30/2026\n---\n\n# App management overview\n\nBody.\n",
        encoding="utf-8")
    (root / "docs" / "app-management" / "no-fm.md").write_text("# Just an H1\n\nNo front matter.\n", encoding="utf-8")
    mt, h1 = se.read_meta(root / "docs" / "app-management" / "overview.md")
    check("reads ms.topic (lowercased)", mt == "overview")
    check("reads H1 after front matter", h1 == "App management overview")
    mt2, h12 = se.read_meta(root / "docs" / "app-management" / "no-fm.md")
    check("no front matter -> ms_topic None, H1 read", mt2 is None and h12 == "Just an H1")
    mt3, h13 = se.read_meta(root / "docs" / "missing.md")
    check("missing file -> (None, None)", mt3 is None and h13 is None)

    lk = se.build_meta_lookup([{"path": "docs/app-management/overview.md"}], root)
    check("build_meta_lookup maps path -> (ms_topic, h1)", lk["docs/app-management/overview.md"] == ("overview", "App management overview"))
    # end-to-end: an overview hub gets ms.topic (-> god-node downstream) AND a title-match concept
    enr = se.enrich([{"path": "docs/app-management/overview.md", "score": 100, "ms_author": "alex"}],
                    "app management", lk)[0]
    check("hub enriched with ms.topic=overview", enr["ms_topic"] == "overview")
    check("hub H1 drives the title match too", enr.get("provenance") == "EXTRACTED")


print("\nCLI _load accepts json string / file / stdin marker:")
check("json string", se._load(json.dumps([{"path": "a.md"}]))[0]["path"] == "a.md")

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

#!/usr/bin/env python3
"""Adversarial regression tests for parse_vtt + feature_notes — run:
    python tests/test_harvest_scripts.py

parse_vtt: malformed WebVTT (empty, header-only, junk, CRLF, NOTE/STYLE blocks, missing
timestamps, inline tags) and term windowing. feature_notes: multi-note, missing-ado, kind
preservation, the intentional idempotency guard, render/list. These two are the H4 (transcript)
and H10 (prior-notes) harvest sources.
"""
import os
import sys
import tempfile
from pathlib import Path

# Redirect feature_notes state to a temp dir BEFORE import (it binds the path at import).
_TMP = tempfile.mkdtemp(prefix="atlas_fn_test_")
os.environ["ATLAS_STATE_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import parse_vtt as pv  # noqa: E402
import feature_notes as fn  # noqa: E402

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


print("parse_vtt — malformed / adversarial input:")
check("empty -> []", pv.parse_vtt("") == [])
check("header only -> []", pv.parse_vtt("WEBVTT\n") == [])
check("whitespace junk -> []", pv.parse_vtt("   \n\n  \t\n") == [])
check("no timestamp/voice -> Unknown",
      pv.parse_vtt("WEBVTT\n\njust text") == [{"speaker": "Unknown", "text": "just text"}])

vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v Alice>Hello\n\n"
       "00:00:03.000 --> 00:00:05.000\n<v Alice>still me\n\n"
       "00:00:05.000 --> 00:00:07.000\n<v Bob>Bob now")
t = pv.parse_vtt(vtt)
check("same-speaker cues merged", len(t) == 2 and "still me" in t[0]["text"])
check("speaker change splits", t[1]["speaker"] == "Bob")
check("CRLF normalized",
      len(pv.parse_vtt("WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000\r\n<v X>hi")) == 1)
check("NOTE block skipped",
      pv.parse_vtt("WEBVTT\n\nNOTE a note\n\n00:00:01.000 --> 00:00:02.000\n<v X>real")
      == [{"speaker": "X", "text": "real"}])
check("inline tags stripped",
      pv.parse_vtt("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v X>hi <b>there</b>")
      == [{"speaker": "X", "text": "hi there"}])
try:
    pv.parse_vtt("WEBVTT\n\ngarbage --> notatime\ntext")
    check("malformed timestamp no crash", True)
except Exception:
    check("malformed timestamp no crash", False)

turns = [{"speaker": "A", "text": "intro"}, {"speaker": "B", "text": "the shortcut feature"},
         {"speaker": "A", "text": "outro"}, {"speaker": "B", "text": "unrelated"}]
w = pv.window_by_terms(turns, ["shortcut"], 1)
check("window keeps match + context", len(w) == 3 and any(x.get("match") for x in w))
check("empty terms -> all turns", pv.window_by_terms(turns, [], 1) == turns)
check("no match -> empty", pv.window_by_terms(turns, ["nonexistentterm"], 1) == [])

print("\nfeature_notes — H10 prior-notes log:")
fn.add("70001", "first note", "decision", "alex")
fn.add("70001", "second note", "hold", "casey")
check("two notes for same ado", len(fn.read("70001")["notes"]) == 2)
check("read missing ado -> empty", fn.read("99999")["notes"] == [])
check("kinds preserved", {n["kind"] for n in fn.read("70001")["notes"]} == {"decision", "hold"})
# Intentional idempotency guard: identical (date+kind+note) is NOT re-appended, so a re-run
# that auto-logs the same context doesn't duplicate. This is by design (see add()).
res = fn.add("70001", "first note", "decision", "alex")
check("identical note deduped (idempotency guard)", res["status"] == "duplicate")
check("dedup left count at 2", len(fn.read("70001")["notes"]) == 2)
# A genuinely different note (different text) still appends.
fn.add("70001", "third distinct note", "decision", "alex")
check("distinct note appends", len(fn.read("70001")["notes"]) == 3)
check("render non-empty", "first note" in fn.render("70001"))
check("list includes feature", any(f.get("ado") == "70001" for f in fn.list_features()))

# Cleanup
import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

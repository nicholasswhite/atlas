#!/usr/bin/env python3
"""Regression tests for assignment_log (writer-assignment notification ledger) — run:
    python tests/test_assignment_log.py

Covers pending-detection, mark/idempotency, the simulation contract (not marking keeps an item
pending), reassignment (per-writer keying), dedup, multi-id slates, and list. State is redirected
to a temp dir BEFORE import (the module binds its path at import).
"""
import os
import sys
import tempfile
from pathlib import Path

# Redirect ledger state to a temp dir BEFORE import (the module binds the path at import time).
_TMP = tempfile.mkdtemp(prefix="atlas_assign_test_")
os.environ["ATLAS_STATE_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import assignment_log as al  # noqa: E402

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


print("assignment_log — pending detection:")

slate = [
    {"ado": "90001013", "title": "Desktop Widget Controls", "timeline": "2607"},
    {"ado": "90001015", "title": "Recover deleted workspace groups", "timeline": "2607"},
]

# Fresh: everything on the slate is pending.
p = al.pending("Morgan Example", slate)
check("fresh slate: all pending", len(p) == 2)
check("pending carries title", p[0]["title"] == "Desktop Widget Controls")
check("pending carries timeline", p[0]["timeline"] == "2607")
check("nothing notified yet", al.is_notified("Morgan Example", "90001013") is False)

print("\nassignment_log — mark + idempotency:")

res = al.mark_notified("Morgan Example", "90001013", "Desktop Widget Controls", "2607")
check("mark returns marked", res["status"] == "marked")
check("now notified", al.is_notified("Morgan Example", "90001013") is True)

# After marking one, only the other is pending.
p = al.pending("Morgan Example", slate)
check("one marked -> one pending", len(p) == 1 and p[0]["ado"] == "90001015")

# Idempotent re-mark.
res = al.mark_notified("Morgan Example", "90001013")
check("re-mark -> already", res["status"] == "already")

print("\nassignment_log — simulation contract:")

# The simulation contract: detecting pending must NOT mark anything. After a pending() call with
# no mark, the item is still pending (a later real run will catch it).
p_before = al.pending("Casey Example", [{"ado": "55555", "title": "Example key management", "timeline": "2608"}])
p_after = al.pending("Casey Example", [{"ado": "55555", "title": "Example key management", "timeline": "2608"}])
check("pending is read-only (still pending on re-check)", len(p_before) == 1 and len(p_after) == 1)
check("pending didn't mark notified", al.is_notified("Casey Example", "55555") is False)

print("\nassignment_log — reassignment (per-writer keying):")

# Same feature, different writer: Alex already notified, Morgan is not -> only Morgan is pending.
al.mark_notified("Alex Example", "70001", "Shared Feature", "2607")
check("Alex notified for 70001", al.is_notified("Alex Example", "70001") is True)
check("Morgan NOT notified for 70001", al.is_notified("Morgan Example", "70001") is False)
p = al.pending("Morgan Example", [{"ado": "70001", "title": "Shared Feature", "timeline": "2607"}])
check("reassigned feature pending for new writer", len(p) == 1)
p = al.pending("Alex Example", [{"ado": "70001", "title": "Shared Feature", "timeline": "2607"}])
check("not pending for original writer", len(p) == 0)

print("\nassignment_log — dedup, id alias, list:")

# Dedup within a slate + accept "id" as an alias for "ado".
dup = [{"ado": "80001", "title": "A"}, {"id": "80001", "title": "A dup"},
       {"ado": "80002", "title": "B"}]
p = al.pending("Alex Example", dup)
check("dedup within slate", len(p) == 2)
check("'id' alias accepted", any(x["ado"] == "80001" for x in p))

# Empty / missing-id rows are skipped, no crash.
p = al.pending("Alex Example", [{"title": "no id"}, {}, {"ado": "", "title": "blank"}])
check("rows without ado skipped", p == [])

# list for a writer shows notified entries.
lst = al.list_for("Morgan Example")
check("list_for writer shows notified", "90001013" in lst["features"])
# list summary (writer=None) shows counts.
summary = al.list_for(None)
check("list summary has writers", "Morgan Example" in summary and "Alex Example" in summary)

print("\nassignment_log — reassignment-away detection + clear:")

# Morgan was notified about 90001013 (above). Now the worklist shows it assigned to Casey.
worklist = [
    {"ado": "90001013", "writer": "Casey Example", "title": "Desktop Widget Controls", "timeline": "2607"},
    {"ado": "90001015", "writer": "Morgan Example", "title": "Recover deleted workspace groups", "timeline": "2607"},
]
events = al.detect_reassignments(worklist)
moved = [e for e in events if e["ado"] == "90001013"]
check("reassignment detected", len(moved) == 1)
check("from previous writer", moved[0]["from_writer"] == "Morgan Example")
check("to new writer", moved[0]["to_writer"] == "Casey Example")
check("event carries title/timeline", moved[0]["title"] == "Desktop Widget Controls" and moved[0]["timeline"] == "2607")

# Same writer (no change) must NOT be an event: 90001015 is still Morgan's, but it was never marked
# notified, so it's not in the ledger -> no event regardless.
check("no event for unchanged/unledgered", all(e["ado"] != "90001015" for e in events))

# Absence != departure: a feature in the ledger but NOT in the current worklist (closed/cut, or
# outside the window) must NOT fire a reassignment.
al.mark_notified("Alex Example", "90090", "Shipped Thing", "2606")
events_absent = al.detect_reassignments([])  # empty worklist
check("absence does not fire departure", all(e["ado"] != "90090" for e in events_absent))

# clear removes the previous writer's entry (after the departure notice is sent).
res = al.clear("Morgan Example", "90001013")
check("clear removes entry", res["status"] == "cleared")
check("after clear, no longer notified", al.is_notified("Morgan Example", "90001013") is False)
# After clearing Morgan, the reassignment no longer re-fires for her.
events_after = al.detect_reassignments(worklist)
check("cleared departure doesn't re-fire", all(e["from_writer"] != "Morgan Example" or e["ado"] != "90001013" for e in events_after))
# Clear of an absent entry is a graceful no-op.
check("clear absent -> absent", al.clear("Nobody", "00000")["status"] == "absent")

# Round-trip: after Casey is marked (new owner) and Morgan cleared, a re-assignment BACK to Morgan
# would make her pending again (fresh notice).
al.mark_notified("Casey Example", "90001013", "Desktop Widget Controls", "2607")
back = al.pending("Morgan Example", [{"ado": "90001013", "title": "Desktop Widget Controls", "timeline": "2607"}])
check("reassigned-back feature pending again for prev writer", len(back) == 1)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

#!/usr/bin/env python3
"""Regression tests for crw_tags.advance_tag — run: python tests/test_crw_tags.py

Covers the approval hop (draft->ready) across both tracks, the messy real-world CRW comment
forms (entity-encoded delimiters, nested <span>/&nbsp;, pre-existing <b>), the cumulative +
single-bold rules, idempotency, the no-draft / no-tracking guards, multi-id preservation,
own-id targeting, and the explicit --to ladder.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import crw_tags as ct  # noqa: E402

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


def bolded(crw):
    """Return the list of tag tokens wrapped in <b>...</b> inside the (first) comment."""
    return re.findall(r"<b>(\w+)</b>", crw)


print("crw_tags — approval hop (draft -> ready):")

# 1. Simple raw comment, WN draft -> ready.
r = ct.advance_tag("<!-- 12345 wndraft -->", "wn")
check("wndraft -> wnready advanced", r["status"] == "advanced" and r["to_tag"] == "wnready")
check("wnready is present + bolded", "<b>wnready</b>" in r["crw"])
check("from_tag reported", r["from_tag"] == "wndraft")
check("wndraft kept (cumulative)", "wndraft" in r["crw"] and "wnready" in r["crw"])
check("exactly one bolded tag", bolded(r["crw"]) == ["wnready"])

# 2. ID track.
r = ct.advance_tag("<!-- 999001 iddraft -->", "id")
check("iddraft -> idready advanced", r["status"] == "advanced" and r["to_tag"] == "idready")
check("idready bolded", bolded(r["crw"]) == ["idready"])

# 3. Entity-encoded (visible) delimiters are preserved.
r = ct.advance_tag("&lt;!-- 12345 wndraft --&gt;", "wn")
check("encoded form preserved", r["crw"].strip().startswith("&lt;!--") and r["crw"].strip().endswith("--&gt;"))
check("encoded advance bolds wnready", "<b>wnready</b>" in r["crw"])

# 4. Messy real-world body: nested span + &nbsp; + pre-existing <b> on the draft.
messy = '&lt;!-- 90001010,<span>&nbsp;</span><span style="x">wn' + 'draft</span>&nbsp; --&gt;'
r = ct.advance_tag(messy, "wn")
check("messy body advances", r["status"] == "advanced")
check("messy body cleaned (no span)", "<span" not in r["crw"])
check("messy id preserved", "90001010" in r["crw"])
check("messy single bold = wnready", bolded(r["crw"]) == ["wnready"])

# 4b. An encoded synthetic tracking format for item 90001012 (approval replay fixture):
#     escaped delimiters + comma after the id + a PRE-BOLDED draft tag. Locks the format Atlas
#     advanced against a synthetic field with a fictional reviewer approval.
real_id = "line-of-business apps on iOS/iPadOS&lt;!-- 90001012, <b>iddraft</b> --&gt;<br>"
r = ct.advance_tag(real_id, "id")
check("real 90001012 ID marker: iddraft -> idready", r["status"] == "advanced" and r["to_tag"] == "idready")
check("real marker keeps escaped delimiters", "&lt;!--" in r["crw"] and "--&gt;" in r["crw"])
check("real marker: iddraft kept + single bold = idready", "iddraft" in r["crw"] and bolded(r["crw"]) == ["idready"])
# the WN variant had spaces inside the bold (`<b> wndraft </b>`)
real_wn = "apps on iOS/iPadOS&lt;!-- 90001012<b> wndraft </b>--&gt;<br>"
r2 = ct.advance_tag(real_wn, "wn")
check("real 90001012 WN marker: wndraft -> wnready", r2["status"] == "advanced" and bolded(r2["crw"]) == ["wnready"])

# 5. Both-track comment: advancing WN keeps ID tags and re-bolds only wnready (unbold previous).
both = "<!-- 12345 iddraft idready idstaged <b>wndraft</b> -->"
r = ct.advance_tag(both, "wn")
check("both-track advance", r["status"] == "advanced")
check("ID tags all kept", all(t in r["crw"] for t in ("iddraft", "idready", "idstaged")))
check("previous bold (wndraft) removed", "<b>wndraft</b>" not in r["crw"])
check("only wnready bolded now", bolded(r["crw"]) == ["wnready"])
check("ladder order preserved",
      r["crw"].index("iddraft") < r["crw"].index("wndraft") < r["crw"].index("wnready"))

print("\ncrw_tags — idempotency + guards:")

# 6. Idempotent: ready already present -> no change.
r = ct.advance_tag("<!-- 12345 wndraft <b>wnready</b> -->", "wn")
check("already ready -> status already", r["status"] == "already")
check("already -> crw unchanged", r["crw"] == "<!-- 12345 wndraft <b>wnready</b> -->")

# 7. A comment with an id but no recognized tag, and no --own to anchor on, isn't treated as a
#    marker (the finder won't mistake a stray numeric comment for one) -> no_tracking, no change.
r = ct.advance_tag("<!-- 12345 -->", "wn")
check("id-only comment, no own -> no_tracking", r["status"] == "no_tracking")
check("id-only -> crw unchanged", r["crw"] == "<!-- 12345 -->")
# 7a. ...but WITH --own matching that id, advance the id-only marker in place (don't seed a dup).
r = ct.advance_tag("<!-- 12345 -->", "wn", own_id="12345")
check("id-only + own -> advanced in place", r["status"] == "advanced")
check("id-only advance bolds wnready + no dup id",
      "<b>wnready</b>" in r["crw"] and r["crw"].count("12345") == 1)

# 7b. THE CORRECTED RULE (2026-06-23): the ready tag does NOT require the draft tag. Not every
#     writer sets iddraft/wndraft, and unless Atlas drafted the item itself the marker may be
#     absent. So a comment carrying only the OTHER track's draft tag (or none) still advances to
#     ready, preserving whatever tags are there. The real gate — a real draft blurb + PM approval
#     — is the skill layer's job, not a tag check here.
r = ct.advance_tag("<!-- 12345 iddraft -->", "wn")  # only iddraft present, ask for wnready
check("wnready without wndraft -> advanced", r["status"] == "advanced")
check("wnready added", "<b>wnready</b>" in r["crw"])
check("existing iddraft preserved", "iddraft" in r["crw"])
r = ct.advance_tag("<!-- 12345 wndraft -->", "id")  # only wndraft present, ask for idready
check("idready without iddraft -> advanced", r["status"] == "advanced")
check("idready added, wndraft preserved", "<b>idready</b>" in r["crw"] and "wndraft" in r["crw"])

# 8. No marker at all + no --own to anchor -> no_tracking (nothing to write, nowhere to put it).
r = ct.advance_tag("Just some prose, no comment here.", "id")
check("no marker, no own -> no_tracking", r["status"] == "no_tracking")
r = ct.advance_tag("", "id")
check("empty crw, no own -> no_tracking", r["status"] == "no_tracking")
r = ct.advance_tag(None, "wn")
check("None crw, no own -> no_tracking", r["status"] == "no_tracking")

# 8a. SEEDING: no marker but --own given -> create a fresh marker carrying just the ready tag,
#     never inventing a draft tag the writer didn't use. This is the path for a writer who drafted
#     the blurb manually with no tracking tags at all.
r = ct.advance_tag("### My blurb\nSome draft text.", "wn", own_id="90001015")
check("no marker + own -> seeded", r["status"] == "seeded")
check("seeded marker carries id + ready", "90001015" in r["crw"] and "<b>wnready</b>" in r["crw"])
check("seeded did NOT invent a draft tag", "wndraft" not in r["crw"])
check("seeded preserved the original blurb", "### My blurb" in r["crw"])
r = ct.advance_tag("", "id", own_id="999001")
check("empty crw + own -> seeded", r["status"] == "seeded" and "999001" in r["crw"] and "idready" in r["crw"])

print("\ncrw_tags — multi-id, targeting, explicit --to:")

# 9. Multiple ids preserved comma-separated.
r = ct.advance_tag("<!-- 90001003, 90001007 iddraft -->", "id")
check("multi-id advanced", r["status"] == "advanced")
check("both ids preserved", "90001003" in r["crw"] and "90001007" in r["crw"])
check("ids reported", r["ids"] == ["90001003", "90001007"])

# 10. own_id targets the right comment when several exist.
multi = "<!-- 111111 wndraft --> filler <!-- 222222 wndraft -->"
r = ct.advance_tag(multi, "wn", own_id="222222")
check("own_id targets 2nd comment", "<!-- 222222 wndraft <b>wnready</b> -->" in r["crw"])
check("other comment untouched", "<!-- 111111 wndraft -->" in r["crw"])

# 11. Explicit --to: ready -> staged (predecessor present).
r = ct.advance_tag("<!-- 12345 wndraft wnready -->", "wn", to_tag="wnstaged")
check("ready -> staged advanced", r["status"] == "advanced" and r["to_tag"] == "wnstaged")
check("staged bolded", bolded(r["crw"]) == ["wnstaged"])

# 12. Explicit --to staged without ready -> not_ready (the staged hop still requires ready, which
#     Atlas sets first via the staging skill; the ready hop above has no such precondition).
r = ct.advance_tag("<!-- 12345 wndraft -->", "wn", to_tag="wnstaged")
check("staged w/o ready -> not_ready", r["status"] == "not_ready")

# 13. Invalid track / to_tag.
check("invalid track", ct.advance_tag("<!-- 1 wndraft -->", "xx")["status"] == "invalid")
check("invalid to_tag", ct.advance_tag("<!-- 1 wndraft -->", "wn", to_tag="wnfoo")["status"] == "invalid")

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

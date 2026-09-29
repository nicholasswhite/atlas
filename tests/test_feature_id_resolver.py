#!/usr/bin/env python3
"""Tests for feature_id_resolver — run: python tests/test_feature_id_resolver.py

Covers the deterministic feature-ID bridge: candidate validation vs vocabulary, handle assembly +
confidence-by-tier, the confidence gate, L0 catalog lookup (by id + by name), the L0>L1>L2
selection ladder (incl. L1-preferred-over-L2 and reject-then-fall-through), the never-fabricate
guard (unresolved -> open_question, zero handles), idempotent merge/dedup, catalog overlay, and
the seed loader.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import feature_id_resolver as r  # noqa: E402

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


VOCAB = r._DEFAULT_VOCAB


# --- validate_candidate ----------------------------------------------------
print("validate_candidate:")

v = r.validate_candidate({"kind": "service", "id": "ExampleDeploymentService"}, VOCAB)
check("known service namespace ok", v["ok"])

v = r.validate_candidate({"kind": "service", "id": "DeploymentService"}, VOCAB)
check("known service name ok", v["ok"])

v = r.validate_candidate({"kind": "service", "id": "TotallyMadeUpSvc"}, VOCAB)
check("unknown service rejected", not v["ok"])

v = r.validate_candidate({"kind": "service", "id": "X", "svc_namespace": "DemoProduction"}, VOCAB)
check("service accepted via known svc_namespace", v["ok"])

v = r.validate_candidate({"kind": "toggle", "id": "ExampleProduct.AppInUse.Detection"}, VOCAB)
check("well-formed toggle id ok", v["ok"])

v = r.validate_candidate({"kind": "toggle", "id": "bad id!!"}, VOCAB)
check("malformed toggle id rejected", not v["ok"])

v = r.validate_candidate({"kind": "frob", "id": "x"}, VOCAB)
check("bad kind rejected", not v["ok"])

v = r.validate_candidate({"kind": "service", "id": ""}, VOCAB)
check("empty id rejected", not v["ok"])


# --- assemble_handle -------------------------------------------------------
print("\nassemble_handle:")

h = r.assemble_handle("service", "ExampleDeploymentService", "DemoProduction", "H6:engms", "L1")
check("L1 -> EXTRACTED", h and h["confidence"] == "EXTRACTED")
check("handle carries svc_namespace", h["svc_namespace"] == "DemoProduction")

h = r.assemble_handle("service", "ExampleDeploymentService", None, "servicecatalog", "L2")
check("L2 -> INFERRED", h and h["confidence"] == "INFERRED")
check("null svc_namespace normalized to None", h["svc_namespace"] is None)

h = r.assemble_handle("toggle", "Some.Flag", None, "catalog", "L0", confidence="EXTRACTED")
check("L0 honors explicit confidence", h and h["confidence"] == "EXTRACTED")

check("bad kind -> None", r.assemble_handle("frob", "x", None, "s", "L1") is None)
check("empty id -> None", r.assemble_handle("service", "", None, "s", "L1") is None)
check("bad confidence -> None", r.assemble_handle("service", "x", None, "s", "ZZZ") is None)


# --- gate ------------------------------------------------------------------
print("\ngate:")

handles = [
    {"id": "a", "confidence": "EXTRACTED"},
    {"id": "b", "confidence": "INFERRED"},
    {"id": "c", "confidence": "AMBIGUOUS"},
]
g = r.gate(handles)
check("EXTRACTED -> draftable", g["draftable"][0]["id"] == "a")
check("INFERRED -> annotate_only", g["annotate_only"][0]["id"] == "b")
check("AMBIGUOUS -> blocked", g["blocked"][0]["id"] == "c")
check("gate of [] is empty buckets", r.gate([]) == {"draftable": [], "annotate_only": [], "blocked": []})


# --- lookup ----------------------------------------------------------------
print("\nlookup:")

catalog = {
    "90001002": {"feature_name": "App in-use detection",
                 "handles": [{"kind": "service", "id": "DeploymentService",
                              "svc_namespace": "DemoProduction", "source": "PM", "tier": "L0",
                              "confidence": "EXTRACTED"}]},
}
check("lookup by ado id hits", r.lookup(catalog, "90001002")[0]["id"] == "DeploymentService")
check("lookup by normalized name hits", bool(r.lookup(catalog, "0", "app  IN-use  detection!")))
check("lookup miss -> []", r.lookup(catalog, "999") == [])
check("lookup empty catalog -> []", r.lookup({}, "1", "x") == [])


# --- effective_catalog + dedup ---------------------------------------------
print("\neffective_catalog + dedup:")

seed_anchors = {"100": {"feature_name": "f", "handles": [{"kind": "service", "id": "A",
                "svc_namespace": None, "source": "seed", "tier": "L0", "confidence": "EXTRACTED"}]}}
runtime = {"100": {"handles": [{"kind": "service", "id": "B", "svc_namespace": None,
           "source": "rt", "tier": "L0", "confidence": "EXTRACTED"}], "verified": True},
           "200": {"feature_name": "g", "handles": []}}
eff = r.effective_catalog(seed_anchors, runtime)
check("overlay unions handles (seed + runtime)", len(eff["100"]["handles"]) == 2)
check("overlay: runtime fields win (verified)", eff["100"]["verified"] is True)
check("overlay adds runtime-only key", "200" in eff)

dups = [{"kind": "service", "id": "A", "svc_namespace": None},
        {"kind": "service", "id": "A", "svc_namespace": None},
        {"kind": "toggle", "id": "A", "svc_namespace": None}]
check("dedup by (kind,id,svc_namespace)", len(r._dedup_handles(dups)) == 2)


# --- resolve: the ladder ---------------------------------------------------
print("\nresolve — ladder:")

# L0 catalog hit.
res = r.resolve({"ado_id": "90001002"}, [], catalog, VOCAB)
check("L0 hit -> resolved tier L0", res["status"] == "resolved" and res["tier"] == "L0")
check("L0 handle is draftable", len(res["gated"]["draftable"]) == 1)

# L0 with a stored INFERRED handle stays annotate-only.
cat_inf = {"5": {"feature_name": "z", "handles": [{"kind": "service", "id": "PolicyService",
           "svc_namespace": None, "source": "st", "tier": "L0", "confidence": "INFERRED"}]}}
res = r.resolve({"ado_id": "5"}, [], cat_inf, VOCAB)
check("L0 stored INFERRED stays annotate_only", len(res["gated"]["annotate_only"]) == 1)

# L1 explicit valid.
res = r.resolve({"ado_id": "1", "feature_name": "new feature"},
                [{"kind": "service", "id": "ExampleDeploymentService", "source": "H6:engms", "tier": "explicit"}],
                {}, VOCAB)
check("L1 explicit -> resolved tier L1", res["status"] == "resolved" and res["tier"] == "L1")
check("L1 -> EXTRACTED draftable", len(res["gated"]["draftable"]) == 1)

# L1 toggle valid.
res = r.resolve({"ado_id": "2"},
                [{"kind": "toggle", "id": "ExampleProduct.Foo.Bar", "source": "H6", "tier": "explicit"}],
                {}, VOCAB)
check("L1 toggle resolves EXTRACTED", res["tier"] == "L1" and res["handles"][0]["kind"] == "toggle")

# L1 candidate rejected (unknown service) but an L2 candidate exists -> falls through to L2.
res = r.resolve({"ado_id": "3"},
                [{"kind": "service", "id": "MadeUp", "source": "H1", "tier": "explicit"},
                 {"kind": "service", "id": "DemoProduction", "source": "servicecatalog", "tier": "servicecatalog"}],
                {}, VOCAB)
check("L1 reject + L2 present -> tier L2", res["tier"] == "L2")
check("L2 -> INFERRED annotate_only", len(res["gated"]["annotate_only"]) == 1)
check("rejection recorded in notes", any("rejected" in n for n in res["notes"]))

# L1 preferred over L2 when both valid.
res = r.resolve({"ado_id": "4"},
                [{"kind": "service", "id": "ExampleDeploymentService", "source": "H6", "tier": "explicit"},
                 {"kind": "service", "id": "DemoProduction", "source": "st", "tier": "servicecatalog"}],
                {}, VOCAB)
check("L1 preferred over L2", res["tier"] == "L1")
check("L2 set-aside noted", any("set aside" in n for n in res["notes"]))


# --- resolve: never fabricate ----------------------------------------------
print("\nresolve — never fabricate:")

res = r.resolve({"ado_id": "777", "feature_name": "mystery"}, [], {}, VOCAB)
check("nothing -> unresolved", res["status"] == "unresolved")
check("unresolved -> zero handles (never fabricate)", res["handles"] == [])
check("unresolved -> open_question for PM", res["open_question"]["needs"] == "PM confirmation")
check("unresolved tier is None", res["tier"] is None)

# An unknown-tier candidate is ignored, not invented into a handle.
res = r.resolve({"ado_id": "778"},
                [{"kind": "service", "id": "ExampleDeploymentService", "source": "x", "tier": "guess"}], {}, VOCAB)
check("unknown-tier candidate ignored -> unresolved", res["status"] == "unresolved")
check("unknown-tier noted", any("unknown tier" in n for n in res["notes"]))


# --- merge -----------------------------------------------------------------
print("\nmerge:")

rt = {}
rt = r.merge(rt, "900", [{"kind": "service", "id": "DeploymentService", "svc_namespace": "DemoProduction",
             "source": "PM", "tier": "L0", "confidence": "EXTRACTED"}],
             verified=True, source="PM:Test", feature_name="My Feature")
check("merge creates entry", "900" in rt and rt["900"]["verified"] is True)
check("merge stores feature_name", rt["900"]["feature_name"] == "My Feature")
check("merge stamps updated", "updated" in rt["900"])

# Idempotent re-merge of the same handle does not duplicate.
rt = r.merge(rt, "900", [{"kind": "service", "id": "DeploymentService", "svc_namespace": "DemoProduction",
             "source": "PM", "tier": "L0", "confidence": "EXTRACTED"}])
check("re-merge idempotent (no dup)", len(rt["900"]["handles"]) == 1)

# After merge, a fresh resolve finds it at L0 by name.
eff = r.effective_catalog({}, rt)
res = r.resolve({"ado_id": "0", "feature_name": "my feature"}, [], eff, VOCAB)
check("merged mapping resolves at L0 by name", res["tier"] == "L0")


# --- seed loader -----------------------------------------------------------
print("\nload_seed:")

with tempfile.TemporaryDirectory() as d:
    md = Path(d) / "known-feature-ids.md"
    md.write_text('intro\n\n```json\n{"vocabulary":{"service_namespaces":["FooSvc"],'
                  '"service_names":[],"flag_id_pattern":"^x$"},"anchors":{"1":{"handles":[]}}}\n```\n',
                  encoding="utf-8")
    seed = r.load_seed(md)
    check("seed parses json fence", seed["vocabulary"]["service_namespaces"] == ["FooSvc"])
    check("seed parses anchors", "1" in seed["anchors"])

    missing = Path(d) / "nope.md"
    check("missing seed -> default vocab", r.load_seed(missing)["vocabulary"] == r._DEFAULT_VOCAB)

    nofence = Path(d) / "nofence.md"
    nofence.write_text("# no json here\n", encoding="utf-8")
    check("no-fence seed -> empty anchors", r.load_seed(nofence)["anchors"] == {})

# Round-trip save/load of the runtime catalog.
with tempfile.TemporaryDirectory() as d:
    cat_path = Path(d) / "feature_id_map.json"
    r.save_runtime(rt, cat_path)
    loaded = r.load_runtime(cat_path)
    check("runtime save/load round-trips", loaded["900"]["feature_name"] == "My Feature")


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)

#!/usr/bin/env python3
"""Resolve a feature to an explicitly sourced telemetry handle.

The agent or a caller provides candidate identifiers. This deterministic module
validates them, selects explicit evidence before catalog inference, and persists
verified mappings. It never queries a service or invents an unresolved identifier.

The fallback vocabulary is FICTIONAL demo data. Supply a reviewed vocabulary and
explicit adapter for a real environment; see references/feature-id-bridge.md.
L0 = previously verified catalog; L1 = explicit source; L2 = service-catalog
inference; unresolved = an open question. Inferred handles are not draftable facts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CONFIDENCE_VALUES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
KINDS = ("service", "toggle")

# tier -> confidence for assembled (L1/L2) handles. L0 carries its stored confidence.
_TIER_CONFIDENCE = {"L1": "EXTRACTED", "L2": "INFERRED"}
# candidate-tier aliases the judgement layer may use.
_TIER_ALIAS = {"explicit": "L1", "l1": "L1", "servicecatalog": "L2", "l2": "L2"}

# Fictional validation vocabulary for offline examples only.
_DEFAULT_VOCAB = {
    "service_namespaces": [
        # Fictional environment names.
        "DemoInternal", "DemoEarly", "DemoPreview", "DemoProduction",
        "DemoGatewayInternal", "DemoGatewayEarly", "DemoGatewayProduction",
        # Fictional telemetry services.
        "ExampleIdentityService", "ExampleDeploymentService",
    ],
    "service_names": [
        # Fictional service names;
        # provide your own reviewed vocabulary for an adapter.
        "ExampleCertificateService", "ExampleConfigurationService", "ExampleIntentService",
        "ExampleGraphService", "ExamplePolicyWorker", "ExamplePolicyAdmin", "ExampleDownloadService",
        "ExampleComplianceWorker", "ExampleCertificateWorker", "ExampleFrontend",
        "ExampleManagementFrontend", "ExampleAnalyticsFrontend", "ExampleConnectivityService", "ExampleImportWorker",
        "DeploymentService", "EnrollmentService", "PolicyService",
    ],
    "flag_id_pattern": r"^[A-Za-z0-9][A-Za-z0-9._\-]{2,79}$",
}


# ---------------------------------------------------------------------------
# Paths / IO (the only non-pure part — kept out of the resolver functions).
# ---------------------------------------------------------------------------

def _state_root() -> Path:
    """Root for Atlas runtime state. Honors $ATLAS_STATE_DIR (the sandbox redirects writes to a
    disposable location); defaults to the plugin's own state/ dir."""
    env = os.environ.get("ATLAS_STATE_DIR")
    return Path(env) if env else (Path(__file__).resolve().parent.parent / "state")


def _seed_path() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "known-feature-ids.md"


def _catalog_path() -> Path:
    return _state_root() / "feature_id_map.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load_seed(path: Path | str | None = None) -> dict:
    """Load the version-controlled seed (vocabulary + anchors) from the first ```json fence in
    known-feature-ids.md. Missing file / no fence / bad JSON -> default vocab + empty anchors."""
    p = Path(path) if path else _seed_path()
    fallback = {"vocabulary": dict(_DEFAULT_VOCAB), "anchors": {}}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return fallback
    m = re.search(r"```json\s*(.*?)```", text, re.DOTALL)
    if not m:
        return fallback
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    data.setdefault("vocabulary", dict(_DEFAULT_VOCAB))
    data.setdefault("anchors", {})
    return data


def load_runtime(path: Path | str | None = None) -> dict:
    """Load the runtime catalog (the accumulating, git-ignored layer)."""
    p = Path(path) if path else _catalog_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_runtime(catalog: dict, path: Path | str | None = None) -> Path:
    p = Path(path) if path else _catalog_path()
    _atomic_write(p, json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    return p


# ---------------------------------------------------------------------------
# Pure helpers.
# ---------------------------------------------------------------------------

def _norm(text: str | None) -> str:
    """Normalize a feature name/area for matching: lowercase, strip, collapse non-alnum runs."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _handle_key(h: dict) -> tuple:
    return (h.get("kind"), h.get("id"), h.get("svc_namespace"))


def _dedup_handles(handles: list) -> list:
    """Dedup a handle list by (kind, id, svc_namespace), keeping first occurrence."""
    seen: set = set()
    out: list = []
    for h in handles or []:
        k = _handle_key(h)
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def effective_catalog(seed_anchors: dict, runtime: dict) -> dict:
    """Overlay the runtime layer onto the seed anchors. Same key -> runtime fields win, handles
    are unioned (seed first). This is what `lookup` reads."""
    out: dict = {k: dict(v) for k, v in (seed_anchors or {}).items()}
    for k, e in (runtime or {}).items():
        if k in out:
            merged = dict(out[k])
            merged.update(e)
            merged["handles"] = _dedup_handles(
                list(out[k].get("handles") or []) + list(e.get("handles") or [])
            )
            out[k] = merged
        else:
            out[k] = dict(e)
    return out


# ---------------------------------------------------------------------------
# The deterministic resolver surface.
# ---------------------------------------------------------------------------

def lookup(catalog: dict, ado_id: str | int | None, name: str | None = None) -> list:
    """L0 — catalog hit. Exact ado_id key first, then a normalized feature-name match. Returns a
    (copied) list of handles, or []."""
    if not catalog:
        return []
    aid = str(ado_id).strip() if ado_id is not None else ""
    if aid and aid in catalog and catalog[aid].get("handles"):
        return [dict(h) for h in catalog[aid]["handles"]]
    if name:
        n = _norm(name)
        if n:
            for entry in catalog.values():
                if _norm(entry.get("feature_name")) == n and entry.get("handles"):
                    return [dict(h) for h in entry["handles"]]
    return []


def validate_candidate(candidate: dict, vocabulary: dict | None = None) -> dict:
    """Validate an explicit (L1) candidate against the known vocabulary. Returns
    {ok, kind, normalized, reason}. A `service` id must be a known namespace/service name (or its
    svc_namespace must be); a `toggle` id must match the well-formed flag-id pattern (we can't
    confirm a flag exists without the ECS registry, so 'well-formed + explicitly stated' is the
    bar — provenance carries the rest)."""
    vocab = vocabulary or _DEFAULT_VOCAB
    kind = (candidate.get("kind") or "").strip().lower()
    cid = (candidate.get("id") or "").strip()
    svc_ns = (candidate.get("svc_namespace") or "").strip()
    if kind not in KINDS:
        return {"ok": False, "kind": kind, "normalized": cid, "reason": f"kind must be one of {KINDS}"}
    if not cid:
        return {"ok": False, "kind": kind, "normalized": cid, "reason": "empty id"}
    if kind == "service":
        known = {s.lower() for s in vocab.get("service_namespaces", [])} | \
                {s.lower() for s in vocab.get("service_names", [])}
        if cid.lower() in known or (svc_ns and svc_ns.lower() in known):
            return {"ok": True, "kind": kind, "normalized": cid, "reason": "known service/namespace"}
        return {"ok": False, "kind": kind, "normalized": cid,
                "reason": "service id not in known vocabulary (extend known-feature-ids.md or use L2)"}
    # toggle
    pattern = vocab.get("flag_id_pattern") or _DEFAULT_VOCAB["flag_id_pattern"]
    if re.match(pattern, cid):
        return {"ok": True, "kind": kind, "normalized": cid, "reason": "well-formed flag id"}
    return {"ok": False, "kind": kind, "normalized": cid, "reason": "flag id is not well-formed"}


def assemble_handle(kind: str, hid: str, svc_namespace: str | None,
                    source: str, tier: str, confidence: str | None = None) -> dict | None:
    """Build a schema-valid query handle, or None if invalid. Confidence is derived from the tier
    for L1/L2; an explicit `confidence` is honored (used by L0 to carry the stored value)."""
    kind = (kind or "").strip().lower()
    hid = (hid or "").strip()
    tier = (tier or "").strip().upper()
    if kind not in KINDS or not hid:
        return None
    conf = confidence or _TIER_CONFIDENCE.get(tier)
    if conf not in CONFIDENCE_VALUES:
        return None
    return {
        "kind": kind,
        "id": hid,
        "svc_namespace": (svc_namespace or None) or None,
        "source": source or "unknown",
        "tier": tier,
        "confidence": conf,
    }


def gate(handles: list) -> dict:
    """Confidence gate: bucket handles into draftable (EXTRACTED), annotate_only (INFERRED), and
    blocked (AMBIGUOUS / anything else)."""
    buckets = {"draftable": [], "annotate_only": [], "blocked": []}
    for h in handles or []:
        conf = h.get("confidence")
        if conf == "EXTRACTED":
            buckets["draftable"].append(h)
        elif conf == "INFERRED":
            buckets["annotate_only"].append(h)
        else:
            buckets["blocked"].append(h)
    return buckets


def _result(ado_id: str, status: str, tier: str | None, handles: list,
            notes: list, open_question: dict | None = None) -> dict:
    return {
        "ado_id": ado_id,
        "status": status,
        "tier": tier,
        "handles": handles,
        "gated": gate(handles),
        "open_question": open_question,
        "notes": notes,
    }


def resolve(anchor: dict, candidates: list, catalog: dict | None = None,
            vocabulary: dict | None = None) -> dict:
    """Resolve a doc-feature to telemetry query handles via the L0>L1>L2 ladder. `catalog` is the
    effective (seed+runtime) catalog; `candidates` are the judgement layer's proposals. The
    highest-confidence tier that yields >=1 handle wins. Nothing confident -> unresolved +
    open_question (never fabricate). Pure."""
    catalog = catalog or {}
    vocabulary = vocabulary or _DEFAULT_VOCAB
    ado_id = str(anchor.get("ado_id") or "").strip()
    name = anchor.get("feature_name") or anchor.get("title")
    notes: list = []

    # L0 — catalog hit.
    hits = lookup(catalog, ado_id, name)
    if hits:
        handles = []
        for h in hits:
            handles.append(assemble_handle(
                h.get("kind"), h.get("id"), h.get("svc_namespace"),
                h.get("source") or "catalog", "L0",
                confidence=h.get("confidence") or "EXTRACTED"))
        handles = [h for h in handles if h]
        if handles:
            return _result(ado_id, "resolved", "L0", _dedup_handles(handles), notes)

    # L1 (explicit, validated) and L2 (ServiceCatalog inferred) from the candidates.
    l1: list = []
    l2: list = []
    for c in candidates or []:
        tier = _TIER_ALIAS.get((c.get("tier") or "").strip().lower())
        if tier == "L1":
            v = validate_candidate(c, vocabulary)
            if v["ok"]:
                h = assemble_handle(c.get("kind"), v["normalized"], c.get("svc_namespace"),
                                    c.get("source") or "H?:explicit", "L1")
                if h:
                    l1.append(h)
            else:
                notes.append(f"L1 candidate rejected ({v['reason']}): {c.get('id')!r}")
        elif tier == "L2":
            h = assemble_handle(c.get("kind") or "service", c.get("id"), c.get("svc_namespace"),
                                c.get("source") or "servicecatalog", "L2")
            if h:
                l2.append(h)
            else:
                notes.append(f"L2 candidate malformed: {c.get('id')!r}")
        else:
            notes.append(f"candidate ignored (unknown tier {c.get('tier')!r}): {c.get('id')!r}")

    # Highest-confidence tier with handles wins; don't mix tiers (keeps the gate unambiguous).
    if l1:
        if l2:
            notes.append(f"{len(l2)} L2 (INFERRED) candidate(s) set aside in favor of L1 (EXTRACTED)")
        return _result(ado_id, "resolved", "L1", _dedup_handles(l1), notes)
    if l2:
        return _result(ado_id, "resolved", "L2", _dedup_handles(l2), notes)

    # L3 — nothing confident. Surface an open question for the PM (the safety valve, not a failure).
    oq = {
        "topic": "feature_id",
        "ask": f"What's the owning service namespace or ECS feature-flag ID for "
               f"'{name or ado_id or 'this feature'}'? (Atlas H7 needs it to query telemetry.)",
        "needs": "PM confirmation",
    }
    notes.append("No confident telemetry identifier; H7 will record source_unavailable "
                 "(reason: feature_id_unresolved) and ask the PM.")
    return _result(ado_id, "unresolved", None, [], notes, open_question=oq)


def merge(runtime: dict, ado_id: str | int, handles: list, verified: bool = False,
          source: str | None = None, feature_name: str | None = None) -> dict:
    """Persist resolved handles into the runtime catalog (additive + idempotent). On a re-merge,
    handles union (deduped); `verified` only ever flips False->True; `source`/`feature_name`
    update if provided. Returns the mutated runtime dict (caller saves)."""
    aid = str(ado_id).strip()
    entry = runtime.get(aid, {"handles": [], "verified": False})
    entry["handles"] = _dedup_handles(list(entry.get("handles") or []) + list(handles or []))
    if verified:
        entry["verified"] = True
    if source:
        entry["source"] = source
    if feature_name:
        entry["feature_name"] = feature_name
    entry["updated"] = _now()
    runtime[aid] = entry
    return runtime


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _emit(obj: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False))
    else:
        print(json.dumps(obj, indent=2, ensure_ascii=False))


def _explain(res: dict) -> None:
    g = res.get("gated", {})
    lines = [
        f"feature {res.get('ado_id') or '?'}: {res.get('status')} "
        f"(tier {res.get('tier') or '-'})",
        f"  handles: {len(res.get('handles', []))} "
        f"(draftable {len(g.get('draftable', []))}, "
        f"annotate {len(g.get('annotate_only', []))}, "
        f"blocked {len(g.get('blocked', []))})",
    ]
    for h in res.get("handles", []):
        lines.append(f"    - [{h['confidence']}] {h['kind']}:{h['id']}"
                     + (f" @ {h['svc_namespace']}" if h.get("svc_namespace") else "")
                     + f"  ({h['source']})")
    if res.get("open_question"):
        lines.append(f"  open question: {res['open_question']['ask']}")
    for n in res.get("notes", []):
        lines.append(f"  note: {n}")
    print("\n".join(lines), file=sys.stderr)


def _parse_json_arg(raw: str | None, default):
    if raw is None:
        return default
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atlas feature-ID bridge resolver (deterministic).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("resolve", help="Resolve a doc-feature to telemetry query handles.")
    pr.add_argument("--anchor", required=True, help='JSON: {"ado_id","feature_name"/"title"}')
    pr.add_argument("--candidates", default="[]", help="JSON list of candidate handles.")
    pr.add_argument("--seed", default=None, help="Override seed path (known-feature-ids.md).")
    pr.add_argument("--catalog", default=None, help="Override runtime catalog path.")
    pr.add_argument("--explain", action="store_true")
    pr.add_argument("--json", action="store_true")

    pl = sub.add_parser("lookup", help="L0 catalog lookup by ado id / feature name.")
    pl.add_argument("--id", required=True)
    pl.add_argument("--name", default=None)
    pl.add_argument("--seed", default=None)
    pl.add_argument("--catalog", default=None)
    pl.add_argument("--json", action="store_true")

    pv = sub.add_parser("validate", help="Validate one explicit candidate vs the vocabulary.")
    pv.add_argument("--candidate", required=True, help="JSON: a single candidate.")
    pv.add_argument("--seed", default=None)
    pv.add_argument("--json", action="store_true")

    pg = sub.add_parser("gate", help="Bucket handles by confidence.")
    pg.add_argument("--handles", required=True, help="JSON list of handles.")
    pg.add_argument("--json", action="store_true")

    pm = sub.add_parser("merge", help="Persist handles into the runtime catalog.")
    pm.add_argument("--id", required=True)
    pm.add_argument("--handles", required=True, help="JSON list of handles.")
    pm.add_argument("--name", default=None, help="Feature name (for name-based L0 lookup).")
    pm.add_argument("--verified", action="store_true")
    pm.add_argument("--source", default=None)
    pm.add_argument("--catalog", default=None)
    pm.add_argument("--json", action="store_true")

    args = p.parse_args(argv)

    try:
        if args.cmd == "resolve":
            seed = load_seed(args.seed)
            runtime = load_runtime(args.catalog)
            cat = effective_catalog(seed.get("anchors", {}), runtime)
            res = resolve(_parse_json_arg(args.anchor, {}),
                          _parse_json_arg(args.candidates, []),
                          cat, seed.get("vocabulary"))
            if args.explain:
                _explain(res)
            _emit(res, args.json)
            return 0 if res["status"] == "resolved" else 3

        if args.cmd == "lookup":
            seed = load_seed(args.seed)
            runtime = load_runtime(args.catalog)
            cat = effective_catalog(seed.get("anchors", {}), runtime)
            hits = lookup(cat, args.id, args.name)
            _emit({"ado_id": str(args.id), "handles": hits, "hit": bool(hits)}, args.json)
            return 0 if hits else 3

        if args.cmd == "validate":
            seed = load_seed(args.seed)
            res = validate_candidate(_parse_json_arg(args.candidate, {}), seed.get("vocabulary"))
            _emit(res, args.json)
            return 0 if res["ok"] else 1

        if args.cmd == "gate":
            _emit(gate(_parse_json_arg(args.handles, [])), args.json)
            return 0

        if args.cmd == "merge":
            runtime = load_runtime(args.catalog)
            runtime = merge(runtime, args.id, _parse_json_arg(args.handles, []),
                            verified=args.verified, source=args.source, feature_name=args.name)
            path = save_runtime(runtime, args.catalog)
            _emit({"ok": True, "ado_id": str(args.id), "entry": runtime[str(args.id)],
                   "path": str(path)}, args.json)
            return 0
    except json.JSONDecodeError as e:
        print(f"invalid JSON argument: {e}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Summarize explicit, normalized deployment observations without a backend.

This adapter-neutral public module replaces environment-specific query builders.
It neither fetches telemetry nor infers that absence means disabled or removed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(rows: list[dict], handle: dict | None = None) -> dict:
    if not handle or handle.get("confidence") != "EXTRACTED" or not handle.get("id"):
        return {"status": "unavailable", "reason": "No explicitly sourced handle", "observations": []}
    observations = []
    invalid = 0
    for row in rows:
        if row.get("handle") != handle["id"]:
            continue
        if (row.get("state") not in ("enabled", "disabled", "unknown")
                or not row.get("source") or not row.get("observed_at") or not row.get("environment")):
            invalid += 1
            continue
        entry = {k: row[k] for k in ("handle", "environment", "state", "source", "observed_at")}
        if entry not in observations:
            observations.append(entry)
    if not observations:
        return {"status": "unavailable", "reason": "No valid observations for the handle", "observations": [], "invalid_rows": invalid}
    states = {}
    for entry in observations:
        states.setdefault(entry["environment"], set()).add(entry["state"])
    conflicts = sorted(environment for environment, values in states.items()
                       if "enabled" in values and "disabled" in values)
    return {"status": "conflict" if conflicts else "observed", "observations": observations,
            "conflicting_environments": conflicts, "invalid_rows": invalid,
            "note": "Observed environments only; no global availability claim."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON containing rows and handle")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    print(json.dumps(summarize(payload.get("rows", []), payload.get("handle")), indent=2))

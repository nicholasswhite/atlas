# Feature identifier bridge

A documentation feature's ID is not automatically a telemetry handle. The
resolver accepts explicitly sourced candidates and a reviewed vocabulary.

1. **L0:** Reuse a previously verified catalog entry, preserving its confidence.
2. **L1:** Validate an explicit identifier against the vocabulary.
3. **L2:** Retain a service-catalog inference as annotation-only.
4. **Unresolved:** Return an open question and no invented handle.

The resolver performs no live query. The fallback vocabulary is fictional demo
data; a connected deployment must replace it with an approved source. `gate`
separates extracted/draftable handles, inferred/annotation-only handles, and
ambiguous/blocked handles. A valid string format is not proof of access or product
availability.

[`telemetry.py`](../scripts/telemetry.py) consumes explicit normalized observations
after the handle check. Missing rows stay unavailable, conflicting states remain
conflicting, and observed environments do not imply a global release.

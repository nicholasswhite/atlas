# Dossier and patch shape

The Python store retains its established schema. `ado_id` is a numeric feature
key; it does not imply a connected account. Fields such as writer and release
are current anchor observations. A patch is additive; omitted fields stay intact.

```json
{
  "scope": {
    "shipping": [{"claim": "Desktop preview", "sources": ["H5:spec"], "confidence": "EXTRACTED"}],
    "out_of_scope": [{"claim": "Mobile", "sources": ["H5:spec"], "confidence": "EXTRACTED"}]
  },
  "facts": [{"field": "requirement", "value": "Version 5", "sources": ["H5:spec"], "confidence": "EXTRACTED"}],
  "dates": [{"value": "2610", "sources": ["H1:work-item"], "confidence": "EXTRACTED"}],
  "conflicts": [{"topic": "availability", "positions": [
    {"value": "preview", "sources": ["H5:spec"]},
    {"value": "general availability", "sources": ["H2:proposal"]}
  ], "resolution": null}],
  "sources": {"reached": ["H1", "H5"], "unavailable": [{"tag": "H7", "reason": "No verified handle"}]},
  "open_questions": ["Confirm release scope."]
}
```

`EXTRACTED` means an explicit source assertion; `INFERRED` is a reasoned link;
`AMBIGUOUS` preserves uncertainty. Interpretation remains the agent/reviewer's
responsibility. Only the source's own scope is supported.

Repeated facts are merged by field and value, preserving source lists. Conflict
positions are retained, and an existing nonempty resolution is not replaced by
a null patch. Coverage collapses granular source IDs to the H1–H10 source class,
while individual claims retain their specific references. See
[`examples/aurora-sync.json`](../examples/aurora-sync.json) for runnable input.

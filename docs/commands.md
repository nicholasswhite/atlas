# Command reference

Run commands from the repository root with Python 3.11 or newer. All modules use
the standard library. Each module supports `--help`.

| Command | Result |
| --- | --- |
| `python scripts/demo.py --output demo-output` | Complete fictional run in a new directory, including HTML walkthrough. |
| `python tests/run_all.py` | Each test suite in a separate process. |
| `python scripts/dossier_store.py --help` | Initialize, merge, validate/render and load a persistent dossier. |
| `python scripts/lifecycle.py --signals-file signals.json` | Compute the next action from supplied evidence. |
| `python scripts/doc_footprint.py --help` | Reconcile a document scan and graph. |
| `python scripts/doc_edit_plan.py --help` | Group proposed edits and typed handoffs. |
| `python scripts/owner_gate.py --help` | Read local ownership metadata against an explicit roster. |
| `python scripts/landscape.py --help` | Group related work and shared release announcements. |
| `python scripts/crw_tags.py --help` | Transform a tracking tag; does not verify approval or write a work item. |
| `python scripts/timeline_move.py --help` | Plan a release move from supplied branch/timeline evidence. |
| `python scripts/parse_vtt.py --help` | Parse a supplied transcript and optionally select topic windows. |
| `python scripts/feature_id_resolver.py --help` | Resolve explicitly sourced candidate handles against a vocabulary. |
| `python scripts/telemetry.py observations.json` | Summarize normalized observations without querying a backend. |
| `python scripts/wrap_untrusted.py --source H5:spec --body "Example"` | Serialize supplied text as a source-labeled XML data envelope. |

Stateful modules default to ignored `state/` under this checkout. Set
`ATLAS_STATE_DIR` to select a dedicated state directory before importing them.
Their files contain the input you supply; choose an appropriate private location
for non-demo work. The example provides only fictional data.

The dossier merge API expects claim objects with source lists, not bare strings.
Use [the example](../examples/aurora-sync.json) and
[schema reference](../references/dossier-schema.md) as a starting point.

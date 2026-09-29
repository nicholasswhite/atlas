# Connecting an agent host

All live connections are supplied by the host. Keep credentials and actual
authorization outside the repository. Enabling a skill or setting
`simulation_only` to false does not grant permission or create an executor.

## Read contracts

| Source | Minimum useful return value |
| --- | --- |
| Work tracking | Stable feature ID, title, writer, related work, versioned fields, discussion references. |
| Specifications | Source locator, observed version/date, relevant text, applicable product scope. |
| Conversations | Authorized thread excerpt, speaker identity, timestamp, provenance; omit unrelated personal data. |
| Transcript | Consented, supplied VTT text or bounded excerpts. `parse_vtt.py` parses local input only. |
| Documentation | Checked revision, article paths, metadata, and published/staged observations. |
| Scan and graph | Candidate article paths, scores or evidence class, ownership hints, and source references. |
| Deployment observations | Explicitly evidenced handle plus normalized environment/state/source/time rows. |
| Prior decisions | Source-linked local feature notes and unresolved review tasks. |

Every read returns either evidence or an explicit unavailable/error state. A
failed read does not establish absence, deletion, approval, or general availability.
Separate source access from evidence completeness and interpretation confidence.

## Normalized telemetry

`telemetry.py` accepts JSON with `handle` and `rows`:

```json
{
  "handle": {"id": "AuroraSync", "confidence": "EXTRACTED"},
  "rows": [{"handle": "AuroraSync", "environment": "preview", "state": "enabled",
            "source": "fixture:deployment-observation", "observed_at": "2026-01-01T00:00:00Z"}]
}
```

States are `enabled`, `disabled`, or `unknown`. Conflicting observations remain
conflicting; an observed preview environment says nothing about every customer.
No backend query, service namespace, or account access is included.

## Write contracts for a future deployment

A real executor must accept a bounded plan, verify current authorization and
ownership, bind approval to the actual content/revision, check the simulation
setting, and return a durable result. Record uncertainty after an interrupted
write; do not convert a timeout into a retry or success.

Treat updating a work field, editing a document, staging a branch, merging a pull
request, publishing, and sending a message as separate operations. The public
Python planners do none of them. Final publication and messages remain explicit
review points in the provided workflow. Host policy and owner instructions take
precedence over the example skill.

## Compatibility vocabulary

`ado_id` is the historical numeric feature key (five to nine digits), not an
Azure connection. `crw` is a draft field that may hold HTML tracking markers.
`id` means in-development content; `wn` means release announcements. `ms.author`
and `ms.topic` are supported DocFX metadata names; ownership can also be read
from `author` front matter. Adapters map their own schema into these fields.

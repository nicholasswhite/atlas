# Atlas

### Carry a product feature through a documentation workflow you can inspect.

Atlas brings the work around a release into one connected process: gather the
context, preserve the evidence, draft the announcement, find the affected
documentation, and prepare the changes for review. Unclear facts and ownership
boundaries become explicit handoffs.

**This public edition contains the working Python planning and state modules,
portable agent workflow instructions, and an offline demonstration.** The demo
requires Python 3.11 or newer and no packages, model account, or credentials.

```text
Feature intake → Related work → Source gathering → Evidence dossier
                                                        ↓
Review & handoffs ← Staging preview ← Document plan ← Draft
```

## Run the whole example

From a local checkout:

```console
python scripts/demo.py --output demo-output
```

Open **`demo-output/index.html`** for a visual walkthrough, then inspect the
dossier and proposed changes. The directory must be new; the demo never replaces
existing output. Run `python scripts/demo.py` to use and remove temporary output.

The fictional **Aurora Sync** release exercises decisions that matter:

- Source-linked claims merge without duplicating on a second pass.
- Missing production evidence stays missing; preview does not become general availability.
- A document supported by the scan and graph can become a proposed edit after an ownership recheck.
- Another writer's document, an unassigned article, and a broad overview go to review.
- Approval advances the release state; an absent or mismatched approval holds staging.
- The final result is a local staging preview and an unsent notification draft.

The example uses a plainly labeled **template draft**, not an AI model. It runs
the actual state and planning code. For the AI workflow, an agent host performs
source interpretation, drafting, and review according to the included
[pipeline skill](skills/atlas-pipeline/SKILL.md).

## What is inside

| Part | What it does |
| --- | --- |
| Evidence dossier | Persists sources, claims, confidence, conflicts, missing inputs, and draft state as JSON and readable Markdown. |
| Release relationships | Groups shared announcements and related features before processing a worklist. |
| Lifecycle resolver | Distinguishes drafted, approved, staged, and published evidence; surfaces inconsistent signals. |
| Documentation impact | Reconciles a scan with a document graph, then separates credible edits from tangential or broad matches. |
| Ownership and edit plan | Checks configured writers and article metadata, batches proposed changes, and queues cross-owner work. |
| Continuity | Keeps feature notes, assignment-notification state, timeline-move plans, and review handoffs. |
| Source helpers | Parses supplied transcripts, resolves explicitly evidenced identifiers, and summarizes normalized deployment observations. |
| Agent instructions | Describes the complete research-to-review workflow and the contracts a real adapter must meet. |

[Architecture](docs/architecture.md) · [Adapter contracts](docs/adapters.md) ·
[Command reference](docs/commands.md) · [Maturity and boundaries](docs/limitations.md)

## Why this exists

Release writing often starts with fragments: a work item, a specification,
review comments, a changed release date, and documentation owned by several
people. A fluent paragraph is only one part of finishing that work.

Nicholas White designed Atlas to preserve that surrounding context and carry it
forward. He developed and refined the original workflow with AI coding
assistance, drawing on his product-documentation work. The public edition keeps
the core decisions inspectable while replacing organization-specific material
with portable configuration and fictional examples.

## What has been demonstrated

The original workflow was exercised in supervised dry runs and deterministic
offline tests; production writes remained disabled. This edition demonstrates
the planning and state behavior locally. It does not include connected enterprise
adapters, a background service, or a production publishing executor. Flipping a
configuration flag does not create those capabilities.

Source labels and confidence are evidence aids, not a guarantee of truth. The
external-content envelope makes data boundaries visible; it does not guarantee
resistance to prompt injection. An adapter must enforce actual access and write
permissions independently of the agent's instructions.

## Verify

```console
python tests/run_all.py
python scripts/demo.py
```

Tests cover repeatable merges, conflicting states, ownership decisions, document
impact, approval-tag transforms, missing identifiers, transcript parsing, and the
public example. The CI configuration runs these checks on Windows and Linux.
See [verification](VERIFICATION.md) for the dated local result; configured CI is
not a claim about a particular hosted run.

## Provenance and reuse

This is a standalone public-source edition with a new history. It contains no
original work records, employer repositories, contact roster, or connected
credentials. [Provenance and attribution](NOTICE.md) explains what was retained,
generalized, replaced, and excluded. No general open-source license is granted
by this release; existing rights remain with their respective holders.

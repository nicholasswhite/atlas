# Documentation impact and edit planning

Discovery and execution are separate. The reconciler accepts a lexical/semantic
scan and a documentation graph, each supplied by an explicit adapter or fixture.
Scores and inferred links are candidate evidence, not permission to edit.

The reconciler normalizes paths, merges sources, checks agreement, records
ownership hints and confidence, and demotes broad overview pages or off-platform
matches. The planner turns this into bounded batches and typed handoffs:

- Supported, owned target: proposed edit after an actual metadata recheck.
- Inferred target: suggested edit for a reviewer.
- Another owner's target: cross-owner handoff.
- Unknown owner: resolve ownership first.
- Broad overview/hub: review relevance instead of editing on graph centrality.

The modules do not include the original host's scan service, graph generator, or
RepoEditor. The offline demo supplies both candidate sets and creates only local
proposed files. A connected host supplies real discovery and an authorized
executor; final publishing remains a separate decision.

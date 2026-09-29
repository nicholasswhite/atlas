# Maturity and boundaries

The public edition is an inspectable workflow implementation and integration
reference. It has a runnable offline example and deterministic tests. The
original workflow had supervised dry runs; production writes stayed disabled.

- **Agent host required for AI work.** The Python modules do not interpret a
  product specification or generate an LLM draft. The skill describes that work.
- **Adapters are explicit integration work.** Enterprise services, authorization,
  live telemetry queries, transcript fetching, and publishing are not bundled.
- **No background service.** Nothing wakes up or schedules itself.
- **Local state, not a distributed coordinator.** Atomic file replacement does
  not create a distributed lock. Serialize writers; review interrupted external
  attempts before retrying them.
- **Plans are proposals.** A document path, confidence label, or configured owner
  is not sufficient authority to edit a real repository.
- **Evidence requires judgment.** `EXTRACTED` says a source explicitly states a
  claim; it does not prove the source is correct or current. The scan/graph
  heuristics are not a trained relevance model.
- **Boundaries are layered.** XML serialization prevents text from becoming XML
  structure. It cannot make an LLM immune to malicious instructions in content.
- **Tests have a defined scope.** Offline fixtures exercise mechanics and failure
  paths; they do not establish customer adoption, time savings, model quality,
  cloud reliability, or successful production publication.

The release retains some historical schema names for compatibility. The included
roster and service vocabulary are fictional, and unknown ownership defaults to
review. The simulation setting is an integration contract; a real write adapter
must enforce it in code.

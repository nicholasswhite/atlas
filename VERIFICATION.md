# Verification

The public edition is verified locally with Python 3.11 or newer using only the
standard library. Run the commands below from a clean checkout:

```console
python tests/run_all.py
python scripts/demo.py
```

The example uses fictional data and temporary state by default. Its draft is a
template, while the actual Python modules perform dossier merging, lifecycle
resolution, document-impact reconciliation, ownership checks, review routing,
and local staging preparation. Tests also hold staging on an invalid approval,
reject escaping fixture paths, preserve existing output directories, and block
network use during the public example.

The test suite does not establish connected service availability, model research
quality, production publication, or a successful remote CI run. See
[maturity and boundaries](docs/limitations.md).

## Local release-candidate check — September 29, 2026

- All **19 independent test suites passed** on Windows with the available
  Python interpreter.
- The temporary offline demo passed: repeat merging retained three fixture
  facts, one article was eligible for a proposed edit, and three articles were
  routed to overview, cross-owner, and unknown-owner review.
- The retained-output variant produced the HTML walkthrough, source envelopes,
  dossier, proposed document, staging preview, lifecycle states, and unsent notice.
- Relative Markdown documentation links resolved to files in the release.
- The public tree was checked for original staff identifiers, source work-item
  examples, private service endpoints, and machine paths; the checked patterns
  were absent after replacement with fictional material.

These results describe this local candidate. The Windows/Linux CI matrix is
configured but was not executed as a hosted workflow by this local check.

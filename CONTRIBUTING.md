# Contributing

Thank you for helping make this research easier to inspect and reuse. Useful contributions include clearer documentation, bug fixes for analysis tools, and evidence-backed corrections to artifact descriptions.

## Report an issue

Include the affected file or command, Python version, expected behavior, and a minimal example or error message. For a research discrepancy, identify the benchmark, model, inference mode, task ID, and the relevant paper table or artifact. Remove credentials and unrelated private data before sharing logs.

## Submit a change

1. Fork the repository and create a branch for one focused change.
2. Keep the README, project homepage, and documentation in English. Preserve experimental prompts unless the change explicitly studies their effect.
3. Explain the resulting behavior and how you checked it in your pull request.
4. Run the offline checks from the repository root:

   ```bash
   python -m unittest discover -s tests -v
   python -m compileall -q scripts tests
   python scripts/verify_artifacts.py
   python scripts/summarize_results.py --format json --output outputs/summary.json
   ```

No API credentials or paid model calls are needed for these checks. Add a focused regression test when fixing behavior that could silently alter research results.

## Preserve research provenance

Treat `results/` as a historical archive. Do not overwrite archived records with new runs or modify a checksum just to make verification pass. Propose corrections with their source and rationale, and explain any necessary manifest change. Place exploratory outputs in the ignored `outputs/` directory.

Keep paper-transcribed values separate from recomputed metrics. Report the evaluated task count, missing records, and the denominator for label counts. A task can have multiple error labels, and `UNKNOWN` is a fallback rather than an additional taxonomy subtype.

For new experiments, record dataset and evaluator revisions, environment details, model/provider identifiers, inference settings, sanitization, and failure-handling behavior. Label the run as a new experiment rather than a reproduction of the historical API outputs.

## Licenses

Original contributions are offered under the repository's MIT license. Retain upstream attribution and the separate terms for paper and benchmark-derived material described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

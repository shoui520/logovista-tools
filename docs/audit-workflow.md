# lvcore Audit Workflow

`lvcore-audit` is the deterministic corpus harness for `src/lvcore-experimental`.
The reader package owns open/search/entry/render/resource behavior. Audit owns
validation, scorecards, corpus baselines, and diff judgment.

Run from the repository root:

```bash
export LV_CORPUS_PATH=/path/to/LOGOVISTA_SSED_DICTS_WINDOWS
export PYTHONPATH=src/lvcore-experimental:src/lvcore-audit

python3 -m lvcore_audit corpus "$LV_CORPUS_PATH" --jobs 0 --full \
  --output /tmp/lvcore_audit_current.json

python3 -m lvcore_audit diff tests/corpus_baseline.json \
  /tmp/lvcore_audit_current.json --strict
```

`lvcore_audit corpus` output is canonical JSON: sorted keys, deterministic row
ordering, no timestamps, and package identifiers instead of absolute corpus
paths. Private per-package outputs can be written with `--output-dir`.

## Diff Decisions

When the strict diff is nonempty, classify each delta before changing the
baseline:

| Delta shape | Verdict | Action |
|---|---|---|
| Counter moved from reader-side to audit-side with the same total | Intentional | Document in the commit message and accept. |
| Counter disappears because the underlying feature was cut in the phase | Intentional | Document in the commit message and accept. |
| New diagnostic code explicitly added by the phase | Intentional | Document in the commit message and accept. |
| Resolved counter goes up | Intentional improvement | Document in the commit message and accept. |
| Resolved counter goes down | Regression | Fix the code. |
| Unresolved or error counter goes up outside the phase plan | Regression | Fix the code. |
| Native sampled search misses go up after native-only fallback cuts | Expected | Record the honest native-only baseline. |
| Schema field is renamed by a typed dataclass refactor | Intentional | Bump the affected schema version and document. |
| `total_packages` changes | Regression | Investigate; the corpus target is fixed. |

If a delta does not fit these rules, record the uncertainty in the phase report,
make the best defensible call, and keep the baseline honest.

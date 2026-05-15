# lvcore Budgeted Corpus Baseline

This baseline exercises deterministic partial-scan behavior for the
`--max-bytes-per-scan` audit path. It is not the full compatibility baseline.

- Capture date: 2026-05-14
- Audit schema: `lvcore.audit.corpus.v1`
- Corpus shape: 170 package directories, 161 detected SSED packages, 9 unknown
  resource/helper directories
- Sample limits: `--sample-entries 1 --sample-search-hits 1`
- Scan budget: `--max-bytes-per-scan 65536`
- Observed budget diagnostics: `scan_truncated=2`
- Scorecard status: `blocked_by_diagnostics` means the budgeted audit still has
  diagnostic counters such as sampled native search misses, but no named
  residual packages. This status label was corrected after Phase 4 without
  changing package counts or counters.
- Body-source rows include `schema: lvcore.body_source.v1` and
  `model_version: 1`, matching the full baseline serialization contract.
- Native exact-search candidate fix: budgeted sampled misses dropped from 32
  to 29, while `scan_truncated` rose from 2 to 29. This is intentional for the
  64 KiB budget path: exact search no longer range-stops after the first
  out-of-range seek candidate, so a tiny audit budget may truncate additional
  multi-candidate probes instead of accepting a premature miss. The unbudgeted
  full baseline is the compatibility contract and now has zero sampled native
  search misses.
- Reader/audit boundary hardening: public body-source JSON no longer includes
  sidecar table names, column names, row counts, or per-table summaries.
  Budgeted counters are unchanged; only the per-target body-source schema was
  narrowed.

Capture command:

```bash
export PYTHONPATH=src/lvcore-experimental:src/lvcore-audit
python3 -m lvcore_audit corpus "$LV_CORPUS_PATH" --jobs 0 \
  --sample-entries 1 --sample-search-hits 1 --max-bytes-per-scan 65536 \
  --output tests/corpus_baseline_budgeted.json
```

The expected result is deterministic partial output with explicit
`scan_truncated` diagnostics where a sampled scan hits the byte budget.

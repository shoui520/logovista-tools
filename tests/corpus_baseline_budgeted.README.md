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

Capture command:

```bash
export PYTHONPATH=src/lvcore-experimental:src/lvcore-audit
python3 -m lvcore_audit corpus "$LV_CORPUS_PATH" --jobs 0 \
  --sample-entries 1 --sample-search-hits 1 --max-bytes-per-scan 65536 \
  --output tests/corpus_baseline_budgeted.json
```

The expected result is deterministic partial output with explicit
`scan_truncated` diagnostics where a sampled scan hits the byte budget.

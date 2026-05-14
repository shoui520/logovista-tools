# lvcore Corpus Baseline

This baseline is the Phase 0 audit-extraction baseline for
`src/lvcore-experimental`.

- Capture date: 2026-05-14
- Audit schema: `lvcore.audit.corpus.v1`
- Capture source: the Phase 0 commit containing this file, based on
  `322dab6f0b80280ab62ff2076fba04fd64b5b822`
- Corpus shape: 170 package directories, 161 detected SSED packages, 9 unknown
  resource/helper directories

Capture command:

```bash
export PYTHONPATH=src/lvcore-experimental:src/lvcore-audit
python3 -m lvcore_audit corpus "$LV_CORPUS_PATH" --jobs 0 --full \
  --output tests/corpus_baseline.json
```

The future Rust port's day-one compatibility check is:

```bash
python3 -m lvcore_audit diff tests/corpus_baseline.json \
  <rust-audit-output.json> --strict
```

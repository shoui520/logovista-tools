# lvcore Corpus Baseline

This baseline is the Phase 0 audit-extraction baseline for
`src/lvcore-experimental`.

- Capture date: 2026-05-14
- Audit schema: `lvcore.audit.corpus.v1`
- Capture source: the Phase 0 commit containing this file, based on
  `322dab6f0b80280ab62ff2076fba04fd64b5b822`
- Corpus shape: 170 package directories, 161 detected SSED packages, 9 unknown
  resource/helper directories

Intentional update history:

- Phase: Phase 1 reader-path cuts
- Native title/body-heading heuristic: body bytes are no longer promoted to
  resolved search headings. Title pointers that point at HONMON are now counted
  as `title_pointer_is_body_pointer` fallback rows, with the native index
  display key retained as the app-facing heading.
- Sidecar supplements: reader-attached supplement blocks were removed from
  `EntryDocument`; sidecar roles, address matches, and row counts are now audit
  counters only. This intentionally lowers reader-rendered link counts that
  previously came only from synthetic sidecar supplement blocks.
- Sidecar candidates: non-SQLite sidecar candidates are now counted by the
  audit harness rather than reader telemetry, so `non_sqlite_or_unknown`
  appears under unsupported role counts as a non-compatibility-significant
  category.

Latest intentional update:

- Phase: Phase 3 composition/lazy/deterministic discovery
- Gaiji source discovery: package-matched sibling `*_GAIJI` image directories
  are now part of deterministic gaiji source resolution. This changes
  `_DCT_KANJIGN5` image-resource inventory from 14 to 506 because its sibling
  gaiji image directory is now visible to the reader. Sampled resolved gaiji,
  unresolved gaiji, media, link, body-source, and search counters are unchanged;
  this is an inventory expansion, not a compatibility regression.

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

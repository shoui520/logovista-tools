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

Intentional update:

- Phase: Phase 3 composition/lazy/deterministic discovery
- Gaiji source discovery: package-matched sibling `*_GAIJI` image directories
  are now part of deterministic gaiji source resolution. This changes
  `_DCT_KANJIGN5` image-resource inventory from 14 to 506 because its sibling
  gaiji image directory is now visible to the reader. Sampled resolved gaiji,
  unresolved gaiji, media, link, body-source, and search counters are unchanged;
  this is an inventory expansion, not a compatibility regression.

Latest intentional update:

- Phase: Phase 4 schema/version invariants
- Public body-source dictionaries now include `schema:
  lvcore.body_source.v1` and `model_version: 1`. No counters, package counts,
  sampled render/search results, or resolved/unresolved resource counts changed;
  this is a serialization contract update for the Rust-port audit baseline.

Latest intentional update:

- Phase: review hardening after Phase 4
- The closure scorecard status now reports `blocked_by_diagnostics` when the
  only remaining blockers are diagnostic counters such as sampled native search
  misses. It reserves `blocked_by_named_residuals` for actual named residual
  packages/sidecars. No counters or package classifications changed.

Latest intentional update:

- Phase: native exact-search candidate fix
- Exact search no longer stops the whole component after an out-of-range row
  from one byte-seek candidate. Real SSED exact probes can visit multiple
  candidate leaf regions for raw, normalized, symbol, and kana-folded keys; a
  later candidate may contain the matching native row.
- `sample_search_miss` / `native_search_misses` dropped from 433 to 0. This is
  an intentional compatibility improvement, not a sampling change.
- Sampled search hits dereferenced/rendered rose from 549 to 982 because the
  formerly missed native rows now resolve to entries. Secondary counters for
  title resolution, gaiji/media/link resources, sidecar bodies, and opcode
  diagnostics also rose because those entries are now actually rendered by the
  audit.
- The closure scorecard status changed from `blocked_by_diagnostics` to
  `closure_ready_for_deeper_audit` because no sampled native search misses,
  true display-unresolved gaiji, unresolved media, hard SSED failures, or
  named residual blockers remain in this baseline. A small number of sampled
  unresolved link-target diagnostics remains nonblocking audit telemetry.

Latest intentional update:

- Phase: reader/audit boundary hardening
- Public `BodySourceInfo.to_dict(debug=False)` no longer serializes sidecar
  table names, column names, row counts, or per-table summaries. It keeps only
  sidecar name/kind/storage/role/support status plus a table count. Full
  sidecar schema remains available through explicit debug output.
- The corpus counters did not change. The baseline diff is a schema narrowing
  in per-target `body_source.sidecars[]` rows only.

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

# lvcore Review Checklist

Use this checklist when reviewing changes under `src/lvcore-experimental`.

## Scope

- lvcore remains reader-only.
- lvcore does not add writer, authoring, repacking, or importer behavior.
- lvcore does not import or shell out to the older research toolkit.
- LVED and LVLMultiView detection remains separate from SSED body-source
  classification.
- Dense or sidecar-backed HONMON remains an SSED reader concern, not a deferred
  package-family bucket.

## Public API Shape

- Friendly APIs expose Package, SearchResults, SearchHit, Entry,
  EntryDocument, Resource, Diagnostic, and BodySource concepts.
- SearchHit exposes reader-facing heading, heading source, and title status.
- Raw page/row numbers, pointers, component internals, control payloads, and
  byte summaries appear only through explicit debug or inspection output.
- EntryDocument, LinkTarget, and ResourceRef changes stay enum/string-field
  friendly so a future Rust API and opaque C ABI can represent them cleanly.

## Rendering And Diagnostics

- Friendly HTML and plain text remain readable and escaped.
- Friendly output does not leak raw opcodes, offsets, dense-anchor bytes,
  title/body pointers, gaiji codes, or media payload bytes.
- Debug output can expose bounded control IDs, pointer details, sidecar mapping
  details, span summaries, and diagnostics.
- Recoverable body, title, opcode, gaiji, media, and link issues become
  diagnostics rather than uncaught exceptions.
- Unknown or unsupported controls are hidden from friendly output and visible in
  debug output.

## Corpus Validation

- `validate` and `corpus-validate` keep stable JSON fields for package family,
  body source, sidecar roles, resources, title dereference, diagnostics, and
  sample limits.
- Title dereference reports distinguish resolved titles, expected fallback
  headings, and real failures.
- Sidecar reports distinguish body-critical schemas from media/resource,
  examples/idioms, search, kanji-support, ancillary, non-SQLite, and unknown
  roles.
- Full-corpus reports are private artifacts and are not committed.

## Public/Private Boundary

- Public docs, tests, and comments do not name private research sources.
- Tests use tiny synthetic fixtures, not private dictionary text.
- Generated dictionaries, private reports, rendered private HTML, logs,
  `__pycache__`, and `.pytest_cache` are not committed.
- Public docs avoid overclaiming renderer parity or full corpus compatibility
  beyond measured validation results.

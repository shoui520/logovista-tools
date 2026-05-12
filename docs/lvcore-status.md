# lvcore Status

`src/lvcore-experimental` is a clean Python reader-core proof of concept. It is
reader-only, does not import `logovista_tools`, and is judged against real
LogoVista reader behavior rather than writer-generated fixtures.

This page is intentionally separate from the `logovista-tools` toolkit status.
The toolkit can classify and inspect package families that lvcore has not
implemented as reader paths.

## Package-Family Scope

| Family / Layer | lvcore status | Notes |
|---|---|---|
| SSED package detection and loading | Active target | Includes SSEDINFO/SSEDDATA parsing, component reads, and package diagnostics. |
| Direct body-stream SSED | Supported for observed reader cases | Search hits can dereference to entries and render friendly/plain/debug output. |
| Dense/sidecar SSED | Supported where structural providers are known | Treated as SSED body-source variants, not as LVED or LVLMultiView. |
| SSED native indexes/search/titles | Active target with current sampled misses closed | Exact/forward/backward lookup, grouped rows, title fallback, and debug diagnostics are modeled. |
| SSED links/media/resources/gaiji | Active target with current known blockers closed | Media/resource bytes remain untouched and require explicit resource APIs. |
| LVED/WebView2 | Detected/classified only | Separate SQLCipher/SQLite family; no lvcore reader path yet. |
| LVLMultiView | Detected/classified only | Separate SQLite/viewer-resource family; no lvcore reader path yet. |
| Writer/importer behavior | Out of scope | Writer research stays in `logovista-tools`. |

## Current SSED Reader Position

The latest local SSED closure audit reported:

```text
SSED packages:                         162
hard SSED reader compatibility blockers: 0
native sampled search misses:            0
unresolved gaiji/media/link:             0
```

One local package copy is treated as a broken package/component-integrity
residual, not a format-support blocker. Do not describe SSED as universally
closed outside the audited corpus; the correct claim is closure-ready for the
current known SSED reader targets.

## Reader Features

Current lvcore SSED coverage includes:

- package-family detection for SSED, LVED, and LVLMultiView;
- SSEDINFO/SSEDDATA parsing, component reads, and LogoFontCipher handling where
  implemented;
- text-span parsing with friendly, semantic, LogoVista-like, and debug render
  profiles;
- native exact/forward/backward index lookup;
- title-pointer heading resolution and safe fallback headings;
- direct body-stream, dense-anchor, dense-marker, renderer-sidecar, and
  supported SQLite sidecar body-source handling;
- structured `SearchResults`, `SearchHit`, `EntryDocument`, `LinkTarget`, and
  `ResourceRef` concepts;
- `COLSCR.DIC`, `PCMDATA.DIC`, sidecar BLOB media, GA16/GAI16, image-backed
  gaiji, and Unicode gaiji resource handling;
- reason-level diagnostics for unsupported or malformed structures.

These app-facing structures remain experimental. They are deliberately simple
enough to inform a future Rust/C ABI model, but they are not stable public API.

## Friendly / Debug Boundary

Friendly rendering must not expose raw opcodes, payload bytes, SQL rows,
pointers, offsets, or private internals. It may show readable text, safe
placeholders, and `lvcore-resource://...` / `lvcore-entry://...` style
application URLs.

Debug/developer output may expose decoded payload fields, raw pointers,
component names, sidecar table/row mappings, diagnostics, offsets, lengths, and
bounded structural details. Raw media/gaiji bytes are only exposed through
explicit resource-byte APIs or CLI output paths.

## Validation Command

```bash
PYTHONPATH=src/lvcore-experimental python3 -m lvcore corpus-validate \
  /path/to/corpus \
  --json --full --jobs 0 --progress --output-dir out/lvcore-corpus
```

The validator reports package-family counts, SSED body-source/render-support
counts, native search sampling, title status, media/link/gaiji resource
counters, sidecar-role counters, index parser diagnostics, body decode
telemetry, and top blockers.

## Boundary With Toolkit Status

- `logovista-tools` package classification/model generation can cover SSED,
  LVED, LVLMultiView, SIZK, and wrapper files.
- lvcore reader compatibility currently targets SSED.
- LVED/LVLMultiView being unimplemented in lvcore does not mean the toolkit
  cannot classify or inspect those families.

# Project Status and Roadmap

The project is a **research alpha**. Many observed dictionaries are supported,
but the internal model is still being refined as the corpus grows.

The important boundary: this is no longer mainly an exporter. Exporters are
views over the model. The core deliverable is a lossless, evidence-preserving
LogoVista dictionary model.

## Stable / High Confidence

- `SSEDINFO` `.IDX` parsing.
- `SSEDDATA` `.DIC` expansion.
- EPWING-like component block composition.
- JIS X 0208 text decoding.
- CP932 / Shift_JIS-2004 extension cell fallback for observed JIS-row symbols
  such as circled numbers, unit glyphs, and dingbat-like markers.
- Body-stream `HONMON.DIC` extraction for supported dictionaries.
- Index-derived body boundaries for entries whose first section is not `0001`.
- Common `*TITLE.DIC` extraction.
- Observed `*INDEX.DIC` branch-page and leaf-row parsing for forward,
  backward, keyword, cross-reference, body-only, alternate, and text-like
  index components.
- Dictionary-local `.uni` gaiji mapping, including UTF-16 surrogate-pair
  sequences, older 12-byte `.uni` files, and explicit trailer accounting.
- `GA16HALF` / `GA16FULL` bitmap header parsing, glyph slicing, and PNG
  rendering, including packages where glyph slots are addressed by `.uni`
  record order rather than only by the sequential header range.
- Full-corpus byte accounting for observed `MENU.DIC`, `*TITLE.DIC`,
  `*INDEX.DIC`, `.uni`, `GA16*`, `COLSCR.DIC`, and `PCMDATA.DIC`
  components.

## Supported / Corpus-Inferred

- Dense HONMON ID-table detection.
- Raw HONMON numeric ID decoding for `DictFULLDB`, renderer DB, and Android DB
  dereference paths.
- Observed Windows LogoFontCipher AES-CBC decryption for encrypted
  `HONMON.DIC` and sidecars, including large streaming sidecars.
- Windows `EXINFO.INI` parsing and CP932 auxiliary text-index extraction,
  including sibling eight-hex-digit `00000xxx.idx` sidecar trees.
- Windows renderer SQLite extraction through raw HONMON ID anchors and
  `t_contents` rows, with optional `media` BLOB export.
- Android body DB extraction through raw HONMON ID anchors and the observed
  `rowid * 5` mapping.
- Structured `MENU.DIC` extraction with menu hierarchy, link labels,
  packed-BCD destination pointers, and named component/body targets.
- `COLSCR.DIC` media pointer decoding and referenced BMP/JPEG/PNG extraction.
- `PCMDATA.DIC` audio/media pointer decoding, unreferenced-record discovery,
  referenced-range byte coverage, and portable WAV/MP3 writing for classified
  payloads.
- Package image discovery from iOS `img`, Windows `Templates` / `HANREI/img`,
  and Android resource folders.
- SQL/`DictFULLDB`-assisted gaiji validation reports.
- Raw-resource gaiji readiness reports that separate Unicode mappings,
  bitmap-backed glyphs, image-backed glyphs, probable formatting helpers,
  missing search fallbacks, and true display-unresolved codes.
- Standalone `SPINDEX.DIC` inspection for observed Windows suffix-index
  resources.
- LVED/WebView2 `main.data` / `.dbc` SQLCipher classification and validation
  for observed OXFPEU4/KQCMPROS packages.
- `--jobs` process-level parallelism for corpus-scale scanning, extraction,
  audit, gaiji/media reports, LVED inspection, and GA16 rendering.
- Redacted SSED package profiles with component metadata, wrapper/resource
  counts, body-source hints, index parse metrics, control-opcode censuses, and
  lossless sampled decode metrics.
- Full-stream `HONMON.DIC` byte accounting with redacted per-dictionary reports
  and corpus summaries.
- Entry-level lossless span JSONL preserving raw offsets/bytes for controls,
  JIS text, gaiji, media references, padding, and measured problem spans.
- Draft `LV-IR v0` model that names the shared package/component/address/
  entry/span/control/gaiji/media/index/title/menu/issue records future
  exporters and writer experiments should consume.
- Corpus capability matrix generation from redacted `profile`,
  `honmon-bytes`, `component-forensics`, and optional `gaiji-readiness`
  outputs.
- Strict, forensic, and lenient text-span parsing modes for sampled body
  slices and entry-level IR dumps.
- Observed `1f0b`/`1f0c` literal/preformatted body spans.
- Observed `1f3b`/`1f5b` URL body spans.
- Observed `1f1a`/`1f1c` fixed two-byte-argument controls and
  `1f44`/`1f64` extended link controls.

## Experimental / Active Reverse Engineering

- Full `0x1f` control opcode semantics.
- Implementing `LV-IR v0` as emitted command output rather than only a draft
  specification.
- Formal private-corpus regression baselines generated from redacted profiles.
- Shared typed address/component objects used by every parser and exporter.
- Official-renderer parity checks.
- Dictionary-specific semantic profiles for section codes, named images, and
  virtual selectors.
- Broader LVED/WebView2 corpus coverage beyond observed OXFPEU4/KQCMPROS
  packages.
- LogoVista writer support.

## Known Limitations

- Not all dictionaries store definitions in `HONMON.DIC`.
- Some Windows titles store raw body IDs in `HONMON.DIC` and renderer HTML in
  encrypted SQLite sidecars. Raw HONMON remains the anchor table, but final body
  text requires dereferencing the sidecar.
- Not every product that declares `DictFULLDB` has an unreadable `HONMON.DIC`;
  several still have readable raw body streams. Audit the raw layer first.
- Some control opcodes are structurally recognized with neutral tags, but their
  exact renderer presentation is not fully modeled.
- The current Windows SSED corpus has one known physical tail anomaly:
  `NANDOKU3` ends with a lone final `0x1f` byte after the last decoded text
  cell. It is covered and reported as a truncated control, not guessed.
- The companion component-forensics pass has narrow residuals outside HONMON:
  `NANDOKU2` has a 3-byte nonzero physical tail after full `FHINDEX.DIC` pages;
  `25IGAKU` has one title-stream `1f1f` control with unknown renderer
  semantics; `ITALIAN` has one standalone title byte `0x11`; and three `.uni`
  files have small nonzero trailers after all parsed records.
- `ARCHSIC3` has 235 in-range `PCMDATA.DIC` pointer ranges whose payload bytes
  are accounted for but whose audio codec/container is not yet classified.
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is dictionary-specific.
- The default raw-resource gaiji readiness pass has one display-unresolved
  dictionary: `NGYOKTUK`. Its encrypted `vlpljblF` sidecar decrypts to a
  row-ordered `HONBUN` renderer database that matches raw HONMON entry slices,
  so `gaiji-readiness --renderer-sidecars` can recover entry-level display.
  This is contextual renderer evidence, not a dictionary-global gaiji map.
- `dump-ir` is still a lossless entry-span JSONL inspection format. It covers
  one LV-IR slice, but it is not yet a full `LV-IR v0` package export.
- Observed `DictFtsDB` `.dbc` payloads for OXFPEU4/KQCMPROS are LVED
  SQLCipher packages. Future `.dbc` variants should still be classified on
  their own evidence instead of assumed to be SSED or LVED.
- LogoFontCipher support covers the key schedule observed in tested Windows
  decryptors. Treat unrelated encrypted-looking payloads separately until their
  reader or key schedule is identified.

## Roadmap

Recently landed:

1. Redacted SSED package profiles with corpus-level summary metrics.
2. Strict, forensic, and lenient text-span parsing modes.
3. Entry-level lossless span JSONL from `HONMON.DIC`.
4. Measured unknown controls, bytes, gaiji, media references, and index leaf
   parse coverage.
5. Full-corpus `HONMON.DIC` byte accounting with zero unknown controls, zero
   unknown bytes, and zero invalid JIS cells on the 169-target Windows SSED
   corpus.
6. Full-corpus component forensics for menu/title/index/gaiji/media resources,
   including new index variants, wrapped PNG `COLSCR.DIC` records, GA16 byte
   coverage, `.uni` trailer accounting, and raw `PCMDATA.DIC` range coverage.
7. Full-corpus gaiji readiness reporting, including the JIS-grid GA16 range
   correction, `.uni` record-order GA16/GAI16 addressing, renderer `HONBUN`
   sidecar evidence, and refined capability-matrix gaiji status.

Next priorities:

1. **LV-IR implementation.** Make a command emit the `LV-IR v0` package layout
   described in `spec/lv-ir-v0.md`, starting with components, entries,
   titles, indexes, menus, gaiji, media references, media records, issues, and
   metrics.
2. **NGYOKTUK renderer-backed gaiji.** Keep `NGYOKTUK` as the named
   raw-resource exception: its display is recoverable through row-aligned
   `HONBUN` HTML, but a lossless IR/exporter must preserve raw gaiji
   provenance because some codes are contextual rather than dictionary-global.
3. **Corpus capability matrix refinement.** Use matrix output to separate
   writer-v0 blockers from lossless-repacker blockers, then tighten media and
   menu readiness rules as the IR implementation lands.
4. **Corpus regression harness.** Commit redacted expected metrics generated
   from owned corpora, then add a comparison command that flags changed shape
   counts, unknown counts, parse failures, and dereference coverage without
   storing dictionary text.
5. **Parser unification.** Make `entries`, `titles`, `menus`, `indexes`,
   media extractors, and exporters consume the same classification/profile
   layer instead of each command rediscovering package shape independently.
6. **Renderer parity.** Build small local parity fixtures for body text,
   literal spans, URL spans, gaiji images, named section images, media links,
   menu destinations, and dense-anchor renderer bodies.
7. **Exporter layer.** Implement debug HTML first, then Yomitan structured v3
   and MDict as views over the versioned IR rather than separate parsers.
8. **Writer research.** Start only after the model can round-trip addresses,
   indexes, gaiji/media references, and dense-anchor relationships with
   measurable unknowns near zero on the corpus.

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
- Common `*INDEX.DIC` branch-page and leaf-row parsing for forward, backward,
  keyword, and cross-reference indexes.
- Dictionary-local `.uni` gaiji mapping, including UTF-16 surrogate-pair
  sequences and older 12-byte `.uni` files.
- `GA16HALF` / `GA16FULL` bitmap header parsing, glyph slicing, and PNG
  rendering.

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
- `COLSCR.DIC` media pointer decoding and referenced BMP/JPEG extraction.
- `PCMDATA.DIC` audio/media pointer decoding, unreferenced-record discovery,
  and portable WAV/MP3 writing.
- Package image discovery from iOS `img`, Windows `Templates` / `HANREI/img`,
  and Android resource folders.
- SQL/`DictFULLDB`-assisted gaiji validation reports.
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
- Strict, forensic, and lenient text-span parsing modes for sampled body
  slices and entry-level IR dumps.
- Observed `1f0b`/`1f0c` literal/preformatted body spans.
- Observed `1f3b`/`1f5b` URL body spans.
- Observed `1f1a`/`1f1c` fixed two-byte-argument controls and
  `1f44`/`1f64` extended link controls.

## Experimental / Active Reverse Engineering

- Full `0x1f` control opcode semantics.
- Expanding the first lossless span model into a stable public IR schema for
  all text, index, link, gaiji, and media layers.
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
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is dictionary-specific.
- `dump-ir` is a lossless span JSONL inspection format. It is not yet the
  stable public IR schema.
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

Next priorities:

1. **Versioned model schema.** Promote `dump-ir` from an inspection JSONL into
   a documented public IR with typed `Address`, `Component`, `Span`, `Entry`,
   `IndexRow`, `GaijiOccurrence`, and `MediaReference` records.
2. **Corpus regression harness.** Commit redacted expected metrics generated
   from owned corpora, then add a comparison command that flags changed shape
   counts, unknown counts, parse failures, and dereference coverage without
   storing dictionary text.
3. **Parser unification.** Make `entries`, `titles`, `menus`, `indexes`,
   media extractors, and exporters consume the same classification/profile
   layer instead of each command rediscovering package shape independently.
4. **Renderer parity.** Build small local parity fixtures for body text,
   literal spans, URL spans, gaiji images, named section images, media links,
   menu destinations, and dense-anchor renderer bodies.
5. **Exporter layer.** Implement debug HTML first, then Yomitan structured v3
   and MDict as views over the versioned IR rather than separate parsers.
6. **Writer research.** Start only after the model can round-trip addresses,
   indexes, gaiji/media references, and dense-anchor relationships with
   measurable unknowns near zero on the corpus.

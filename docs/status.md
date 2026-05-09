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
  index components, including `0xa1` MULTI selector indexes.
- `MULTI*.DIC` selector descriptor parsing and cross-checking against declared
  `SSEDINFO` component records.
- Text-like `RIGHT.DIC`, `TOC.DIC`, and `IDXJUMP.DIC` sidecar decoding,
  including the `1f49` / `1f69` TOC link control pair.
- Dictionary-local `.uni` gaiji mapping, including UTF-16 surrogate-pair
  sequences, two-section and single-section 12-byte `.uni` files, and explicit
  trailer accounting.
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
- Corpus-wide `vlpljbl*` classification by suffix, raw/decrypted magic,
  SQLite schema, and inferred role. Observed roles include decryptor binaries,
  fonts, renderer body DBs, media stores, search indexes, row-ordered `HONBUN`,
  and block/offset body DBs.
- Windows `EXINFO.INI` parsing and CP932 auxiliary text-index extraction,
  including sibling eight-hex-digit `00000xxx.idx` sidecar trees.
- Windows `HC????.dll` renderer plugin classification, including PE
  import/export extraction, `SSDicLib.dll` API usage, `EXINFO` `HTMLDLL`
  correlation, numeric-index correlation, `vlpljbl*` companion names, and
  embedded SQL/HTML/image template strings.
- Windows renderer SQLite extraction through raw HONMON ID anchors and
  `t_contents` rows, with optional `media` BLOB export.
- Windows renderer SQLite extraction for the `BRINEN15` dense-anchor variant:
  marker-at-byte-0 HONMON ID rows, `f_data_id` / `f_contents` `t_contents`
  schemas, and two-column `t_media(f_name, f_blob)` JPEG stores.
- Android body DB extraction through raw HONMON ID anchors and the observed
  `rowid * 5` mapping.
- Structured `MENU.DIC` extraction with menu hierarchy, link labels,
  packed-BCD destination pointers, and named component/body targets.
- `COLSCR.DIC` media pointer decoding and referenced BMP/JPEG/PNG extraction.
- `PCMDATA.DIC` audio/media pointer decoding, unreferenced-record discovery,
  referenced-range byte coverage, and portable WAV/MP3 writing for classified
  payloads.
- Package image discovery from iOS `img`, Windows `Templates` / `HANREI/img`,
  Android resource folders, and sibling `*_GAIJI` companion directories.
- SQL/`DictFULLDB`-assisted gaiji validation reports.
- Raw-resource gaiji readiness reports that separate Unicode mappings,
  bitmap-backed glyphs, image-backed glyphs, probable formatting helpers,
  missing search fallbacks, and true display-unresolved codes.
- Standalone `SPINDEX.DIC` inspection for observed Windows suffix-index
  resources.
- LVED/WebView2 `main.data` / `.dbc` SQLCipher classification and validation
  for observed OXFPEU4/KQCMPROS packages.
- LVLMultiView package classification for observed ESPRANT2/YROPPO/MOROKU
  packages, including SSEDINFO facade parsing, LogoFontCipher SQLite payload
  roles, `menuData.xml` href resolution, static HTML/viewer-file reporting,
  and encrypted PDF resource detection where present.
- SIZK / NHK 文学のしずく read-aloud package inspection for the observed 30
  package set, including EXINFO-declared `shizuku.uni`, `HTMLs/b121`-`b124`
  template selectors, tiny four-entry HONMON streams, loose MP3 files, and
  synchronized UTF-16 text/time sidecars.
- `--jobs` process-level parallelism for corpus-scale scanning, extraction,
  audit, gaiji/media reports, LVED inspection, and GA16 rendering.
- Redacted SSED package profiles with component metadata, wrapper/resource
  counts, body-source hints, index parse metrics, control-opcode censuses, and
  lossless sampled decode metrics.
- Corpus-wide `0x1f` opcode atlas with payload lengths, component roles,
  surrounding context, paired-control behavior, examples, confidence labels,
  and explicit unresolved opcode reporting.
- Full-stream `HONMON.DIC` byte accounting with redacted per-dictionary reports
  and corpus summaries.
- Entry-level lossless span JSONL preserving raw offsets/bytes for controls,
  JIS text, gaiji, media references, padding, and measured problem spans.
- Draft Decoded LogoVista Model v0 that names the shared package/component/
  address/entry/span/control/gaiji/media/index/title/menu/sidecar/issue records
  future exporters and writer experiments should consume.
- `dump-package-model`, a package-level JSON report that gathers SSEDINFO,
  HONMON/body-source classification, entry spans, title/index/menu summaries,
  gaiji/media resources, Windows sidecar evidence, family notes, and
  inconsistencies into one decoded model object. Large per-package runs can
  keep it bounded with skipped row models and opt-in full profile/index
  boundary scans. The command is now family-aware: LVED SQLCipher and
  LVLMultiView packages are classified into deferred models instead of being
  treated as failed SSED/HONMON packages.
- `dump-package-models`, a corpus-scale model harness with package-family
  target discovery, process-level parallelism, path-aware progress output,
  resumable deterministic model paths, and clean failure JSON. The current
  local corpus model pass completed 261 targets with zero failures:
  202 SSED, 45 LVED SQLCipher, and 14 LVLMultiView packages.
- Chunked decoded model output via `--chunked`: `package.json` plus JSONL files
  for components, entries, title/index/menu samples, gaiji, media,
  dereferences, issues, and metrics. Chunked `package.json` files keep the
  decoded-model schema and remain readable by `capability-matrix --model-dir`.
- Corpus capability matrix generation from Decoded Model v0 reports. Legacy
  redacted `profile` / `honmon-bytes` / `component-forensics` inputs remain
  supported, but `--model-dir` is now the preferred planning path.
- Strict, forensic, and lenient text-span parsing modes for sampled body
  slices and entry-level IR dumps.
- Observed `1f0b`/`1f0c` literal/preformatted body spans.
- Observed `1f3b`/`1f5b` URL body spans.
- Observed `1f1a`/`1f1c` fixed two-byte-argument controls and
  `1f44`/`1f64` extended link controls.
- Full text-stream opcode atlas over 7,026,978,819 expanded bytes, with one
  unresolved singleton title-stream opcode/anomaly: `25IGAKU` `FHTITLE.DIC`
  `1f1f`.
- Focused all-in staged audits over 182 high/medium/mobile/low-priority SSED
  package targets, covering 3,687,534,595 expanded HONMON bytes with zero
  unknown HONMON controls, zero unknown HONMON bytes, and zero invalid JIS
  cells.
- Follow-up audits over 17 previously excluded Britannica/Genius-family SSED
  package targets, covering 1,101,215,744 more expanded HONMON bytes with zero
  unknown HONMON controls, zero unknown HONMON bytes, and zero invalid JIS
  cells. This pass exposed and closed the `BRINEN15` marker-at-byte-0 dense
  anchor plus renderer SQLite schema variant.
- Full corpus Decoded Model v0 generation over 261 package targets with
  chunked output, resume, progress, gaiji readiness, and family-aware deferred
  models for LVED/LVLMultiView. The derived capability matrix currently reports
  `legacy_writer_v0` as green 158, yellow 16, red 28, gray 59, and
  `lossless_repacker` as green 134, red 68, gray 59.

## Experimental / Active Reverse Engineering

- Full renderer semantics for structurally known `0x1f` controls.
- Tightening Decoded LogoVista Model v0 from a first emitted package report
  into a stable schema that every parser/exporter/writer experiment can target.
- Formal private-corpus regression baselines generated from redacted profiles.
- Shared typed address/component objects used by every parser and exporter.
- Official-renderer parity checks.
- Dictionary-specific semantic profiles for section codes, named images, and
  virtual selectors.
- Broader LVED/WebView2 corpus coverage beyond observed OXFPEU4/KQCMPROS
  packages. This is deferred until SSED model stabilization because LVED is a
  separate SQLCipher/SQLite package family.
- Broader LVLMultiView corpus coverage. This is also deferred and remains a
  separate package-family reader target, not an SSED writer target.
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
- The observed SSED corpus has one known physical tail anomaly:
  `NANDOKU3` ends with a lone final `0x1f` byte after the last decoded text
  cell. It is covered and reported as a truncated control, not guessed.
- The companion component-forensics pass has narrow residuals outside HONMON:
  `NANDOKU2` has a 3-byte nonzero physical tail after full `FHINDEX.DIC` pages;
  `25IGAKU` has one title-stream `1f1f` control/anomaly with unknown renderer
  semantics; `ITALIAN` has one standalone title byte `0x11`; and three `.uni`
  files have small nonzero trailers after all parsed records. `HABGESPA.uni`
  is not in this residual group anymore; it is parsed as a single-section
  simple12 `.uni` file.
- `ARCHSIC3` has 235 in-range `PCMDATA.DIC` pointer ranges whose payload bytes
  are accounted for but whose audio codec/container is not yet classified.
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is dictionary-specific.
- The default raw-resource gaiji readiness pass has one display-unresolved
  dictionary: `NGYOKTUK`. Its encrypted `vlpljblF` sidecar decrypts to a
  row-ordered `HONBUN` renderer database that matches raw HONMON entry slices,
  so `gaiji-readiness --renderer-sidecars` can recover entry-level display.
  This is contextual renderer evidence, not a dictionary-global gaiji map.
- `dump-package-model` embeds sampled rows by default so normal runs stay
  manageable; use zero-valued limits for exhaustive per-package inspection.
  Chunked output externalizes row families, but extraction is not yet fully
  streaming internally.
- `dump-package-model` now emits a shared `readiness` object and top-level
  `writer_readiness`. `capability-matrix --model-dir` consumes those decoded
  model reports directly, so new matrix work no longer needs to recombine
  separate `profile` / `honmon-bytes` / `component-forensics` status names.
- `dump-ir` remains a narrower lossless entry-span JSONL inspection command for
  HONMON-specific debugging.
- Observed `DictFtsDB` `.dbc` payloads for OXFPEU4/KQCMPROS are LVED
  SQLCipher packages. Future `.dbc` variants should still be classified on
  their own evidence instead of assumed to be SSED or LVED.
- LogoFontCipher support covers the key schedule observed in tested Windows
  decryptors. Treat unrelated encrypted-looking payloads separately until their
  reader or key schedule is identified.
- `vlpljbl*` classification is broader than extraction. Some roles are
  identified but not yet fully exported as entries, especially block/offset
  SQLite bodies and search/index-only stores.

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
8. Focused ignored-dictionary pass over older Britannica/Genius-family SSED
   packages, including `BRINEN15` raw-anchor dereferencing into LogoFontCipher
   renderer HTML and JPEG media extraction.
9. Corpus-scale Decoded Model v0 generation over the combined local corpus:
   261 package targets, zero failures, path-aware progress, resumable chunked
   bundles, and a model-derived capability matrix.

Next priorities:

1. **Decoded model stabilization.** Continue tightening the shared enum/status
   vocabulary used by `dump-package-model`, then migrate older commands toward
   emitting evidence for the model instead of independently naming package
   shape and readiness. Keep LVED and LVLMultiView as classified/deferred
   package families while SSED remains the active deep-reverse-engineering
   target.
2. **Writer-readiness split.** Separate `read_existing_package`,
   `export_existing_package`, `lossless_repack_existing_package`, and
   `author_core_ssed_v0`. Dense-HONMON packages block lossless repacking of
   existing products, but they should not automatically block authoring a clean
   core SSED dictionary.
3. **First-class dereference records.** Make `dereferences.jsonl` represent
   typed relationships between raw HONMON anchors, index/menu/media pointers,
   sidecar/database rows, and final body/media targets. This is the main model
   gap before writer/repacker work.
4. **Capability matrix identity.** Include package family, platform,
   `target_path`, and `model_path` in matrix rows so same-title SSED/LVED/iOS/
   Android/Windows packages are unambiguous outside `dump-package-models`
   progress output.
5. **NGYOKTUK renderer-backed gaiji.** Keep `NGYOKTUK` as the named
   raw-resource exception: its display is recoverable through row-aligned
   `HONBUN` HTML, but a lossless IR/exporter must preserve raw gaiji
   provenance because some codes are contextual rather than dictionary-global.
6. **Corpus regression harness.** Commit redacted expected metrics generated
   from owned corpora, then add a comparison command that flags changed shape
   counts, unknown counts, parse failures, and dereference coverage without
   storing dictionary text.
7. **Parser unification.** Make `entries`, `titles`, `menus`, `indexes`,
   media extractors, and exporters consume the same classification/profile
   layer instead of each command rediscovering package shape independently.
8. **Streaming model output / memory-aware scheduling.** `--chunked` fixes
   output shape, but large package workers still build bounded sections in
   memory. Add streaming JSONL paths or size-aware scheduling before exhaustive
   all-limits corpus runs.
9. **Renderer parity.** Build small local parity fixtures for body text,
   literal spans, URL spans, gaiji images, named section images, media links,
   menu destinations, and dense-anchor renderer bodies.
10. **Exporter layer.** After the decoded model stabilizes, implement debug HTML
   first, then Yomitan structured v3 and MDict as views over the same model
   rather than separate parsers.
11. **Writer research.** Start only after the model can round-trip addresses,
   indexes, gaiji/media references, and dense-anchor relationships with
   measurable unknowns near zero on the corpus.

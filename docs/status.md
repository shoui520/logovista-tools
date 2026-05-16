# Project Status and Roadmap

The project is a **research alpha**. Many observed dictionaries are supported,
but the internal model is still being refined as the corpus grows.

This page describes `logovista-tools`: the research toolkit, package
classifiers, decoded model reports, extractors, verification tools, and
experimental plain-SSED writer primitives. The clean reader-core proof of
concept has its own status page: [lvcore Status](lvcore-status.md).

The important boundary: this is no longer mainly an exporter. Exporters are
views over the model. The core toolkit deliverable is a lossless,
evidence-preserving LogoVista dictionary model.

LVED SQLCipher and LVLMultiView are separate package families handled by
toolkit classification/model-report paths. They are not SSED packages, and
dense/sidecar SSED packages are not LVED/LVLMultiView. lvcore-specific scope
and non-SSED reader status are tracked separately in
[lvcore Status](lvcore-status.md).

## Stable / High Confidence

- `SSEDINFO` `.IDX` parsing.
- `SSEDDATA` `.DIC` expansion.
- EPWING-like component block composition.
- JIS X 0208 text decoding.
- CP932 / Shift_JIS-2004 extension cell fallback for observed JIS-row symbols
  such as circled numbers, unit glyphs, and dingbat-like markers.
- Body-stream `HONMON.DIC` / `HONMON.DIN` extraction for supported dictionaries.
- Streaming body-entry extraction by default, with index-derived body
  boundaries available for forensic/debug extraction.
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
- Observed Mac OS X SSED `HONMON.DIN` AES-CBC decryption. The payload decrypts
  to normal `SSEDDATA` and then uses the same component expansion path as
  `HONMON.DIC`.
- Corpus-wide `vlpljbl*` classification by suffix, raw/decrypted magic,
  SQLite schema, and inferred role. Observed roles include decryptor binaries,
  fonts, renderer body DBs, media stores, search indexes, row-ordered `HONBUN`,
  and block/offset body DBs.
- Cross-platform plain SQLite sidecar classification for `.db` / `.sqlite`
  payloads outside the `vlpljbl*` naming convention, including Android body DBs,
  Android index metadata, examples/idioms, link-reference rows, kanji-support
  tables, template navigation/filter tables, search/conjugation helpers, and
  ancillary helper DBs.
- `EXINFO.INI` parsing and CP932 auxiliary text-index extraction, including
  sibling eight-hex-digit `00000xxx.idx` sidecar trees and sharded
  `00000xxx_n.idx` variants. `EXINFO.INI` and numeric auxiliary indexes are not
  Windows-only in the observed corpus.
- Windows `DICPROF.INI` profile/manifest classification as package metadata,
  including declared dictionary directory/catalog names and required-file
  lists.
- Windows `HC????.dll` renderer plugin classification and code-level behavior
  modeling, including PE import/export extraction, renderer bridge evidence,
  `EXINFO` `HTMLDLL` correlation, numeric-index correlation, `vlpljbl*`
  companion names, embedded SQL/HTML/image template strings, and observed
  control effects for body, gaiji, picture, link, audio, and private layout
  directives. `hc` and `hc-render` now emit per-renderer behavior profiles that
  classify exact body strategy separately from product hook parity.
  `hc-render` applies the common HC control semantics to raw body slices,
  automatically uses clear renderer body sidecars when present, and reports
  product-specific hooks as named behavior gaps rather than claiming exact
  plugin parity.
- Renderer/app SQLite extraction through raw HONMON ID anchors and
  `t_contents` rows, with optional `media` BLOB export.
- Renderer SQLite extraction for the `BRINEN15` dense-anchor variant:
  marker-at-byte-0 HONMON ID rows, `f_data_id` / `f_contents` `t_contents`
  schemas, and two-column `t_media(f_name, f_blob)` JPEG stores.
- Android body DB extraction through raw HONMON ID anchors and the observed
  `rowid * 5` mapping.
- Structured `MENU.DIC` extraction with menu hierarchy, link labels,
  packed-BCD destination pointers, null/sentinel destinations, and named
  component/body targets.
- `COLSCR.DIC` media pointer decoding and referenced BMP/JPEG/PNG extraction.
- `PCMDATA.DIC` audio/media pointer decoding, unreferenced-record discovery,
  referenced-range byte coverage, and portable WAV/MP3 writing for classified
  payloads, including shared WAVE data-slice stores such as `ARCHSIC3`.
- Package image discovery from iOS `img`, Windows `Templates` / `HANREI/img`,
  Android resource folders, platformless `res` / `resources` / `templates`
  folders, and sibling `*_GAIJI` companion directories.
- SQL/`DictFULLDB`-assisted gaiji validation reports.
- Raw-resource gaiji readiness reports that separate Unicode mappings,
  bitmap-backed glyphs, image-backed glyphs, probable formatting helpers,
  missing search fallbacks, and true display-unresolved codes.
- Standalone `SPINDEX.DIC` inspection for observed Windows and Mac OS X
  auxiliary suffix-index resources.
- `FIGURE.DIC` is recognized as a compressed type-`0xd0` figure/resource
  component. Its generic record grammar is still unresolved, so toolkit output
  should report it as a named resource stream rather than inventing raster
  image output.
- LVED/WebView2 `main.data` / `.dbc` SQLCipher package-family classification
  and limited validation for inspected packages. Toolkit support treats LVED as
  a separate SQLite/SQLCipher family rather than a failed SSED/HONMON package.
- LVLMultiView package-family classification for observed ESPRANT2/YROPPO/MOROKU
  packages, including SSEDINFO facade parsing, LogoFontCipher SQLite payload
  roles, `menuData.xml` href resolution, static HTML/viewer-file reporting,
  and encrypted PDF resource detection where present.
- Loose media/resource decoders for Britannica `whatday` HTML fragments,
  Britannica `top/top_*.dat` address/image lists, PROYAL53 `dat/*`
  LogoFontCipher WAVE payloads, LVLMultiView MOROKU law PDFs, and extensionless
  renderer SQLite sidecars such as CJJC160.
- SIZK / NHK 文学のしずく focused dictionary-family inspection for the observed
  30 Windows-corpus dictionaries, including EXINFO-declared `shizuku.uni`,
  `HTMLs/b121`-`b124` template selectors, tiny four-entry HONMON streams,
  loose MP3 files, and synchronized UTF-16 text/time sidecars.
- `--jobs` process-level parallelism for corpus-scale scanning, extraction,
  audit, gaiji/media reports, LVED inspection, and GA16 rendering.
- Redacted SSED package profiles with component metadata, wrapper/resource
  counts, body-source hints, index parse metrics, control-opcode censuses, and
  lossless sampled decode metrics.
- Corpus-wide `0x1f` opcode atlas with payload lengths, component roles,
  surrounding context, paired-control behavior, examples, confidence labels,
  and explicit anomaly reporting.
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
  LVLMultiView packages are classified into family-specific non-SSED models
  instead of being treated as failed SSED/HONMON packages.
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
- First-class `dereferences.jsonl` records for dense HONMON anchors,
  DictFULLDB/renderer/Android body links, index body/title pointers, menu
  destinations, and COLSCR/PCMDATA media references.
- Strict, forensic, and lenient text-span parsing modes for sampled body
  slices and entry-level IR dumps.
- Experimental author-core SSED writer primitives in Python. Current coverage
  includes normal-layout `SSEDINFO` encoding, compressed `SSEDDATA` emission
  with a literal-only diagnostic mode, parallel chunk compression for large
  components, block/pointer helpers, body-stream `HONMON.DIC` entry encoding,
  title stream encoding, simple and tagged index page encoding with branch/leaf
  page splitting, upper-bound branch keys, final-sibling `ff` sentinels,
  depth-capped branch key widths, `1f04`/`1f05` halfwidth ASCII display spans,
  deterministic Unicode-to-JIS/gaiji allocation, `.uni` emission, and
  `GA16HALF` / `GA16FULL` emission. Synthetic tests validate both parser
  roundtrip and branch traversal invariants. ASCII/fullwidth-ASCII index keys
  are normalized to uppercase JIS row-3 cells while body/title display text
  stays unchanged. Importers also emit normalized lookup aliases that strip
  lookup-blocking punctuation, spaces, and hyphen-like characters, apply Unicode
  compatibility normalization, and fold katakana to hiragana for Japanese
  lookup keys. Duplicate simple keys and large tagged groups may span adjacent
  leaves, matching observed LogoVista index behavior.
- `verify-written-package` validates writer output as a package, not just as
  individual parseable components. It checks `SSEDINFO` ranges against
  `SSEDDATA` headers, expanded sizes, branch upper-bound rows, final `ff`
  sentinels, exact traversal landing pages, duplicate-key contiguity,
  body/title pointer row boundaries, and `.uni` / GA16 resource consistency.
  Its traversal model accounts for same-key and same-branch-prefix groups that
  continue across adjacent sibling leaf pages.
- Experimental standalone Python reader core in `src/lvcore-experimental`.
  This clean reimplementation is tracked separately in
  [lvcore Status](lvcore-status.md) so toolkit package-family support is not
  conflated with lvcore reader scope.
- `1fe2`/`1fe3` is now modeled as a private renderer-directive span rather
  than visible color text. Plain and HTML body renderers suppress directive
  strings such as `SQL:`, `IMG:`, and `RUB:` while lossless spans preserve them.
- Observed `1f0b`/`1f0c` literal/preformatted body spans.
- Observed `1f3b`/`1f5b` URL body spans.
- Observed `1f1a` tab/column positioning, `1f1c` media-layout controls, and
  `1f44`/`1f64` extended link controls.
- Full text-stream opcode atlas over 7,026,978,819 expanded bytes. The only
  singleton anomaly is the known vendor title-stream defect
  `25IGAKU` `FHTITLE.DIC` `1f1f`.
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
  chunked output, resume, progress, gaiji readiness, and family-aware non-SSED
  models for LVED/LVLMultiView. The model-derived capability matrix is the
  preferred planning report for `read_existing`, `export_existing`,
  `author_core_ssed_v0`, and `lossless_repack_existing`; regenerate it after
  focused parser fixes before quoting aggregate readiness counts.

## Experimental / Active Reverse Engineering

- Full renderer semantics for structurally known `0x1f` controls. Current
  renderers are conservative and should not be described as perfect clones of
  any proprietary renderer.
- Continuing to tighten Decoded LogoVista Model v0 so older research commands
  feed the same package/body-source/readiness vocabulary instead of inventing
  local status names.
- Formal private-corpus regression baselines generated from redacted profiles.
- Shared typed address/component objects used by every parser and exporter.
- Reader-behavior fixtures for generated SSED primitives. These are useful
  regression fixtures, not proof that lvcore is compatible with the historical
  corpus.
- Dictionary-specific semantic profiles for section codes, named images, and
  virtual selectors.
- Broader LVED/WebView2 corpus coverage as a separate SQLCipher/SQLite package
  family.
- Broader LVLMultiView corpus coverage as a separate SQLite/viewer-resource
  package family, not an SSED writer target.
- Full LogoVista writer support. The current writer code is a research
  primitive layer for clean plain-HONMON SSED packages, not a complete package
  authoring product.

## Known Limitations

- Not all dictionaries store definitions in `HONMON.DIC`.
- Some Windows titles store raw body IDs in `HONMON.DIC` and renderer HTML in
  encrypted SQLite sidecars. Raw HONMON remains the anchor table, and the model
  represents those packages as dense-HONMON dereference variants rather than
  plain body streams.
- Not every product that declares `DictFULLDB` has an unreadable `HONMON.DIC`;
  several still have readable raw body streams. Audit the raw layer first.
- Some control opcodes are structurally recognized with conservative tags, but
  their exact renderer presentation is not fully modeled. The current SSED
  blocker class is renderer/resource completeness, not unknown `0x1f` byte
  accounting.
- The observed SSED corpus has one known physical tail anomaly:
  `NANDOKU3` ends with a lone final `0x1f` byte after the last decoded text
  cell. It is covered and reported as a truncated control, not guessed.
- The companion component-forensics pass has narrow residuals outside HONMON:
  `NANDOKU2` has a 5-byte partial tail after full `FHINDEX.DIC` pages, with
  three nonzero tail bytes;
  `25IGAKU` has one malformed singleton title-stream `1f1f` sequence treated as
  a vendor data defect; `ITALIAN` has one standalone title byte `0x11`; and
  three `.uni` files have small nonzero trailers after all parsed records.
  `HABGESPA.uni` is not in this residual group anymore; it is parsed as a
  single-section simple12 `.uni` file.
- Named UI/style images such as `exam.png` are discovered. Mapping them to
  semantic entry regions is dictionary-specific; the first implemented HC proof
  case is `HC013A.dll`, where section `1f09 0011` starts an example block and
  the renderer inserts the `exam` image once for that contiguous examples
  region.
- The default raw-resource gaiji readiness pass intentionally does not use
  Windows renderer databases. `NGYOKTUK` has no direct `.uni`/GA16/image gaiji
  resources, but its encrypted `vlpljblF` sidecar decrypts to row-ordered
  `HONBUN` renderer HTML that matches raw HONMON entry slices. Authoritative
  model/gaiji readiness runs should include renderer sidecar evidence for that
  package; the result is display-ready but contextual rather than a
  dictionary-global `code -> Unicode` map.
- Current lvcore reader-side counters and SSED closure status are tracked in
  [lvcore Status](lvcore-status.md), not in this toolkit status page.
- The Panel subsystem has a decoded optional-navigation model. Complete
  Windows-style Panel packages use `Panels.dtd`, `Panels.xml`, `Panel.html`,
  `Cell.html`, and external `.bin` tables; Mac/mobile packages can represent
  the same model with `Panels.plist` or nested menu plists. The common `.bin`
  files are fixed-width little-endian label-to-address tables, with observed
  id-prefixed, declared-count-mismatch, empty/zero-width, and headerless
  big-endian UTF-8 variants. The current decode pass covers 4,544 non-decryptor
  `.bin` candidates with no binary decode failures. Rows mostly target
  `HONMON.DIC`; a small observed set targets `MENU.DIC`. Panel rows are
  optional navigation/sidebar data, not ordinary entry bodies.
- `CCALTSTR.HA` / `CCALTSTR.FU` are decoded as fixed-record custom-character
  alternate-string tables. They provide short alternate strings keyed by
  JIS-grid custom-character codes and are relevant to search/headword fallback,
  not body rendering or media/resource storage.
- `dump-package-model` embeds sampled rows by default so normal runs stay
  manageable; use zero-valued limits for exhaustive per-package inspection.
  Chunked output externalizes row families, but extraction is not yet fully
  streaming internally.
- `dump-package-model` now emits a shared `readiness` object and top-level
  `writer_readiness`. `capability-matrix --model-dir` consumes those decoded
  model reports directly, adds path/family/package-layer identity columns, and
  splits readiness into `read_existing`, `export_existing`,
  `author_core_ssed_v0`, and `lossless_repack_existing`. Menu destination
  readiness treats packed `000000000000` payloads as null/sentinel
  destinations rather than unresolved component pointers.
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
10. First-class dereference records in Decoded Model v0 and chunked
    `dereferences.jsonl`.

Next priorities:

1. **Decoded model consistency.** Keep `dump-package-model` and
   `capability-matrix --model-dir` aligned with shared enum/status vocabulary
   so research, exporter planning, and writer planning do not drift.
2. **Package-family classification.** Keep SSED package layers, LVED,
   LVLMultiView, and wrapper/resource families explicit. Keep dictionary-family
   reports such as SIZK, subsystems such as Panels, and auxiliary resources
   such as numeric `.idx` trees out of the package-family taxonomy.
3. **Renderer/control semantics.** Expand synthetic tests for structurally known
   controls, gaiji/media/link behavior, and conservative LogoVista-like output
   without claiming pixel-perfect renderer parity.
4. **Corpus regression harness.** Commit redacted expected metrics generated
   from owned corpora, then add a comparison command that flags changed shape
   counts, unknown counts, parse failures, and dereference coverage without
   storing dictionary text.
5. **Parser unification.** Make `entries`, `titles`, `menus`, `indexes`,
   media extractors, and exporters consume the same classification/profile
   layer instead of each command rediscovering package shape independently.
6. **Writer research.** Continue the experimental Python author-core path only
   for clean plain-HONMON SSED packages. It remains a reverse-engineering proof
   checkpoint, not the lvcore compatibility target and not an LVED/LVLMultiView
   writer.
7. **lvcore reader work.** Track reader-core priorities separately in
   [lvcore Status](lvcore-status.md).

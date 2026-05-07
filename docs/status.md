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

## Experimental / Active Reverse Engineering

- Full `0x1f` control opcode semantics.
- Lossless entry IR with raw JIS cells, normalized display text, raw controls,
  gaiji refs, media refs, links, section markers, and typed addresses.
- Strict and forensic parser modes.
- Redacted corpus regression profiles.
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
- Some control opcodes are recognized only enough to avoid corrupting nearby
  text.
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is dictionary-specific.
- Current outputs are inspection/extraction formats, not a stable public IR.
- `DictFtsDB` `.dbc` payloads now split into at least two families: LVED
  SQLCipher packages such as OXFPEU4/KQCMPROS, and any future `.dbc` variants
  that still need independent classification.
- LogoFontCipher support covers the key schedule observed in tested Windows
  decryptors. Treat unrelated encrypted-looking payloads separately until their
  reader or key schedule is identified.

## Roadmap

Near term:

1. Build a corpus classifier that emits stable, redacted package profiles.
2. Add strict and forensic parsing modes.
3. Move low-level decoding toward lossless spans instead of flattened text.
4. Make unknown controls, pointers, gaiji, media, and unparsed bytes measurable.

Model work:

1. Introduce typed addresses and component objects.
2. Preserve raw JIS cells separately from normalized display text.
3. Keep half-width and full-width gaiji spaces distinct in the IR.
4. Store media/link/control payloads as structured spans.
5. Centralize dictionary classification so commands consume the same profile.

Downstream views:

1. Debug HTML with raw addresses and unresolved spans visible.
2. Lossless JSON IR.
3. Yomitan structured v3 and MDict export from the IR.
4. LogoVista writer experiments once the emitted structures are well enough
   specified.

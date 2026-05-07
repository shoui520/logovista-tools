# CLI Command Reference

This page documents the current command-line interface. Commands are grouped by task: discovery, expansion, extraction, resources, platform sidecars, and validation.

## CLI Commands

### Parallel Jobs

Corpus-scale commands support `--jobs`:

```bash
--jobs 1     run serially; this is the default
--jobs 8     use eight worker processes
--jobs 0     use os.cpu_count()
```

This applies to commands that operate across many dictionaries or resources:
`scan`, `entries`, `resources`, `colscr`, `pcmdata`, `extras`, `rendererdb`,
`spindex`, `audit-honmon`, `gaiji-report`, `ga16`, `titles`, `indexes`,
`menus`, `fulldb`, `profile`, `honmon-bytes`, `component-forensics`,
`dump-ir`, and LVED payload inspection.

For huge corpora, start with a moderate value such as `--jobs 8` or `--jobs 16`
when also writing many JSON/media files. `--jobs 0` is useful for CPU-heavy
audits on local SSD/NVMe storage, but it can be counterproductive on slow or
network-mounted disks.

### `scan`

Find SSED dictionaries under one or more roots.

```bash
logovista-tools scan /path/to/LogoVista
logovista-tools scan /path/to/LogoVista --json
```

The scanner looks for `.IDX` / `.idx`, validates the `SSEDINFO` magic, and
keeps only dictionaries with a discoverable `HONMON.DIC`.
Use `profile` when you need a redacted inventory that also records incomplete
packages with missing `HONMON.DIC` or missing declared components.

### `info`

Print metadata for either an `.IDX` or `.DIC`.

```bash
logovista-tools info DICT.IDX --all
logovista-tools info HONMON.DIC
```

For `.IDX`, this lists component records, block ranges, component types, and
filenames. For `.DIC`, this prints the SSED chunk count, declared logical block
range, and storage mode (`plain` or `logofont_cipher`).

### `expand`

Decompress one plain or LogoFontCipher-encrypted `SSEDDATA` file:

```bash
logovista-tools expand HONMON.DIC honmon.expanded
```

The output is the expanded EPWING/JIS-style byte stream for that component.

### `decrypt`

Decrypt an observed Windows LogoFontCipher file without SSED expansion:

```bash
logovista-tools decrypt HONMON.DIC honmon.ssed
logovista-tools decrypt vlpljblF vlpljblF.sqlite
```

Use this for sidecars that decrypt to another container such as SQLite. For
encrypted `HONMON.DIC`, `info`, `expand`, `entries`, and `audit-honmon` already
decrypt transparently when the optional crypto dependency is installed.
The command streams input to output, so large sidecars such as DAIJIRN4's
610 MB `vlpljblb` do not need to be loaded into memory.

### `lved`

Inspect modern LVED/WebView2 packages whose body data lives in SQLCipher
`main.data` or `.dbc` payloads rather than SSED/HONMON.

```bash
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/KQCMPROS --dict-id 751 --json
```

Useful options:

```bash
--dict-id N                         numeric dictionary id used by the viewer metadata path
--dict-code CODE                    product/dictionary code when it cannot be inferred
--key-file PATH                     read an explicit local SQLCipher key; never print it
--memory-dump PATH                  count/test LVEDVIEWER dump key candidates without printing them
--write-decrypted PATH              write one plaintext SQLite copy for local analysis
--json                              emit machine-readable JSON
```

`--write-decrypted` requires exactly one discovered payload and either
`--dict-id` or `--key-file`. Memory dumps can validate candidates, but the
command does not use memory-dump-only recovery to write decrypted output.

Report fields include payload classification, size, SHA-256, sampled entropy,
inferred dictionary code, and key-validation status. Reports deliberately do
not emit explicit, derived, or memory-dump-recovered keys.

### `compose`

Create an EPWING-like image by expanding each component listed by the `.IDX`
and placing it at its declared logical block.

```bash
logovista-tools compose DICT.IDX book.expanded --quiet
```

This is useful for binary comparison and for understanding the book layout.
It does not turn LogoVista into a fully standard EPWING book; it just writes
the expanded components into the block positions specified by `SSEDINFO`.

### `entries`

Extract readable body entries from expanded `HONMON.DIC`.

```bash
logovista-tools entries /path/to/LogoVista --out-dir bodies
```

Output layout:

```text
bodies/
  summary.json
  DICT_ID/
    summary.json
    raw_entries.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "GENIUSEB",
  "dict_title": "ジーニアス英和大辞典",
  "entry_index": 2,
  "block": 25769,
  "offset": 158,
  "length": 3048,
  "heading": "a, A <hA235>ei<hA235>【名】(複 →[語法])",
  "body": "a, A <hA235>ei<hA235>【名】...\n1英語アルファベットの第1字."
}
```

Useful options:

```bash
--dict NAME                         extract only matching dictionary ids
--limit N                           stop after N emitted entries per dictionary
--gaiji drop                        omit all unresolved gaiji
--gaiji h-placeholder               keep half-width gaiji placeholders only
--gaiji placeholder                 keep half-width and full-width placeholders
--image-gaiji                       preserve unresolved PNG-backed gaiji as <img:code>
--html                              also emit body_html with inline HTML img tags
--media-placeholder                 preserve 1f4d media payloads as placeholders
--section-markers                   preserve 1f09 section markers as placeholders
--section-image CODE=IMAGE_KEY      insert a named image at a section marker in HTML output
--no-index-boundaries               slice only on HONMON entry markers
--no-skip-dense-marker-honmon       force extraction on placeholder HONMON
```

When `--html` is used, rows include `body_html` in addition to `body`.
PNG-backed gaiji are rendered as package-relative image tags such as:

```html
<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d">
<img src="resource/kmkimges/b167_1.png" alt="b167" class="lv-gaiji lv-gaiji-b167">
```

Exporters for Yomitan, MDict, or another HTML-capable format should copy the
referenced PNG files into the target package and rewrite `src` paths if their
archive layout differs.

`--section-image` is intentionally explicit. For HAESPJPN, `0011=exam` is a
useful first-pass rule because `0011` marks Spanish example lines and
`exam.png` is the dictionary's `用例` badge. Other dictionaries may use the
same section code differently.

### `audit-honmon`

Audit whether raw `HONMON.DIC` plus raw `*INDEX.DIC` pointers can produce
coherent body text.

```bash
logovista-tools audit-honmon /path/to/LogoVista --out-dir honmon-audit
logovista-tools audit-honmon /path/to/LogoVista --dict KOJIEN7 --json
```

Output layout:

```text
honmon-audit/
  honmon_audit.json
```

This command is intentionally raw-first. It expands `HONMON.DIC`, parses
index-derived body boundaries, samples decoded body slices, probes 32-byte
HONMON ID/token records, checks `*TITLE.DIC` availability, and records
`DictList.plist` declarations. It does not read SQLite or `DictFULLDB` body
text. The database metadata is included only to explain where dense raw IDs
would be dereferenced by the separate `fulldb` command.

Useful options:

```bash
--dict NAME                         audit only matching dictionary ids
--sample-limit N                    keep at most N readable body samples
--max-slices N                      inspect at most N candidate raw slices per dictionary
--max-id-records N                  probe at most N 32-byte HONMON records; 0 = full scan
--no-index-boundaries               sample marker-only slicing
--no-skip-dbc                       include legacy/mobile .dbc-adjacent SSED
                                    stubs in this raw HONMON audit
--json                              also print the JSON report
```

Important status values:

```text
raw_honmon_body_stream              HONMON/IDX yields readable entries
mixed_or_dense_but_raw_slices_readable
                                    dense signals exist, but raw slices are readable
dense_honmon_id_table_dictfulldb    HONMON stores raw numeric body IDs
dense_honmon_token_table_dictfulldb HONMON stores opaque raw tokens/anchors
dense_honmon_id_table_rendererdb    HONMON stores raw numeric IDs that resolve
                                    to a Windows renderer SQLite sidecar
dense_honmon_id_table_androiddb     HONMON stores raw numeric IDs that resolve
                                    to an Android app body database
idx_title_only_no_readable_honmon_body
                                    indexes/titles exist, but sampled HONMON bodies do not
skipped_dbc                         .dbc-adjacent package skipped by default;
                                    use lved for LVED SQLCipher payloads
```

### `profile`

Write stable, redacted SSED package profiles. Profiles include catalog
component metadata, wrapper/resource counts, body-source hints, raw
`*INDEX.DIC` parse metrics, sampled lossless decode metrics, control-opcode
censuses, unknown-control counts, unknown-byte counts, and forensic issue
samples without emitting dictionary body text.

```bash
logovista-tools profile /path/to/LogoVista --jobs 0 --out-dir profiles
logovista-tools profile /path/to/LogoVista --no-hash --max-slices 25 --jobs 0
```

Useful options:

```bash
--parse-mode forensic                record issues and continue; default
--parse-mode strict                  treat unknown/unsafe text bytes as strict failures
--max-slices N                       sampled HONMON slices per dictionary; 0 = uncapped
--max-issue-samples N                forensic issue samples kept per dictionary
--no-hash                            skip component SHA-256 hashes for faster exploratory runs
--jobs 0                             use all detected CPU cores
```

Output layout:

```text
profiles/
  summary.json
  DICT_ID/
    profile.json
```

`summary.json` is an aggregate object with corpus-level shape counts, body
source hints, measured unknown totals, and hotspot lists. Each `profile.json`
contains per-component catalog metadata, `honmon.decode_aggregate`, and
`indexes.aggregate`.

### `honmon-bytes`

Decode every byte of each expanded `HONMON.DIC` and write redacted byte
coverage reports. This is the strongest raw text-stream audit command: unlike
`profile`, it does not sample entry slices.

```bash
logovista-tools honmon-bytes /path/to/LogoVista --jobs 0 --out-dir honmon-bytes
logovista-tools honmon-bytes /path/to/LogoVista --dict HAESPJPN --json
```

Output layout:

```text
honmon-bytes/
  summary.json
  DICT_ID/
    honmon_bytes.json
```

Each per-dictionary report records package-relative paths, HONMON storage mode,
expanded byte count, entry-marker count, byte-shape classification, full-stream
span statistics, control-opcode census, unknown-control census, issue counts,
and issue samples with logical block/offset addresses. It does not emit body
text or spans.

Useful options:

```bash
--parse-mode forensic                record issues and continue; default
--parse-mode strict                  treat unknown/unsafe bytes as strict failures
--max-issue-samples N                forensic issue samples kept per dictionary
--jobs 0                             use all detected CPU cores
```

The current Windows SSED corpus run accounts for 3,497,793,539 expanded HONMON
bytes with zero unknown controls, zero unknown bytes, zero invalid JIS cells,
and one known truncated final `0x1f` byte in `NANDOKU3`.

### `component-forensics`

Forensically account for non-HONMON core components: `MENU.DIC`,
`*TITLE.DIC`, structured `*INDEX.DIC`, text-like `INDEX.DIC` outliers,
`.uni` / `.UNI`, `GA16HALF` / `GA16FULL` / `GAI16*`, `COLSCR.DIC`, and
`PCMDATA.DIC`.

```bash
logovista-tools component-forensics /path/to/LogoVista --jobs 0 --out-dir component-forensics
logovista-tools component-forensics /path/to/LogoVista --dict GENIUSEB --json
```

Output layout:

```text
component-forensics/
  summary.json
  DICT_ID/
    component_forensics.json
```

Each report records declared component status, expanded byte sizes, structural
coverage, residual nonzero bytes, unknown text controls/bytes, index page
coverage, `.uni` record/trailer counts, GA16 glyph byte coverage, `COLSCR`
media record coverage, and `PCMDATA` referenced-range coverage. It does not
emit dictionary body text, media payloads, gaiji bitmaps, or proprietary data.

Useful options:

```bash
--dict NAME                         scan only matching dictionary ids
--parse-mode forensic                record issues and continue; default
--parse-mode strict                  treat unknown/unsafe text bytes as strict failures
--max-issue-samples N                issue samples kept per dictionary
--jobs 0                             use all detected CPU cores
--json                              also print the aggregate JSON summary
```

The current Windows SSED corpus component pass covers 1,231 present components:
84 `MENU.DIC`, 307 `*TITLE.DIC`, 536 structured `*INDEX.DIC`, one text-like
`INDEX.DIC`, 314 GA16 resources, 59 `COLSCR.DIC`, and 12 `PCMDATA.DIC`.
Remaining residuals are reported by exact dictionary/component instead of
silently skipped.

### `dump-ir`

Emit entry-level lossless span JSONL from expanded `HONMON.DIC`. This is the
debug/model path, not a user-facing readable extractor.

```bash
logovista-tools dump-ir /path/to/LogoVista --dict HAESPJPN --limit 10 --out-dir ir
```

Each entry row contains:

```text
schema                              logovista-lossless-entry-v1
address                             HONMON block/offset and component offset
stats                               measured byte/control/gaiji/media counts
unknown_control_ops                 opcode frequency map
issue_counts                        forensic issue frequency map
spans                               ordered offset spans with raw bytes
```

By default padding spans and `raw_hex` are included so the byte stream can be
audited directly. Use `--no-padding-spans` or `--no-raw` only when the output
size is too large for the task.

### `resources`

Discover package image resources.

```bash
logovista-tools resources /path/to/LogoVista --dict HAESPJPN
logovista-tools resources /path/to/LogoVista --dict HAESPJPN --json
```

LogoVista packages often include a top-level `img` directory plus
`resourcesCopy.plist` and `gaijiicon.plist`. Windows packages can put HTML
renderer assets in `Templates` and `HANREI/img`; Android packages can omit
plist manifests and put images in `resource/kmkimges`, `appendix/img`, or
`manual/contents/img`. The resource scanner checks all of those locations,
groups theme variants such as
`b13d_n.png` / `b13d_w.png` and Android-style `b167_1.png` / `b167_3.png`, and
reports code-like resources such as `b13d` or `b167` as image-backed gaiji.
PNG, GIF, JPEG, WebP, BMP, and SVG files are all treated as portable package
resources.
Named images such as `exam.png`, `esp.png`, or `jpn.png` are reported as package
resources for format exporters to use when reconstructing dictionary-specific
styling.

### `colscr`

Inspect or extract images stored in `COLSCR.DIC` and referenced by raw
`HONMON.DIC` media controls.

```bash
logovista-tools colscr /path/to/LogoVista --dict OUKOKU11 --out-dir colscr
logovista-tools colscr /path/to/DICT/DICT.IDX --write-media --out-dir colscr
```

Output layout:

```text
colscr/
  summary.json
  DICT_ID/
    colscr_summary.json
    colscr_manifest.jsonl
    media/
      00001_0200_00017649_0030.bmp
```

Each manifest row records the HONMON media-control position, section code,
raw 18-byte payload, decoded logical block/offset, image type, dimensions,
bit depth, BMP compression mode when relevant, and optional output filename.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           stop after N media references per dictionary
--write-media                       write referenced BMP/JPEG/PNG files
--json                              emit a machine-readable summary
```

### `pcmdata`

Inspect or extract audio/media records stored in `PCMDATA.DIC` and referenced
by raw `HONMON.DIC` controls.

```bash
logovista-tools pcmdata /path/to/LogoVista --dict HAESPJPN --out-dir pcmdata
logovista-tools pcmdata /path/to/DICT/DICT.IDX --write-audio --out-dir pcmdata
```

Output layout:

```text
pcmdata/
  summary.json
  DICT_ID/
    pcmdata_summary.json
    pcmdata_manifest.jsonl
    audio/
      00001_00023193_0000.wav
```

The manifest includes the raw 16-byte payload, decoded logical block range,
visible link label when present, codec, chunk layout, sample rate, channel
count, bit depth, source (`honmon` or `unreferenced`), and optional output
filename.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           stop after N HONMON audio references per dictionary
--write-audio                       write portable .wav/.mp3 files
--no-include-unreferenced           skip sequential records not referenced by HONMON
--json                              emit a machine-readable summary
```

For `fmt `/`data` PCM records, `--write-audio` wraps the raw chunks in a
standard `RIFF/WAVE` container. For MPEG Layer III records stored inside WAVE
chunks, it writes the `data` chunk as `.mp3`. For native `ID3`/MP3 records, it
writes the MP3 payload directly.

### `extras`

Parse Windows `EXINFO.INI` metadata and auxiliary side-panel files.

```bash
logovista-tools extras /path/to/DICT --out-dir extras
logovista-tools extras /path/to/LogoVista --dict DAIJIRN4 --json
```

Windows packages often declare side UI through fields such as `IDXCOUNT`,
`IDXNAME0`, and `IDXINFO0`. HTML entries are reported as HTML files. Text
auxiliary indexes such as DAIJIRN4's `0000015E.IDX` are parsed as CP932 tab
trees whose first two columns are eight-digit hexadecimal block/offset
pointers. Rows are resolved against the `.IDX` component ranges when possible.

The command also scans for sibling eight-hex-digit `*.idx` files such as
`00000152.idx`, even when `EXINFO.INI` does not reference them. These are
reported separately as `numeric_indexes` and written as `numeric_*.jsonl`.

Output layout:

```text
extras/
  summary.json
  DICT_ID/
    extras_summary.json
    aux_0_0000015E.IDX.jsonl
    numeric_0000015E.IDX.jsonl
```

### `rendererdb`

Extract renderer/app SQLite bodies by following raw HONMON ID anchors.

```bash
logovista-tools rendererdb /path/to/DICT --out-dir rendererdb
logovista-tools rendererdb /path/to/DICT --limit 20 --no-html
logovista-tools rendererdb /path/to/DICT --write-media --media-limit 100
logovista-tools rendererdb /path/to/DICT --write-ziptomedia --ziptomedia-limit 100
```

This command handles layouts where `HONMON.DIC` is a dense 32-byte anchor
table, not a body stream. It still starts from raw HONMON:

1. Expand `HONMON.DIC`.
2. Decode 32-byte records whose visible field is a full-width decimal ID.
3. Discover a sibling body SQLite payload.
4. For Windows renderer DBs, decrypt observed LogoFontCipher sidecars such as
   `vlpljblb` when needed, query `t_contents`, and emit only rows whose
   `f_DataId` exists in raw HONMON. Mixed-case and lowercase column variants
   such as `f_DataId` / `f_dataid` are normalized.
5. For the observed Android body DB shape, query the `DICTID(Html)` table and
   emit rows where `rowid * 5` exists in raw HONMON.

Output layout:

```text
rendererdb/
  summary.json
  DICT_ID/
    rendererdb_summary.json
    rendererdb_entries.jsonl
    vlpljblb.sqlite
    media/
      3djr_0002.gif
    ziptomedia/
      000010.wav
```

Rows include `data_id`, raw HONMON block/offset, type, group id, title HTML,
plain title, search title, keyword text, plain body text, and HTML unless
`--no-html` is used. `--write-media` exports BLOBs from `media` or `t_media` using magic bytes
to choose `.gif`, `.png`, `.jpg`, `.bmp`, or `.bin`; filenames are preserved
when the renderer HTML already references the original media name. Some Windows
packages also use `lved.ziptomedia:NAME.wav` links. `--write-ziptomedia`
discovers a sibling sound directory such as `_DCT_NAME_Sound_Files`, decrypts
LogoFontCipher-wrapped loose sound files, and writes portable `.wav` / `.mp3`
assets for the references that are physically present.

### `spindex`

Inspect standalone `SPINDEX.DIC` resources.

```bash
logovista-tools spindex /path/to/DICT --out-dir spindex
logovista-tools spindex /path/to/LogoVista --limit 200 --json
```

The observed `SPINDEX.DIC` files are not listed in product `SSEDINFO`
component tables, so this command handles them as separate SSED containers. It
expands only the compressed chunks that are physically present, parses the
internal reversed-key index pages, and reports whether child pages are present,
missing from the physical file, or outside the declared logical range.

Output layout:

```text
spindex/
  summary.json
  DICT_ID/
    SPINDEX_summary.json
    SPINDEX_internal_rows.jsonl
```

Internal row output includes both the stored reversed key and its forward
spelling:

```json
{
  "key_reversed": "CITEROHPAID",
  "key": "DIAPHORETIC",
  "child_block": 55753,
  "child_status": "present_full_page"
}
```

### `gaiji-report`

Write per-dictionary gaiji audit reports.

```bash
logovista-tools gaiji-report /path/to/LogoVista --dict HAESPJPN --out-dir gaiji-report
logovista-tools gaiji-report /path/to/LogoVista --dict KOJIEN7 --no-sql-cache --max-sql-rows 1000
logovista-tools gaiji-report /path/to/LogoVista --dict DAIJIRN4 --renderer-sidecars
```

Output layout:

```text
gaiji-report/
  summary.json
  DICT_ID/
    gaiji_report.json
```

The report combines:

- raw gaiji occurrence counts from expanded `HONMON.DIC` and `*TITLE.DIC`;
- dictionary-local `.uni` mappings and plist fallback mappings;
- `GA16HALF` / `GA16FULL` bitmap coverage;
- package PNG/image gaiji coverage;
- SQLite text evidence from declared `DictFULLDB` and sibling app-cache
  databases;
- optional Windows renderer SQLite sidecar evidence when `--renderer-sidecars`
  is used;
- aligned validation for cache tables that expose `Block` and `Offset`.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--no-sql-cache                      use declared DictFULLDB only
--renderer-sidecars                 decrypt/use Windows renderer SQLite sidecars
--max-sql-rows N                    scan at most N rows per SQLite table; 0 = full scan
--max-aligned-entries N             align at most N raw HONMON entries; 0 = full scan
--alignment-tolerance N             byte tolerance for Block/Offset cache matching
--include-unused-mapped             include mapped codes not seen in raw scans
```

Per-code rows include fields such as:

```json
{
  "code": "a126",
  "placeholder": "<hA126>",
  "raw_count": 17690,
  "mapped": "é",
  "mapping_source": "uni",
  "bitmap": {"resource": "GA16HALF", "width": 8, "height": 16},
  "sqlite_text_hits_for_display": 5249,
  "aligned_hits": 5232,
  "aligned_misses": 0,
  "status": "validated_aligned"
}
```

Status values are deliberately conservative. `validated_aligned` is strongest:
the raw entry pointer matched a SQLite row by `Block`/`Offset`, and the mapped
display text was present in that row. `db_text_evidence` means the display text
appeared somewhere in SQL/`DictFULLDB`, but not in an aligned row. SQL evidence
is not treated as authority because several app caches normalize or flatten
characters. For example, GENIUSEB validates many IPA/symbol gaiji through SQL,
but its cache omits some accent display forms that are present in `.uni`.

### `uni`

Inspect LogoVista `.uni` / `.UNI` gaiji mapping files.

```bash
logovista-tools uni /path/to/DICT/DICT.uni
logovista-tools uni /path/to/DICT/DICT.uni --json --limit 50
```

This command reports the detected layout, half/full record counts, mapped
records, fallback/search records, legacy/alternate fields, and raw 16-bit
fields for each record.

### `ga16`

Render LogoVista bitmap gaiji resources to portable PNG files.

```bash
logovista-tools ga16 /path/to/DICT/GA16HALF out/gaiji
logovista-tools ga16 /path/to/DICT out/gaiji --variants
logovista-tools ga16 /path/to/LogoVista out/gaiji --limit 10 --json
```

The input may be one resource file, one dictionary directory, or a collection
root. Directory inputs are grouped under `out_dir/DICT_ID/`. File names are
code-stable:

```text
hA126.png       half-width resource, single-variant output
zB121.png       full-width resource, single-variant output
hA126_n.png     black-theme variant
hA126_w.png     white-theme variant
```

Useful options:

```bash
--code A126                         render only one code; may be repeated
--limit N                           render at most N glyphs per resource
--variants                          write black _n.png and white _w.png variants
--foreground RRGGBB[AA]             ink color for single-variant output
--background RRGGBB[AA]             background color for single-variant output
--prefix auto|h|z|g                 choose filename prefix
--json                              emit a machine-readable summary
```

Default output is black ink on transparent background. `--variants` mirrors
the theme pattern used by packaged image resources such as `b13d_n.png` and
`b13d_w.png`.

### `titles`

Extract headword/title lines from expanded `*TITLE.DIC` components:

```bash
logovista-tools titles /path/to/LogoVista --out-dir titles
```

Output layout:

```text
titles/
  summary.json
  DICT_ID/
    titles_summary.json
    raw_titles.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "component": "FKTITLE.DIC",
  "line_index": 2,
  "text": "はこべ‐じお【塩】"
}
```

Title extraction is especially useful for dictionaries whose `HONMON.DIC` is a
placeholder table rather than a body stream.

### `menus`

Extract `MENU.DIC` menu lines, hierarchy, link labels, and destination
pointers:

```bash
logovista-tools menus /path/to/LogoVista --out-dir menus
logovista-tools menus /path/to/LogoVista --dict HAIKSAIJ --limit 50
```

Output layout:

```text
menus/
  summary.json
  DICT_ID/
    menus_summary.json
    raw_menus.jsonl
    menu_tree.json
```

Each JSONL row looks like:

```json
{
  "dict_id": "GENIUSEB",
  "dict_title": "ジーニアス英和大辞典",
  "component": "MENU.DIC",
  "line_index": 1,
  "section_code": null,
  "depth": 1,
  "path": ["はしがき"],
  "text": "はしがき",
  "links": [
    {
      "label": "はしがき",
      "destination": {
        "payload": "000256780002",
        "encoding": "bcd",
        "block": 25678,
        "offset": 2,
        "absolute_offset": 52586498,
        "target": {
          "component": "HONMON.DIC",
          "component_type": "00",
          "kind": "body",
          "start_block": 25678,
          "end_block": 64404,
          "relative_offset": 2,
          "offset_in_block": 2
        }
      }
    }
  ],
  "destination": {
    "payload": "000256780002",
    "encoding": "bcd",
    "block": 25678,
    "offset": 2,
    "absolute_offset": 52586498,
    "target": {
      "component": "HONMON.DIC",
      "component_type": "00",
      "kind": "body",
      "start_block": 25678,
      "end_block": 64404,
      "relative_offset": 2,
      "offset_in_block": 2
    }
  }
}
```

`menu_tree.json` contains the same records nested by inferred section depth.
Section depth is derived from the numeric ordering of section control codes in
that menu component, so `0001`/`0002`/`0003`, `0022`/`0023`, and similar style
families become practical tree levels.

Each destination is resolved against the `.IDX` component block ranges. The
`target` object names the component and classifies it as `body`, `menu`,
`title`, `index`, `media-image`, `media-audio`, `gaiji-resource`, or generic
`component`. `relative_offset` is the byte offset inside the expanded target
component.

### `indexes`

Extract lookup keys and pointer rows from expanded `*INDEX.DIC` components:

```bash
logovista-tools indexes /path/to/LogoVista --out-dir indexes
logovista-tools indexes /path/to/LogoVista --dict KOJIEN7 --component FHINDEX.DIC --limit 50
```

Output layout:

```text
indexes/
  summary.json
  DICT_ID/
    indexes_summary.json
    raw_indexes.jsonl
```

Each leaf row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "kind": "leaf",
  "component": "FHINDEX.DIC",
  "page_index": 98,
  "logical_block": 44310,
  "row_index": 12,
  "key": "?ASHURA'",
  "target_key": "?Ashura'",
  "body": {"block": 284, "offset": 1794},
  "title": {"block": 38005, "offset": 334},
  "tagged": true,
  "target_count_hint": 1,
  "continued_group": false
}
```

Useful options:

```bash
--dict NAME                         extract only matching dictionary ids
--component NAME                    extract only matching index filename(s)
--include-internal                  also emit binary-search tree branch rows
--limit N                           stop writing JSONL rows after N rows
--gaiji drop                        omit all unresolved gaiji in keys
--gaiji h-placeholder               keep half-width gaiji placeholders only
--gaiji placeholder                 keep half-width and full-width placeholders
```

### `fulldb`

Extract formatted bodies from LogoVista products that declare a `DictFULLDB`
payload in `DictList.plist`.

```bash
logovista-tools fulldb /path/to/LogoVista --dict KOJIEN7 --out-dir fulldb
```

This command still starts from the raw files:

1. Find a dictionary with `SSEDINFO` / `SSEDDATA`.
2. Expand `HONMON.DIC`.
3. Decode the 32-byte HONMON slots that contain decimal body IDs.
4. Resolve `DictFULLDB` from `DictList.plist`.
5. Emit HTML/plain body rows whose IDs were present in raw HONMON.

By default, this command only follows a `DictFULLDB` path declared by
LogoVista metadata. It does not grab arbitrary neighboring database files.
`--allow-db-fallback` is available for experiments, but it is intentionally
opt-in.

Output layout:

```text
fulldb/
  summary.json
  DICT_ID/
    fulldb_summary.json
    fulldb_entries.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "data_id": 755,
  "record_index": 754,
  "block": 13,
  "offset": 1602,
  "type": 2,
  "title": "アイ‐アイ 【aye-aye】",
  "html": "<rn></rn><a name=\"000007550000\"></a><div class=\"midashi\">...",
  "plain": "アイ‐アイ 【aye-aye】（啼なき声に由来。一説に..."
}
```

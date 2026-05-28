# CLI Command Reference

This page documents the `logovista-tools` command-line interface. Commands are
grouped by task: discovery, expansion, extraction, resources, package sidecars,
writing, and validation.

`src/lvcore-experimental` has its own separate reader-core CLI. It is
reader-only, does not import `logovista_tools`, and targets real LogoVista
package compatibility rather than authoring. See
[`src/lvcore-experimental/README.md`](../src/lvcore-experimental/README.md)
for `python3 -m lvcore` commands such as `search`, `render`, and resource
inspection. Audit and corpus scorecard commands live in the sibling
`lvcore-audit` package as `python3 -m lvcore_audit package` and
`python3 -m lvcore_audit corpus`. The lvcore CLI also exposes explicit resource inspection:
`resources`, `resource-info`, and `resource-bytes`, plus `sidecars` for
role/support inspection of SSED supplemental sidecars and `gaiji` for
dictionary-local gaiji display/resource readiness. `resources --include-gaiji`
lists package-level gaiji resources alongside entry resources. Resource bytes
are written only when requested with an output path; normal render output uses
safe `lvcore-resource://...` references. Sidecar BLOB media, GA16/GAI16 glyph
bytes, image-backed gaiji assets, and media store payloads remain untouched and
are not printed to the terminal by default.

## CLI Commands

### Console Output And Errors

Commands print lifecycle/progress status to stderr so a terminal user can see
that work has started, which package discovery is happening, and whether the
command finished cleanly. Machine-readable payloads such as JSON and JSONL stay
on stdout, so shell pipelines can still parse them safely.

Use `--verbose` before or after the subcommand for extra progress details.
Unexpected internal exceptions still include a traceback in verbose mode, while
common input/usage errors stay as concise CLI errors:

```bash
logovista-tools --verbose entries /path/to/LogoVista --limit 100
logovista-tools entries --verbose /path/to/LogoVista --limit 100
```

By default, common operator errors such as missing paths, directories supplied
where a file is required, and permission failures are reported as familiar
single-line CLI errors rather than raw Python tracebacks.

Commands that need package context, such as `entries`, `colscr`, and
`pcmdata`, expect a collection root, dictionary package directory, or direct
`.IDX` path. Supplying a component file such as `HONMON.DIC`, `COLSCR.DIC`, or
`PCMDATA.DIC` now fails with an explanation and an example command instead of
silently producing an empty result.

### Parallel Jobs

Corpus-scale commands support `--jobs`:

```bash
--jobs 1     run serially; this is the default
--jobs 8     use eight worker processes
--jobs 0     use os.cpu_count()
```

This applies to commands that operate across many dictionaries or resources:
`scan`, `extract`, `entries`, `resources`, `panels`, `colscr`, `pcmdata`, `extras`, `rendererdb`, `hc-render`, `hc-render-entry`, `reader-demo`, `repacker`,
`spindex`, `audit-honmon`, `gaiji-report`, `gaiji-readiness`, `ga16`,
`titles`, `indexes`, `menus`, `fulldb`, `profile`, `honmon-bytes`,
`opcode-atlas`, `component-forensics`, `dump-ir`, `dump-package-models`, LVED
payload inspection, LVLMultiView inspection, Hourei inspection, and SIZK SSED read-aloud
inspection.
`capability-matrix` is also corpus-oriented, but it reads already-generated
report directories rather than re-scanning raw dictionary files.

For local corpus work, use the host's exposed parallelism unless current memory
or I/O pressure says otherwise. A moderate explicit value such as `--jobs 8`,
`--jobs 16`, or `--jobs 36` can be useful when also writing many JSON/media
files. `--jobs 0` uses all available CPUs and is appropriate for CPU-heavy
audits on local SSD/NVMe storage, but it can be counterproductive on slow or
network-mounted disks.

### `scan`

Find LogoVista package families under one or more roots.

```bash
logovista-tools scan /path/to/LogoVista
logovista-tools scan /path/to/LogoVista --json
```

The scanner uses the shared package-target discovery layer. It detects:

- SSED packages by parseable `.IDX` / `.idx` files with `SSEDINFO` or Android
  `LVEDINFO` catalogs,
- `LVED_SQLITE3` payloads by `main.data` / `.dbc` files, plus Android
  hidden-package `DICT.db` SQLCipher payloads, using lightweight payload
  classification,
- LVLMultiView packages by `menuData.xml` plus `*lvdat` / `*lvbat` payloads,
- LogoVista電子法令 Professional / Hourei packages by `_DataBase/hore_base.db`,
  `_DataBase/hore_search_a.db`, `_DataBase/horejo_base.db`, and
  `_Programs/LVMultiView.exe`.

SSED rows include `HONMON.DIC`, `HONMON.DIN`, and Android `HONMON.DIW`
readiness where available. Mac OS X packages that declare `HONMON.DIN` but
store an extensionless ZipCrypto `HONMON` wrapper are resolved automatically
when the package has the observed eight-hex renderer/index stem needed for the
`casKet<stem>` password. Rows for SSED, LVED_SQLITE3, LVLMultiView, and Hourei
include a `title` and `title_source` in JSON output. LVED_SQLITE3 title probing
reads plaintext SQLite directly. Encrypted desktop LVED_SQLITE3 payloads are
probed with the local `.key` file by decrypting only the SQLite pages needed to
read title-bearing `info` rows; Android SQLCipher packages use viewer
`dictinfo.xml` metadata for title and key derivation. `scan` does not decrypt
the whole payload by default. Use `--decode-titles` only to allow a slow
full-decrypt fallback if the bounded page-level probe cannot identify a title.
Use `lved --inspect-sqlite` when you need full decrypted schema or feature
inspection with local keys or Android metadata.
Use `profile` when you need a redacted inventory that also records incomplete
packages with missing body components or missing declared components.

### `info`

Print metadata for either an `.IDX`, `.DIC`, or `.DIN`.

```bash
logovista-tools info DICT.IDX --all
logovista-tools info HONMON.DIC
logovista-tools info HONMON.DIN --try-decrypt
```

For `.IDX`, this lists component records, block ranges, component types, and
filenames. For `.DIC` / `.DIN`, this prints the SSED chunk count, declared
logical block range, and storage mode (`plain`, `logofont_cipher`, or the
observed Mac `macos_logofont_cipher` variant).

### `expand`

Decompress one plain or LogoFontCipher-encrypted `SSEDDATA` file:

```bash
logovista-tools expand HONMON.DIC honmon.expanded
```

The output is the expanded EPWING/JIS-style byte stream for that component.

### `decrypt`

Decrypt an observed LogoFontCipher file without SSED expansion:

```bash
logovista-tools decrypt HONMON.DIC honmon.ssed
logovista-tools decrypt HONMON.DIN honmon.ssed
logovista-tools decrypt vlpljblF vlpljblF.sqlite
```

Use this for sidecars that decrypt to another container such as SQLite or for
manual inspection of encrypted SSED components. For encrypted `HONMON.DIC` /
`HONMON.DIN`, `info`, `expand`, `entries`, and `audit-honmon` already decrypt
transparently when the optional crypto dependency is installed.
The command streams input to output, so large sidecars such as DAIJIRN4's
610 MB `vlpljblb` do not need to be loaded into memory.

### `vlpljbl`

Classify Windows `vlpljbl*` siblings across a package or corpus:

```bash
logovista-tools vlpljbl /path/to/LogoVista --jobs 0 --out-dir vlpljbl-audit
logovista-tools vlpljbl /path/to/DICT/vlpljblF --json
```

The command tests raw magic and LogoFontCipher-decrypted magic, optionally
opens SQLite payloads to report table names/columns/row counts, and writes a
redacted `summary.json`. It does not keep decrypted sidecars. Current role
labels include:

```text
logofont_decryptor_binary       PE decryptor program, usually vlpljbl.bin/.exe
font                            OpenType/CFF font, plain or LogoFontCipher
sqlite_renderer_body            body/searchable renderer rows
sqlite_renderer_body_with_media body rows plus media tables
sqlite_row_ordered_honbun_renderer_body
sqlite_honbun_data_id_body
sqlite_block_offset_body        SQLite rows carry raw Block/Offset/Body
sqlite_media_store              media-only store
sqlite_search_index             search/title index only
sqlite_category_search_index    KWIT category search tables
sqlite_examples_idioms          D_Example/D_Idiom-style supplemental rows
sqlite_supplemental             usage/supplemental rows such as D_Goyo
sqlite_link_reference           block/offset/title link-reference rows
sqlite_kanji_support            kanji lookup/support tables
sqlite_search_or_conjugation    search/conjugation helper tables
sqlite_template_navigation      template phrase/navigation helper DB
sqlite_android_index_metadata   Android UI/index metadata DB
sqlite_ancillary                chronology or tiny helper metadata DB
```

The same schema classifier is used for plain `.db` / `.sqlite` sidecars
outside the `vlpljbl*` naming convention, including Android package DBs and
Windows `EXINFO.INI` `SQLNAME` / `ROSQLNAME` references. Only body-capable
SQLite roles are accepted as `rendererdb` entry-body sidecars.

### `hc`

Inspect Windows `HC????.dll` HTML renderer plugins:

```bash
logovista-tools hc /path/to/LogoVista --jobs 0 --out-dir hc-audit
logovista-tools hc /path/to/DICT/HC014F.dll --json
```

The command parses enough PE metadata without third-party dependencies to
report:

- exported renderer hooks such as `epwing2HtmlBodydata`,
  `epwing2HtmlBodydataVertical`, `initializeSQL`, `pluginFunction`, and
  `getCustomCharacterDIB`;
- imported dictionary bridge API families such as body, gaiji, picture, SQL,
  and plugin hooks;
- the `EXINFO.INI` `HTMLDLL` declaration;
- sibling eight-hex-digit numeric indexes and whether one matches the HC code;
- sibling `vlpljbl*` resources;
- embedded renderer evidence such as `DIC????` tokens, `vlpljbl*` sidecar
  names, SQL snippets, HTML path templates, and image templates.
- `renderer_effects`, a behavior-level summary of the decoded entrypoints and
  bridge imports: body-byte access, Unicode-first/bitmap-fallback gaiji,
  picture controls, sound ranges, internal address links, Panel hooks, SQL
  search hooks, and private layout directives.
- `behavior_profile`, a per-renderer behavior contract that names the renderer
  family, implemented semantics, exact body strategy, and product-specific hook
  gaps.

`hc` emits discovery and per-file progress to stderr. Its JSON output is
redacted: it includes names, hashes, import/export names, and short embedded
format strings, but not dictionary body data.

The repository's current HC reverse-engineering notes group renderers by exact
SHA-256 binary. The combined Windows/recovered-GEN corpus has 158 HC files and
109 exact binary families; implementation status is tracked at that exact
binary-family level.

### `hc-render`

Render raw `HONMON.DIC` / `HONMON.DIN` body slices with the common behavior
observed in Windows `HC????.dll` renderer loops:

```bash
logovista-tools hc-render /path/to/DICT --limit 20 --out-dir hc-render
logovista-tools hc-render /path/to/DICT --compare-rendererdb --write-ziptomedia
logovista-tools hc-render /path/to/DICT --json --no-hash
```

This command is not an exact HC DLL emulator. It applies the shared renderer
semantics that are now understood:

- SSED JIS text, style, section, heading, literal, URL, and line-break controls;
- internal address links from paired link controls;
- `COLSCR.DIC` picture/media placeholders from `1f3c` / `1f4d`-style payloads;
- `PCMDATA.DIC` audio range links from `1f4a` / `1f6a`, plus MWALEARN-style
  A427/A428 `SoundData` sound-ID links, using the package `sound` image asset
  when present. Generated standalone HTML rewrites the browser-facing link to a
  safe `#lv-audio-...` fragment and preserves the recovered HC
  `lved.sond:...` target in `data-lv-original-href`;
- KOJIEN6-style `1f39` / `1f59` movie/video ranges, resolving the embedded
  eight-digit movie ID against sibling `_MOVIE` resources and preserving the
  recovered `lved.movi` target in metadata;
- Unicode gaiji first, then image-backed gaiji, then a safe placeholder;
- private renderer directive suppression with metadata counts;
- vertical-rendering hints as classes/metadata rather than hard-coded layout.

Product branch rules are implemented only after the renderer path is
understood. These are branch-subset implementations unless a representative
visual comparison and wrapper/section-state mapping proves more. The initial
proof case is `HC013A.dll`: `hc-render` recognizes its example-section
rule and inserts the discovered `exam` image when a `1f09 0011` example block
starts, matching the renderer's `exam.png` template without requiring a manual
`--section-image` option.
`HC0158.dll` also has a decoded raw-HONMON style-marker subset:
B3xx formatter codes become CSS spans for rank stars, part-of-speech,
conjugation, labels, and red emphasis; `PCMDATA.DIC` ranges render as
`sound.png` links when that template image is present; numbered/SVG gaiji remain
resource-backed images.
`HC0157.dll` has a decoded gaiji-plane style-marker subset:
A14D/A14E accent markers, B156/B15A/B15C/B160-B17D/B221-B226/B228-B22A/
B23C-B241 CSS span markers, B22D-B23B red circled-number wrappers, and
`sound.png` audio links. These mappings come from the renderer body loop and
the matching CSS/resource files; full product layout remains a named gap.
`HC0146.dll` has decoded marker branches whose output
templates are understood: B232/B233 color-font delimiters, B240 abbreviation
text, nonprinting B44F-B451/B236/B237/B241 template selectors, and classed
image-gaiji templates for B157-B159, B25A-B351, B23B, and B357-B424.
The all-family code-level pass has decompiled representatives for all 109 exact
HC SHA-256 families, but `hc-render` only implements shared semantics and
product rules whose branch tables and data paths are understood.
The generated top-level `hc_entries.html` is the primary visual inspection
file. If a supported renderer/app sidecar supplies exact entry-body HTML,
`hc_entries.html` uses that sidecar body rather than the raw dense-HONMON anchor
records, while `raw_honmon_entries.html` remains available for decoder
inspection. The command writes a normalized product stylesheet and copies
template/image assets next to the output HTML, including renderer `Templates/`
assets needed by sidecar bodies with bare `sound.png`-style references. Shared
fallback rules use low-specificity selectors so copied product CSS can override
generic fallback layout. This improves inspection quality, but the command
keeps exact DLL-hook parity separate from visual review status. A package may
report `reference_render_parity_confirmed=true` when its reviewed output is
locked, while still reporting `exact_hc_parity=false` because unexercised
`modifyHeadword*`, SQL/search, Panel, plugin/user-data, or reached custom-DIB
hooks remain named gaps. HC export/import capability alone is not treated as a
gap: `custom_dib_export` records `getCustomCharacterDIB`, and
`bridge_bitmap_import` records `SDicGetCustomCharacterBitmap`.

Each dictionary output directory contains:

```text
hc_render_summary.json
hc_entries.jsonl
hc_entries.html
raw_honmon_entries.html
```

When a body-capable renderer sidecar is present, `hc-render` automatically runs
the schema-backed sidecar resolver for clear `t_contents`, `HONBUN`, Android
body DB, media table, and ziptomedia cases, writing its own nested
`rendererdb/` output. `--compare-rendererdb` remains available as an explicit
request, but it is no longer required for dense renderer-sidecar packages.

The summary separates `exact_body_html_available`,
`reference_render_parity_confirmed`, and `exact_hc_parity`.
For example, a `t_contents` package may have exact sidecar body HTML while
still reporting product-specific gaps for Panel handlers, SQL search UI,
plugin callbacks, user-data hooks, reached custom gaiji bitmap/DIB branches,
or `modifyHeadword*`. The command does not fake exact product behavior.

### `hc-render-entry`

Render specific reference entries by resolving the query through the
dictionary's native index rows first, then dereferencing the matched body
pointer:

```bash
logovista-tools hc-render-entry /path/to/DICT crocodile --out-dir refs
logovista-tools hc-render-entry /path/to/DICT et --limit 1 --out-dir refs
logovista-tools hc-render-entry /path/to/DICT "heart-lung machine" --out-dir refs
```

This command is the preferred path for visual reference work. It does not use
lvcore and does not generate the whole dictionary just to find one entry. The
lookup scans parsed LogoVista index rows, scores exact and flattened matches
above prefix/contains matches, dereferences the body pointer into `HONMON`, and
then renders that entry with the same HC renderer profile used by `hc-render`.
For dense-HONMON/renderer-sidecar dictionaries, the command resolves the native
index hit to the renderer body sidecar when the schema is understood, so the
result is a real entry render rather than a raw numeric anchor.

For direct body-stream dictionaries, focused rendering uses the native index
body pointer as a HONMON landing address. It keeps a full generic `1f09 0001`
entry when the hit starts that slice, but can narrow a target that lands inside
a broader generic slice to the next native-index body boundary. That distinction
is what lets indexed child entries render independently without cutting off
example sections from normal primary entries.

For markerless streams where `1f09 0001` is not the body-entry marker,
`hc-render-entry` also uses native-index section boundaries as the entry
intervals. If the matched row points to visible text inside one of those
sections, the output starts at the closest preceding native-index section
boundary instead of rendering the whole HONMON component.

Some native rows target visible `1f41...1f61` heading spans inside a larger
generic marker group. In that shape, `hc-render-entry` treats the visible
heading as the focused entry start and stops at the next visible peer heading.
Empty heading spans used as renderer anchor stubs are ignored by this generic
rule.

Each output dictionary directory contains one HTML file per rendered hit plus
`matches.jsonl` and `summary.json`. The HTML filename is based on the requested
query, for example `et_1.html` or `crocodile_1.html`. Use `--limit` only when
the requested term legitimately has multiple matching entries.

### `hc-render-lock`

Render only dictionaries that have confirmed HC visual-parity locks and check
the generated reference HTML for regression hazards:

```bash
logovista-tools hc-render-lock /path/to/ssed-corpus \
  --out-dir reports/hc-render-lock --jobs 12
```

The command uses the reviewed reference-entry list in
`logovista_tools.hc_regression`, writes all generated HTML and JSON under
`reports/`, and exits nonzero when a guardrail fails. It checks that local
`img`/`object`/stylesheet/script references exist with exact on-disk casing,
that previous/next Windows reader chrome (`backH`, `forwardH`, `backV`,
`forwardV`) is absent, that table markup is balanced enough to avoid
browser-hostile nested-table explosions, and that known visual invariants such
as green hyperlink CSS, blue headword CSS for known Windows-reader profiles,
and text-scale inline SVG/image assets remain present.

### `reader-demo`

Generate a static web-reader proof page for one SSED dictionary package:

```bash
logovista-tools reader-demo /path/to/_DCT_HAESPJPN --dict HAESPJPN \
  --query danzar --query 再開 --out-dir reader-demo
```

This command is intentionally a proof-of-concept integration surface, not a
bulk extractor. It uses the HC entry renderer, native index lookup, decoded
Panel rows/cells, decoded `MENU.DIC` and `TOC.DIC` rows when a package has
those components, selected auxiliary indexes/resources, entry `lvaddr://`
links, copied template/media assets, and resolved `PCMDATA.DIC` or `SoundData`
audio files to
build one browser page under `<out-dir>/<DICT>/index.html`. Dense HONMON
packages that render through schema-backed sidecars are dereferenced through
the same HC rendering path as normal query renders; internal body IDs are not
used as visible entry labels when a body/title can be resolved.

The page contains:

- rendered seed entries from `--query`;
- pre-rendered targets from Panel rows, direct Panel cells, MENU/TOC body
  destinations, and one level of entry-internal links;
- a Panel navigator that preserves Panel hierarchy: root cells reveal child
  cells, child cells reveal decoded `.bin` rows, and row buttons load entries;
- `MENU.DIC` / `TOC.DIC` navigation rows when the package declares and supplies
  those components. Body targets load entries; decoded non-body targets such as
  TOC-to-TOC or MENU-to-MENU jumps either scroll to the embedded target row or
  open a bounded preview of the target menu region;
- selected `KWINDEX.DIC`, `KWTITLE.DIC`, `MUL*.DIC`, `MULTI*.DIC`,
  `RIGHT.DIC`, and `IDXJUMP.DIC` auxiliary resources where present. Some
  packages carry `RIGHT.DIC` / `IDXJUMP.DIC` as loose sidecars rather than
  declared catalog components; `reader-demo` samples those too. Auxiliary
  text/title streams are sampled with bounded prefix reads so large title
  components do not need to be fully expanded for a sidebar preview;
- `HANREI/` and `HANREI.chm` references where present. Loose HTML HANREI files
  are copied into the demo tree; CHM files are copied, and when `7z`/`7zz` is
  available their `.hhc` / `.hhk` sitemap files and HTML pages are extracted.
  The demo sidebar uses the sitemap labels and opens the first resolved HANREI
  page in an iframe immediately, rather than leaving the viewer blank;
- `CCALTSTR.HA` / `CCALTSTR.FU` alternate-string tables where present. These
  tables are also available to gaiji resolution as lower-priority Unicode
  fallbacks behind `.uni` and plist mappings;
- in-entry `lvaddr://` links that route to embedded entries. Decimal
  `lvaddr://00000042/0016` and hex-derived `lvaddr://0000002a/0010` forms are
  normalized to the same internal address key;
- PCMDATA and SoundData sound links rewritten to local WAV/MP3 files and a
  page-level audio player.

Useful sampling controls:

- `--menu-row-limit` and `--menu-preview-limit` bound MENU/TOC rows and
  menu-target preview rows.
- `--index-row-limit` bounds KW/MUL auxiliary index rows.
- `--hanrei-page-limit` bounds extracted/listed HANREI links.
- `--ccaltstr-row-limit` bounds alternate-string rows shown in the sidebar.

`reader-demo` reports missing menu components explicitly. Some Panel-capable
dictionaries do not declare `MENU.DIC`; some package copies declare `MENU.DIC`
but do not include the file. In those cases the generated page can still prove
Panel, entry-link, and PCMDATA behavior, but it cannot prove menu navigation
for that package copy.

### `repacker`

Losslessly parse and re-emit existing LogoVista dictionary files:

```bash
logovista-tools repacker /path/to/ssed-corpus \
  --out-dir /path/to/reports/logovista-tools-repacker-v2
```

`repacker` is a model-proof command for existing packages. It reads the package
`SSEDINFO` catalog, resolves each declared active component case-insensitively,
parses plain `SSEDDATA` headers, chunk offsets, command counts, init bytes, and
chunk payloads, then writes a repacked copy from that parsed structure. A file
passes only when the repacked bytes have the same SHA-256 as the original.
Because byte identity implies expansion identity, expanded-byte hashing is off
by default; pass `--expanded-hash` when a slower explicit expanded SHA-256 is
useful for a focused check.

The command also includes dictionary resource files that are not ordinary
catalog-declared components but are part of the dictionary model:

- encrypted `SSEDDATA` payloads are preserved byte-for-byte after decrypted
  header validation;
- `SPINDEX.DIC` is preserved byte-for-byte after standalone suffix-index
  inspection;
- eight-hex/numeric auxiliary `.idx` trees are preserved byte-for-byte after
  CP932 text-index parsing;
- `.uni` Unicode maps, `GA16HALF`/`GA16FULL`/`GAI16H*`/`GAI16F*` bitmap-gaiji
  resources, `CCALTSTR.*`, and Panel `.bin` files are parsed and preserved
  byte-for-byte;
- plain and LogoFontCipher SQLite databases are preserved byte-for-byte after
  SQLite/schema validation. SQLite byte identity is treated as physical
  packaging proof, not a claim that rebuilding a database from SQL rows would
  reproduce the same pages.

This is intentionally different from `write-plain`. `write-plain` authors new
SSED packages and uses the toolkit compressor. `repacker` preserves the
observed physical layout of an existing component and is therefore suitable for
checking whether the toolkit understands old package containers byte-for-byte.

Viewer/front-end and loose media assets are intentionally excluded: `HC*.dll`,
`.chm`, `HANREI`, HTML/CSS/JS/XML/DTD files, `.INI`, `Templates/`, `img/`,
`images/`, gaiji-template image directories, `_Sound_files`, ordinary bitmap/
audio assets, and the `vlpljbl.bin`/`vlpljbl.exe` decryptor binaries. Missing
catalog-declared components are reported separately because some historical
packages contain inert/missing declarations.

### `multiview`

Inspect LogoVista `LVLMultiView` packages such as ESPRANT2, YROPPO, and
MOROKU. These packages have an SSEDINFO-like `.IDX` facade and `menuData.xml`,
but the readable bodies are LogoFontCipher-encrypted SQLite payloads. The
observed law subfamily uses `blvbat`, `hlvbat`, `ilvbat`/`ilvdat`, `jlvbat`,
and `nlvbat`/`nlvdat`; ESPRANT2 uses `blvdat`.

```bash
logovista-tools multiview /path/to/multiview-corpus --jobs 12 --out-dir multiview-audit
logovista-tools multiview /path/to/_DCT_YROPPO08 --json
logovista-tools multiview /path/to/_DCT_ESPRANT2 --json
logovista-tools multiview /path/to/_DCT_ESPRANT2 --write-showcase --out-dir multiview-audit
```

Report fields include:

- the parsed `.IDX` facade layout and declared component records;
- missing declared component files;
- payload storage (`plain` or `logofont_cipher`);
- SQLite schema role, table families, row counts, and index counts;
- `menuData.xml` tag/attribute counts and `href` resolution status;
- bundled static HTML directories and dedicated viewer executable/DLL files;
- LogoFontCipher-encrypted `Resources/*` assets such as PDFs.
- bounded showcase validation counts for missing `href`, missing `src`,
  leaked `lved_*` protocol links, and browser-hostile unbalanced table/cell
  output.

Useful options:

```bash
--write-decrypted                 keep decrypted SQLite payload copies under out-dir
--write-decrypted-resources       with --write-decrypted, also decrypt Resources/* assets
--write-showcase                  write bounded reader-oriented HTML pages
--showcase-limit N                maximum menu/body targets to render
--jobs N                          inspect packages in parallel; 0 means all CPUs
--json                            emit machine-readable summary
```

Observed payload roles:

```text
blvdat                 content/search body; t_contents + t_search schema
blvbat                 law body tables; viewer name hint hore_body.db
hlvbat                 case digest/body table; viewer name hint hanrei_youshi.db
ilvbat / ilvdat        HTML index table; viewer name hint index.db
jlvbat / jlvdat        subject index table; viewer name hint jiko_sakuin.db
nlvbat                 law metadata; viewer name hint yroppo.db
nlvdat                 law metadata; viewer name hint mo6.db
```

`--write-showcase` rewrites observed LVLMultiView body links such as
`lved_mark:amark:*` and `lved_ref:ahsection:*` into bounded local links or
explicit deferred anchors. It also copies referenced package assets from
`Templates/`, `Help/image/`, and `Help/` into the private showcase output, so
the generated validation can be used as a reader-readiness smoke test rather
than just a static dump.

### `hourei`

Inspect LogoVista電子法令 Professional packages. This is a separate plain-SQLite
law-browser family and should not be classified as SSED, LVED_SQLITE3, or
LVLMultiView.

```bash
logovista-tools hourei /path/to/hourei-package-or-corpus --out-dir hourei-audit
logovista-tools hourei /path/to/hourei-package-or-corpus --deep --out-dir hourei-deep
logovista-tools hourei /path/to/hourei-package-or-corpus --write-showcase --query 民法 --query 会社法
```

Report fields include:

- top-level `_DataBase`, `_Kanpou`, `_KanpouImg`, `_Manual`, and `_Programs`
  file/byte counts;
- core SQLite schema summaries for `hore_base.db`, `hore_search_a.db`,
  `horejo_base.db`, `hore_kanpou.db`, `hore_aramashi.db`, and `USER.db`;
- corpus-wide table/column-set counts across the per-law shard databases;
- body-fragment HTML class, `href`, and `src` signals from sampled or full
  shard scans;
- structured link/readiness counts for `lved_ref&1`, `lved_ref&4`,
  `lved_ref&&`, `lved_mark&&`, `lved_unsafe`, and body image resolution;
- cached `_DataBase/HTMLs/H`, `_DataBase/HTMLs/I`, and `_DataBase/HTMLs/P`
  surface counts;
- manual/help and program file summaries.

Useful options:

```bash
--deep                            scan every per-law shard body row
--sample-rows N                   shard databases sampled without --deep
--write-showcase                  write bounded reader-oriented HTML pages
--query TEXT                      showcase search query; may be repeated
--showcase-limit N                maximum hits per showcase query
--jobs N                          inspect packages in parallel; mostly useful for multiple roots
--json                            emit machine-readable summary
```

The showcase preserves the stored HTML body fragments, substitutes the official
CSS placeholders in `LVLawBrowser_00.css`, rewrites known `lved_mark&&` and
`lved_ref...` links to local fragments/pages where possible, makes the observed
mode-4 future/revision law hooks inert with `data-lv-*` metadata, decodes the
CP932 `_Programs/index_panel.html` kana panel, writes cached law index/preview
pages where available, suppresses `lved_unsafe`, and copies resolved
`_DataBase/image/` assets. Its manifest includes validation counters for
missing local `href`/`src` targets and leaked live `lved_*` hrefs. It is a
practical reader probe, not a claim of exact official viewer UI parity.

### `sizk`

Inspect NHK 文学のしずく / SIZK read-aloud packages.

```bash
logovista-tools sizk /path/to/ssed-corpus --jobs 0 --out-dir sizk-audit
logovista-tools sizk /path/to/_DCT_SIZK0101 --write-playback-jsonl --out-dir sizk-audit
logovista-tools sizk /path/to/_DCT_SIZK0101 --json
```

The observed SIZK packages are SSED packages sold as a read-aloud set, not a
separate platform wrapper. They are not normal headword-index dictionaries.
Each package has a tiny `HONMON.DIC` with four entries. The first gaiji in each
entry selects one of four HTML templates:

```text
b121  overview page
b122  author page
b123  narrator page
b124  playback page
```

The command resolves:

- `EXINFO.INI` declarations such as `HTMLDLL=HC0190.dll`, `MP3NAME`, and
  `GAIJI=shizuku.uni`;
- the tiny HONMON entries, section codes, template selector gaiji, and
  template paths under `HTMLs/`;
- the simple12 `shizuku.uni` gaiji file, including blank template-selector
  records and real mapped records;
- loose read-aloud sidecars: `shizuku.mp3`, `shizuku_honbun.txt`,
  `shizuku_time.txt`, and `Templates/honbun.html`;
- synchronized playback row counts, duration, samples, and mismatch issues.

`--write-playback-jsonl` writes the full aligned time/text rows to
`DICT_ID/playback.jsonl` and keeps `summary.json` concise by linking to that
file.

### `lved`

Inspect modern LogoVista `LVED_SQLITE3` packages whose body data lives in
SQLCipher `main.data`, `.dbc`, or Android hidden-package `DICT.db` payloads
rather than SSED/HONMON. The command keeps the historical `lved` name, but the
format distinction is the decrypted SQLite3 data model rather than the viewer
executable name.

```bash
logovista-tools lved /path/to/lved-sqlite3-corpus --json
logovista-tools lved /path/to/lved-sqlite3-corpus --jobs 12 --inspect-sqlite --json
logovista-tools lved /path/to/lved-sqlite3-corpus --jobs 12 --inspect-reader --search 犬 --search-mode exact --search-mode forward --json
logovista-tools lved /path/to/KOJIEN7 --search 右 --render-dir /tmp/koji-lved-pages
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/KQCMPROS --dict-id 751 --json
```

Useful options:

```bash
--dict-id N                         numeric dictionary id used by the viewer metadata path
--dict-code CODE                    product/dictionary code when it cannot be inferred
--key-file PATH                     read an explicit local SQLCipher key; never print it
--memory-dump PATH                  count/test local memory-dump key candidates without printing them
--write-decrypted PATH              write one plaintext SQLite copy for local analysis
--inspect-sqlite                    inspect decrypted SQLite schema, table roles, and bounded feature signals
--inspect-retained                  parse retained res/*.idx, tree.idx, and *INDEX.DIC resources
--inspect-loose-resources           inspect loose image/font/runtime resource signatures and dimensions
--inspect-reader                    inspect reader surfaces, render signals, retained tree use, and bounded search probes
--search QUERY                      run a real SQLite search probe; may be repeated
--search-mode MODE                  exact, forward, backward, partial, fulltext, filter, advanced, advanced1, advanced2, or advanced3
--search-limit N                    maximum hits per query/mode
--render-dir PATH                   write generic static HTML pages for search hits/info pages
--render-link-limit N               maximum linked lved.dataid pages to materialize before writing stubs
--json                              emit machine-readable JSON
```

If a payload has a sibling `DICTCODE.key`, `main.data.key`, payload-stem key,
or exactly one `.key` file in the payload directory, the command uses that key
automatically for validation and `--write-decrypted`. Reports include only the
key file name/match rule, never the key text.

For Android SQLCipher packages, the command can also use nearby viewer
`dictinfo.xml` metadata. The metadata supplies the numeric dictionary id,
dictionary code, title, and font references; the derived key is tested locally
but never emitted. Android font names are reported as external runtime
dependencies, not as missing dictionary resources.

`--write-decrypted` requires exactly one discovered payload and a local `.key`,
Android `dictinfo.xml` metadata, `--dict-id`, or `--key-file`. Memory dumps can
validate candidates, but the command does not use memory-dump-only recovery to
write decrypted output.

Report fields include payload classification, size, SHA-256, sampled entropy,
inferred dictionary code, and key-validation status. With `--inspect-sqlite`,
reports also include exact SQLite schema, table roles, row counts, small value
distributions, and bounded text-signal evidence. Text-signal counts are sampled
from inspected columns and are not complete row-text exports. Reports
deliberately do not emit explicit, derived, memory-dump-recovered keys,
decrypted databases, or dictionary row text.

`--inspect-retained` parses package-local SSED-like resources retained beside
the SQLCipher payload. Observed decoded roles include UTF-8/CP932 text
navigation trees targeting SQLite `content.id`, GENIUS53's CP932 auxiliary
hex-tree rank/filter file, and GENIUS53 retained SSEDATA `BHINDEX.DIC` /
`BKINDEX.DIC` indexes. Pair it with `--inspect-sqlite` to correlate tree
targets against SQLite tables.

`--inspect-loose-resources` parses loose resource files by file signature rather
than extension alone. It reports image media types and dimensions, font
container signatures, web-runtime text encodings, PDFs, desktop metadata files,
and unknowns. It does not emit HTML/CSS/JS/resource text.

`--inspect-reader` adds reader-facing analysis: `info` home/HANREI/copyright
surfaces, body fragment vs full-document signals, top HTML classes,
`lved.*` protocols, retained tree summaries, optional-provider readiness, and
optional real search probes.

Search probes use the recovered native model:

- `exact`: `filter` boundary token match such as `∥query∥`, with `forward`
  equality fallback.
- `forward`: FTS `forward:<query>*`.
- `backward`: reversed query against FTS `back`.
- `partial`: character-token FTS against `part`.
- `fulltext`: FTS against `fts`.
- `advanced`/`advanced1`/`advanced2`/`advanced3`: optional product-specific
  FTS columns. READERS3/RPLUSREV use a secondary `searchsub`/`listsub`
  provider for `advanced3`.

`--render-dir` writes a private generic HTML showcase. It preserves SQLite
body/info fragments inside a simple wrapper, rewrites `lved.dataid:` and
`lved.info:` links to local pages, rewrites `lved.binran:` links when a
`binran` table exists, resolves `lved.image:` / `lved.imag:` and
`lved.media.sound:` assets through SQLite `media`, `mediasub`, `sound`, or loose
resources, resolves `lved.pdf:` media links, rewrites GENIUSE6-style
`lved.dict.<code>:pictlink...` picture-dictionary links to generated info
pages, writes retained text-tree pages when decoded `res/*.idx` trees are
present, and writes bounded entry stubs for linked entries. Retained tree rows
whose entry targets are valid but outside the materialization budget are marked
with `data-lv-deferred-entry` rather than emitted as broken local links.
Viewer/user-state hooks such as `lved.bookmark:` and `lved.plugin:` are
suppressed to inert links with the original target retained as metadata.
SSED-style `lved.addr...` links are resolved through same-block HTML anchors
when that bridge is present, otherwise deferred the same way. Address-style
image links such as `lved.imag00001234:0567:0000002c` are currently deferred.
Bare numeric `href` values in LVED entry HTML are treated as content-id links.
Relative appendix hooks like `050000/0000` are currently preserved as deferred
metadata because their info/content bridge is not proven.
The generated pages are for structural testing; exact LVEDVIEWER visual parity
still needs product-template/CSS/JS handling.

### `compose`

Create an EPWING-like image by expanding each component listed by the `.IDX`
and placing it at its declared logical block.

```bash
logovista-tools compose DICT.IDX book.expanded --quiet
```

This is useful for binary comparison and for understanding the book layout.
It does not turn LogoVista into a fully standard EPWING book; it just writes
the expanded components into the block positions specified by `SSEDINFO`.

### `extract`

Interactive end-user extraction for one dictionary package.

```bash
logovista-tools extract /path/to/_DCT_DICT
```

The command discovers the package `.IDX`, derives the output directory name
from the dictionary id, asks what to extract, and writes a tree like:

```text
DICT_ID/
  extract-summary.json
  entries/
    entries.json
    entries.csv
    entries.txt
  titles/
  indexes/
  menus/
  media/
  audio/
  gaiji/
    gaiji-map.tsv
    ga16-bmp/
  sqlite/
  vlpljbl/
```

`extract` is meant for people who want usable data without knowing SSED
component names. It automatically chooses raw `HONMON.DIC` extraction for plain
body dictionaries and renderer/DICT.db sidecar extraction for dense
HONMON/body-database dictionaries when that structure is present. Gaiji Unicode
maps are applied during entry/title/index/menu decoding. Optional categories
include:

```text
entries   full entries as JSON, CSV, and/or newline-separated text
sqlite    applicable plaintext/decrypted SQLite database copies
media     COLSCR images and PCMDATA/SoundData audio as readable files
indexes   all title streams, index rows, and menus
gaiji     gaiji-map.tsv plus GA16/GAI16 glyph BMP files and image-backed gaiji assets
vlpljbl   non-executable vlpljbl resources, decrypted when LogoFontCipher-backed
```

For automation, the same command can run non-interactively:

```bash
logovista-tools extract /path/to/_DCT_DICT --yes --all --out-dir extracted
logovista-tools extract /path/to/_DCT_DICT --yes --entries --formats json,txt
```

### `entries`

Extract readable body entries from expanded `HONMON.DIC` / `HONMON.DIN`.

```bash
logovista-tools entries /path/to/LogoVista --out-dir bodies
logovista-tools entries /path/to/DICT/DICT.IDX --limit 20 --print
logovista-tools entries /path/to/DICT/DICT.IDX --limit 20 --html --print --print-format html
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
  "dict_id": "SYNTH",
  "dict_title": "Synthetic Dictionary",
  "entry_index": 2,
  "block": 25769,
  "offset": 158,
  "length": 3048,
  "heading": "sample headword",
  "body": "sample headword\nsample definition line"
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
--print                             print emitted entries to the terminal after writing JSONL
--print-format text|html|jsonl      terminal format used with --print
--media-placeholder                 preserve 1f4d media payloads as placeholders
--section-markers                   preserve 1f09 section markers as placeholders
--section-image CODE=IMAGE_KEY      insert a named image at a section marker in HTML output
--index-boundaries                  also use parsed index body pointers as entry boundaries
--full-scan                         use full HONMON expansion and forensic boundary accounting
--no-skip-dense-marker-honmon       force extraction on placeholder HONMON
```

By default, `entries` uses a streaming HONMON reader and stops as soon as the
requested `--limit` is emitted. Partial streaming summaries report
`entry_markers_seen` and leave `entry_markers` null until the marker scan is
complete. `--index-boundaries`/`--full-scan` keep the older forensic behavior
for dictionaries whose entries need index-derived boundaries.

When `--html` is used, rows include `body_html` in addition to `body`.
Use `--print` for interactive browsing in the terminal; without it, extracted
entries are written to `raw_entries.jsonl` under `--out-dir`.
PNG-backed gaiji are rendered as package-relative image tags such as:

```html
<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d">
<img src="resource/kmkimges/b167_1.png" alt="b167" class="lv-gaiji lv-gaiji-b167">
```

Exporters for Yomitan, MDict, or another HTML-capable format should copy the
referenced PNG files into the target package and rewrite `src` paths if their
archive layout differs.

`--section-image` is intentionally explicit for the generic `entries` command.
For HAESPJPN, `0011=exam` is a useful raw-entry rule because `0011` marks
Spanish example lines and `exam.png` is the dictionary's example badge. The
HC-aware `hc-render` command now applies the `HC013A.dll` rule automatically
and groups contiguous `0010`/`0011`/`0012` example sections.

### `audit-honmon`

Audit whether raw `HONMON.DIC` plus raw `*INDEX.DIC` pointers can produce
coherent body text.

```bash
logovista-tools audit-honmon /path/to/LogoVista --out-dir honmon-audit
logovista-tools audit-honmon /path/to/LogoVista --dict KOJIEN7 --json
```

Default output layout:

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
                                    to a renderer/app SQLite sidecar
dense_honmon_id_table_androiddb     HONMON stores raw numeric IDs that resolve
                                    to an Android app body database
idx_title_only_no_readable_honmon_body
                                    indexes/titles exist, but sampled HONMON bodies do not
skipped_dbc                         .dbc-adjacent package skipped by default;
                                    use lved for LVED_SQLITE3 payloads
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

An earlier Windows SSED corpus run accounted for 3,497,793,539 expanded HONMON
bytes with zero unknown controls, zero unknown bytes, zero invalid JIS cells,
and one known truncated final `0x1f` byte in `NANDOKU3`.

### `opcode-atlas`

Build a corpus-wide atlas of `0x1f` text-stream control opcodes from expanded
SSED text components. The command scans `HONMON.DIC`, `MENU.DIC`,
`*TITLE.DIC`, and text-like TOC/RIGHT/IDXJUMP/INDEX components. It does not
scan binary B-tree index pages as text.

```bash
logovista-tools opcode-atlas /path/to/LogoVista --jobs 0 --out-dir opcode-atlas
logovista-tools opcode-atlas /path/to/LogoVista --dict KQLATINO --json
```

Output layout:

```text
opcode-atlas/
  opcode_atlas.json
  opcode_atlas.md
  per_dictionary/
    DICT_ID.json
```

The merged atlas records, for each opcode:

```text
count
payload_lengths
component roles and component types
dictionary list and dictionary count
common payload values and prefixes
previous/next control context
sample text/control context
classification family and confidence
```

The current full corpus pass scanned 547 text-stream components and
7,026,978,819 expanded bytes. It observed 713,941,069 controls and 40 distinct
`0x1f` opcodes. The only singleton anomaly is `25IGAKU` `FHTITLE.DIC` `1f1f`,
which appears as a malformed standalone title-stream sequence between two title
lines. It is treated as a vendor data defect rather than a global opcode model
gap.

### `component-forensics`

Forensically account for non-HONMON core components: `MENU.DIC`,
`*TITLE.DIC`, structured `*INDEX.DIC`, `MULTI*.DIC` selector descriptors,
text-like `RIGHT.DIC` / `TOC.DIC` / `IDXJUMP.DIC` / `INDEX.DIC` outliers,
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
coverage, MULTI selector references and embedded selector payload accounting,
`.uni` record/trailer counts, GA16 glyph byte coverage, `COLSCR` media record
coverage, and `PCMDATA` referenced-range coverage. It does not
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

The component pass prints progress as each dictionary finishes, which matters
for large multi-core corpus runs. Remaining residuals are reported by exact
dictionary/component instead of silently skipped.

### `capability-matrix`

Build a writer/exporter capability matrix from Decoded LogoVista Model v0
reports. This is now the preferred path because the package model is the
authoritative place where package family, HONMON shape, body source,
gaiji/media/menu readiness, sidecar requirements, and unresolved issues are
normalized.

```bash
logovista-tools capability-matrix \
  --model-dir out/package-model \
  --out-dir out/capability-matrix
```

Legacy redacted-report mode is still available for older audit outputs. It
combines the older `profile`, `honmon-bytes`, and `component-forensics`
reports, but new corpus work should prefer `--model-dir`.

```bash
logovista-tools capability-matrix \
  --profile-dir out/profiles \
  --honmon-bytes-dir out/honmon-bytes \
  --component-forensics-dir out/components \
  --out-dir out/capability-matrix
```

In legacy mode, if a `gaiji-readiness` report is available, pass it to refine
the gaiji column from blunt unresolved-span counts into display/search
readiness:

```bash
logovista-tools capability-matrix \
  --profile-dir out/profiles \
  --honmon-bytes-dir out/honmon-bytes \
  --component-forensics-dir out/components \
  --gaiji-readiness-dir out/gaiji-readiness \
  --out-dir out/capability-matrix
```

Output layout:

```text
capability-matrix/
  summary.json
  capability_matrix.json
  capability_matrix.csv
  capability_matrix.md
```

The matrix records one row per dictionary:

```text
target_path
model_path
package_family
platform
raw_honmon_body
indexes_fully_parsed
titles_fully_parsed
gaiji_fully_resolved
media_refs_resolved
menu_pointers_resolved
unknown_controls
unknown_bytes
structural_text_issues
read_existing_status
read_existing_blockers
export_existing_status
export_existing_blockers
author_core_ssed_v0_status
author_core_ssed_v0_blockers
lossless_repack_existing_status
lossless_repack_existing_blockers
```

Status values are `yes`, `partial`, `no`, `n/a`, or `unknown`.
The readiness profile statuses are planning signals:

```text
green   no observed blocker for that profile
yellow  usable with degradation or dictionary-specific rules
red     blocked without more reverse engineering or a non-core sidecar path
gray    not applicable to this package family
```

`read_existing_status` asks whether the current model can parse/read the
existing package as a package. `export_existing_status` asks whether the
existing dictionary can be converted to an external format from current model
evidence. `author_core_ssed_v0_status` asks whether the observed SSED core is
stable enough for a clean plain-HONMON authoring subset. `lossless_repack_existing_status`
is stricter: it asks whether the observed package can be reproduced/repacked
with its existing sidecars, menus, media, and title/index structures.

Dense-HONMON packages can still be `read_existing_status=green`: the model can
classify the HONMON shape and expose dereference records. They may remain red or
yellow for `export_existing` or `lossless_repack_existing` until the requested
output path consumes the sidecar body provider. Dense-HONMON writing is outside
`author_core_ssed_v0`.

Compatibility aliases (`legacy_writer_v0_status`, `lossless_repacker_status`,
and `writer_repacker_status`) remain in CSV/JSON output for older scripts, but
new work should use the four explicit profiles above.

With `--model-dir`, this command does not re-parse dictionaries. It reads
`*_decoded_model_v0.json` reports and derives matrix rows from the model's
shared readiness object. For chunked model reports, it reads each bundle's
`package.json`. This prevents command-specific shape/status drift.

### `dump-ir`

Emit entry-level lossless span JSONL from expanded `HONMON.DIC`. This is the
debug/model path, not a user-facing readable extractor. It is an entry-span
subset of the draft [Decoded LogoVista Model v0](../spec/decoded-model-v0.md),
not the full package-level model.

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

### `dump-package-model`

Emit one package-level [Decoded LogoVista Model v0](../spec/decoded-model-v0.md)
JSON report for one dictionary package. This is the consolidation command for
reverse engineering: it gathers the current component table, wrapper evidence,
HONMON classification, entry spans, title/index/menu summaries, gaiji
resources, media resources, Windows sidecars, first-class dereference records,
package-family notes, and inconsistencies into one object.

```bash
logovista-tools dump-package-model /path/to/_DCT_HAESPJPN --out-dir package-model
logovista-tools dump-package-model /path/to/LogoVista --dict HAESPJPN --no-raw --out-dir package-model
logovista-tools dump-package-model /path/to/_DCT_OXFPEU4 --out-dir package-model
```

The output file is:

```text
package-model/
  DICT_ID_decoded_model_v0.json
```

Important options:

```bash
--entry-limit N                    sample N HONMON entry spans; use 0 for all
--profile-max-slices N             sample N body slices for package profiling; use 0 for all
--title-limit N                    sample N title rows; use 0 for all
--index-limit N                    sample N index rows; use 0 for all
--menu-limit N                     sample N menu nodes; use 0 for all
--media-limit N                    inspect N COLSCR/PCMDATA references; use 0 for all
--deep-sidecars                    open/decrypt renderer DB sidecars for verified body-link dereferences
--full-profile-indexes             run the exhaustive profile index scan
--full-entry-boundaries            collect all index-derived HONMON boundaries
--skip-row-models                  skip title/index/menu row extraction
--gaiji-readiness                  embed gaiji display/search readiness summary
--renderer-sidecar-gaiji           use renderer sidecars for contextual gaiji evidence
--no-raw                           omit raw_hex from embedded entry spans
--no-padding-spans                 omit padding spans from embedded entry spans
--include-internal-indexes         include internal/auxiliary index files
--chunked                          write package.json + JSONL files
--json                             print the model JSON to stdout as well
```

By default, `dump-package-model` is bounded for practical per-package use: it
does not run the expensive full profile index scan, and sampled entry spans use
marker boundaries unless `--full-entry-boundaries` is requested. Use
`--skip-row-models` for very large packages when the model only needs component
and capability summaries.

The command is intentionally not pretty. It is a contradiction finder and the
current package-level authority. Missing declared components, empty raw-body
packages, unresolved menu targets, raw gaiji gaps, sidecar-only body evidence,
and sampled decode failures are emitted as `inconsistencies` records rather
than hidden behind command-specific output. The emitted `readiness` and
`writer_readiness` objects are what `capability-matrix --model-dir` consumes.

Dereference rows connect raw pointers to targets. They include dense HONMON
anchor records, DictFULLDB/renderer/Android body links, index body/title
pointers, `MENU.DIC` destinations, and `COLSCR.DIC`/`PCMDATA.DIC` media
references. Without `--deep-sidecars`, encrypted renderer DB body links are
structural `unverified` records; with `--deep-sidecars`, readable renderer DB
rows are checked where the schema is supported.

`dump-package-model` is family-aware. SSED packages receive the full SSED model.
`LVED_SQLITE3` and LVLMultiView packages receive family-specific
non-SSED models: they are classified, their obvious payloads/resources are
summarized, and SSED HONMON parsing plus SSED writer readiness are marked not
applicable. This keeps mixed corpus runs honest without forcing separate
package families through SSED parsers.

Use `--chunked` when output size matters:

```bash
logovista-tools dump-package-model /path/to/_DCT_HAESPJPN \
  --chunked \
  --out-dir package-model
```

This writes `package-model/HAESPJPN_decoded_model_v0/package.json` plus JSONL
record files for components, entries, title/index/menu samples, gaiji, media,
dereferences, and issues. `package.json` keeps the normal decoded-model schema
and can still be consumed by `capability-matrix --model-dir`.

### `dump-package-models`

Emit Decoded LogoVista Model v0 reports for every package under one or more
corpus roots. This is the corpus-scale harness that should feed capability
matrix and writer-readiness planning.

```bash
logovista-tools dump-package-models /path/to/LogoVista \
  --out-dir /path/to/reports/model-v0 \
  --jobs 0 \
  --resume \
  --progress \
  --gaiji-readiness \
  --chunked
```

The command discovers SSED `.IDX` catalogs, `LVED_SQLITE3` `main.data` / `.dbc` payloads,
and LVLMultiView `menuData.xml` packages. SSED packages receive full decoded
models. `LVED_SQLITE3` and LVLMultiView packages receive family-specific non-SSED models
so the corpus summary remains complete without forcing those formats through
SSED parsers.

Default output layout:

```text
model-v0/
  targets.json
  summary.json
  failures.json
  failures/
    *_failure.json
  ssed/
    DICTID_<pathhash>_decoded_model_v0.json
  lved_sqlcipher/
    DICTID_<pathhash>_decoded_model_v0.json
  multiview_sqlite/
  hourei_sqlite/
    DICTID_<pathhash>_decoded_model_v0.json
```

With `--chunked`, each package path is a bundle directory:

```text
model-v0/
  targets.json
  summary.json
  failures.json
  ssed/
    DICTID_<pathhash>_decoded_model_v0/
      package.json
      components.jsonl
      entries.jsonl
      titles.jsonl
      indexes.jsonl
      menus.jsonl
      gaiji.jsonl
      media_refs.jsonl
      media_records.jsonl
      dereferences.jsonl
      issues.jsonl
      metrics.json
```

Important options:

```bash
--jobs 0                         use all detected CPU cores
--resume                         skip existing valid decoded model JSON files
--progress                       print one status line as each package finishes
--dict ID                        limit to one dictionary id; can be repeated
--family FAMILY                  limit to ssed/lved_sqlcipher/multiview_sqlite/hourei_sqlite
--allow-failures                 exit 0 even if some workers fail
--gaiji-readiness                embed gaiji readiness in each model
--renderer-sidecar-gaiji         use renderer sidecars as contextual gaiji evidence
--chunked                        write one package.json + JSONL bundle per package
--skip-row-models                skip title/index/menu row extraction
--no-raw                         omit raw_hex from embedded entry spans
```

Worker failures are written to `failures/*.json` and summarized in
`failures.json`. Console output stays short; detailed tracebacks are stored in
failure JSON so long corpus runs remain inspectable. Progress lines include the
relative target path, so same-title packages in SSED/LVED/iOS/Android/Windows
collections remain distinguishable while the run is active.

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
`manual/contents/img`. Platformless/core-SSED packages can use generic
portable folders such as `res`, `resources`, lowercase `templates`, `image`,
or `images`. Some Windows packages also ship a sibling companion directory
such as `_DCT_KANJIGN5_GAIJI`; the resource scanner checks those `*_GAIJI`
directories too. The scanner checks all of those locations,
groups theme variants such as
`b13d_n.png` / `b13d_w.png` and Android-style `b167_1.png` / `b167_3.png`, and
reports code-like resources such as `b13d` or `b167` as image-backed gaiji.
PNG, GIF, JPEG, WebP, BMP, and SVG files are all treated as portable package
resources.
Named images such as `exam.png`, `esp.png`, or `jpn.png` are reported as package
resources for format exporters to use when reconstructing dictionary-specific
styling.

### `loose-media`

Decode loose media/resource files that sit outside the main SSED component
catalog.

```bash
logovista-tools loose-media /path/to/_DCT_BRI2017P_Media_whatday
logovista-tools loose-media /path/to/_DCT_BRI2016_Media/top --json
logovista-tools loose-media /path/to/_DCT_PROYAL53/dat --write-decoded --out-dir loose-media
logovista-tools loose-media /path/to/multiview-corpus --write-decoded
```

Handled families:

- Britannica `whatday/*.body` and `whatday/*.top`: CP932 HTML fragments with
  month/day identity and decoded `lved.addrXXXXXXXX:YYYY` address links.
- Britannica `top/top_*.dat`: CP932 five-line records containing id, title,
  description, hexadecimal body address, and image filename. Image names are
  resolved against sibling `thumb/`, `mini/`, and `full/` directories.
- PROYAL53 `dat/*`: extensionless LogoFontCipher-wrapped `RIFF/WAVE` audio
  files exported as `.wav`.
- LVLMultiView law `Resources/inshizei`, `minji`, `zenkoku`, and `zeihou`:
  extensionless LogoFontCipher-wrapped PDFs. MOROKU23 uses the Mac OS X
  LogoFontCipher variant; the other observed MOROKU resources use the Windows
  variant.
- Extensionless renderer SQLite sidecars such as CJJC160's dict-code-named
  file: LogoFontCipher-wrapped SQLite exported as `.sqlite`.

By default the command reports structural metadata and decoded relationships.
Use `--include-text` only when you explicitly want plaintext/HTML fields in
the JSON output. Use `--write-decoded` to write decrypted `.wav`, `.pdf`, or
`.sqlite` payloads; bytes are not rewritten beyond decryption.

### `panels`

Decode LogoVista Panel metadata, inline Panel cells, and Panel `.bin`
navigation tables.

```bash
logovista-tools panels /path/to/LogoVista --dict ZYAKUKOG
logovista-tools panels /path/to/DICT --include-unreferenced --json
logovista-tools panels /path/to/Resources/Panels.plist
```

The command parses Windows `Panels.xml`, Mac/mobile `Panels.plist`, and nested
mobile menu plists where present. It resolves external `type=bin` references
against package-local `Panel/`, `bin/`, package-root, and sibling `_Panel`
locations with case-insensitive path handling and normalized path separators.
If no SSED `.IDX` package is found, `panels` falls back to treating the
argument as a direct Panel metadata file or resource directory; this is useful
for inspecting app/plugin resource bundles that contain `Panels.plist`,
`Panel.html`, and `Cell.html` without the dictionary catalog files.

Decoded output includes two navigation surfaces:

- inline cells from menu panels, including `lved.panel:<panel-id>` transitions
  and `lved.addr<block>:<offset>` direct address actions;
- external `.bin` rows, where each label resolves to an SSED block/offset
  target and, when an `.IDX` catalog is available, to the target component
  kind such as `body` or `menu`.

When `--out-dir` is used, the command writes `summary.json` and a flat
`navigation.jsonl` file suitable for a reader/sidebar UI. `summary.json`
contains the decoded metadata, inline cells, sampled BIN rows, full navigation
rows, target-kind counts, and parse failures. `navigation.jsonl` contains one
row per inline cell or BIN record.

The parser handles the common little-endian address-label grammar, id-prefixed
rows, declared-count mismatch tables, empty/zero-width tables, and the observed
headerless big-endian UTF-8 mobile table. `vlpljbl.bin` is intentionally ignored
because that name is a decryptor binary, not Panel data.

Panel rows are optional navigation/sidebar data. They are not merged into normal
entry body extraction and are not a replacement for native index search.

### `colscr`

Inspect or extract images stored in `COLSCR.DIC` and referenced by raw
`HONMON.DIC` media controls.

```bash
logovista-tools colscr /path/to/LogoVista --dict OUKOKU11 --out-dir colscr
logovista-tools colscr /path/to/DICT/DICT.IDX --write-media --out-dir colscr
```

Do not pass `COLSCR.DIC` directly. The command needs the package `.IDX` or
directory so it can scan `HONMON.DIC` media controls and then resolve the
target records in `COLSCR.DIC`.

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

With `--limit`, `colscr` scans `HONMON.DIC` through the random SSEDDATA reader
and stops after the requested media references instead of expanding the whole
body stream first. Full runs without `--limit` still scan the complete stream.

### `pcmdata`

Inspect or extract audio/media records referenced by raw `HONMON.DIC`
controls. The normal backing store is `PCMDATA.DIC`. If a package has no
physical `PCMDATA.DIC`, the same command can also decode MWALEARN-style loose
`Sound/SoundData` plus `Sound/WaveFile.map`, or KOJIEN6-style sibling
`_PCM_U` encrypted MP3 resources plus `_PCM_U/WaveFile.map`.

```bash
logovista-tools pcmdata /path/to/LogoVista --dict HAESPJPN --out-dir pcmdata
logovista-tools pcmdata /path/to/DICT/DICT.IDX --write-audio --out-dir pcmdata
logovista-tools pcmdata /path/to/_DCT_MWALEARN --dict MWALEARN --write-audio
logovista-tools pcmdata /path/to/_DCT_KOJIEN6 --dict KOJIEN6 --write-audio
```

Do not pass `PCMDATA.DIC` directly. The command needs the package `.IDX` or
directory so it can scan `HONMON.DIC` audio controls and then resolve the
target ranges in `PCMDATA.DIC`, `Sound/WaveFile.map`, or `_PCM_U/WaveFile.map`.

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

For `PCMDATA.DIC`, the manifest includes the raw 16-byte payload, decoded
logical block range, visible link label when present, codec, chunk layout,
sample rate, channel count, bit depth, source (`honmon` or `unreferenced`), and
optional output filename. For `SoundData`, the manifest records the A427/A428
HONMON marker position, hexadecimal sound ID, `WaveFile.map` row, RIFF/WAVE
chunk layout, and optional output filename. For `_PCM_U`, the manifest records
the matched decimal start block, encrypted numeric file stem, decrypted MP3
classification, and optional output filename.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           stop after N HONMON audio references per dictionary
--write-audio                       write portable .wav/.mp3 files
--no-include-unreferenced           skip sequential records not referenced by HONMON
--json                              emit a machine-readable summary
```

With `--limit`, `pcmdata` scans only enough `HONMON.DIC` bytes to find the
requested audio references plus a small label lookahead. Complete runs without
`--limit` still scan the whole body stream, and unreferenced record discovery
remains disabled for limit-bounded runs. `_PCM_U` records are addressable by
the PCMDATA start block in the `1f4a` payload; they are not scanned as a
sequential component.

For `fmt `/`data` PCM records, `--write-audio` wraps the raw chunks in a
standard `RIFF/WAVE` container. For MPEG Layer III records stored inside WAVE
chunks, it writes the `data` chunk as `.mp3`. For native `ID3`/MP3 records, it
writes the MP3 payload directly. For shared-WAVE stores such as `ARCHSIC3`,
each HONMON pointer range is emitted as a `wave_data_slice` and wrapped with
the component-level `fmt ` parameters.

### `extras`

Parse `EXINFO.INI` metadata and auxiliary side-panel files.

```bash
logovista-tools extras /path/to/DICT --out-dir extras
logovista-tools extras /path/to/LogoVista --dict DAIJIRN4 --json
```

Windows packages often declare side UI through fields such as `IDXCOUNT`,
`IDXNAME0`, and `IDXINFO0`, but `EXINFO.INI` is not Windows-only in the
observed corpus. Text auxiliary indexes such as DAIJIRN4's `0000015E.IDX` are
parsed as CP932 tab trees whose first two columns are eight-digit hexadecimal
block/offset pointers. Rows are resolved against the `.IDX` component ranges
when possible.

The command also scans for sibling eight-hex-digit `*.idx` files such as
`00000152.idx`, even when `EXINFO.INI` does not reference them. These numeric
auxiliary trees are SSED-side resources, not Windows-only files; original iOS
packages can carry them too. Sharded variants such as `00000151_0.idx` use the
same CP932 tab-tree grammar and are included in the same numeric auxiliary
index scan. These are reported separately as `numeric_indexes` and written as
`numeric_*.jsonl`.

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
4. For renderer DBs, decrypt observed LogoFontCipher sidecars such as
   `vlpljblb` when needed, or open plain app DBs directly, query body tables,
   and emit only rows whose
   `f_DataId` exists in raw HONMON. Mixed-case and lowercase column variants
   such as `f_DataId` / `f_dataid` are normalized. The `BRINEN15` variant is
   also normalized: marker-at-byte-0 HONMON ID records, `f_data_id`,
   `f_midashi`, `f_contents`, and `f_media` map to the same canonical
   renderer-body fields. Some renderer DBs also contain non-decimal
   `f_DataId` values outside the raw dense-HONMON ID namespace; these are
   counted and sampled as sidecar-only rows rather than coerced to integers.
5. For the observed Android body DB shape, query the `DICTID(Html)` table and
   emit rows where `rowid * 5` exists in raw HONMON.

It also handles the observed row-ordered `HONBUN` renderer database shape used
by `NGYOKTUK`: `vlpljblF` decrypts with LogoFontCipher to a SQLite database
whose `HONBUN` rows are ordered exactly like raw HONMON entry slices. In that
case output records carry the renderer `ID`, raw HONMON entry offset, title,
plain text, and HTML.

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

For `t_contents`, rows include `data_id`, raw HONMON block/offset, type, group
id, title HTML, plain title, search title, keyword text, plain body text, and
media reference text when present. HTML is included unless `--no-html` is used.
For row-ordered `HONBUN`, rows include
renderer `ID`, raw HONMON entry offset, title, plain body text, and HTML unless
`--no-html` is used. `--write-media` exports BLOBs from `media` or `t_media`
using magic bytes to choose `.gif`, `.png`, `.jpg`, `.bmp`, or `.bin`;
filenames are preserved when the renderer HTML already references the original
media name. Two-column `t_media(f_name, f_blob)` stores are treated as untyped
media and still exported by filename/magic. PROYAL53 also uses
`lved.ziptomedia:NAME.wav` links. `--write-ziptomedia` discovers a sibling
sound directory such as `_DCT_NAME_Sound_Files`, decrypts LogoFontCipher-wrapped
loose sound files, and writes portable `.wav` / `.mp3` assets for the
references that are physically present. In `hc-render`, exact renderer-sidecar
HTML additionally rewrites extracted `lved.ziptomedia:` hrefs to local files
when the audio has been materialized, or to safe `#lv-ziptomedia-...` fragments
when it has not. It also rewrites `lved.dataid:` hrefs to local entry/anchor
targets and normalizes renderer-sidecar or fixed-HTML `lved.addr...` /
`lved.image...` links to app-facing address or local-image targets in the
generated visual HTML.

### `spindex`

Inspect standalone `SPINDEX.DIC` resources.

```bash
logovista-tools spindex /path/to/DICT --out-dir spindex
logovista-tools spindex /path/to/LogoVista --limit 200 --json
```

The observed `SPINDEX.DIC` files are not listed in product `SSEDINFO`
component tables, so this command handles them as separate SSED containers.
They are auxiliary SSED resources rather than Windows-only files; Mac OS X
package layouts can use this family too. The command expands only the
compressed chunks that are physically present, parses the internal reversed-key
index pages, and reports whether child pages are present, missing from the
physical file, or outside the declared logical range.

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
- optional renderer SQLite sidecar evidence when `--renderer-sidecars`
  is used;
- aligned validation for cache tables that expose `Block` and `Offset`.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--no-sql-cache                      use declared DictFULLDB only
--renderer-sidecars                 decrypt/use renderer SQLite sidecars
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

### `gaiji-readiness`

Classify gaiji display/search readiness from raw SSED resources, without using
SQLite as the primary source.

```bash
logovista-tools gaiji-readiness /path/to/LogoVista --jobs 0 --out-dir gaiji-readiness
logovista-tools gaiji-readiness /path/to/LogoVista --dict GENIUSEB --json
logovista-tools gaiji-readiness /path/to/LogoVista --dict NGYOKTUK --renderer-sidecars
```

Output layout:

```text
gaiji-readiness/
  summary.json
  gaiji_readiness.csv
  gaiji_readiness.md
  DICT_ID/
    gaiji_readiness.json
```

The command scans text-bearing SSED components (`HONMON.DIC`, `MENU.DIC`,
`*TITLE.DIC`, and text-like `INDEX.DIC` outliers), then checks each raw gaiji
code against dictionary-local `.uni` / plist mappings, GA16 bitmap resources,
package image resources, and optional Windows renderer `HONBUN` sidecars.

Per-code buckets:

```text
unicode_mapped             display text comes from .uni or plist
bitmap_backed              no Unicode display, but a GA16 glyph is available
image_backed               no Unicode display, but a package image is available
formatting_helper          blank bitmap code or full-width unbacked code, treated as a probable renderer helper
renderer_entry_backed      raw code has no direct resource, but matched HONBUN HTML exists
display_unresolved         raw code has no Unicode, bitmap, image, or helper evidence
unused_mapping             mapping exists but was not seen in scanned raw text
unused_bitmap              bitmap exists but was not seen in scanned raw text
unused_image_asset         image asset exists but was not seen in scanned raw text
```

Flags are separate from display buckets. `raw_occurrence_unmapped` means the
raw code has no Unicode text mapping even if it can render as a bitmap/image.
`search_fallback_missing` means `.uni` has display text but no fallback/search
sequence for a character where exporters may want one. It does not make the
code display-unresolved.

The GA16 coverage model uses both observed addressing views:

- JIS-grid header range: `A121..A17E`, then `A221..A27E`, etc.;
- `.uni` record order: for many Windows packages, glyph slot `n` corresponds
  to `.uni` half/full record `n`, even when the record's code field is sparse
  or non-sequential.

The JIS-grid correction resolves raw codes such as `A322`, `A65C`, and `AC3D`
that looked outside the old flat `start_code + index` range. The `.uni`
record-order view is still required for GENIUSEB/RDRSP2-style packages where
raw codes such as `A430` are bitmap-backed by GAI16 resources even though they
are not in the header grid range.

`--renderer-sidecars` is intentionally separate from the default raw-resource
policy. It can classify entry-level display as recovered when renderer rows
align to raw HONMON entries. `NGYOKTUK` needs this: the same raw gaiji code can
render as different Unicode characters in different entry contexts, so it
cannot be represented as one dictionary-global `code -> Unicode` map.

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
logovista-tools ga16 /path/to/DICT/DICT.IDX out/gaiji
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
--limit N                           render at most N glyphs per resource; 0 means uncapped
--variants                          write black _n.png and white _w.png variants
--foreground RRGGBB[AA]             ink color for single-variant output
--background RRGGBB[AA]             background color for single-variant output
--prefix auto|h|z|g                 choose filename prefix
--json                              emit a machine-readable summary
```

Default output is black ink on transparent background. `--variants` mirrors
the theme pattern used by packaged image resources such as `b13d_n.png` and
`b13d_w.png`.

When a `.uni` / `.UNI` sidecar is present next to a GA16 file, `ga16` uses the
`.uni` record order as the preferred filename code for each glyph slot. That is
why `--code A430` can render a GENIUSEB glyph even though the GAI16 header's
grid range starts at `A121`.

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
  "dict_id": "SYNTH",
  "dict_title": "Synthetic Dictionary",
  "component": "FKTITLE.DIC",
  "line_index": 2,
  "text": "sample title"
}
```

Title extraction is especially useful for dictionaries whose `HONMON.DIC` is a
placeholder table rather than a body stream.

When `--limit` is used, `titles` decodes incrementally and stops after enough
title data has been read to emit the requested rows. Component summaries include
`stream_complete`, `bytes_decoded`, and `lines_decoded` so callers can tell a
bounded browse from a complete extraction.

### `menus`

Extract `MENU.DIC` and `TOC.DIC` navigation lines, hierarchy, link labels, and
destination pointers:

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
  "dict_id": "SYNTH",
  "dict_title": "Synthetic Dictionary",
  "component": "MENU.DIC",
  "line_index": 1,
  "section_code": null,
  "depth": 1,
  "path": ["sample menu"],
  "text": "sample menu",
  "links": [
    {
      "label": "sample link",
      "destination": {
        "payload": "000256780002",
        "encoding": "bcd",
        "block": 25678,
        "offset": 2,
        "is_null": false,
        "absolute_offset": 52586498,
        "target": {
          "component": "HONMON.DIC",
          "component_type": "00",
          "kind": "body",
          "start_block": 25678,
          "end_block": 64404,
          "block": 25678,
          "offset": 2,
          "relative_offset": 2,
          "offset_in_block": 2,
          "absolute_book_offset": 52586498,
          "address_key": "00025678:0002"
        }
      }
    }
  ],
  "destination": {
    "payload": "000256780002",
    "encoding": "bcd",
    "block": 25678,
    "offset": 2,
    "is_null": false,
    "absolute_offset": 52586498,
    "target": {
      "component": "HONMON.DIC",
      "component_type": "00",
      "kind": "body",
      "start_block": 25678,
      "end_block": 64404,
      "block": 25678,
      "offset": 2,
      "relative_offset": 2,
      "offset_in_block": 2,
      "absolute_book_offset": 52586498,
      "address_key": "00025678:0002"
    }
  }
}
```

`menu_tree.json` contains the same records nested by inferred section depth.
Section depth is derived from the numeric ordering of section control codes in
that menu component, so `0001`/`0002`/`0003`, `0022`/`0023`, and similar style
families become practical tree levels.

Each destination is resolved against the `.IDX` component block ranges by the
shared navigation resolver used by Panels and the reader demo. The `target`
object names the component and classifies it as `body`, `menu`, `toc`, `right`,
`idxjump`, `title`, `index`, `media-image`, `media-audio`, `gaiji-resource`, or
generic `component`. `relative_offset` is the byte offset inside the expanded
target component, and `address_key` is the reader-facing decimal
`block:offset` key. A six-byte payload of `000000000000` is emitted as
`is_null: true` with no target and is counted as a null/sentinel destination
rather than an unresolved pointer. `TOC.DIC` `1f49`/`1f69` links use the final
six bytes of the 10-byte start payload as a big-endian target pointer.

When `--limit` is used, `menus` decodes incrementally and reads one extra menu
record where possible before stopping. This keeps the requested final row from
being the synthetic flush of a chopped prefix while avoiding full component
expansion for normal browsing.

### `encyclopedia-index`

Parse LVEDBRSR `encyclop.idx` navigation sidecars such as the one used by
KOJIEN6:

```bash
logovista-tools encyclopedia-index /path/to/LogoVista --dict KOJIEN6 --out-dir encyclopedia-index
logovista-tools encyclopedia-index /path/to/DICT/DICT.IDX --json --limit 0
```

This is not a `MENU.DIC` parser. `encyclop.idx` is a CP932, tab-indented
sidecar whose rows point back into the normal SSED block/offset address space.
The command records section headings, row depth, display label, target block,
target offset, and whether the row is target-bearing.

Output layout:

```text
encyclopedia-index/
  encyclopedia-index-summary.json
```

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           sample row limit; use 0 for all rows in JSON
--json                              emit a machine-readable summary
--jobs N                            inspect packages in parallel
```

KOJIEN6 also declares `SCRMENU.DIC`, a separate compressed type-`0x10`
screen/menu stream. That component is reported as a known advanced menu
resource, but its generic record grammar is not collapsed into `MENU.DIC`
output.

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
  "dict_id": "SYNTH",
  "dict_title": "Synthetic Dictionary",
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

When `--limit` is used, `indexes` scans pages through the random SSEDDATA reader
and stops after the requested emitted row count. The component summary is marked
with a partial warning when the limit stops the page scan before the full B-tree
has been walked.

## Experimental Writer Commands

The writer commands below are proof-of-concept research tools for clean
plain-HONMON SSED authoring. They are not lvcore reader APIs, not LVED or
LVLMultiView writers, and not a historical package repacker.

### `write-plain`

Build an experimental author-core plain-HONMON SSED package from a supported
text source. This is a writer research command, not a historical package
repacker.

```bash
logovista-tools write-plain KOUJIEN7.csv \
  --out-dir /tmp/writer-out \
  --dict-id KOUJSMK \
  --title "Writer Smoke" \
  --compression compressed \
  --json
```

Supported input formats are KOUJIEN-style CSV, Yomitan v3 zip packages, and
JMdict XML gzip files. The common writer path emits a self-contained SSED
package with `HONMON.DIC`, title streams, FK/FH/BK/BH indexes, and
dictionary-local `.uni` / GA16 resources when gaiji are required.
`--compression literal` is retained only as a noncanonical, size-inflating
diagnostic mode for private stress tests.

The JMdict import path preserves more source structure than a flat glossary
import. It emits `KWTITLE.DIC` / `KWINDEX.DIC` keyword rows for selected
gloss/tag/category lookup terms, `MENU.DIC` links for about/licence/category
navigation, root-level `Panels.dtd`, `Panels.xml`, `Panel.html`, and
`Cell.html` plus `Panel/*.bin` fixed-record address-label tables for kana,
part-of-speech, field/domain, dialect, priority/common, source-language, and
example-bearing browse surfaces. It may also emit `CCALTSTR.HA` /
`CCALTSTR.FU` alternate-string tables for generated custom-character codes with
ASCII transliteration fallbacks. Numeric `00000xxx.idx` sidecar trees are not
emitted by the JMdict path because MENU and Panels cover the intended
navigation model without a platform-specific auxiliary text tree.

JMdict gaiji output is deliberately redundant in the LogoVista style:
display text uses Unicode through `.uni` where possible; loose `Templates/`
PNG/SVG gaiji are generated from the configured vector font preference list;
GA16 bitmap resources are generated only from LogoVista bitmap resources or
embedded bitmap strikes in the configured bitmap-preference fonts. The font
families remain separate so the GA16 output does not silently fall through to
the Template vector list. TTC/TTF fonts such as MS Gothic and SimSun may still
be valid GA16 bitmap sources when their embedded bitmap strike tables are used;
their outline/vector data is not used for GA16.

The writer separates display text from lookup aliases. Body and title text keep
their display spelling, but index keys are generated from raw and normalized
aliases. The current normalization emits compatibility-normalized aliases,
strips punctuation/separators/spaces/hyphen-like characters that would block
lookup, folds katakana to hiragana for Japanese lookup keys, and preserves
source-specific aliases such as bracketed KOUJIEN headword spellings. This is
necessary for entries displayed with separators, for example `かな‐れい`, to be
found by a plain lookup key such as `かなれい`.

Generated simple indexes (`FHINDEX.DIC` / `BHINDEX.DIC`) carry the broad alias
set. Generated tagged indexes (`FKINDEX.DIC` / `BKINDEX.DIC`) carry one exact
display-key row per entry. This separation is intentional: it matches observed
LogoVista index conventions and prevents alias-heavy imports from bloating the
exact lookup indexes.

JSON reports include import counts, duplicate merges, skipped rows, long-key
drops, emitted search-key/alias counts, headword-markup cleanup counts,
flattened markup/media counts, gaiji allocation counts, and output file sizes.
JMdict reports additionally include keyword target counts, MENU item counts,
Panel BIN row counts, Template gaiji counts, CCALTSTR row counts, and optional
showcase output metadata.

### `verify-written-package`

Verify a writer-generated plain-HONMON SSED package as a whole:

```bash
logovista-tools verify-written-package /tmp/writer-out/KOUJSMK
logovista-tools verify-written-package /tmp/writer-out/KOUJSMK --json
```

Checks include:

```text
SSEDINFO block ranges and component overlaps
SSEDDATA header kind/start/end and expanded byte size
branch upper-bound keys
final-sibling ff sentinel rows
forward/backward traversal landing pages
duplicate-key contiguity
title/body pointer component ranges and row boundaries
.uni / GA16 resource consistency
```

The traversal check follows observed sibling-page behavior, including duplicate
keys and branch-prefix groups that continue into adjacent leaf pages. The
command exits with status `0` only when no structural errors are found. Warnings
are included in the report but do not make the package fail.

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
  "dict_id": "SYNTH",
  "dict_title": "Synthetic Dictionary",
  "data_id": 755,
  "record_index": 754,
  "block": 13,
  "offset": 1602,
  "type": 2,
  "title": "sample title",
  "html": "<rn></rn><a name=\"000007550000\"></a><div class=\"midashi\">...",
  "plain": "sample plain text..."
}
```

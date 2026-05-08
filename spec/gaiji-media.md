# Gaiji, Images, and Media

Dictionary-local gaiji maps, bitmap resources, image-backed symbols, `COLSCR.DIC`, `PCMDATA.DIC`, and validation against auxiliary payloads.

## Gaiji

Gaiji are dictionary-specific characters and formatting markers. A gaiji code
is not globally meaningful. The same code can map to different Unicode text in
different dictionaries:

| Code | `HAESPJPN` | `GENIUSEB` | `KOJIEN7` | `HAFRAN` | `OUKOKU11` |
| --- | --- | --- | --- | --- | --- |
| `A126` | `é` | `ɑ̃` | `Ö` | `é` | `ä` |
| `A138` | `ñ` | `ō` | `ñ` | `⑥` | `ŋ` |

The extractor therefore builds a gaiji profile per dictionary. Do not use a
global replacement table such as `A126 = é`.

Observed resources include:

```text
GA16HALF
GA16FULL
GAI16H*
GAI16F*
*.uni
*.UNI
image/icon folders
iOS Gaiji.plist / GaijiS.plist fallback files
```

The current extractor:

- loads primary Unicode mappings from dictionary-local `.uni` / `.UNI` files;
- uses `Gaiji.plist` and `GaijiS.plist` only as fallbacks for codes missing
  from `.uni`;
- parses `GA16HALF` and `GA16FULL` headers, slices individual bitmap glyph
  records, and renders them to transparent PNG files;
- discovers PNG-backed gaiji from package image folders;
- emits unresolved half-width gaiji as `<hXXXX>` by default;
- can emit all unresolved gaiji as placeholders with `--gaiji placeholder`;
- can drop unresolved gaiji with `--gaiji drop`.

For conversion work, keeping unresolved placeholders is usually better than
dropping. It lets a later gaiji pass replace `<hXXXX>` / `<zXXXX>` with images
if the character has no Unicode equivalent.

## Image Resources

Some dictionaries ship ready-made PNG resources outside `HONMON.DIC`. The iOS
HAESPJPN package has a top-level `img` directory with files such as:

```text
img/b13d_n.png
img/b13d_w.png
img/b13e_n.png
img/b13e_w.png
img/exam.png
```

The `_n` and `_w` suffixes are theme variants of the same asset. In HAESPJPN,
`b13d_n.png` is the black-theme image for the `B13D` full-width gaiji, and
`b13d_w.png` is the corresponding white-theme image. The body stream can refer
to that asset by the gaiji bytes `b1 3d`; the extractor can preserve it as
`<img:b13d>` or emit inline HTML:

```html
<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d">
```

Package metadata helps classify these resources:

```text
resourcesCopy.plist  complete-ish list of PNG resources to copy into the app package
gaijiicon.plist      keys that the app treats as gaiji/icon resources
```

OUKOKU11 is an Android-only layout with no plist manifests. Its image assets are
still local and usable:

```text
resource/kmkimges/b167_1.png
resource/kmkimges/b167_3.png
appendix/img/10_00.gif
manual/contents/img/rei.png
```

The `_1` and `_3` suffixes behave like theme variants, with `_1` used as the
normal/dark-on-transparent asset and `_3` used as the white/light-on-transparent
asset. Because no plist identifies gaiji icons, the toolkit classifies
code-shaped filenames such as `b167` as image-backed gaiji and keeps named
resources such as `rei` or `waka` as ordinary package images.

Windows dictionaries can also keep renderer assets in `Templates/`. In SINMEI7
Windows this directory contains named images such as `exam.png`, numbered
appendix images, BMP panels, and code-shaped gaiji images such as `B222.png`.
The resource scanner treats those as package resources and reports BMP files as
well as PNG/GIF/JPEG files.

Named images such as `exam.png` are not necessarily referenced by filename in
`HONMON.DIC`. They may be style resources used by the app for semantic regions
such as examples. The toolkit reports these resources, while the exporter or a
dictionary-specific style layer decides when to insert them. The `entries`
command supports explicit section-image rules such as `--section-image
0011=exam`; this preserves the raw section marker and inserts the named image
in `body_html`.

## `COLSCR.DIC` Media Resources

`COLSCR.DIC` is a compressed SSED component, usually listed as component type
`0xd2` in `SSEDINFO`. It stores larger inline media used by the body stream.
The body does not name these images by filename. Instead, `HONMON.DIC` contains
`1f 4d` media controls with an 18-byte binary payload.

The useful pointer is encoded in the final six bytes of that payload:

```text
payload bytes 12..15  target logical block, packed BCD decimal
payload bytes 16..17  target offset in block, packed BCD decimal
```

For example:

```text
000000000000000000000000000176490030
```

decodes to logical block `17649`, offset `30`. In OUKOKU11, that is exactly
inside the `COLSCR.DIC` block range.

The pointed record has a simple wrapper:

```text
offset  size  meaning
0x00    4     ASCII magic: data
0x04    4     image payload size, little endian
0x08    n     image payload
```

The payload is not one fixed format. Verified examples include:

| Dictionary | Media refs | Valid records | Payload formats |
| --- | ---: | ---: | --- |
| `GENIUSEB` | 1,081 | 1,081 | BMP, 8 bpp; includes RLE8-compressed BMPs |
| `HAESPJPN` | 274 | 274 | BMP, 1 bpp |
| `IBIO5` | 1,324 | 1,324 | JPEG/JFIF |
| `IPHYCHE5` | 4,189 | 4,189 | BMP, 8 bpp |
| `KANJIGN5` | 952 | 952 | BMP, 8 bpp |
| `KenE7J5` | 96 | 96 | BMP, 8 bpp |
| `MEIKYOU2` / `Gen2013` family | observed | observed | PNG |
| `NKGORIN2` | 28,841 | 28,841 | BMP, mixed 8/24/1 bpp |
| `OUKOKU11` | 2,579 | 2,579 | BMP, 24 bpp |

For OUKOKU11 specifically, all raw media references resolve to strict
`data`-wrapped BMP records. Section code `0200` corresponds to `:筆順`
stroke-order images and section code `0201` corresponds to `:図版` figure
images. A strict scan of expanded `COLSCR.DIC` finds the same 2,579 records as
the `HONMON.DIC` media controls, with no unreferenced records under the current
parser.

Corpus-wide component forensics currently covers 59 `COLSCR.DIC` components
with zero nonzero unparsed bytes and zero invalid referenced records. PNG
records use the same wrapper as BMP/JPEG records: ASCII `data`, a little-endian
payload length, and then a native PNG byte stream beginning with the PNG
signature. Width and height are recovered from the IHDR chunk when present.

## `PCMDATA.DIC` Audio/Media Resources

`PCMDATA.DIC` is a compressed SSED component, usually listed as component type
`0xd8`. It is a sequential media store used mainly for audio. The first
expanded 2048-byte block is a small directory/header area. In all currently
tested dictionaries it starts with:

```text
0108 0000 ... 0000
```

and then contains 16-byte directory-looking rows such as:

```text
0000 0e00 0000 0002 0000 0000 0000 0000
0001 0e00 0000 0002 0000 0000 0000 0000
```

The exact purpose of this first block is still not classified, but actual
media records begin at expanded offset `2048`.

Raw `HONMON.DIC` references use `1f 4a` with a 16-byte payload. The same
control may render visible text such as `→音声1` until the closing `1f 6a`.
The useful pointer fields are:

```text
payload bytes 0..1    kind, observed 0001
payload bytes 2..3    flags, dictionary dependent
payload bytes 4..7    start logical block, packed BCD decimal
payload bytes 8..9    start offset in block, packed BCD decimal
payload bytes 10..13  end logical block, packed BCD decimal
payload bytes 14..15  end offset in block, packed BCD decimal
```

For example:

```text
00010000000231930000000231991579
```

decodes to start block `23193`, offset `0`, end block `23199`, offset `1579`.
In HAESPJPN this points at the first audio record and the visible label is
`→音声1`.

The pointed record formats observed so far are:

```text
fmt  + data
fmt  + fact + data
ID3/MP3 payload
unclassified raw payload, byte-addressed by valid HONMON pointers
```

The `fmt `/`data` records are RIFF/WAVE chunks without the outer `RIFF` and
`WAVE` wrapper. They are followed by a 12-byte zero trailer. For portable
export, the toolkit wraps PCM chunks into a standard WAVE file. If the WAVE
format tag is `0x0055` (`MPEG Layer III`), the toolkit writes the `data` chunk
as `.mp3` instead. Native `ID3`/MP3 records are written directly as `.mp3`.

Verified examples:

| Dictionary | HONMON refs | Unique refs | Unreferenced records | Payload formats |
| --- | ---: | ---: | ---: | --- |
| `HAESPJPN` | 9,996 | 9,996 | 10 | PCM WAVE chunks, mono 16 kHz, 16-bit |
| `KenE7J5` | 14,811 | 14,776 | 525 | PCM WAVE chunks, mono 11.025 kHz, 8-bit |
| `GENIUS53` | 105,805 | 100,835 | 2 | Native ID3/MP3 records |
| `RDRSP2` | 14,050 | 13,995 | 0 | MPEG Layer III stored inside WAVE chunks |
| `Readers3` | 14,050 | 13,995 | 0 | MPEG Layer III stored inside WAVE chunks |
| `ROYALEGR` | 295 | 295 | 0 | PCM WAVE chunks |
| `SINMEI7` | 84,372 | 68,437 | 0 | Native ID3/MP3 records |
| `ARCHSIC3` | 235 | 235 | 0 | unclassified raw payload ranges |

The unreferenced count matters: `PCMDATA.DIC` is not merely a lookup table for
HONMON references. It is a sequential media store, and some records can exist
between referenced ranges. The `pcmdata` command therefore reports both
HONMON-referenced records and unreferenced records found in nonzero gaps.

Corpus-wide component forensics currently covers 12 `PCMDATA.DIC` components
with zero nonzero unparsed bytes. `ARCHSIC3` contributes 235 referenced ranges
whose start/end pointers are valid and whose bytes are therefore accounted for,
but the payload codec/container is not yet identified. Those ranges are kept as
`unknown_audio_payload` instead of being forced into WAV or MP3.

## `.uni` Files

`.uni` files are dictionary-specific gaiji mapping tables. They are not a
universal character map, and they are not all the same container layout.

Two layouts are currently supported.

`Ver2` layout:

```text
offset  size  meaning
0x00    6     ASCII magic: "Ver2  "
0x06    4     half-width gaiji record count, big endian
0x0a    ...   half-width records, 16 bytes each
...     4     full-width gaiji record count, big endian
...     ...   full-width records, 16 bytes each
```

Simple 12-byte layout:

```text
offset  size  meaning
0x00    4     half-width gaiji record count, big endian
0x04    ...   half-width records, 12 bytes each
...     4     full-width gaiji record count, big endian
...     ...   full-width records, 12 bytes each
```

The simple layout appears in at least `IWKOKUG8` and `KENROWA`. `IWKOKUG8`
uses a zero half count followed by full-width records.

Each `Ver2` 16-byte record is eight big-endian 16-bit fields:

```text
field  meaning
0      gaiji code, for example A126 or B121
1      metadata/flags or glyph metadata, not fully classified
2..3   display Unicode sequence
4..5   fallback/search Unicode sequence
6..7   legacy/alternate fields, not reliable as display text
```

Each simple 12-byte record is six big-endian 16-bit fields:

```text
field  meaning
0      gaiji code
1      metadata/flags or glyph metadata, not fully classified
2..3   display Unicode sequence
4..5   fallback/search Unicode sequence
```

The display sequence can be:

- one BMP codepoint, for example `00E9` -> `é`;
- a base character plus combining mark, for example `0075 032F` -> `u̯`;
- a UTF-16 surrogate pair, for example `D834 DD10` -> `U+1D110`.

The toolkit now combines valid surrogate pairs and ignores lone surrogate
code units. Older builds skipped all surrogate code units, which lost
supplementary-plane gaiji such as musical symbols and rare CJK characters.

Fields `4..5` behave like search/fallback text in dictionaries where they are
populated. For example, GENIUSEB maps `ɑ́` to fallback `a`, and KenE7J5 maps
`Á` to fallback `A`. These are useful for lookup normalization, but should not
replace the display sequence.

Fields `6..7` in `Ver2` are not a second display mapping. They often contain
legacy glyph codes or alternate values. Some look superficially useful
(`é` records often carry `É`), while others decode to control characters,
radical forms, or unrelated symbols. Treat them as diagnostic metadata until a
specific dictionary proves otherwise.

Some `Ver2` files contain duplicate gaiji codes across the half and full
sections. `GENIUSEB.uni`, for example, has both half and full records for
`A121`, with different display text. The compatibility map used by older
text renderers is still flattened and later records override earlier records.
The parsed `.uni` records and lossless span output keep half/full space
information so future exporters can make a more precise choice.

Corpus summary from the local test collection:

```text
Ver2 files:    GENIUSEB, HAESPJPN, HAFRAN, KOJIEN7, and most others
simple12 files: IWKOKUG8, KENROWA
```

The current Windows SSED component-forensics pass saw 90 `.uni` / `.UNI`
files. All declared records parse under the two layouts above. The only
residuals are trailers after the parsed record tables:

```text
total trailing bytes:         72
nonzero trailing bytes:       14
nonzero trailer dictionaries: HKDKSR14, HKDKSR30, YHOUGO4
```

These trailer bytes are reported as file-tail evidence. They are not used as
display mappings.

Representative inspector output:

```text
logovista-tools uni GENIUSEB.uni --limit 2
format: ver2
records: 854 half=384 full=470 mapped=430 fallback=190 legacy=311 metadata=542
half A121 meta=0000 display='á' fallback='a' legacy='Â'

logovista-tools uni KENROWA.uni --limit 2
format: simple12
records: 376 half=97 full=279 mapped=199 fallback=0 legacy=0 metadata=49

logovista-tools uni OUKOKU11.UNI --limit 2
format: ver2
records: 894 half=48 full=846 mapped=568 fallback=0 legacy=537 metadata=479
```

## `GA16HALF` / `GA16FULL`

`GA16HALF` and `GA16FULL` contain bitmap glyphs. The observed header is:

```text
offset  size  meaning
0x08    1     glyph width
0x09    1     glyph height
0x0a    2     first gaiji code, big endian
0x0c    2     glyph count, big endian
0x800   ...   bitmap data
```

Typical values:

```text
GA16HALF  width=8   height=16  glyph bytes=16
GA16FULL  width=16  height=16  glyph bytes=32
```

Bitmap data is a dense run of 1bpp glyphs:

```text
row_bytes    = ceil(width / 8)
glyph_bytes  = row_bytes * height
glyph_offset = 0x800 + glyph_index * glyph_bytes
```

Rows are stored top-to-bottom. Bits inside each row are MSB-first, and set bits
are ink pixels. In HAESPJPN, this renders `A126` as `é` and `A138` as `ñ`,
matching EBWin and the `.uni` text mapping.

Two code-addressing views are observed:

```text
JIS-grid header range  A121..A17E, then A221..A27E, etc.
.uni record order      code = matching half/full .uni record[glyph_index].code
```

The header range is not `start_code + glyph_index`. The low byte is a JIS cell,
valid from `0x21` through `0x7e`; the next glyph after `A17E` is therefore
`A221`. This row/cell rule is required for dictionaries such as `ARCHSIC3`,
`LMEDEJ12`, `MEIKYOU`, `NANDOKU3`, `NANDOKU4`, `Dconci87`, `Bri2019P`,
`KQBIZEJ`, `IBIO4`, and `IBIO4VRS`.

Several Windows packages also need `.uni` record order. GENIUSEB, RDRSP2,
Readers3, RPLUSREV, KENE7J5, KQNEWEJ6, KQNEWJE5, and related packages have
GA16 glyph slots that align with `.uni` records. Their `.uni` records can
contain sparse/non-sequential codes such as `A430`; the matching bitmap exists
at that record index even though the GAI16 header grid starts at `A121`.

For extraction and readiness checks, use both views. For rendering a GA16 dump
to stable filenames, prefer `.uni` record-order codes when a matching `.uni`
sidecar is present, then fall back to the JIS-grid header range for glyph slots
beyond the parsed record table.

Some resources are valid headers with zero glyphs. For example, an empty
`GA16HALF` may declare width, height, and start code but have count `0`. The
toolkit reports these cleanly and emits no PNGs.

Corpus-wide component forensics currently covers 314 GA16-style resources
(`GA16HALF`, `GA16FULL`, `GAI16H*`, and `GAI16F*`) with zero missing glyph
bytes, zero nonzero trailing bytes, and zero nonzero unknown header bytes.
Offset `0x00` is a version/header byte in observed resources; width/height,
start code, and count are the fields required for re-rendering the bitmap
glyph stream.

The `ga16` command converts bitmap glyphs to transparent RGBA PNGs. With
single-variant output, names use the resource kind as a prefix:

```text
hA126.png  half-width resource glyph A126
zB121.png  full-width resource glyph B121
```

With `--variants`, the command writes LogoVista-style theme pairs:

```text
hA126_n.png  black ink on transparent background
hA126_w.png  white ink on transparent background
```

These images can be copied into Yomitan, MDict, or HTML output packages and
referenced with normal inline `<img>` tags for gaiji that have no usable
Unicode mapping.

## Gaiji Readiness Buckets

`gaiji-readiness` separates display readiness from search/fallback readiness.
It scans raw text-bearing SSED components and classifies each dictionary-local
code into one primary display bucket:

```text
unicode_mapped       .uni or plist provides display text
bitmap_backed        GA16/GAI16 glyph exists, but no Unicode display text
image_backed         package image exists, but no Unicode display text
formatting_helper    blank bitmap code or unbacked full-width code, treated as probable renderer-only helper
renderer_entry_backed renderer HONBUN HTML is aligned to raw entries and supplies display
display_unresolved   raw occurrence with no Unicode/image/bitmap/helper evidence
unused_mapping       mapping exists but raw scans did not see it
unused_bitmap        bitmap exists but raw scans did not see it
unused_image_asset   image exists but raw scans did not see it
```

The `formatting_helper` bucket is corpus-inferred, not a universal rule. It is
used for raw codes whose only bitmap evidence is a blank glyph, and for raw
full-width codes with no mapping, no bitmap, and no package image. Prior
observed packages use those codes as blank/style helpers. Real display glyphs
normally have nonblank `.uni`, GA16, or image backing.

`renderer_entry_backed` is also deliberately scoped. It means direct raw gaiji
resources are absent, but a renderer database has rows aligned to raw HONMON
entries. `NGYOKTUK` is the observed case: its LogoFontCipher `vlpljblF`
sidecar decrypts to `HONBUN` HTML rows that exactly match raw entry order.
Some raw codes in that dictionary are context-dependent, so there is no safe
single `code -> Unicode` map. Lossless output should preserve the raw code and
use the matched renderer HTML for display.

Flags are orthogonal:

```text
raw_occurrence_unmapped     raw code has no Unicode display text
search_fallback_missing     display text exists, but .uni fallback/search text is absent
formatting_helper_candidate formatting-helper heuristic was applied
renderer_contextual_required entry-level renderer evidence is needed for display
display_unresolved          primary bucket is display_unresolved
```

`search_fallback_missing` is a lookup/export quality signal. It does not block
display fidelity because the display text is already known. For example,
accented Latin gaiji in HAESPJPN display correctly from `.uni`, but many lack
ASCII fallback text such as `e` for `é`.

## SQL/DictFULLDB Validation

SQL and `DictFULLDB` payloads are useful for validating gaiji mappings, but
they are evidence, not the primary source. The toolkit uses them in two ways.

First, it scans readable SQLite text columns and counts occurrences of
informative mapped display strings from `.uni` / plist resources. Single ASCII
characters are skipped for validation because a match on `a`, `/`, or `-` is
too ambiguous to prove anything.

Second, when a cache table has `Block` and `Offset` columns, it builds an
aligned row index. Raw `HONMON.DIC` entry slices are matched back to cache rows
by block/offset, with a small byte tolerance because some app caches point just
inside or just before the visible entry wrapper. If a raw entry contains gaiji
`A126`, `.uni` maps `A126` to `é`, and the aligned cache row contains `é`, the
code receives an aligned validation hit.

Aligned validation uses the same index-derived body boundaries as `entries`.
This matters for OUKOKU11: marker-only slicing sees 64,453 `1f09 0001`
boundaries, but parsed indexes expose 82,220 body boundaries. With index
boundaries enabled, a 10,000-entry validation pass produced 7,996 aligned
gaiji hits and only 8 mapped misses; marker-only slicing missed many legitimate
non-`0001` entries.

The report status values are:

```text
validated_aligned                strongest evidence; raw pointer and SQL text agree
db_text_evidence                 mapped display appears in SQL, but not via aligned row
mapped_no_db_evidence            raw/mapped code exists, SQL did not confirm it
mapped_unvalidated_uninformative mapped text is too ambiguous for SQL validation
mapped_unused_in_raw_scan        mapping exists, but raw HONMON/TITLE scans did not see it
image_asset_only                 no Unicode map, but package PNG gaiji exists
bitmap_asset_only                no Unicode map, but GA16 bitmap exists
unresolved                       raw code has no Unicode, image, or bitmap coverage
```

This distinction matters. HAESPJPN produces strong aligned evidence for accent
gaiji such as `A126` -> `é` and `A138` -> `ñ`. GENIUSEB, however, shows that
some SQLite cache text is normalized: many IPA/symbol mappings validate, while
some accent display forms from `.uni` do not appear in the cache. The correct
reaction is to flag that discrepancy, not to overwrite `.uni`.

Some sibling SQLite files are not useful validation sources. Android
`android_metadata` tables and standalone `*_indexinfo.db` metadata tables are
skipped so locale/index metadata does not pollute text evidence.

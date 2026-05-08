# Corpus Findings

Observed dictionary behavior. These notes are evidence from the local corpus, not universal claims about every LogoVista product ever shipped.

## Windows SSED Corpus Profile

The current Windows SSED corpus pass profiled 169 packages with raw SSED expansion,
raw `*INDEX.DIC` scanning, sampled lossless HONMON span decoding, and no
SQLite body text. The command shape was:

```bash
logovista-tools profile /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-slices 25 --max-issue-samples 10 --no-hash \
  --out-dir /tmp/lv-profile-corpus
```

Aggregate result:

```text
profiles:                 169
package status:           136 ok, 33 incomplete
HONMON shapes:            110 body_stream_indexed
                           33 body_stream_marker_sliced
                           18 dense_marker_table
                            8 none/missing
body source hints:        143 honmon
                           18 honmon_anchor_dereference
                            8 none
expanded HONMON bytes:    3,497,793,539
entry markers counted:    26,656,375
index body boundaries:    12,370,613
unknown text controls:    0
unknown text bytes:       0
unknown index leaf bytes: 0
strict span failures:     0
```

This pass produced two concrete text-stream findings:

- `1f0b` / `1f0c` are zero-argument literal/preformatted spans. ROYALEGR uses
  them around box-drawing table rows; NKGORIN2 uses them around ASCII numeric
  character references such as `&#x4E05;`.
- `1f3b` / `1f5b` are zero-argument URL spans. GEN2001 uses them around URL
  display blocks.

The `unknown index leaf bytes: 0` result is important. It means the current
branch/leaf parser consumed all observed forward, backward, keyword,
cross-reference, and extra index leaf structures in this corpus. This is still
an empirical result, not a claim that every LogoVista product ever shipped uses
only these layouts.

## Full HONMON Byte Scan

The stronger corpus pass scans every expanded `HONMON.DIC` byte, not sampled
entry slices:

```bash
logovista-tools honmon-bytes /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-issue-samples 20 \
  --out-dir /tmp/lv-honmon-bytes-corpus
```

Aggregate result with the current decoder:

```text
targets:                    169
status:                     161 ok, 8 missing_honmon_file
HONMON byte shapes:         142 marker_rich_text_stream
                            18 dense_marker_table
                             1 text_stream_without_entry_markers
                             8 none/missing
storage modes:              135 plain
                            26 logofont_cipher
                             8 none/missing
expanded HONMON bytes: 3,497,793,539
bytes covered:         3,497,793,539
uncovered bytes:                   0
entry markers:            26,656,375
controls:                460,913,534
known controls:          460,913,534
unknown controls:                  0
unknown bytes:                     0
invalid JIS cells:                 0
truncated controls:                1
truncated gaiji:                   0
```

The full scan produced several corrections to the text-stream model:

- `1f1a` and `1f1c` are fixed two-byte-argument controls. They are structurally
  recognized, but their exact renderer semantics remain neutral.
- `1f44` / `1f64` are an extended link pair with 10-byte and 6-byte payloads.
- JIS cell decoding needs CP932 and Shift_JIS-2004 fallback after ISO-2022-JP.
  This accounts for extension symbols such as `①`, `㎏`, `❾`, and `◦`.
- A bare `0x0a` can occur as a legacy line break byte.

The sole remaining forensic issue is `NANDOKU3`: the expanded stream ends with
a lone final `0x1f` byte after decoded text. It is covered and reported as a
truncated control; no opcode is inferred.

## Full Component Forensics Pass

The companion pass accounts for non-HONMON SSED components and adjacent gaiji
mapping files across the same Windows SSED corpus:

```bash
logovista-tools component-forensics /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-issue-samples 20 \
  --out-dir /tmp/lv-component-forensics-corpus
```

Aggregate component inventory:

```text
packages scanned:        169
component reports:     1,231 ok, 82 missing_file
MENU.DIC:                84
*TITLE.DIC:             307
structured *INDEX.DIC:  536
text-like INDEX.DIC:      1
GA16 resources:         314
COLSCR.DIC:              59
PCMDATA.DIC:             12
.uni/.UNI files:         90
```

The `missing_file` count reflects incomplete local gathered packages whose
`SSEDINFO` tables name components that are not physically present. It is not a
parser failure mode.

Byte-coverage result:

```text
text stream uncovered bytes:          0
text stream unknown controls:         1
text stream unknown bytes:            1
text stream invalid JIS pairs:        0
structured index nonzero residual:    3
GA16 missing glyph bytes:             0
GA16 nonzero trailing bytes:          0
GA16 unknown header nonzero bytes:    0
COLSCR nonzero unparsed bytes:        0
COLSCR invalid referenced records:    0
PCMDATA nonzero unparsed bytes:       0
PCMDATA unclassified ref ranges:    235
.uni trailing bytes:                 72
.uni nonzero trailing bytes:         14
```

The pass added several concrete format details:

- `0x30` `KINDEX.DIC` is a body-only tagged index: grouped rows match the
  `0x70`/`0x90` tagged grammar, but target rows carry a single 6-byte body
  pointer instead of a body/title pair.
- `0x60` `HINDEX.DIC` is a body-only simple index: each key row carries a
  single 6-byte body pointer and uses that same address as the title address.
- `0x72` `BAINDEX.DIC` and `0x92` `FAINDEX.DIC` use the same simple row grammar
  as `0x71`/`0x91`.
- Tagged index pages can contain direct `00 len` rows in addition to grouped
  `80`/`c0` rows.
- Some simple leaf pages are keyless 13-byte pointer tables: 6-byte body
  pointer, one flag byte, and 6-byte title pointer.
- Branch-page slot size uses the full low byte:
  `slot_size = (page_word & 0xff) + 4`. The upper byte/bits are page flags, and
  valid observed slots include 6-byte rows.
- One `INDEX.DIC` outlier is text-like rather than a B-tree page component:
  `KQSYNONM` component type `0x27` is handled as a text stream.
- `COLSCR.DIC` records can wrap PNG payloads with the same `data` + little
  endian size header used by BMP/JPEG records.
- `PCMDATA.DIC` pointer ranges are valid byte-addressed records even when the
  payload codec is not yet classified.

The remaining component anomalies are intentionally small and named:

- `NANDOKU2` `FHINDEX.DIC` has three nonzero physical tail bytes after all full
  2048-byte index pages are parsed.
- `25IGAKU` `FHTITLE.DIC` has one `1f1f` control. It is structurally covered
  as a two-byte control span, but renderer semantics are unknown.
- `ITALIAN` `FHTITLE.DIC` has one standalone `0x11` byte. It is covered as an
  unknown byte span.
- `HKDKSR14`, `HKDKSR30`, and `YHOUGO4` have small nonzero `.uni` trailers
  after all declared records are parsed.
- `ARCHSIC3` has 235 in-range `PCMDATA.DIC` references whose byte intervals are
  covered, but whose payloads are not RIFF/WAVE, native ID3/MP3, or the
  currently classified MPEG-in-WAVE shape.

## HONMON/IDX Corpus Audit

The local `LOGOVISTA_ALL` corpus was audited with raw SSED expansion, raw
index-derived body boundaries, body-slice sampling, 32-byte HONMON record
probing, and title-component probing. SQLite and `DictFULLDB` body text were
not used to decide whether raw HONMON/IDX produced readable body entries.

Valid SSED dictionaries with `HONMON.DIC` fell into these practical groups:

| Group | Dictionaries |
| --- | --- |
| Raw HONMON/IDX gives readable body entries | `Dconci98`, `GENIUS53`, `GENIUSEB`, `HAESPJPN`, `HAIKSAIJ`, `HKKIGAK6`, `IBIO5`, `IPHYCHE5`, `KANJIGN5`, `KENCOLLO`, `KQCOLEXP`, `KQEBHOU`, `KQJCOLLO`, `KQLATINO`, `KQNEWEJ6`, `KQNEWJE5`, `KenE7J5`, `LMEDEJ12`, `MEIKYOU2`, `NIHONSHI`, `NKGORIN2`, `OUKOKU11`, `RDRSP2`, `ROYALEGR`, `Readers3`, `SINMEI7`, `Saitoje`, `ZYAKUKOG` |
| Raw HONMON/IDX exposes IDs, tokens, titles, or search keys, but sampled HONMON bodies are not definitions | `HABGESPA`, `HAFRAN`, `HOUGAKU5`, `IWKOKUG8`, `JSSAURU2`, `KENROWA`, `KOJIEN7`, `NANMED20` |

Several products in the first group still declare SQL or `DictFULLDB` files.
That declaration alone is not enough to classify a dictionary as database-body
only. The raw audit must check the expanded HONMON stream and the raw indexes.
Conversely, the second group proves that some dictionaries need a database or
other payload dereference for final body text, but that does not make HONMON or
IDX irrelevant: they still carry the raw anchor layer.

LVED/WebView2 SQLCipher products such as OXFPEU4 and KQCMPROS are covered as a
separate package family below. They are not failed raw-HONMON body streams.

The body sampler deliberately filters section-only spans, decimal/hex-only ID
records, and short opaque base64-like tokens. Without that filter, dense tables
can appear to contain entries such as `<section:0001>` or `K0NVOzjh`; those are
not coherent dictionary bodies.

## LVED SQLCipher Packages

OXFPEU4 and KQCMPROS are not failed SSED/HONMON dictionaries. They are a
separate LVED/WebView2 package family. The body/search/media data lives in
SQLCipher payloads named `main.data` on Windows and `*.dbc` in mobile-style
packages.

Observed OXFPEU4 facts:

```text
Windows main.data size:    15,937,536 bytes
4096-byte pages:            3,891
Windows/iOS payload match:  byte-identical in the local corpus
decrypted schema:           list, content, media, info, search, FTS backing tables
list rows:                  2,802
content rows:                  70
search rows:                2,802
```

Observed KQCMPROS facts:

```text
Windows main.data size:   197,382,144 bytes
4096-byte pages:           48,189
decrypted schema:          list, content, media, info, search, FTS backing tables
list rows:                135,317
content rows:              64,517
search rows:              135,317
```

The Windows viewer ships WebView2 and SQLCipher. Static .NET inspection shows a
direct `sqlite3_open_v2` -> metadata-derived key -> `sqlite3_key` path. The
validated database key path uses dictionary id/code metadata, not the product
serial. The local LVEDVIEWER memory dump also contains plaintext SQLite headers,
SQL statements, and live key material; this confirms the runtime model, but
memory dumps and recovered keys are not repository artifacts.

The toolkit therefore treats LVED payloads as a distinct package layer:

```bash
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/KQCMPROS --dict-id 751 --json
```

Reports validate the payload without emitting derived, explicit, or recovered
keys.

## Non-iOS Body Streams

OUKOKU11 is useful because it is an Android-only package, not part of
LogoVista's iOS pipeline. It has no `Gaiji.plist`, `GaijiS.plist`,
`resourcesCopy.plist`, or `gaijiicon.plist`, but the raw `.IDX` / `.DIC`
structure is still compatible with the toolkit.

Observed OUKOKU11 layout:

```text
OUKOKU11.IDX
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
KWTITLE.DIC
KWINDEX.DIC
COLSCR.DIC
GA16FULL
GA16HALF
OUKOKU11.UNI
OUKOKU11.db
OUKOKU11_indexinfo.db
resource/kmkimges/
appendix/img/
manual/contents/img/
```

Important findings:

- Uppercase `.UNI` is enough for primary Unicode gaiji mapping. OUKOKU11 has
  568 usable Unicode mappings and no plist mappings.
- `GA16FULL` and `GA16HALF` are normal bitmap resources. The observed counts
  are 771 full-width glyphs from `B121` and 38 half-width glyphs from `A121`.
- `HONMON.DIC` is a real body stream, not a dense ID table. Expanded size is
  18,020,352 bytes.
- Entry starts are index-defined, not marker-defined. Raw marker count is
  64,453, but index-derived body boundaries produce 82,220 coherent entries.
- The app cache table has 70,375 `Block`/`Offset` rows. It is useful for
  validation, but it is not needed to extract body text.
- `OUKOKU11_indexinfo.db` is metadata, not dictionary body text.

A full raw extraction command:

```bash
logovista-tools entries /path/to/OUKOKU11 --dict OUKOKU11 \
  --section-markers --image-gaiji --html --out-dir out/oukoku
```

Expected high-level summary from the local test copy:

```text
entries_emitted:        82,220
index_entry_boundaries: 82,220
entry_markers:          64,453
image_resource_entries: 167
image_gaiji_entries:    56
unknown_controls:       0
```

## Windows Packages

Windows packages checked directly now include SINMEI7, HAESPJPN, IWKOKUG8,
EJJE200, and DAIJIRN4. They share the same SSED/EPWING-like core as the mobile
packages where matching copies are available, but add Windows app sidecars
around it.

### Numeric `00000xxx.idx` Sidecar Trees

The observed eight-hex-digit `*.idx` files are not `SSEDINFO` catalogs and are
not binary patch files. They are CP932 text sidecar trees used for appendices,
classification lists, and browser/search selector panels. Windows packages
usually reference them from `EXINFO.INI`, but they can also appear in mobile
packages and are not always declared there.

Observed row format:

```text
00000000<TAB>00000000<TAB>category label
00000002<TAB>00000002<TAB><TAB>leaf label
```

The first two columns are eight-digit hexadecimal block/offset values. Leading
tabs after the pointer columns define tree depth. Labels are CP932 text and may
contain HTML numeric entities such as `&#xe0;`, which decode to display text
such as `à`.

Pointer semantics:

- `00000000 00000000` is a non-clickable heading/category row.
- Normal pointers resolve against the raw component block table, almost always
  to `HONMON.DIC`.
- In body-stream dictionaries the pointer is a direct body destination.
- In dense-HONMON renderer dictionaries the pointer lands on a 32-byte HONMON
  ID anchor, then that raw ID resolves into `DictFULLDB`, renderer `t_contents`,
  or an Android body table.
- Values such as `10000000 0000ffff`, `30000000 0000ffff`, and
  `60000000 0000ffff` are virtual selector rows. The high nibble is the
  selector ID; in HAESPJPN these map to `西和ABC順`, `和西50音順`, and
  `動詞活用表`.

The filename usually matches the product/plugin code and the Windows renderer
DLL where present: `0000013A.idx` pairs with `HC013A.dll`, `00000135.idx` with
`HC0135.dll`, `00000152.idx` with `HC0152.dll`, `0000015E.IDX` with
`HC015E.dll`, and so on. This is a product identifier, not a universal index
type.

Observed corpus examples:

| Dictionary | File | Rows | Meaning |
|---|---|---:|---|
| `HAESPJPN_WIN` | `0000013A.idx` | 4 | Three virtual selector rows for Spanish/Japanese browse modes and verb conjugation. |
| `SINMEI7_WIN` | `00000135.idx` | 4 | Appendix entries such as accent display and symbol tables. |
| `HAFRAN` iOS/Windows | `00000152.idx` | 1,084 | A-Z French grammar/topic tree; Windows and iOS files are byte-identical. |
| `IWKOKUG8_WIN` | `000002D0.IDX` | 14 | Appendix table for word-formation, word-class, and conjugation material. |
| `DAIJIRN4_WIN` | `0000015E.IDX` | 284 | Field/season-word appendix tree pointing into HONMON ID anchors. |
| `PROYAL53_WIN` | `0000015F.IDX` | 149 | Important-word and grammar-frame appendix tree. |
| `JSSAURU2` iOS | `0000015C.IDX` | 10,298 | Large thesaurus classification tree. |
| `KENROWA` iOS | `0000015B.IDX` | 233 | Abbreviation and topic appendix tree. |
| `MEIKYOU2` iOS | `0000012d.idx` | 443 | Appendix and column tree. |

For conversion work, these files should be preserved as structured navigation
metadata. They are not usually the main lookup index, but they can expose
appendix bodies and topic hierarchies that a plain headword conversion would
otherwise miss.

### SINMEI7 Windows vs iOS

SINMEI7 Windows and SINMEI7 iOS both use the same nine core `SSEDINFO`
components:

```text
HONMON.DIC
MENU.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

Both `HONMON.DIC` files are plain `SSEDDATA`, and raw extraction works without
SQLite. The Windows copy expands to 47,515,648 bytes, has 75,532 entry markers,
and the raw index scan produces 75,529 body boundaries. `MENU.DIC` expands to
8,988,672 bytes and resolves 75,939 menu/body destinations.

Observed platform differences:

- Windows keeps renderer assets in `Templates/`, `HTMLs/`, `HANREI.chm`, and a
  product-specific `HC0135.dll`.
- iOS keeps converted assets in top-level `img/`, `html/`, `OTHER/`, plist
  manifests, app SQL, and `bin/` payloads.
- Windows `EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC0135.dll`, `PCMP3=1`,
  `IDXCOUNT=1`, `IDXINFO0=00000135.idx`, and `IDXTITLE=付録`.
- `00000135.idx` is not `SSEDINFO`; it is CP932 tab-separated appendix metadata.
  Rows contain hex block, hex offset, optional empty/category fields, and a
  display title. The block/offset values point to raw HONMON addresses and to
  decimal-named files in `HTMLs/`, for example `00005a95 00000312` maps to
  `HTMLs/23189-786.html`.
- Windows `Templates/` resources are package images just like iOS `img/`
  resources. The local SINMEI7 Windows copy exposes 203 image/BMP resources and
  29 code-shaped gaiji-image keys after scanning `Templates/`.
- The two `.uni` files have no conflicting values for shared codes. Windows has
  351 usable mappings, iOS has 331. The Windows file contributes extra rare CJK
  and compatibility mappings; the iOS file contributes three radical mappings
  not present in Windows.
- `GA16FULL` and `GA16HALF` are byte-identical across the two copies. `GA16FULL`
  starts at `B221` and has 375 glyph slots; `GA16HALF` has zero glyphs.
- `PCMDATA.DIC` remains parseable on Windows and contains MP3 records referenced
  by raw HONMON controls.

### HAESPJPN Windows vs iOS

HAESPJPN Windows and HAESPJPN iOS use byte-identical raw dictionary components:

```text
HAESPJPN.IDX
HONMON.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
COLSCR.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

The raw `HONMON.DIC` body stream is therefore fully compatible across the two
copies. Both expand to 27,979,776 bytes, expose 71,913 common entry markers,
and the raw index pass finds 79,904 body boundaries. The `entries` command
extracts coherent bodies from the Windows copy without SQLite; `rendererdb`
correctly ignores `HAESPJPN.db` because it is a conjugation/search cache with no
`t_contents` body table.

The differences are packaging and fallback assets:

- Windows has `EXINFO.INI`, `HC013A.DLL`, `Templates/`, `Panel/`, `Panels.xml`,
  `SPINDEX.DIC`, `HANREI.chm`, HTML help files, and `HAESPJPN.db`.
- Windows `EXINFO.INI` uses the legacy singleton form `IDXINFO=0000013A.idx`
  / `IDXTITLE=インデックス`, rather than `IDXCOUNT` / `IDXINFO0`.
- `0000013A.idx` is a CP932 text tree with virtual selector pointers:
  `10000000/ffff` = `西和ABC順`, `30000000/ffff` = `和西50音順`, and
  `60000000/ffff` = `動詞活用表`.
- Windows image resources live in `Templates/` and include `exam.png`,
  `sound.png`, and 122 code-shaped gaiji image keys.
- iOS image resources live in `img/`; its extra `Gaiji.plist` /
  `GaijiS.plist` fallback mappings raise the observed gaiji map from the
  Windows `.UNI` count of 60 to 97 combined mappings.

This is the cleanest current example of high raw-core compatibility with
platform-specific resource wrappers.

### IWKOKUG8 iOS vs Android vs Windows

IWKOKUG8 has been checked across iOS, Android, and Windows. The raw core is
byte-identical across all three copies:

```text
IWKOKUG8.IDX
HONMON.DIC
FKTITLE.DIC / FKINDEX.DIC
FHTITLE.DIC / FHINDEX.DIC
BKTITLE.DIC / BKINDEX.DIC
BHTITLE.DIC / BHINDEX.DIC
GA16FULL / GA16HALF
IWKOKUG8.uni / IWKOKUG8.UNI
```

The shared `HONMON.DIC` is not a body stream. It expands to 10,477,568 bytes
and contains 65,480 numeric ID records in the dense 32-byte anchor-table
layout. Raw title/index extraction still works: the `*TITLE.DIC` streams expose
lookup titles, and the index parser finds 65,468 body/index boundary rows.

The platform body payload differs:

| Platform | Body payload | Raw ID relationship |
| --- | --- | --- |
| iOS | `DictFULLDB` SQLite `IWKOKUG8.sql`, table `t_contents` | `f_DataId` matches raw HONMON IDs |
| Android | Plain `IWKOKUG8.db`, table `IWKOKUG8(Html)` | `data_id = rowid * 5` |
| Windows | Encrypted `vlpljblh`, decrypted SQLite table `t_contents` | `f_DataId` matches raw HONMON IDs |

Observed extraction counts:

```text
Raw HONMON ID records:       65,480
iOS t_contents rows:         65,480
Windows t_contents rows:     65,480
Android Html rows:           65,468
Android raw IDs missing:         12
```

Those 12 missing Android rows correspond to the 12 `f_Type=5` rows present in
the Windows/iOS `t_contents` payload. The normal dictionary bodies line up by
raw ID.

The resource wrappers differ:

- iOS uses `img/` and an iOS `DictList.plist` declaration for `DictFULLDB`.
- Android uses `resource/conf.ini`, `resource/kmkimges/`, `manual/`,
  `innerdata/`, and a plain app DB. Its `media` table uses
  `id/name/type/main` columns and stores 345 SVG blobs plus one additional
  media row.
- Windows uses `EXINFO.INI`, `HC02D0.dll`, `Templates/`, `HANREI/`,
  encrypted `vlpljblh`, and two font sidecars: `vlpljblB` is `Noto Sans JP`
  Regular OpenType/CFF, and `vlpljblN` is `Noto Serif JP` Regular OpenType/CFF.
  These font files are not encrypted SQLite sidecars.

The `rendererdb` command handles both body-cache shapes while still starting
from raw HONMON IDs: `t_contents` for Windows renderer DBs and `rowid * 5` for
the Android `Html` table shape.

### EJJE200 Windows Encryption

EJJE200 is the first observed Windows package with encrypted primary body data.
Its `EXINFO.INI` declares:

```ini
HTML=1
HTMLDLL=HC014F.dll
KWIT=1
IDXINFO0=select.html
ROSQLNAME=EJJE200.db
ENCRYHON=1
```

`HONMON.DIC` does not start with `SSEDDATA` on disk. Static analysis of the
shipped `vlpljbl.bin` shows it is a Crypto++ decryptor using AES-128-CBC
(`Rijndael`, `CBC_Decryption`, `StreamTransformationFilter`). The passphrase is
the obfuscated literal `LogoFontCipher`; each byte is stored XOR `0xff` in the
program. The key schedule is:

```text
digest = SHA256("LogoFontCipher")
AES-128-CBC key = digest[0:16]
AES-CBC IV      = digest[16:32]
key             = a3c48d86dabe8b0c91fb33d9fdf2941b
iv              = 80f2f3736bcec2e51665d02b640edbb0
```

Decrypting `HONMON.DIC` with that key reveals normal `SSEDDATA`:

```text
chunks=4087 start=0x2 end=0xff63 kind=0x0 storage=logofont_cipher
expanded_bytes=133,894,144
entry_markers=1,864,040
index_entry_boundaries=1,864,040
```

Raw entries are coherent without SQLite after decryption:

```text
(mobile)number portability
番号ポータビリティ[情報]

.NET
.NET[情報]
```

`HC014F.dll` is the product HTML renderer. It imports the normal `SSDicLib.dll`
entry/body/gaiji/picture APIs and contains strings for `epwing2HtmlBodydata` and
`pluginFunction`. It also contains the sidecar names `vlpljbl.bin`, `DIC014F`,
and `vlpljblF`, which matches the encrypted sidecar behavior.

`vlpljblF` decrypts with the same LogoFontCipher key to a SQLite database. It is
not the primary body stream. It contains 17 tables named `t_Search_1` through
`t_Search_17`, matching the 17 category checkboxes in `Templates/select.html`
for KWIT partial-match search (`情報`, `電気`, `物理`, ..., `環境`). The table
schema is:

```sql
CREATE TABLE t_Search_N (
  f_type TEXT,
  f_midasi TEXT,
  f_midasi_jis TEXT,
  f_block TEXT,
  f_offset TEXT
);
```

### DAIJIRN4 Windows Renderer Database

DAIJIRN4 is the first observed Windows package where plain `HONMON.DIC` is not
a definition stream and the full formatted body is in a Windows renderer
database. The core `SSEDINFO` table contains 11 components:

```text
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
GA16FULL
GA16HALF
```

There is no `MENU.DIC`, `PCMDATA.DIC`, or `COLSCR.DIC`. `HONMON.DIC` is plain
`SSEDDATA` and expands to 43,106,304 bytes, but the expanded stream is a dense
run of 32-byte anchor records:

```text
1f0a 1f09 0001 1f41 0160 1f04 [8 JIS cells] 1f05 1f61
```

Every fifth record carries an eight-cell full-width decimal ID, for example
`00000025`; the other four records in that group are blank anchors. The marker
start used by raw indexes is two bytes after the 32-byte record start. Example:
data id `25` is at record offset `768`, block `2`, offset `768`, with the
marker target at block `2`, offset `770`.

The count relationship is exact:

```text
HONMON entry markers:       1,347,035
HONMON ID records:            269,407
t_contents rows:              269,386
DB rows matching raw IDs:      269,386
raw IDs missing in DB:             21  terminal trailer anchors only
```

This is not a failed HONMON parse. It is a raw anchor table. The body payload is
the encrypted sibling `vlpljblb`, and the bundled `vlpljbl.bin` is byte-identical
to the EJJE200 decryptor. Decrypting `vlpljblb` with the LogoFontCipher key
produces a 610,735,616-byte SQLite database with three tables:

```sql
CREATE TABLE t_contents (
  f_DataId INTEGER PRIMARY KEY,
  f_Type INTEGER,
  f_DataGroupId INTEGER,
  f_Anchor TEXT,
  f_Title TEXT,
  f_Title_SS TEXT,
  f_Html TEXT,
  f_Keyword TEXT,
  f_Plane TEXT
);

CREATE TABLE t_bunya (
  f_DataId INTEGER NOT NULL PRIMARY KEY,
  f_GenreKey TEXT,
  f_Title TEXT,
  f_TitleSS TEXT
);

CREATE TABLE media (
  No INTEGER NOT NULL PRIMARY KEY,
  f_name TEXT,
  f_type INTEGER,
  f_main BLOB
);
```

Observed row counts and content types:

```text
t_contents: 269,386 rows
t_bunya:     47,375 rows
media:        2,830 rows

f_Type=1  parent entries                 179,350
f_Type=2  child/sub entries               78,144
f_Type=3  idiom/phrase entries             8,328
f_Type=4  kanji entries                    3,283
f_Type=5  late appendix/search rows          273
f_Type=6  terminal/special rows                8

media f_type=2  PNG appendix images           86
media f_type=3  GIF entry figures          2,744
```

`f_Html` is complete renderer HTML. It contains links such as
`lved.dataid:01346760` and inline figure tags such as
`<img src="3djr_0002.gif" class="media">`; those image names resolve to the
`media.f_name` BLOB table. `f_Plane` is the flattened plain/search body. The
`rendererdb` command emits the HTML/plain rows and can write the `media` BLOBs
as portable image files.

`HC015E.dll` confirms this interpretation. Its strings include
`pluginFunction2nd`, `epwing2HtmlBodydata`, the sidecar name `vlpljblb`, and SQL
queries such as `SELECT f_Html FROM t_contents WHERE f_DataId = ?` and
`SELECT f_name, f_main FROM media WHERE f_name = ?`.

`EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC015E.dll`, `IDXCOUNT=3`,
`IDXINFO0=0000015E.IDX`, `IDXINFO1=select.html`, `IDXINFO2=select2.html`,
`ROSQLNAME=DAIJIRN4.db`, `BUBUNDB=1`, `ZENBUNDB=1`, and `VERTICAL=1`.
`0000015E.IDX` is a CP932 tab-tree with 284 rows. Its first two columns are
hex block/offset pointers into the raw HONMON anchor layer; tab depth defines
labels such as `大辞林 第四版 / 分野別索引 / 季語 / 春`.

DAIJIRN4 gaiji/resources are otherwise normal:

```text
DAIJIRN4.uni: simple12, 92 half records, 1,191 full records, 1,243 mappings
GA16HALF:     8x16, start A121, 92 glyphs
GA16FULL:    16x16, start B121, 1,191 glyphs
Templates:  255 portable resources after PNG/GIF/BMP/SVG discovery
```

### PROYAL53 Windows Renderer Database and Ziptomedia

PROYAL53 (`旺文社「プチ・ロワイヤル 仏和（第5版）・和仏（第3版）辞典」`)
is another Windows renderer-database package. Its raw core has the same 11
component shape as DAIJIRN4:

```text
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
GA16FULL
GA16HALF
```

`HONMON.DIC` is plain `SSEDDATA`, expands to 12,040,192 bytes, and is a dense
32-byte raw ID anchor table rather than definition text. Direct `entries`
extraction correctly emits no body entries. `audit-honmon` classifies the
package as `dense_honmon_id_table_rendererdb`.

`EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC015F.dll`, `PCMP3=1`,
`IDXINFO0=0000015F.IDX`, `IDXINFO1=select.html`, `ROSQLNAME=PROYAL53.db`,
`BUBUNDB=1`, and `ZENBUNDB=1`. The auxiliary `0000015F.IDX` is a readable
CP932 tab tree with 149 rows and HONMON anchor destinations for sections such
as `仏和辞典-重要語 / ランク1`.

The encrypted sibling `vlpljblF` decrypts with the LogoFontCipher key to a
SQLite renderer database. Its schema is a lowercase variant of the same idea:

```sql
CREATE TABLE t_contents (
  f_dataid INTEGER PRIMARY KEY,
  f_datagroupid INTEGER,
  f_type INTEGER,
  f_genre INTEGER,
  f_title TEXT,
  f_title_ss TEXT,
  f_keyword TEXT,
  f_sakuin TEXT,
  f_rank INTEGER,
  f_html TEXT,
  f_plane TEXT
);

CREATE TABLE t_media (
  id INTEGER PRIMARY KEY,
  type INTEGER,
  name TEXT,
  main BLOB
);
```

Renderer extraction now normalizes `t_contents` column case and accepts both
`media` and `t_media` BLOB-table names. Observed counts:

```text
raw HONMON ID records:       75,243
t_contents rows:             75,225
rows matching raw IDs:        75,224
DB rows without raw ID:            1
raw IDs missing in DB:            19

f_type=1  main entries        44,538
f_type=2  sub/child entries   30,564
f_type=8..12 special rows        122

t_media rows:                   342
t_media type=1                  116
t_media type=2                  226
```

The raw search/title layer is parseable:

```text
FKINDEX/BKINDEX leaf rows:   362,519 each
FHINDEX/BHINDEX leaf rows:   361,903 each
FK/BK search groups:         314,922
unknown index leaf bytes:          0
title unknown controls:            0
```

`f_Html` is complete renderer HTML and contains enough structure for a
Yomitan/MDict-style export: headword spans, subentry anchors, grammatical
labels, examples, inline references, and media/image tags. `f_keyword` is also
very useful for exact lookup expansion because it contains alternate spellings
and inflected French forms separated by `∥`, for example `abandonné`,
`abandonnee`, plural forms, and conjugated forms. This is better lookup
evidence than trying to infer every exact Yomitan headword from display HTML.

Image resources come from both `Templates/` and `HANREI/img`. The renderer HTML
references assets such as `sound.png`, `b159_M.png`, and BLOB-backed names such
as `00002153-0082-000006ec.png`; `--write-media` preserves original renderer
filenames when possible so those HTML `src` values can be copied or rewritten
directly. PROYAL53 gaiji is otherwise normal:

```text
PROYAL53.uni: simple12, 60 half records, 350 full records, 120 mappings
GA16HALF:     8x16, start A121, 60 glyphs
GA16FULL:    16x16, start B121, 351 glyphs
raw unresolved gaiji codes after .uni/image/bitmap coverage: 0
```

PROYAL53 also introduces loose ziptomedia audio. Renderer HTML links look like:

```html
<a href="lved.ziptomedia:000010.wav"><img src="sound.png" class="gaiji_icon"></a>
```

The physical files live outside the dictionary core in the sibling directory
`_DCT_PROYAL53_Sound_Files/` and have no filename extension. Each observed file
is LogoFontCipher-wrapped audio; decrypting `000010` yields a normal
`RIFF/WAVE` file:

```text
000010.wav: WAVE audio, Microsoft PCM, 16 bit, mono 44100 Hz
```

The package references more audio than is physically present in the local copy:

```text
HTML ziptomedia references:       17,155
distinct referenced sound names:  17,124
loose sound files present:         2,506
unreferenced loose sound files:        0
referenced sound files missing:   14,618
```

That is not a body-decoding blocker. It means a full text/image conversion is
well covered, while complete audio export requires a package/install that
contains the missing ziptomedia files.

### SPINDEX.DIC

`SPINDEX.DIC` is a standalone Windows auxiliary file, not a component declared
inside the product `.IDX`. The local corpus currently contains four copies:
EJJE200, HAESPJPN Windows, SINMEI7 Windows, and the official Windows browser's
EJJE200 install copy. All four are byte-identical:

```text
sha256 aabd6d909fb7bed5d446192fbbf757d18367ca28fb6d72ad69984a842b1a85b9
size   14349 bytes
```

The file is still an SSED container. Its header starts with `SSEDDATA`, has the
submagic bytes `SPDATA`, reports kind byte `0x54`, and declares 116 compressed
chunks over logical blocks `0xd9c8..0xe101`:

```text
declared logical blocks:     1,850
expected expanded bytes:     3,788,800
physical chunks present:     2 / 116
expanded bytes present:      38,208
complete expanded pages:     18
partial expanded pages:      1
```

The physical file is therefore only a prefix of the declared SSED stream. The
chunk table points to later chunk offsets up to roughly `0x206921`, but the
file ends at `0x380d`. The second physical chunk is incomplete. Generic SSED
expansion should not assume that every offset in this file is backed by bytes.

The bytes that are present decode cleanly as index branch pages using the same
page machinery as `FKINDEX.DIC` internal pages:

```text
root page  logical block 0xd9c8  header word 0x601e  rows 33  slot 34
page 2     logical block 0xd9c9  header word 0x4020  rows 56  slot 36
page 3+    logical block 0xd9ca  header word 0x0020  rows 56  slot 36
```

No leaf/result pages are present in the observed physical file. The 19 parsed
pages contain 1,022 internal rows. Eighteen child links point to pages present
in the physical prefix and 1,004 child links point to missing pages.

The keys are stored backward:

```text
CITEROHPAID       -> DIAPHORETIC
DEZIRECREM        -> MERCERIZED
EPATGNITALUSNI    -> INSULATINGTAPE
GNIFRUSOGE        -> EGOSURFING
TEEHSNOITANIMAXE  -> EXAMINATIONSHEET
```

This strongly indicates suffix/backward-search support. The official
`SSDicLib.dll` also exposes `SDicSupportHore` and `epwing2HtmlSupportHore`
strings, which is consistent with 後方 search support. The rows visible in
`SPINDEX.DIC` are separator/fence keys for internal B-tree pages, not
dictionary hits. Because the observed file has no leaf pages and is identical
across unrelated dictionaries, it should be treated as common auxiliary
LogoVista/SSDicLib suffix-search metadata or a bundled search skeleton. It is
not a product-specific dictionary index and cannot produce body entries by
itself.

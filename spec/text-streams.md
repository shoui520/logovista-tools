# Text Streams and Body Storage

Expanded `HONMON.DIC` / `HONMON.DIN`, entry slicing, dense HONMON tables, database-backed body payloads, and outliers.

## Expanded `HONMON.DIC` / `HONMON.DIN`

After SSED expansion, the main body component is not Shift-JIS and not UTF-8.
Windows packages normally name it `HONMON.DIC`; the observed Mac OS X SSED
package names the same type of component `HONMON.DIN`. The expanded payload is
an EPWING/JIS-like stream.

Text is mostly JIS X 0208 pairs:

```text
0x21..0x7e 0x21..0x7e
```

The decoder first wraps these pairs in ISO-2022-JP escape sequences. If that
fails, it converts the 7-bit JIS cell to Shift_JIS and tries CP932, then
Shift_JIS-2004. That fallback is required for observed extension cells such as:

```text
2d21 -> ①
2d54 -> ㎏
2c29 -> ❾
233f -> ◦
```

Without the extension fallback, older Gakken/Houken/Nichigai streams appear to
contain invalid JIS pairs even though the bytes are displayable symbol cells.

The stream also contains `0x1f` control opcodes. Important controls observed:

```text
1f 02             entry/wrapper start in some streams
1f 03             entry/wrapper end in some streams
1f 04             halfwidth conversion span start
1f 05             halfwidth conversion span end
1f 06 / 1f 07     subscript start/end
1f 09 xx xx       entry marker, commonly 1f 09 00 01
1f 0a             line break
1f 0b / 1f 0c     literal/preformatted span start/end
1f 0e / 1f 0f     superscript start/end
1f 10 / 1f 11     italic-ish start/end
1f 12 / 1f 13     emphasis-ish start/end
1f 1a xx xx       tab/column position control
1f 1c xx xx       media block layout control; observed before media refs
1f 36 ...         HC renderer image/link descriptor; 12-byte payload
1f 37 ...         HC renderer internal link descriptor; 10-byte payload
1f 3b / 1f 5b     URL span start/end
1f 3c ...         HC renderer inline picture reference; 18-byte payload
1f 5c             HC renderer picture/reference end
1f 41 xx xx       headword span start
1f 61             headword span end
1f 42             body/cross-reference link start
1f 62 ...         body/cross-reference link end with payload
1f 43             menu/navigation link start
1f 63 ...         menu/navigation link end with payload
1f 44 ...         extended link start with a 10-byte payload; HC renderers
                  can route this through the picture extraction path
1f 48 ...         HC renderer internal link descriptor; 10-byte payload
1f 49 ...         TOC/internal link start with a 10-byte payload
1f 64 ...         extended link end with a 6-byte payload
1f 69             TOC/internal link end
1f 4a ...         jump/audio range start; usually 16-byte payload, with a
                  renderer-observed 14-byte mode-0 form
1f 4b ...         HC renderer link descriptor end/skip; 6-byte payload
1f 4c xx xx       HC renderer layout/control directive
1f 6a             jump/audio range end
1f 4d ...         inline media/reference start with an 18-byte payload
1f 6d             media/reference end
1f 4e ...         HC renderer variable descriptor; 38- or 40-byte payload
1f 4f ...         HC renderer variable descriptor; 34- or 48-byte payload
1f e0 xx xx       bold-ish start
1f e1             bold-ish end
1f e2 xx xx       private renderer directive start
1f e3             private renderer directive end
1f e4/e6 xx xx    HC renderer-private directives
```

The current extractor does not claim full semantic knowledge of every control.
It uses enough structure to preserve line breaks and avoid mixing payload bytes
into visible text.

The HC renderer entries above come from code-level analysis of the Windows
`HC????.dll` renderer loops. They describe how the renderers skip payload bytes
and which controls trigger link, picture, sound, and private-layout behavior.
They should not be interpreted as a claim that every dictionary uses every
renderer-private control.

Renderer-compatible behavior is treated as evidence only when it matches
controls observed in LogoVista/SSED expanded streams. Electronic Book-family
tools also handle EPWING, EBXA-C, and related formats, so generic labels such
as `<SUP>`, `<SUB>`, `<LINK>`, `<FIG>`, and `<WAV>` are not automatically
imported as SSED opcode semantics. The strongest promoted behavior remains
`1f04`/`1f05`, which is the halfwidth-conversion span pair used by LogoVista
text streams.

`1f e2` / `1f e3` are no longer treated as visible color/style spans. A full
Windows corpus pass shows that they wrap renderer directives such as `IMG:`,
`RUB:`, `SMC:`, `IDX:`, `HTM:`, `SQL:`, `GTH:`, `BOX:`, and
`<PlaySound>...`. The directive text is part of the raw model and remains
present in lossless spans, but plain/HTML body rendering suppresses it so
entries do not leak implementation strings such as `ＳＱＬ：` or
`ＩＭＧ：０００１．ｂｍｐ` into user-facing text. The renderer semantics of each
directive prefix are still being cataloged separately.

`1f 0b` / `1f 0c` are observed as a zero-argument paired span. The safest
current label is literal/preformatted. ROYALEGR uses the pair around
box-drawing table rows, where spacing matters. NKGORIN2 uses it around ASCII
numeric character references such as `&#x4E05;`, which strongly suggests
compatible renderers treat this region specially instead of as ordinary JIS
body text.

`1f 3b` / `1f 5b` are observed as a zero-argument paired URL span in GEN2001.
The span encloses URL display text and an italicized duplicate URL line.

`1f 1a` and `1f 1c` have two-byte payloads. `1f1a` is used in nihonshi and
IPHYCHE5 in table-like runs: era-name/readings/date columns and chemical
element table columns. The current model tags it as `tab_column` and preserves
the raw payload as the column/position value. `1f1c 2000` is observed in
IPHYCHE5 immediately before `1f4d` media references; the current model tags it
as `media_layout`. Both controls are nonprinting.

`1f 44` / `1f 64` are an extended link pair. The start control has a 10-byte
payload; the end control has a 6-byte payload. ROYALEGR and KQSYNONM use this
pair. Treating `1f44` as zero-argument leaks binary pointer bytes into the text
stream and creates false unknown bytes / invalid JIS pairs.

`1f 49` / `1f 69` are a table-of-contents/internal link pair observed in
IBIO4VRS `TOC.DIC`. The start control has a 10-byte payload. The first four
bytes behave like outline/path or level bytes; the final six bytes are a
standard big-endian body pointer (`block`, `offset`). For example,
`1f49 00010203 00000004 0130` wraps a visible TOC label and points to block
`4`, offset `0x0130`. The closing `1f69` has no payload.

`1f 4a` starts are followed by 16 bytes of binary target metadata before
visible link text resumes. In PCMDATA dictionaries, the same payload encodes a
sound/media start and end range. In HAESPJPN, treating this as a 15-byte
payload leaks one binary byte into the text stream and produces mojibake before
labels such as `→音声1`. `1f 4d` media starts have an 18-byte payload in the
same dictionary family.

`1f 04` / `1f 05` are a text-mode span pair, not a generic style pair.
Compatible plain-text behavior treats them as `半角開始` / `半角終了`: JIS row-3
fullwidth ASCII cells inside this span to halfwidth ASCII. Raw bodies still
store Latin letters as JIS cells such as `2341` for `Ａ`; the span controls
define the display/export width. Outside the span, the decoded model preserves
the original fullwidth text in `text` and does not narrow it in `normalized`.
The conservative HTML renderer keeps this mode boundary as
`<span class="lv-halfwidth">...</span>` after applying the visible narrowing.
Search indexes do not carry these display controls, so lookup-key decoders
still normalize row-3 cells for practical matching. Writer-side index encoding
normalizes ASCII/fullwidth-ASCII lookup keys to uppercase row-3 JIS cells; this
does not change lowercase display text in body/title streams.

The corpus-wide `opcode-atlas` command scans expanded text-stream components
and emits a per-opcode table with payload lengths, component roles, surrounding
context, pair behavior, examples, and confidence labels. The current full pass
over the Windows corpus scanned 7,026,978,819 expanded text-stream bytes and
observed 713,941,069 `0x1f` controls across 40 distinct opcodes. The only
singleton anomaly is `25IGAKU` `FHTITLE.DIC` `1f1f`: it appears once, by itself,
between two title lines. It is treated as a vendor title-stream defect, not as
evidence for a global zero-argument opcode.

A bare `0x0a` byte, not introduced by `0x1f`, appears once in the current
corpus (`NANDOKU1`). It is handled as a legacy line break byte.

The exact bare two-byte sequence `11 03` appears as a nonprinting separator in
KQNEWEJ6 `0x0d` multi-title streams. It occurs between otherwise readable
title lines, for example around phrase titles such as `have ...` and
`a precious stone`. The toolkit accounts for this exact sequence as a legacy
title separator. A lone `0x11` outside this sequence is still reported as an
unknown byte; this keeps the earlier ITALIAN `FHTITLE.DIC` anomaly visible.

## Full HONMON Byte Accounting

The `honmon-bytes` command decodes the entire expanded `HONMON.DIC` stream
without emitting spans. It is meant to answer a stricter question than entry
extraction: whether every byte is structurally accounted for.

An earlier Windows SSED corpus result:

```text
targets:                    169
HONMON present/expanded:     161
missing HONMON files:          8
expanded HONMON bytes: 3,497,793,539
bytes covered:         3,497,793,539
uncovered bytes:                   0
controls:                460,913,534
known controls:          460,913,534
unknown controls:                  0
unknown bytes:                     0
invalid JIS cells:                 0
truncated controls:                1
```

The one truncated control is `NANDOKU3`, whose expanded HONMON physically ends
with a lone final `0x1f` byte after the last decoded text cell. The byte is
covered and reported as a forensic issue. The toolkit does not synthesize an
opcode for it.

## Entry Slicing

Many body streams use this marker near many entry boundaries:

```text
1f 09 00 01
```

A marker-only strategy is insufficient for some body streams. OUKOKU11 real
entries can begin with other `1f09` section codes, including `0008`, `0003`,
`0004`, `0002`, and `1001`. For example, the first two raw body entries in
one observed package start at:

```text
block 2 offset 2    1f09 0008  <readable title text>
block 2 offset 146  1f09 0003  <readable title text>
```

Those entries are discoverable from raw `*INDEX.DIC` body pointers, not from
the `0001` marker scan. The current `entries` command therefore collects body
pointers from parsed index leaf rows, converts them to HONMON-relative byte
offsets, sorts and deduplicates them with marker starts, then slices from each
boundary to the next. `--no-index-boundaries` restores marker-only slicing for
debug comparison.

This works well for dictionaries where `HONMON.DIC` really is a body stream,
including dictionaries such as GENIUSEB, HAESPJPN, and OUKOKU11.

## Dense HONMON Tables

Some products have a large expanded `HONMON.DIC`, but it is not a definition
body stream. Instead it is a dense run of 32-byte records that look like:

```text
1f09 0001 1f41 .... 1f04 [blank JIS cells] 1f05 1f61 1f0a
```

Blank slots contain repeated JIS blank cells (`2121`). Populated slots contain
body IDs in the same span:

```text
1f0a 1f09 0001 1f41 0160 1f04 2330 2330 ... 1f05 1f61
```

Decoded as text, the ID span can be:

```text
00000755
00197570
00851665
```

Those IDs correspond to `DictFULLDB` body rows such as:

```text
00000755 -> <headword/title text>
00197570 -> <headword/title text>
00851665 -> <headword/title text>
```

The model for these dictionaries is:

- direct HONMON slicing does not recover definitions;
- HONMON is still meaningful as a raw numeric ID/address table;
- `DictList.plist` can name a sibling `DictFULLDB` payload;
- Windows `EXINFO.INI` / renderer sidecars can provide a `t_contents` payload;
- the full body is recovered by following raw HONMON IDs into the appropriate
  payload.

Other dense products use the same 32-byte HONMON structure but store opaque
tokens rather than decimal body IDs. HOUGAKU5 is currently in this class: raw
slices decode as short tokens such as `K0NVOzjh`, not readable legal dictionary
definitions. Those records are still useful as raw linkage evidence, but they
need a separate dereference model.

Observed non-body HONMON dictionaries in the local corpus include:

| Dictionary | Dense raw payload | Lookup/title text available without body DB text |
| --- | --- | --- |
| `HABGESPA` | Numeric ID table | No title components; Spanish keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `HAFRAN` | Numeric ID table | No title components; French keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `HOUGAKU5` | Opaque token table | Index/title linkage exists, but sampled HONMON slices are not definitions. |
| `IWKOKUG8` | Numeric ID table | `*TITLE.DIC` streams expose Japanese lookup titles. |
| `JSSAURU2` | Numeric ID table | Index/title linkage exists; sampled HONMON slices are not definitions. |
| `KENROWA` | Numeric ID table | `*TITLE.DIC` streams expose Russian/Japanese lookup titles. |
| `KOJIEN7` | Numeric ID table | `*TITLE.DIC` streams expose Japanese lookup titles; HONMON IDs resolve to `DictFULLDB`. |
| `NANMED20` | Numeric ID table | `*TITLE.DIC` streams expose alias triples such as `見出し|読み|表示見出し`. |
| `DAIJIRN4_WIN` | Numeric ID anchor table | `*TITLE.DIC` streams expose headwords; HONMON IDs resolve to encrypted Windows renderer table `t_contents`. |
| `IWKOKUG8_ANDROID` | Numeric ID anchor table | Raw core matches iOS/Windows; HONMON IDs resolve to Android `IWKOKUG8(Html)` rows by `rowid * 5`. |

For these, the `entries` command skips body extraction by default and reports a
warning in `summary.json`. Try `fulldb` when `DictList.plist` declares
`DictFULLDB`; try `rendererdb` when a Windows package has `EXINFO.INI` and an
encrypted renderer SQLite sidecar such as `vlpljblb`, or when an Android package
has the observed `DICTID(Html)` app body table.

This is why the toolkit keeps the dense raw layer in scope. Even when a
database is required for final body text, raw `HONMON.DIC` and `*INDEX.DIC`
still define the dictionary's body anchors, lookup pointers, title linkage, and
the exact subset/order of IDs that belong to the packaged dictionary.

Dense HONMON is a supported model variant, not a SSED-format blocker. The
reader/model must classify it and expose the dereference path. A plain core SSED
writer does not need to emit dense HONMON, renderer DBs, Android DBs, or
`DictFULLDB`; writer v0 targets self-contained body-stream `HONMON.DIC`.

## SQLite Sidecars and DictFULLDB Payloads

Many LogoVista products include `.db`, `.sqlite`, or `.sql` files. These are
not Windows-only. Observed packages use SQLite-style sidecars in Windows,
iOS, Android, Mac-adjacent app bundles, and stripped/no-platform SSED layouts.
The filename extension alone does not identify the role.

Practical interpretation:

- `DictFULLDB` is the `DictList.plist`-declared full body payload for some
  products, such as KOJIEN7.
- Windows `EXINFO.INI` can declare app/renderer SQLite with `SQLNAME=` or
  `ROSQLNAME=`.
- Android packages can use a plain package DB such as `DICTID.db`, plus
  metadata DBs such as `*_indexinfo.db`.
- Other plain DBs can be examples/idioms, link-reference tables, kanji-support
  tables, chronology/ancillary tables, template navigation/filter databases,
  or media BLOB stores.
- It may flatten or normalize formatting.
- It may also contain formatted HTML with `div`, `sub`, `object`, `img`, and
  `lved.dataid:` links.
- It is useful for validation, fallback, pointer discovery, and full-body
  extraction when raw HONMON stores IDs instead of definitions.

The `entries` and `titles` commands stay on the native `.DIC`/`.IDX` path. The
`fulldb` command follows declared `DictFULLDB` payloads after decoding body IDs
from raw HONMON records. The `rendererdb` command handles raw-ID-assisted
renderer/app DB bodies, including Windows `t_contents`/`HONBUN` sidecars and
the observed Android `DICTID(Html)` rowid layout. The `gaiji-report` command
reads SQLite only as auxiliary validation evidence.

Renderer/app DBs and declared `DictFULLDB` payloads are related but not the
same declaration mechanism. Renderer/app DBs are not `DictFULLDB` entries in
`DictList.plist`; they are platform body/render caches. The toolkit still
treats them as raw-ID-assisted body sources, not as replacements for raw
parsing: `rendererdb` first decodes dense HONMON IDs and then accepts only DB
rows that match those raw IDs. Renderer DBs can also contain rows whose IDs are
outside that decimal raw-ID namespace. The observed Windows `NANMED20`
`vlpljblF` sidecar has `t_contents.f_DataId` values such as `99A00001`; these
are preserved as sidecar-only rows in reports and are not interpreted as raw
HONMON anchors.

## Non-SSED LVED/WebView2 Packages

OXFPEU4 and KQCMPROS should not be treated as failed `HONMON.DIC` body-stream
decodes. They are a separate LVED/WebView2 package family. The useful payload
is `main.data` on Windows or `*.dbc` in mobile-style packages, and the observed
payloads are SQLCipher 4 databases with 4096-byte pages.

The high entropy and lack of plaintext `SQLite format 3` headers originally
made these payloads look opaque. With the Windows viewer path understood, the
toolkit now classifies and validates them through the `lved` command. Keep
this family separate from the SSED/HONMON model; there is no normal expanded
`HONMON.DIC` body stream to slice. See [LVED SQLCipher Packages](lved-main-data.md).

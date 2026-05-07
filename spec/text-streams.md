# Text Streams and Body Storage

Expanded `HONMON.DIC`, entry slicing, dense HONMON tables, database-backed body payloads, and outliers.

## Expanded `HONMON.DIC`

After SSED expansion, `HONMON.DIC` is not Shift-JIS and not UTF-8. It is an
EPWING/JIS-like stream.

Text is mostly JIS X 0208 pairs:

```text
0x21..0x7e 0x21..0x7e
```

The decoder wraps these pairs in ISO-2022-JP escape sequences and lets Python
decode the character.

The stream also contains `0x1f` control opcodes. Important controls observed:

```text
1f 02             entry/wrapper start in some streams
1f 03             entry/wrapper end in some streams
1f 04             style or text span start
1f 05             style or text span end
1f 06 / 1f 07     subscript start/end
1f 09 xx xx       entry marker, commonly 1f 09 00 01
1f 0a             line break
1f 0b / 1f 0c     literal/preformatted span start/end
1f 0e / 1f 0f     superscript start/end
1f 10 / 1f 11     italic-ish start/end
1f 12 / 1f 13     emphasis-ish start/end
1f 3b / 1f 5b     URL span start/end
1f 41 xx xx       headword span start
1f 61             headword span end
1f 42             link-ish start
1f 62 ...         link-ish end with payload
1f 4a ...         jump/link/media start with a 16-byte payload
1f 4d ...         media/reference start with an 18-byte payload
1f e0 xx xx       bold-ish start
1f e1             bold-ish end
1f e2 xx xx       color/style start
1f e3             color/style end
```

The current extractor does not claim full semantic knowledge of every control.
It uses enough structure to preserve line breaks and avoid mixing payload bytes
into visible text.

`1f 0b` / `1f 0c` are observed as a zero-argument paired span. The safest
current label is literal/preformatted. ROYALEGR uses the pair around
box-drawing table rows, where spacing matters. NKGORIN2 uses it around ASCII
numeric character references such as `&#x4E05;`, which strongly suggests the
official renderer treats this region specially instead of as ordinary JIS body
text.

`1f 3b` / `1f 5b` are observed as a zero-argument paired URL span in GEN2001.
The span encloses URL display text and an italicized duplicate URL line.

`1f 4a` starts are followed by 16 bytes of binary target metadata before
visible link text resumes. In PCMDATA dictionaries, the same payload encodes a
sound/media start and end range. In HAESPJPN, treating this as a 15-byte
payload leaks one binary byte into the text stream and produces mojibake before
labels such as `→音声1`. `1f 4d` media starts have an 18-byte payload in the
same dictionary family.

## Entry Slicing

Many body streams use this marker near many entry boundaries:

```text
1f 09 00 01
```

A marker-only strategy is insufficient for some body streams. OUKOKU11 real
entries can begin with other `1f09` section codes, including `0008`, `0003`,
`0004`, `0002`, and `1001`. For example, the first two raw body entries in
OUKOKU11 start at:

```text
block 2 offset 2    1f09 0008  あ ア
block 2 offset 146  1f09 0003  あ【亜】【亞】
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
00000755 -> アイ‐アイ 【aye-aye】
00197570 -> か・ける 【掛ける・懸ける】
00851665 -> にほん 【日本】
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
| `IWKOKUG8` | Numeric ID table | `*TITLE.DIC` streams expose Japanese lookup titles such as `ああ【嗚呼】`. |
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

## DictFULLDB Payloads

Many LogoVista products include `.db` or `.sql` files. Some are app search
caches; some are explicitly declared by `DictList.plist` as `DictFULLDB`.
KOJIEN7 is in the second category, and its declared body payload happens to be
SQLite.

Practical interpretation:

- Database files may be app/mobile search caches.
- `DictFULLDB` is the declared full body payload for some products.
- It may flatten or normalize formatting.
- It may also contain formatted HTML with `div`, `sub`, `object`, `img`, and
  `lved.dataid:` links.
- It is useful for validation, fallback, pointer discovery, and full-body
  extraction when raw HONMON stores IDs instead of definitions.

The `entries` and `titles` commands do not read SQLite. The `fulldb` command
does, but only after decoding body IDs from raw HONMON records. The
`gaiji-report` command reads SQLite as an auxiliary validation source and keeps
that evidence separate from the raw `.DIC`/`.IDX` extraction path.

Windows renderer DBs and the observed Android body DB shape are related but not
the same declaration mechanism. They are not `DictFULLDB` entries in
`DictList.plist`; they are platform body/render caches. The toolkit still treats
them as raw-ID-assisted body sources, not as replacements for raw parsing:
`rendererdb` first decodes dense HONMON IDs and then accepts only DB rows that
match those raw IDs.

## Outliers

`OXFPEU4` declares `DictFtsDB` rather than `DictFULLDB`. Its SSED side contains
only tiny stub data, and `OXFPEU4.dbc` is an opaque 2048-byte-block payload.
Observed properties:

- size is exactly `7782 * 2048` bytes;
- entropy is effectively maximum at `7.999987` bits per byte;
- no SQLite, SSED, ZIP, gzip, zlib, HTML, or EPWING marker is present;
- no repeated 16-byte blocks were observed;
- fixed XOR and short-period mask probes did not reveal plaintext.

Treat this class as encrypted or otherwise cryptographically packed until a
reader implementation or documented key schedule is available.

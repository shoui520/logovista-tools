# SSED Container

Container, compression, encryption, and component composition notes for `SSEDINFO` / `SSEDDATA`.

## `SSEDINFO` `.IDX`

The `.IDX` file is the catalog for the compressed components. It starts with:

```text
offset  size  meaning
0x00    8     ASCII magic: SSEDINFO; Android SSED packages can use LVEDINFO
0x0c    1     dictionary title byte length
0x0d    var   CP932 dictionary title
0x4d    1     component count
0x80    ...   component records
```

Each component record is `0x30` bytes:

```text
record offset  size  meaning
0x02           1     multi/resource flag
0x03           1     EPWING-ish component type
0x04           4     start logical block, big endian
0x08           4     end logical block, big endian
0x0c           4     component metadata bytes
0x10           1     filename byte length
0x11           var   ASCII filename bytes, followed by NUL padding
```

The logical block size is 2048 bytes. If a component starts at logical block
`N`, a composed book image places it at:

```text
(N - 1) * 2048
```

Observed LVLMultiView packages may have an SSEDINFO-like facade variant where
the component count byte is at `0x4c` and the component record table starts at
`0x7f`; the normal `0x4d` / `0x80` layout is also observed. The record body
layout is unchanged. These packages have absent declared component files and
readable data in LogoFontCipher-encrypted SQLite payloads; see
[LVLMultiView Packages](multiview.md).

Observed Android SSED packages may use `LVEDINFO` as the catalog magic while
keeping the normal SSEDINFO record layout. This is an Android wrapper marker,
not the modern `LVED_SQLITE3` SQLCipher package family by itself.

Component types observed so far:

```text
0x00  HONMON.DIC / HONMON.DIN / HONMON.DIW body/main text component
0x01  MENU.DIC
0x10  SCRMENU.DIC screen/menu navigation stream
0x03  KWTITLE.DIC
0x04  FKTITLE.DIC
0x05  FHTITLE.DIC
0x06  BKTITLE.DIC
0x07  BHTITLE.DIC
0x09  BATITLE.DIC alternate title stream
0x0a  CRTITLE.DIC
0x0d  MUL* title stream used by MULTI selectors
0x20  TOC.DIC table-of-contents text stream
0x28  IDXJUMP.DIC index-jump text stream
0x30  KINDEX.DIC body-only tagged index
0x60  HINDEX.DIC body-only simple index
0x70  BKINDEX.DIC
0x71  BHINDEX.DIC
0x72  BAINDEX.DIC alternate/simple backward index
0x80  KWINDEX.DIC
0x81  CRINDEX.DIC
0x90  FKINDEX.DIC
0x91  FHINDEX.DIC
0x92  FAINDEX.DIC alternate/simple forward index
0xa1  MUL* multi-selector index
0xd0  FIGURE.DIC / MONOSCR.DIC monochrome figure/resource stream
0xd2  COLSCR.DIC media/image resource stream
0xd8  PCMDATA.DIC audio/media resource stream
0xe0  COLSMPL.DIC color-sample resource stream
0xf1  GA16FULL resource
0xf2  GA16HALF resource
0xff  MULTI*.DIC selector descriptor
```

`RIGHT.DIC` is a text/copyright stream. It is observed both as declared
component type `0x02` and, in KCOMPEJ2, as a loose one-block `SSEDDATA`
sidecar that is not listed in the main `SSEDINFO` catalog.

`FIGURE.DIC` and `MONOSCR.DIC` are observed as component type `0xd0`, but they
should not be collapsed into one decoded format. KOJIEN6 `MONOSCR.DIC` is a
64x64 1bpp monochrome bitmap store referenced by HONMON `1f44` / `1f64`
pairs. KQSYNONM/ROYALEGR-style `FIGURE.DIC` uses the same control pair, but
`1f44` carries packed-BCD height/width and `1f64` points to a variable-size
row-padded 1bpp bitmap payload. Both are distinct from `COLSCR.DIC` because
they do not use the `data` wrapper described for the color image store.

`COLSMPL.DIC` is observed as component type `0xe0`. KOJIEN6 stores fixed
2048-byte color-sample records. Each record prefix decodes to compact Munsell
notation, and the label field at byte offset 20 decodes as a JIS color name.
The component is tied to renderer color-map behavior and the `1f14` / `1f15`
text-stream range, not to ordinary image extraction. The exact official
Munsell-to-RGB conversion remains renderer-side behavior rather than a direct
value stored in the component.

`SCRMENU.DIC` is observed as component type `0x10`. It is an advanced
screen/menu navigation stream used by KOJIEN6 instead of ordinary `MENU.DIC`.
It is not a B-tree index page and it is not parsed by the generic `menus`
command. Its recovered controls describe screen groups, `COLSCR.DIC`
background-image pointers, hotspot rectangles, screen jumps, and direct body
targets; see
[Menus, Titles, and Indexes](menus-titles-indexes.md).

The exact semantic names vary by dictionary, but the broad pattern is stable:
title components store readable headword/title streams, index components store
binary search data and pointers, and the type-`0x00` HONMON component often
stores bodies. Windows packages normally name this component `HONMON.DIC`;
observed Mac OS X SSED packages can name the same component `HONMON.DIN`;
observed Android SSED packages can name it `HONMON.DIW`.
`NKGORIN2` additionally stores the declared `HONMON.DIN` as a ZipCrypto member
inside an extensionless package file named `HONMON`; after archive unwrapping,
the member decrypts/expands as ordinary Mac LogoFontCipher `SSEDDATA`.

Some catalogs declare optional resources with start/end block `0`. Observed
Windows renderer packages can list `GA16FULL` / `GA16HALF` this way while
omitting the physical files and using renderer-side gaiji/display evidence
instead. A zero-block declaration is an absent optional resource, not a missing
positive-length component.

Catalog filenames and package-local resource references are resolved
case-insensitively by observed LogoVista-style packages. Portable readers
should prefer exact matches, then a single casefolded match, and should report
casefold collisions as ambiguous. The original on-disk casing remains useful in
diagnostics and reports.

## `SSEDDATA` `.DIC` / `.DIN`

Plain compressed components start with:

```text
offset  size  meaning
0x00    8     ASCII magic: SSEDDATA
0x0f    1     component kind/flags, not fully classified
0x16    2     chunk count, big endian
0x18    4     first logical block number, big endian
0x1c    4     last logical block number, big endian
0x40    ...   chunk offset table
```

The chunk offset table has `chunk_count` big-endian 32-bit offsets. Offsets
are from the beginning of the `.DIC` file.

Each compressed chunk starts with two unused/padding bytes, then:

```text
offset  size  meaning
0x02    2     command count, big endian
0x04    1     initial byte used to fill the sliding window
0x05    ...   command stream
```

Each command is three bytes:

```text
byte0, byte1, literal
```

The first two command bytes are split into:

```text
window_offset = (byte0 << 4) | (byte1 >> 4)
copy_length   = byte1 & 0x0f
```

Expansion uses:

```text
window size: 0xff0 bytes
chunk max:   0x8000 bytes
block size:  2048 bytes
```

For every command:

1. Copy `copy_length` bytes from the sliding window into the output.
2. Write `literal` into both the output and the window.
3. Stop a chunk at `0x8000` bytes, or at a 2048-byte boundary for the final
   command of a short final chunk.

This reproduces known expanded `HONMON.DIC` bytes for tested dictionaries.

Observed encrypted component payloads do not start with `SSEDDATA` on disk but
decrypt to the same `SSEDDATA` bytes before normal expansion. Two AES-CBC
variants are currently documented:

- the Windows LogoFontCipher variant uses the first 16 SHA-256 digest bytes of
  `LogoFontCipher` as the AES-128 key and the remaining 16 digest bytes as the
  IV;
- the observed Mac OS X SSED `HONMON.DIN` variant uses the first 16 ASCII bytes
  of the SHA-256 hex digest as the AES-128 key and a zero IV.
- the observed Android SSED `HONMON.DIW` variant uses the Android viewer's
  BouncyCastle `PBEWITHSHAAND256BITAES-CBC-BC` key derivation with password
  `resworbncidnatsivogol--emulator`, then AES/CBC/PKCS5Padding. The decrypted
  bytes are wrapped as `LV_` + `SSEDDATA` and include a two-byte alignment pad
  before the chunk-offset table.

Both variants use PKCS#7 padding when padding is present. Readers should
validate that decrypted bytes start with `SSEDDATA` before treating the payload
as a component.

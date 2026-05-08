# SSED Container

Container, compression, encryption, and component composition notes for `SSEDINFO` / `SSEDDATA`.

## `SSEDINFO` `.IDX`

The `.IDX` file is the catalog for the compressed components. It starts with:

```text
offset  size  meaning
0x00    8     ASCII magic: SSEDINFO
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

Component types observed so far:

```text
0x00  HONMON.DIC body/main text component
0x01  MENU.DIC
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
0xd2  COLSCR.DIC media/image resource stream
0xd8  PCMDATA.DIC audio/media resource stream
0xf1  GA16FULL resource
0xf2  GA16HALF resource
0xff  MULTI*.DIC selector descriptor
```

`RIGHT.DIC` is a text/copyright stream. It is observed both as declared
component type `0x02` and, in KCOMPEJ2, as a loose one-block `SSEDDATA`
sidecar that is not listed in the main `SSEDINFO` catalog.

The exact semantic names vary by dictionary, but the broad pattern is stable:
title components store readable headword/title streams, index components store
binary search data and pointers, and `HONMON.DIC` often stores bodies.

## `SSEDDATA` `.DIC`

Every compressed `.DIC` component starts with:

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

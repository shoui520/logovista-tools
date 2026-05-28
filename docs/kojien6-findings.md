# KOJIEN6 SSED Findings

KOJIEN6 is still a classic SSED dictionary, but it exercises late and advanced
LVEDBRSR-era features that are not present in simpler packages. The current
toolkit model treats it as SSED plus additional SSED-adjacent resource and
navigation stores, not as LVED_SQLITE3 or LVLMultiView.

## Core Package Shape

The main catalog is a normal `SSEDINFO` package with `HONMON.DIC`, forward and
backward title/index pairs, `KWTITLE.DIC` / `KWINDEX.DIC`,
`CRTITLE.DIC` / `CRINDEX.DIC`, five `MULTI*.DIC` selector descriptor
components, `COLSCR.DIC`, declared `PCMDATA.DIC`, and zero-block GA16
declarations.

The main differences from earlier classic SSED dictionaries are:

- no plain `MENU.DIC`;
- advanced `SCRMENU.DIC` component type `0x10`;
- `COLSMPL.DIC` component type `0xe0`;
- `MONOSCR.DIC` component type `0xd0`;
- embedded selector subcomponents inside physical `MULTI*.DIC` files;
- LVEDBRSR `encyclop.idx` navigation sidecar;
- loose `_PCM_U` encrypted MP3 resources backing normal PCMDATA address ranges;
- loose `_MOVIE` MPEG resources backing movie controls.

## Navigation

KOJIEN6 uses `SCRMENU.DIC` and `encyclop.idx` instead of ordinary
`MENU.DIC`. `SCRMENU.DIC` is a compressed SSED stream with screen-sized menu
backgrounds, hotspot rectangles, screen-to-screen jumps, and leaf targets that
resolve back to `HONMON.DIC`. The toolkit keeps it distinct from ordinary
`MENU.DIC` because the UI model is a screen map rather than a text/menu tree.

The recovered KOJIEN6 `SCRMENU.DIC` shape is:

- 43 screen groups;
- 43 `COLSCR.DIC` background-image pointers, all declaring 800x600 screens;
- 643 hotspot records;
- 498 direct `HONMON.DIC` leaf target records;
- 84 hotspot jumps to another screen group;
- 559 hotspot jumps to direct target records;
- no unresolved hotspot, media, or body pointers in the observed package.

The main controls are:

```text
1f4c .. 1f6c  screen group start/end
1f4d .. 1f6d  screen image/header record
1f4f .. 1f6f  hotspot record with x/y/width/height and SCRMENU destination
1f4b .. 1f6b  direct body-target record
```

The `1f4d` payload stores declared width/height and a BCD pointer to a
`COLSCR.DIC` background image. The `1f4f` payload stores four BCD words as
x/y/width/height, then a BCD pointer into `SCRMENU.DIC`. That destination may
point to another screen start or to a later `1f4b` direct target record. The
`1f4b` payload stores a BCD SSED address that resolves to the body component.

`encyclop.idx` is decoded today. It is a CP932 tab-indented LVEDBRSR navigation
sidecar with section headings and target rows. Rows carry hexadecimal
block/offset addresses that resolve back into the SSED address space. The
current parser records sections, row depth, labels, and target addresses.

## MULTI Components

Earlier observed `MULTI*.DIC` selector descriptors pointed at separate declared
`MUL*` components. KOJIEN6 embeds referenced selector subcomponents inside the
same physical `MULTI*.DIC` file. The descriptor references still use absolute
logical block addresses, and those ranges fall within the parent `MULTI*.DIC`
block range. The parser now distinguishes `embedded_components` from
unreferenced trailing bytes, so the nonzero payload after the descriptor is
accounted for instead of treated as residual junk.

All five KOJIEN6 `MULTI*.DIC` descriptors now account for their referenced
embedded selector slices with zero unreferenced nonzero payload bytes. Observed
descriptor labels include area/period/keyword selectors, country/genre
selectors, seasonal selectors, and kanji selectors such as notation,
radical, and residual stroke count.

## Index Components

KOJIEN6 uses the standard forward/backward title/index families plus large
keyword and cross-reference indexes. Full scans with the current parser
produce zero unknown leaf bytes:

```text
FKINDEX.DIC  leaf rows 478,844  search groups 175,331
FHINDEX.DIC  leaf rows 349,392
BKINDEX.DIC  leaf rows 478,844  search groups 175,331
BHINDEX.DIC  leaf rows 349,392
KWINDEX.DIC  leaf rows 4,501,825
CRINDEX.DIC  leaf rows 61,278
```

This confirms that the large KOJIEN6 `KWINDEX.DIC` is covered by the existing
type-`0x80` direct-row keyword model rather than requiring a separate page
grammar.

## Audio And Video

KOJIEN6 body text uses ordinary `1f4a` / `1f6a` PCMDATA audio controls, but the
declared `PCMDATA.DIC` file is not physically present. The sibling `_PCM_U`
directory supplies the payloads. `_PCM_U/WaveFile.map` maps numeric file stems
to decimal PCMDATA start blocks, and each numeric file decrypts with the
LogoFontCipher AES-CBC variant to an MP3 payload. The `pcmdata` command handles
this as `container_kind: "pcmu"` and can write MP3 files through the normal
`--write-audio` path.

Movie links use `1f39` / `1f59`. The start control has an 18-byte payload; the
visible label begins with an eight-digit movie ID that maps to a sibling
`_MOVIE/<id>` file. The HC renderer exposes this through an `lved.movi`-style
target. Toolkit HTML keeps a structured video target and does not treat the
movie ID as ordinary prose.

## Resource Streams

`COLSCR.DIC` remains the normal `data`-wrapped image store and decodes through
the existing media parser.

`COLSMPL.DIC` is a type-`0xe0` color-sample stream. Records are 2048-byte
blocks. The record prefix stores a compact Munsell color notation, and the
field at byte offset 20 stores a JIS-encoded Japanese color-name label.
HONMON `1f14` / `1f15` controls use `1eNN` sample keys, where `NN` is a
one-based BCD sample number for the first 99 records. HC012C contains a
`SDicGetColorMap` bridge and formats color samples as a `media` span with an
RGB background. The toolkit now parses the exact Munsell/label data and emits
structured color-sample renderer metadata. The RGB values in standalone HTML
are marked as estimates because the official Munsell-to-RGB bridge itself is
not yet recovered.

`MONOSCR.DIC` is a type-`0xd0` monochrome bitmap-cell stream. KOJIEN6 HONMON
uses `1f44` / `1f64` pairs for these cells: `1f44` carries the constant
display-cell payload `00010000006400000064`, and `1f64` carries a six-byte
packed-BCD pointer into `MONOSCR.DIC`. The pointer target decodes as a 64x64
1bpp bitmap. The toolkit materializes referenced cells as transparent PNGs for
HC012C output and keeps this support specific to `MONOSCR.DIC`; older
`FIGURE.DIC` streams with the same component type are not assumed to share the
same practical grammar.

## Current Support Status

Supported:

- package scan and SSED catalog parsing;
- KWINDEX/CRINDEX/title/index parsing under existing index models;
- embedded `MULTI*.DIC` component accounting;
- full HONMON control-byte accounting for the newly observed `1f14`/`1f15` and
  `1f39`/`1f59` pairs;
- `encyclop.idx` sidecar parsing;
- `SCRMENU.DIC` screen-menu parsing with background images, hotspots,
  screen jumps, and direct body targets;
- `SCRMENU.DIC` proof HTML generation with `COLSCR.DIC` backgrounds,
  clickable screen-to-screen hotspots, and `lvaddr://` body-target links;
- `_PCM_U` audio resolution and MP3 extraction;
- `_MOVIE` discovery and movie-link rendering metadata;
- `COLSMPL.DIC` Munsell/label parsing and `1f14` color-sample renderer
  metadata;
- `MONOSCR.DIC` 64x64 1bpp bitmap-cell decoding for `1f44` / `1f64` HC012C
  inline glyph output;
- `COLSCR.DIC` image extraction.

Named gaps:

- exact proprietary KOJIEN6 screen-menu chrome and transitions beyond the
  decoded clickable screen-map proof;
- exact `SDicGetColorMap` Munsell-to-RGB conversion;
- older `FIGURE.DIC` type-`0xd0` resources are separate from `MONOSCR.DIC`;
  the observed KQSYNONM/ROYALEGR form is decoded as variable-size row-padded
  1bpp figure bitmaps rather than KOJIEN6 64x64 cells;
- exact product-level KOJIEN6 HC012C visual parity beyond the shared movie,
  picture, audio, link, gaiji, and text-control semantics currently modeled.

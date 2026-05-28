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
`MENU.DIC`. `SCRMENU.DIC` is a compressed SSED stream whose complete generic
record grammar is not yet public-model complete. The toolkit therefore does
not coerce it into `MENU.DIC` output.

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

`COLSMPL.DIC` is a type-`0xe0` color-sample stream. Observed records are
2048-byte blocks with readable color labels near the header. The paired
`1f14` / `1f15` controls and HC color-map bridge strings show the intended
renderer relationship, but the full record grammar is still named as a gap.

`MONOSCR.DIC` is a type-`0xd0` monochrome figure/resource stream. It should be
kept distinct from `COLSCR.DIC`; the expanded bytes do not use the `data`
wrapper, and the current toolkit does not invent raster output for it.

## Current Support Status

Supported:

- package scan and SSED catalog parsing;
- KWINDEX/CRINDEX/title/index parsing under existing index models;
- embedded `MULTI*.DIC` component accounting;
- full HONMON control-byte accounting for the newly observed `1f14`/`1f15` and
  `1f39`/`1f59` pairs;
- `encyclop.idx` sidecar parsing;
- `_PCM_U` audio resolution and MP3 extraction;
- `_MOVIE` discovery and movie-link rendering metadata;
- `COLSCR.DIC` image extraction.

Named gaps:

- complete `SCRMENU.DIC` generic record grammar;
- complete `COLSMPL.DIC` color-value semantics;
- complete `MONOSCR.DIC` record/image grammar;
- exact product-level KOJIEN6 HC012C visual parity beyond the shared movie,
  picture, audio, link, gaiji, and text-control semantics currently modeled.

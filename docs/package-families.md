# Package Families and Lookup Rules

This page keeps package-family facts separate from reader-core status. A package
family can be classified and modeled by `logovista-tools` even when a particular
reader implementation does not support it yet.

## File Lookup

LogoVista package/resource lookup should be treated as case-insensitive across
platforms. Reports should preserve original on-disk casing, but discovery code
should resolve known dictionary resources by case-insensitive name and report a
collision if two distinct files normalize to the same lookup key.

This applies to core files and auxiliary resources, including:

- `SSEDINFO`, `SSEDDATA`, `HONMON.DIC` / `HONMON.DIN`, title/index/menu components, and
  `EXINFO.INI` / `DICPROF.INI`;
- `.idx`, `.IDX`, `.uni`, `.UNI`, `GA16*`, `GAI16*`, and eight-hex resource
  names;
- Panel assets, sidecars, media/resource files, templates, and package-layer
  wrapper files.

## SSED Core

Classic SSED packages are block-addressed packages built around `SSEDINFO` /
`SSEDDATA` and component records such as:

```text
DICT.IDX
HONMON.DIC
HONMON.DIN
MENU.DIC
KWTITLE.DIC / KWINDEX.DIC
FKTITLE.DIC / FKINDEX.DIC
FHTITLE.DIC / FHINDEX.DIC
BKTITLE.DIC / BKINDEX.DIC
TOC.DIC / RIGHT.DIC / IDXJUMP.DIC
MULTI*.DIC / MUL*.DIC
COLSCR.DIC / PCMDATA.DIC
GA16HALF / GA16FULL
DICT.uni / DICT.UNI
```

Dense-HONMON and sidecar-backed SSED packages remain SSED packages. Their raw
type-`0x00` HONMON component may be named `HONMON.DIC` or, in observed Mac OS X
packages, `HONMON.DIN`. It may be an anchor, marker, or dereference layer rather
than a self-contained body stream, but that does not make the package LVED or
LVLMultiView.

## SSED Package Layers

Observed SSED packages may include application, platform, or product-bundle
material around the raw core:

```text
NoPlatform raw SSED core plus portable resource dirs such as res/, resources/,
          templates/, img/; no reader-specific EXINFO/HC/vlpljbl/plist sidecars
iOS       DictList.plist, Gaiji.plist, GaijiS.plist, img/, html/, *.sql
Android   *.db, resource/conf.ini, resource/kmkimges/, manual/, innerdata/
Windows   EXINFO.INI, DICPROF.INI, HC*.dll, Templates/, HANREI/, vlpljbl*,
          Panel/, Panels.xml, *.chm
Mac OS X  EXINFO.INI, help `.localized` bundles, AppleDouble `._*` metadata,
          encrypted `HONMON.DIN`, and observed HANREI/SPINDEX-style auxiliary
          resources in otherwise normal SSED catalogs
SIZK      SSED read-aloud set bundle with EXINFO.INI, HC0190.dll,
          HTMLs/b12*.html, Templates/honbun.html,
          shizuku.mp3, shizuku_honbun.txt, shizuku_time.txt, shizuku.uni
Panel     Panels.dtd, Panels.xml, Panel.html, Cell.html, and fixed-record
          external .bin label-to-address tables
Numeric   00000xxx.idx / 00000xxx_n.idx CP932 auxiliary text trees; observed
          in Windows and iOS packages and not sufficient evidence for platform
          classification
```

SIZK is listed here because it is an SSED product bundle. It is not a platform
wrapper and not a separate core format.

Separate non-SSED package families include:

```text
LVED      main.data / *.dbc SQLCipher payloads, WebView2 viewer files,
          SQLCipher runtime and plugin/assembly resources
MultiView SSEDINFO-like *.IDX facade, menuData.xml, LOGOVISTAMULTIVIEW,
          *lvbat/*lvdat LogoFontCipher SQLite payloads, Templates/, Resources/
```

## LVED / WebView2

Observed LVED/WebView2 products are a separate package family. Their primary
payloads are SQLCipher databases such as `main.data` or `.dbc`, not SSED
`HONMON.DIC` streams. `logovista-tools` classifies and inspects those packages
separately instead of forcing them into the SSED model. The public format notes
for this family live in [LVED SQLCipher Packages](../spec/lved-main-data.md).

## LVLMultiView

Observed LVLMultiView products are also separate from classic SSED body
streams. They may include a small SSEDINFO-like `.IDX` facade that names
familiar components such as `HONMON.DIC` and `FKINDEX.DIC`, while readable
payloads live in LogoFontCipher-encrypted SQLite files. The observed law
subfamily uses `blvbat`, `hlvbat`, `ilvbat`/`ilvdat`, `jlvbat`, and
`nlvbat`/`nlvdat`; ESPRANT2 uses `blvdat` with a content/search schema and
numeric `menuData.xml` targets. See [LVLMultiView Packages](../spec/multiview.md).

## SIZK Read-Aloud Packages

The SIZK / NHK read-aloud set is SSED-backed, but the raw core is a tiny
selector stream. The substantial read-aloud content lives in loose sidecars:
HTML body files, a template, an MP3, synchronized text/time sidecars, and a
dictionary-local `.uni` mapping. `logovista-tools sizk` resolves those pieces
into structured reports.

## Metadata and Auxiliary Resources

`EXINFO.INI` and `DICPROF.INI` are package metadata, not body streams.
`EXINFO.INI` is not Windows-only in the observed corpus; Windows, Mac OS X, and
some mobile package copies reuse the same reader metadata style. It declares
reader features such as renderer DLLs, auxiliary text indexes, gaiji mapping
filenames, and read-aloud sidecar names. `DICPROF.INI` is an install/profile
manifest with required file lists and dictionary identity fields. A
`DICPROF.INI`-declared catalog or directory name should be treated as metadata
evidence even when it differs from the local folder name.

Eight-hex auxiliary `.idx` files are CP932 tab-tree resources unless they start
with the `SSEDINFO` magic. Large observed auxiliary trees may be sharded with
suffixes such as `_0`, `_1`, `_2`, and `_3`. These are still auxiliary index
trees; they are not additional SSED catalogs.

`SPINDEX.DIC` and `HANREI/` are auxiliary resource families rather than
Windows-only markers. They can appear in Mac OS X package layouts too.

`FIGURE.DIC` is observed as a compressed type-`0xd0` SSED resource component.
It is not the same media store as `COLSCR.DIC`, and current public evidence does
not support treating the expanded bytes as a coherent raster image format.
Until its record grammar is decoded, it should be reported as an unresolved
figure/resource stream rather than rendered as fake image output.

## Writer Target Boundary

The current writer proof of concept targets clean, core SSED packages. LVED and
LVLMultiView are not planned writer targets. That writer boundary is separate
from toolkit package classification and separate from lvcore reader status.

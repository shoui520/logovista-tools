# GENDAIKNOWLEDGE SSED Findings

This page covers the `GEN*` / `GENDAI*` 現代用語の基礎知識 packages. It does
not cover `GENIUS*` dictionary products.

## Package Shape

The audited packages are classic SSED packages. They use ordinary `SSEDINFO`
catalogs, `SSEDDATA` components, `HONMON.DIC`, native title/index components,
`MENU.DIC`, `EXINFO.INI`, `HANREI`, `GA16HALF` / `GA16FULL`, and product
renderer DLL declarations where present.

Renderer families observed in this product line include:

```text
HC00BB  GEN1999 / GEN2000 style vertical renderer
HC009B  GEN2001 through GEN2010 and GENDAI10 style vertical renderer
HC00A9  GEN2011 style vertical renderer
HC00B3  GEN2012 style vertical renderer
HC0136  GEN2013 style vertical renderer
HC013C  GEN2014 style vertical renderer with HTMLs resources
HC02C0  GEN2015 style encrypted-HONMON renderer
HC02C4  GEN2016 style renderer
HC02C7  GEN2017 style renderer
HC02C9  GEN2018 style renderer
HC02CA  GEN2019 style renderer
```

The current HC implementation has branch-subset support for these renderer
families, but exact visual parity for the GEN product line is not claimed.
Known remaining renderer gaps are the same as the HC matrix names for these
families: exact previous/next footer behavior, modifyHeadword behavior where
present, fixed-HTML fallback selection, custom-DIB behavior where a reached
branch proves it, and product visual review.

## GENDAI10 Bundle

`GENDAI10` is a set/bundle package for the 1991-2000 editions plus an
appendix. It is still one SSED package, but `EXINFO.INI` declares 11 auxiliary
CP932 text index trees:

```text
1991.idx
1992.idx
1993.idx
1994.idx
1995.idx
1996.idx
1997.idx
1998.idx
1999.idx
2000.idx
20th.idx
```

These are not eight-hex numeric auxiliary files. They use the same tab-tree
grammar as numeric auxiliary indexes: two eight-hex block/offset columns,
tab-indented labels, and body targets resolved against the SSED catalog.

Current toolkit behavior:

- `extras` decodes all 11 year/appendix trees and resolves body targets.
- `reader-demo` embeds EXINFO-declared and numeric auxiliary text indexes as
  clickable navigation samples.
- `menus` fully decodes the large `MENU.DIC` stream.
- `indexes` fully decodes the native SSED index components when run without a
  row limit.

Full unbounded output is large. Bounded commands should be used for routine
inspection unless the goal is corpus accounting.

## MULTI Selectors

The mid-generation packages can carry `MULTI*.DIC` descriptor components and
paired `MUL*` title/index/menu components. The observed descriptors use the
older external-reference layout: descriptor records point to separately
declared `MUL*` components by component type, logical start block, and block
count. They do not use the KOJIEN6-style embedded selector layout.

Current component forensics accounts for these selector components without
nonzero residual bytes. The associated type `0x0d` title streams, type `0xa1`
selector indexes, and menu-like `MUL*` streams parse through the shared
title/index/menu parsers.

## Media And HANREI

Most annual packages carry `COLSCR.DIC`; sampled media references resolve to
valid image records. The older 1999/2000 packages and the GENDAI10 bundle do
not carry `COLSCR.DIC` in the audited set.

Older annual packages generally ship `HANREI.chm`. Later packages can ship a
loose `HANREI/` HTML directory. Both are handled by the reader-demo HANREI
copy/extraction path where the local environment has the required CHM tools.

## Known Outliers

Some package copies declare `RIGHT.DIC` in `SSEDINFO` but do not ship the file.
This is modeled as a missing optional text component, not as an SSED parse
failure.

One audited package directory contains an unrelated `SESGRASS.IDX` catalog.
`scan` correctly reports it as a separate SSED target because it is a valid
catalog. Treat that as package-copy contamination rather than a
GENDAIKNOWLEDGE format feature.

## Reader-Core Implications

GENDAIKNOWLEDGE reinforces several reader-core requirements:

- EXINFO-declared auxiliary text trees must not be limited to eight-hex
  filenames.
- Bundle/set packages can expose major navigation surfaces through auxiliary
  text trees, not only through `MENU.DIC`.
- Native title/index search, menu browsing, auxiliary tree browsing, and body
  rendering must all resolve to the same typed body targets.
- Missing optional declared text components should produce diagnostics, not
  fatal package rejection.

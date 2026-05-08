# Package Layers

High-level package model and platform wrapper differences.

## Big Picture

A typical raw dictionary core looks like:

```text
DICT.IDX
HONMON.DIC
KWTITLE.DIC
KWINDEX.DIC
FKTITLE.DIC
FKINDEX.DIC
FHTITLE.DIC
FHINDEX.DIC
BKTITLE.DIC
BKINDEX.DIC
GA16HALF
GA16FULL
DICT.uni
```

That core is the stable part. Platform packages wrap it differently:

```text
iOS       DictList.plist, Gaiji.plist, GaijiS.plist, resourcesCopy.plist,
          gaijiicon.plist, img/, html/, OTHER/, *.sql
Android   *.db, resource/conf.ini, resource/kmkimges/, manual/, innerdata/
Windows   EXINFO.INI, HC*.dll, Templates/, HANREI/, *.chm, vlpljbl*,
          eight-hex-digit 00000xxx.idx sidecar trees, sometimes standalone
          auxiliary SPINDEX.DIC and sibling *_Sound_Files/ ziptomedia audio
LVED      main.data or *.dbc, WebView2 viewer files, sqlcipher.dll,
          plugin DLL/assembly resources
```

Observed Windows `vlpljbl*` names are not one format. Content classification
is required before interpreting them:

```text
vlpljbl.bin / vlpljbl.exe   PE Crypto++ LogoFontCipher decryptor binaries
vlpljblB                    usually plain Noto Sans JP OpenType/CFF
vlpljblN                    usually plain Noto Serif JP OpenType/CFF
vlpljblM                    observed plain SQLite media stores
vlpljblF                    observed LogoFontCipher SQLite; role varies
vlpljblb / vlpljblh         observed LogoFontCipher renderer body/media SQLite
vlpljblS                    overloaded: font or search/index SQLite
vlpljbl                     overloaded: font or block/offset body SQLite
```

The stable rule is magic/schema first, suffix second.

`Gaiji.plist` and `GaijiS.plist` are therefore not generic LogoVista files.
They are iOS packaging fallbacks observed in some products. Cross-platform
gaiji handling should start from the dictionary-local `.uni`/`.UNI` file and
then use platform-specific image/plist/font assets where present.

The core raw format has two layers:

1. A container/compression layer: `SSEDINFO` + `SSEDDATA`.
2. An expanded dictionary stream layer: EPWING/JIS-like bytes with text,
   controls, gaiji, links, and index records.

The SQLite database, when present, is best understood as an application cache
or search database. It may contain useful full text, but it is not the only
raw dictionary source, and using it alone loses format information.

There is now a separately observed LVED/WebView2 package family. These products
do not expose a normal SSED/HONMON body core. Their `main.data` Windows payload
or `.dbc` mobile payload is a SQLCipher page database and must be classified as
that package family, not as a broken SSED dictionary. See
[LVED SQLCipher Packages](lved-main-data.md).

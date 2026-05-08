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
SIZK      classic SSED catalog plus HC0190.dll, HTMLs/b121-b124 templates,
          Templates/honbun.html, shizuku.mp3, shizuku_honbun/time sidecars
LVED      main.data or *.dbc, WebView2 viewer files, sqlcipher.dll,
          plugin DLL/assembly resources
MultiView SSEDINFO-like *.IDX facade, menuData.xml, LOGOVISTAMULTIVIEW,
          *lvbat/*lvdat LogoFontCipher SQLite payloads, Templates/, Resources/
```

## Windows HC Renderer Plugins

Windows SSED packages usually declare an HTML renderer in `EXINFO.INI`:

```ini
[GENERAL]
HTML=1
HTMLDLL=HC014F.dll
```

`HC????.dll` files are PE32/i386 product renderer plugins, not dictionary
containers. The official Windows browser loads them through the generic
renderer/bridge layer: `Dic.dll` and `HtmlConvert.dll` import
`LoadLibraryA`/`GetProcAddress`, while `SSDicLib.dll` exports bridge functions
such as `SDicPluginFunction`, `SDicPluginFunction2nd`, and
`SDicPluginFunction3rd`.

The stable entrypoint is `epwing2HtmlBodydata`, exported by every HC plugin
observed in the Windows SSED corpus. Other exports are optional feature hooks:

```text
epwing2HtmlBodydataVertical   vertical HTML rendering
getCustomCharacterDIB         renderer-side gaiji bitmap generation
modifyHeadword*               headword/display-key rewrite hooks
initializeSQL/finalizeSQL     renderer/app SQLite setup
execDicOrgSearch*             dictionary-specific search UI
execDicZenbunSearch           full-text search UI
initializePanel/finalizePanel panel UI
pluginFunction*               product-specific bridge hooks
openUserData/closeUserData    sidecar/user-data lifecycle
createMediaFileFromZip        ziptomedia extraction, observed in PROYAL53
```

HC plugins import raw services from `SSDicLib.dll`, commonly
`SDicGetBodyData`, `SDicGetPictureData`, `SDicGetCustomCharacterBitmap`,
`SDicGetCustomCharacterUincode`, and `SDicGetDictPath`. This makes them useful
renderer-behavior evidence: embedded strings reveal the HTML/CSS/image templates
the official browser used, and imports show which raw APIs a product needed.
They are not, however, a substitute for parsing HONMON/INDEX/TITLE resources.

Numeric sidecar names often share the HC product code
(`HC013A.dll` -> `0000013A.idx`), but this is a convention. `EXINFO.INI`
`HTMLDLL` is the authoritative renderer link; some HC-bearing packages have no
numeric index and a few have a numeric index whose code differs from the HC DLL.

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

There is also an observed LVLMultiView package family. These packages expose an
SSEDINFO-magic `.IDX` facade that declares normal-looking components, but the
declared files are absent and the readable data decrypts from LogoFontCipher
SQLite payloads. The observed law subfamily uses payloads such as `blvbat`,
`hlvbat`, `ilvbat`/`ilvdat`, `jlvbat`, and `nlvbat`/`nlvdat`; ESPRANT2 uses a
single `blvdat` content/search payload. See
[LVLMultiView Packages](multiview.md).

The SIZK / NHK 文学のしずく packages are a small SSED-backed read-aloud
subfamily. Their `.IDX` catalog declares `HONMON.DIC`, `GA16FULL`, and
`GA16HALF`, but the package is driven by renderer templates rather than by
normal search indexes. `HONMON.DIC` is a tiny body stream with four entries.
Each entry begins with a full-width gaiji selector (`b121` through `b124`) that
chooses a sibling HTML template in `HTMLs/`:

```text
b121  overview
b122  author introduction
b123  narrator introduction
b124  read-aloud playback
```

`EXINFO.INI` declares `HTMLDLL=HC0190.dll`, `MP3NAME=shizuku.mp3`, and
`GAIJI=shizuku.uni`. The actual playback transcript is stored outside SSED as
UTF-16 text/time files plus `Templates/honbun.html`; all 30 observed packages
have synchronized time/text/template rows.

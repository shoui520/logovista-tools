# Package Layers

High-level package model, SSED package layers, and separate non-SSED families.

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

That core is the stable part. Retail packages add application, platform, or
product-bundle layers around it:

```text
NoPlatform raw SSED core plus portable resource dirs such as res/, resources/,
          templates/, img/; no EXINFO/HC/vlpljbl/plist/application sidecars
iOS       DictList.plist, Gaiji.plist, GaijiS.plist, resourcesCopy.plist,
          gaijiicon.plist, img/, html/, OTHER/, *.sql; can also carry
          eight-hex auxiliary .idx trees
Android   *.db, resource/conf.ini, resource/kmkimges/, manual/, innerdata/
Windows   EXINFO.INI, HC*.dll, Templates/, HANREI/, SPINDEX.DIC,
          sibling *_GAIJI/, *.chm, vlpljbl*; some packages also carry
          DICPROF.INI
Mac OS X  EXINFO.INI, help `.localized` bundles, AppleDouble `._*` metadata,
          encrypted `HONMON.DIN`, and observed HANREI/SPINDEX-style auxiliary
          resources inside an otherwise normal SSED catalog
```

`NoPlatform` is not an observed retail LogoVista wrapper. It is the model value
for stripped core-SSED packages and for future SSED writer output that should
not depend on a specific LogoVista reader implementation.

SQLite `.db` sidecars are not a package-family marker by themselves. They occur
across Android, Windows, iOS/no-platform, and app-bundle layouts. The observed
roles include body/render caches, media BLOB stores, examples/idioms,
link-reference rows, kanji support tables, template navigation/filter tables,
and ancillary metadata. Treat them by schema and declaration context, not by
extension or platform.

Separate non-SSED package families include:

```text
LVED      main.data or *.dbc SQLCipher payloads, WebView2 viewer files,
          sqlcipher.dll and plugin DLL/assembly resources
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
containers. The Windows browser loads them through a generic renderer/bridge
layer using dynamic library calls and product-specific bridge hooks.

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
createMediaFileFromZip        PROYAL53 ziptomedia extraction hook
```

HC plugins import raw dictionary services for body, picture, gaiji, menu,
search, SQL, package-path, and plugin lifecycle access. Code-level analysis of
the renderer loops shows the same basic model across products:

- `epwing2HtmlBodydata` obtains body bytes, decodes JIS pairs and `0x1f`
  controls, and writes HTML directly.
- `1f42/1f62` and `1f43/1f63` become `lved.addr` links.
- `1f3c`, `1f4d`, and in picture-capable products `1f44` call the picture
  extraction path and produce generated image links/placeholders.
- `1f4a/1f6a` becomes an `lved.sond` sound link when the sound range is active.
- Gaiji rendering is Unicode-first where the Unicode bridge is imported, with
  bitmap fallback through the custom-character bitmap bridge.
- Private renderer controls such as `1fe0`..`1fe6` mutate layout/style state
  and should not appear literally in friendly output.

Embedded strings still provide useful HTML/CSS/image template evidence, and
imports show which raw service families a product needed. The HC DLLs are not a
substitute for parsing HONMON/INDEX/TITLE resources; they are renderer semantic
evidence for controls and bridge behavior.

Loose media/resource side files are not always declared as SSED components.
Observed examples include Britannica `whatday/*.body` / `*.top` HTML fragments,
Britannica `top/top_*.dat` CP932 address/image lists, PROYAL53 `dat/*`
LogoFontCipher-wrapped WAVE files, and LVLMultiView law `Resources/*`
LogoFontCipher-wrapped PDFs. Treat these as package resources addressed by
their surrounding renderer/UI metadata, not as `HONMON.DIC` body streams.

`logovista-tools hc-render` implements the common renderer semantics as a
toolkit HTML renderer: text/style controls, internal links, `COLSCR.DIC`
picture placeholders, `PCMDATA.DIC` sound ranges, Unicode/image gaiji fallback,
private directive suppression, and vertical-rendering metadata. It also invokes
the clear schema-backed renderer sidecar paths (`t_contents`, `HONBUN`,
HC0155-style `main(ID, Class, C_text, J_text, Pinyin)`, Android body DBs,
media tables, and ziptomedia references) automatically when those sidecars are
present. The command distinguishes **exact entry-body HTML** from **exact HC
DLL parity**. For dense renderer DB products such as the modern `t_contents`
family, and for HC0155's ID-keyed `main` table, the entry body can be taken
from the same renderer sidecar data that the HC plugin queries; product hooks
can still remain open.

Current code-level renderer families are:

```text
modern_dense_t_contents_renderer  dense HONMON ID anchors -> t_contents f_Html
hc0155_main_id_text_renderer      dense HONMON ID anchors -> main C_text/J_text
ejje_search_sidecar_renderer      t_Search_* category SQL helper tables
britannica_panel_media_renderer   Panel lifecycle + Media/HTMLs resources
britannica_yearbook_array_renderer array_no-based t_contents indirection
panel_enabled_renderer            Panel lifecycle hooks around normal rendering
simple_htmls_vertical_renderer    HTMLs/block-offset template fallback
sizk_readaloud_renderer           read-aloud HTML/audio template fallback
shared_vertical_renderer          common body loop with vertical entrypoint
shared_body_renderer              common body loop only
```

Panel hooks, SQL search UI hooks, plugin callbacks, user-data callbacks,
custom gaiji DIB generation, and product-specific `modifyHeadword*` behavior
remain named behavior gaps unless their exact data path is separately decoded.

Numeric sidecar names often share the HC product code
(`HC013A.dll` -> `0000013A.idx`), but this is a convention. `EXINFO.INI`
`HTMLDLL` is the authoritative renderer link; some HC-bearing packages have no
numeric index and a few have a numeric index whose code differs from the HC DLL.

## Metadata and Auxiliary Indexes

Packages commonly include `EXINFO.INI`, and some also include `DICPROF.INI`.
Both are CP932 INI-style metadata files, not SSED components. `EXINFO.INI` is
not Windows-only in the observed corpus; Mac OS X packages and some mobile
package copies reuse much of the same metadata behavior.

`EXINFO.INI` is the reader-side feature declaration. Observed keys include:

```text
HTMLDLL     renderer plugin filename, usually HC????.dll
IDXCOUNT    number of auxiliary text indexes
IDXINFO     single auxiliary index filename
IDXINFO0..  numbered auxiliary index filenames
IDXTITLE    display title for the first auxiliary index
IDXNAME0..  display names for numbered auxiliary indexes
GAIJI       dictionary-local .uni filename when it does not match the .IDX stem
MP3NAME     loose read-aloud audio filename in SIZK packages
```

`DICPROF.INI` is closer to an install/profile manifest. Observed keys include:

```text
[GENERAL]
DicName
DicId
DicDir
RequiredFiles

[REQUIRED]
FILE1..FILEn
```

`DicDir` and `FILEn` are package metadata. They can name a canonical
dictionary/catalog basename that differs from the local folder name, so tools
should not assume a folder-name mismatch is a typo when `DICPROF.INI` declares
the name explicitly.

Auxiliary `.idx` trees referenced by `EXINFO.INI` are not SSEDINFO catalogs
when they do not start with `SSEDINFO`. They are CP932 tab-separated text
trees. Each non-empty row starts with two eight-hex-digit fields:

```text
block_hex<TAB>offset_hex<TAB>label
block_hex<TAB>offset_hex<TAB><TAB>child label
```

The number of leading empty tab fields after the pointer gives the tree depth.
The pointer can resolve to a normal SSED component address, or to a virtual
selector when the high nibble of the block is nonzero and the offset is
`ffff`.

The common auxiliary index filename is eight hexadecimal digits, such as
`0000013A.idx`. Some large auxiliary sets are sharded with a decimal suffix,
such as `00000151_0.idx` through `00000151_3.idx`; these use the same CP932
tab-tree row grammar. These files are not Windows-only; original iOS package
copies can carry them too. A same-code `.uni` file can be declared by
`EXINFO.INI` for the auxiliary/renderer layer, so the gaiji basename is not
guaranteed to match the main dictionary `.IDX` stem.

## CCALTSTR Alternate-String Tables

Some Windows SSED packages carry `CCALTSTR.HA` and, less commonly,
`CCALTSTR.FU`. These are fixed-record custom-character alternate-string tables,
not body streams, Panel files, media stores, compressed containers, or SQLite
sidecars.

The decoded table layout is:

```text
offset  size  meaning
0x00    8     ASCII magic: SDICALTH for .HA, SDICALTF for .FU
0x08    2     uint16le version, observed as 1
0x0a    2     uint16be start custom-character code
0x0c    2     uint16be record count
0x0e    2     reserved/flags
0x10    ...   record_count fixed records

record:
0x00    2     uint16be custom-character code
0x02    60    NUL-terminated alternate string, ASCII with CP932 fallback
```

The exact file size is `16 + record_count * 62`. Record codes advance in JIS
row/cell order, so the code after `A17E` is `A221`, not `A17F`.

`CCALTSTR` keys overlap the package-local custom-character codes from `.uni`
resources, but the value field is a separate short alternate string rather than
the display glyph mapping. Reader impact is search/headword normalization:
these tables provide roman/ASCII fallback strings for custom characters. They
should not be merged into entry body rendering.

## Panel Subsystem

Some SSED packages include a Panel UI/navigation subsystem alongside the raw
dictionary core. The decoded Panel samples currently come from Windows package
copies, but Panel is not a Windows platform marker. A complete observed Panel
set uses:

```text
Panels.dtd
Panels.xml
Panel.html
Cell.html
Panel/*.bin or sibling/mobile bin tables
```

Other observed layouts store `.bin` payloads in a sibling `_Panel` directory,
a package-level `bin/` directory, or the package root. Panel XML paths may use
Windows backslashes, while mobile plist paths normally use slash-separated names
without the `.bin` suffix. Portable tooling must normalize path separators,
append `.bin` for plist path references when needed, and perform
case-insensitive package-local lookup.

`Panels.dtd` is a small XML schema:

```text
panels      -> information, panel+
information -> dictionaryName, creationDate
panel       -> title, data+
data        -> cell*
cell        -> #PCDATA
```

Important attributes:

```text
panels: version, fontsize, color/bkcolor/linecolor, focused_*,
        noref_*, cell_w, cell_h
panel:  index, paneltype=(menu|contents|mixed),
        datatype=(internal|external|mixed), flow, direction,
        count_all/count_x/count_y, style attributes, font-stretch,
        multi-line, cell_w, cell_h
data:   type=(inline|bin|html), filename
cell:   ref, direction, color/bkcolor, fontsize
```

Menu panels normally use inline `cell` elements whose `ref` values point to
other Panel `index` values. Content panels normally use external
`data type="bin"` rows, or less commonly `data type="html"`.

Mac and mobile packages can represent the same model as plist:

```text
Panels.plist:
  dictionaryName, creationDate, panel dictionary
  panel id -> paneltype, datatype, title, layout attributes, data array
  data item -> type=(bin|html), filename

mobile menu plist:
  nested arrays/dicts
  item/title, optional child array, optional path naming a bin table
```

The common decoded Panel `.bin` grammar is fixed-width and little-endian:

```text
uint32 record_count
uint32 text_width
repeat record_count:
    uint32 target_block
    uint32 target_offset
    byte[text_width] label_text_stream
```

The exact file size is normally `8 + record_count * (8 + text_width)`. Decoded
records are label-to-address rows for optional Panel navigation surfaces. The
target is an SSED logical block/offset pair; in the decoded Windows corpus
these rows target `HONMON.DIC`, with a small observed set targeting `MENU.DIC`.

Observed compatible variants are:

```text
id-prefixed row:
  uint32le record_id
  uint32le target_block
  uint32le target_offset
  byte[text_width] label_text_stream

declared-count mismatch:
  same little-endian row grammar, but physical rows are fewer than the
  declared header count

empty/zero-width:
  count=0,text_width=0 placeholder, or zero-width address rows

headerless UTF-8:
  repeated uint32be target_block, uint32be target_offset,
  fixed-width NUL-padded UTF-8 label bytes
```

`label_text_stream` is not an arbitrary byte label. It is a NUL-padded
LogoVista text stream using JIS pairs, gaiji pairs, and known `0x1f` display
controls such as halfwidth and superscript spans. Readers should decode it with
the same conservative text-stream logic used for body/title text.

`Panel.html` and `Cell.html` are viewer templates for presenting the Panel
tree/cells. They do not define the binary record grammar. The Windows reader
also advertises Panel availability through `EXINFO.INI` `GENERAL/PANELXML` and
renderer panel lifecycle hooks. A reader that does not implement the original
UI can still expose the decoded Panel rows as an optional navigation API
without merging them into ordinary entry body rendering.

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
then use platform-specific image/plist/font assets where present. Windows
packages can also place image-backed gaiji in a sibling companion directory,
as observed with `_DCT_KANJIGN5_GAIJI`.

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

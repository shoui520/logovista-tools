# LogoVista Exporter

The exporter is a new pipeline for converting supported LogoVista packages to
other dictionary formats. It is separate from the experimental SSED writer:

```text
LogoVista source package -> Export IR -> target backend
```

The first backend is `epwing-v6`. The initial source adapters are SSED and
LVED_SQLITE3.

## Current Command

```bash
logovista-tools export epwing-v6 SOURCE OUTDIR
```

Useful options:

```bash
--source-family auto|ssed|lved-sqlite3
--gaiji-policy unicode-map,ga16
--include-media / --no-include-media
--drop-style-markers / --no-drop-style-markers
--diagnostics-json PATH
--limit N
--progress-every N
--quiet
--json
```

`SOURCE` may be a direct SSED `.IDX`, a package directory, an LVED_SQLITE3
package directory containing `main.data` / `main.dbc`, or a direct LVED_SQLITE3
payload. Encrypted LVED_SQLITE3 payloads require a matching local `.key` file.

The command prints progress to stderr by default. JSON output remains on stdout,
so `--json` can still be redirected or piped safely. Use `--quiet` to suppress
progress output, and `--progress-every` to control long entry/index row phase
updates.

When writing EPWING output, the backend replaces the generated subbook
directory, `CATALOGS`, stale `LANGUAGE`, and the generated `.map` file in the
output directory. This prevents old broken export artifacts from surviving a
rerun. Other unrelated files in the output directory are left alone.

## Export IR

The target-neutral IR is intentionally conservative:

- `ExportBook`: title, source family, source identity, entries, indexes,
  navigation, resources, gaiji, copyright, diagnostics.
- `ExportEntry`: source target, headword, readings, search keys, body events.
- `ExportBody`: structural events such as text, line break, emphasis, ruby,
  gaiji ref, link, media ref, section boundary, and unsupported marker.
- `ExportNavigation`: typed navigation surfaces such as menu and copyright.
- `ExportGaiji`: source code, Unicode mapping, Template fallback, GA16 fallback,
  and nonliteral-marker status.

The IR is not a visual HC renderer. HC/product layout behavior that does not
map cleanly to the target format is reported as diagnostics.

## EPWING v6 Backend

The backend currently emits an EPWING v6-style subbook layout:

```text
CATALOGS
<SUBBOOK>.map
<SUBBOOK>/
  DATA/
    HONMON2
    HONMONG
    HONMONS
  GAIJI/
    GA16HALF
    GA16FULL
```

It deliberately targets `HONMON2`, not plain `HONMON`.

No `LANGUAGE` file is emitted. EB Library treats this file as optional language
/ character-code metadata, and reference EPWING books commonly work without it.
The exporter relies on the `CATALOGS` and `HONMON2` metadata instead.

Current lowering behavior:

- SSED plain `HONMON` text/control slices are lowered to conservative EPWING
  body text and directly mappable style controls.
- SSED dense-HONMON packages with renderer SQLite sidecars are exported from
  the renderer body rows, not from the raw numeric HONMON anchors.
- LVED_SQLITE3 stored HTML is flattened into conservative body events.
- Search keys are emitted into text, word, and endword index records.
- Menu surfaces are emitted as EPWING candidate streams, so menu rows become
  clickable when their LogoVista target resolves to an exported entry.
- SSED copyright/about surfaces are extracted from `HANREI` HTML directories
  and from `HANREI.chm` when `7z`/`7zz`/`7za` is available. LVED_SQLITE3
  copyright/about surfaces come from understood `info` rows.
- Copyright surfaces are indexed where present and written as EPWING copyright
  text, not normal dictionary entries.
- Unicode-first gaiji behavior is represented through an EBWin-compatible
  `.map`, with generated GA16 bitmap fallback files.

Deferred behavior:

- Native `HONMONG` / `HONMONS` media control authoring is scaffolded but not
  implemented.
- HC visual parity is intentionally not ported to EPWING.
- LVLMultiView, Hourei, MDict/MDX, and Yomitan backends are planned through the
  same IR but are not implemented in this initial backend.

## Validation

The exporter tests cover:

- SSED source IR construction from a generated plain-HONMON fixture.
- LVED_SQLITE3 source IR construction from a SQLite fixture.
- EPWING v6 output structure, `CATALOGS`, `HONMON2`, `HONMONG`, `HONMONS`,
  GA16 resources, and `.map`.
- No stale `LANGUAGE` file is retained across reruns.
- Text, menu, and copyright index record emission.
- EPWING menu candidate control emission.
- SSED HANREI copyright HTML extraction.
- Dense HONMON sidecar-body export for `t_contents` fixtures.
- CLI smoke export.
- Default CLI progress output and `--quiet` suppression.

Real corpus behavior remains authoritative. Synthetic fixtures only prove that
the code path and baseline structures are internally coherent.

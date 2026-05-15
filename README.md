# logovista-tools

`logovista-tools` is a raw-first reverse-engineering toolkit for
LogoVista/SystemSoft dictionary packages. It classifies package families,
preserves raw addresses and bytes in reports, builds decoded package models,
and provides extractors, validators, and an experimental plain-SSED writer
proof of concept.

The repository also contains `src/lvcore-experimental`, a separate clean
reader-core prototype. Do not read toolkit status and lvcore status as the same
thing: they have different scopes and different readiness bars.

No dictionary data is included in this repository.

## Project Tracks

| Track | Scope | Status |
|---|---|---|
| `logovista-tools` | Research toolkit, package classifiers, decoded model reports, extractors, verification, and experimental plain-SSED writer primitives. | See [Project Status and Roadmap](docs/status.md). |
| `src/lvcore-experimental` | Independent reader-core prototype. It is reader-only and currently targets SSED reader behavior. | See [lvcore Status](docs/lvcore-status.md). |
| Format notes | Public, behavior-level format documentation derived from observed structures. | See [Format Notes](spec/README.md). |

## Toolkit Package-Family Status

This table is about `logovista-tools`, not lvcore.

| Family / Layer | Toolkit status |
|---|---|
| SSED `SSEDINFO` / `SSEDDATA` / component block mapping | High confidence for observed package families. |
| Body-stream and dense/sidecar `HONMON.DIC` / `HONMON.DIN` variants | Classified and modeled; supported extractors vary by body-source shape. |
| `*INDEX.DIC` / `*TITLE.DIC` / `MENU.DIC` | High structural coverage for the current SSED corpus. |
| `.uni`, `GA16*`, image gaiji, `COLSCR.DIC`, `PCMDATA.DIC` | Parsed or modeled for observed SSED resource variants. |
| Windows / Android / iOS / Mac OS X SSED packaging layers | Supported per observed package family. These are package layouts around SSED, not separate core formats. |
| LVED/WebView2 `main.data` / `.dbc` SQLCipher packages | Handled as a separate non-SSED format family by toolkit classification/model reports and LVED inspection code. |
| LVLMultiView SQLite packages | Handled as a separate package family by toolkit classification/model reports and MultiView inspection code. |
| SIZK read-aloud SSED bundle | Inspected and reported for the observed NHK read-aloud set-sale package set. It is SSED with loose audio/template sidecars, not a platform wrapper. |
| Plain-SSED writer proof of concept | Experimental author-core primitives for clean generated SSED packages. |

## lvcore Reader Status

This table is about `src/lvcore-experimental`, not the toolkit.

| Family / Layer | lvcore status |
|---|---|
| SSED reader path | Active compatibility target. The current proof of concept opens, searches, dereferences, and renders known SSED reader cases with diagnostics. The sibling `lvcore-audit` package owns corpus validation and scorecards. |
| Dense/sidecar SSED | Treated as SSED body-source variants, not as LVED or LVLMultiView. |
| LVED/WebView2 | Detected/classified only in lvcore; not implemented as a lvcore reader path. |
| LVLMultiView | Detected/classified only in lvcore; not implemented as a lvcore reader path. |
| Writer/importer behavior | Out of scope for lvcore. |

The detailed lvcore status table, current counters, boundaries, and audit
commands live in [docs/lvcore-status.md](docs/lvcore-status.md).

## Install

Use Python 3.10 or newer.

```bash
git clone https://github.com/shoui520/logovista-tools.git
cd logovista-tools
python -m pip install -e .
```

Encrypted Windows body streams require AES support:

```bash
python -m pip install -e ".[crypto]"
```

Verify the CLI:

```bash
logovista-tools --help
```

You can also run from a source checkout without installing:

```bash
./logovista-tools --help
```

The public command name is `logovista-tools`. `logovista_tools` is only the
Python import/module name; Python import package directories cannot contain
hyphens.

CLI status and progress messages are written to stderr; JSON/JSONL data stays
on stdout. Use `--verbose` before or after a subcommand for extra progress
details; expected input errors remain concise, while unexpected internal
failures include tracebacks in verbose mode.
For interactive body browsing, `entries --print` prints emitted entries to the
terminal while still writing the JSONL output under `--out-dir`.
For end-user extraction without knowing LogoVista internals, use
`logovista-tools extract /path/to/_DCT_DICT`; it interactively offers entries,
SQLite sidecars, media, titles/indexes/menus, gaiji maps/glyphs, and vlpljbl
resources.

## Quick Start

Scan a LogoVista collection:

```bash
logovista-tools scan /path/to/LogoVista
```

For corpus-scale commands, `--jobs 0` uses all CPUs reported by Python:

```bash
logovista-tools dump-package-models /path/to/LogoVista \
  --out-dir out/package-models \
  --jobs 0 \
  --resume \
  --progress \
  --gaiji-readiness \
  --chunked
```

Inspect one package model:

```bash
logovista-tools dump-package-model /path/to/_DCT_HAESPJPN --out-dir out/package-model
```

Run the lvcore reader audit:

```bash
PYTHONPATH=src/lvcore-experimental:src/lvcore-audit python3 -m lvcore_audit corpus \
  /path/to/LogoVista \
  --full --jobs 0 --progress --output-dir out/lvcore-corpus
```

The full command reference is in [docs/commands.md](docs/commands.md).

## Documentation Map

| Page | Purpose |
|---|---|
| [CLI Command Reference](docs/commands.md) | Toolkit CLI commands plus pointers to the separate lvcore and lvcore-audit CLIs. |
| [Project Status and Roadmap](docs/status.md) | `logovista-tools` capability status and roadmap. |
| [lvcore Status](docs/lvcore-status.md) | Reader-core status table, boundaries, and audit counters. |
| [Package Families](docs/package-families.md) | SSED package layers, non-SSED families, SIZK, and file lookup behavior. |
| [Corpus Findings](docs/corpus-findings.md) | Observed behavior from real dictionaries and platform comparisons. |
| [Legal and Data Policy](docs/legal.md) | Repository scope and data-handling policy. |
| [Format Notes Index](spec/README.md) | Spec-style notes for containers, text streams, indexes, media, gaiji, LVED, and LVLMultiView. |

## Development

Run tests:

```bash
pytest -q
```

The repository intentionally does not include proprietary dictionary files,
decrypted databases, generated full-body exports, extracted media, vendor DLLs,
or generated gaiji assets.

When adding support for a new dictionary family, prefer:

1. classify the package and components;
2. preserve raw addresses and bytes in reports;
3. add measurable unknown counts;
4. document confidence and corpus evidence;
5. add synthetic tests for parser behavior.

# Documentation Coverage Map

This page maps the current source tree to the docs that describe it. Treat the
source code and focused tests as authoritative when a report or old note
disagrees.

## Toolkit Source Areas

| Source area | Primary docs |
|---|---|
| `cli.py` command wiring and UX | [CLI Command Reference](commands.md) |
| `decoded_model.py`, `model_readiness.py`, `capability_matrix.py` | [Decoded LogoVista Model v0](../spec/decoded-model-v0.md), [Project Status and Roadmap](status.md) |
| `ssed.py`, `crypto.py`, `macos_zip.py` | [SSED Container](../spec/ssed-container.md), [Package Layers](../spec/package-layers.md), [Android Packages](android-packages.md) |
| `entries.py`, `text.py`, `opcodes.py`, `hcrender.py` | [Text Streams and Body Storage](../spec/text-streams.md), [HC Renderer Parity Matrix](hc-renderer-parity.md) |
| `indexes.py`, `titles.py`, `menus.py`, `panels.py`, `windows.py` auxiliary-tree parsers | [Menus, Titles, and Indexes](../spec/menus-titles-indexes.md), [Package Layers](../spec/package-layers.md) |
| `gaiji.py`, `ga16.py`, `colscr.py`, `pcmdata.py`, `sounddata.py`, `resources.py` | [Gaiji, Images, and Media](../spec/gaiji-media.md), [CLI Command Reference](commands.md) |
| `lved.py` | [LVED_SQLITE3 Packages](../spec/lved-main-data.md), [LVED_SQLITE3 Corpus Findings](lved-sqlite3-corpus.md), [Android Packages](android-packages.md) |
| `multiview.py` | [LVLMultiView Packages](../spec/multiview.md), [LVLMultiView Corpus Findings](multiview-findings.md) |
| `hourei.py` | [Hourei SQLite Packages](../spec/hourei.md), [Hourei Corpus Findings](hourei-findings.md) |
| `hc_re*.py`, `hc_parity.py`, `hc_regression.py` | [HC Renderer Parity Matrix](hc-renderer-parity.md), [Corpus Findings](corpus-findings.md) |
| `repacker.py` | [CLI Command Reference](commands.md), [Project Status and Roadmap](status.md) |
| `writer.py`, `writer_jmdict.py`, `writer_verify.py` | [CLI Command Reference](commands.md), [Menus, Titles, and Indexes](../spec/menus-titles-indexes.md), [Gaiji, Images, and Media](../spec/gaiji-media.md) |

## lvcore Source Areas

| Source area | Primary docs |
|---|---|
| `reader.py`, `entry_views.py`, `surfaces.py` | [lvcore Status](lvcore-status.md), [`src/lvcore-experimental/README.md`](../src/lvcore-experimental/README.md), [`ARCHITECTURE.md`](../src/lvcore-experimental/ARCHITECTURE.md) |
| `package.py`, `dictionary.py`, service stores | [`ARCHITECTURE.md`](../src/lvcore-experimental/ARCHITECTURE.md), [`FUTURE_RUST.md`](../src/lvcore-experimental/FUTURE_RUST.md) |
| `body_provider.py`, `body_source*.py`, `package_entries.py`, `sidecar_body.py` | [`README.md`](../src/lvcore-experimental/README.md), [`ARCHITECTURE.md`](../src/lvcore-experimental/ARCHITECTURE.md) |
| `package_search.py`, `index.py` | [`README.md`](../src/lvcore-experimental/README.md), [Menus, Titles, and Indexes](../spec/menus-titles-indexes.md) |
| `document.py`, `render.py`, `inspect.py`, `resource_locations.py` | [`ARCHITECTURE.md`](../src/lvcore-experimental/ARCHITECTURE.md), [lvcore Status](lvcore-status.md) |

## Current Underdocumented Areas

These areas are implemented or partially implemented in source, but should not
be treated as fully specified yet:

- Product-specific HC renderer hooks beyond confirmed visual-parity entries.
  The parity matrix names hook gaps; exact DLL parity is not claimed.
- LVED_SQLITE3 product-template rendering. The toolkit can render generic
  structural HTML and resolve many protocols/resources, but exact LVEDVIEWER
  CSS/JS behavior is still a separate renderer task.
- LVLMultiView and Hourei UI parity. Both have working package inspections and
  bounded showcases, but their official viewer interfaces are not fully
  reimplemented.
- Writer output beyond the current plain-HONMON/JMdict research path. Writer
  tests prove internal conventions, not compatibility with every observed
  retail package shape.
- Full-text search performance architecture for the future reader. Native
  index modes are modeled; scalable corpus-wide full-text search remains a
  reader-core design target rather than a settled implementation.

## Hygiene Rules

Public docs and source comments must not contain private local paths, private
corpus folder names, generated report bodies, dictionary text, decrypted
databases, key strings, or proprietary viewer material. Use generic paths such
as `/path/to/ssed-corpus` and keep private evidence under uncommitted report
directories.

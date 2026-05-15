# LogoVista Format Notes

These notes describe the current reverse-engineered model of the observed
LogoVista/SystemSoft SSED dictionary family.

The pages are organized by layer. They intentionally separate raw format facts
from platform packaging observations and product-specific behavior.

| Page | Layer |
|---|---|
| [Package Layers](package-layers.md) | Raw core files and iOS/Android/Windows wrappers. |
| [Decoded LogoVista Model v0](decoded-model-v0.md) | Draft package-level model for components, addresses, entries, spans, gaiji, media, indexes, titles, menus, sidecars, and issues. |
| [SSED Container](ssed-container.md) | `SSEDINFO`, `SSEDDATA`, compression, encryption, and component composition. |
| [Text Streams and Body Storage](text-streams.md) | Expanded `HONMON.DIC` / `HONMON.DIN`, controls, body boundaries, dense HONMON, and database-backed bodies. |
| [Menus, Titles, and Indexes](menus-titles-indexes.md) | `MENU.DIC`, `*TITLE.DIC`, `*INDEX.DIC`, branch pages, and leaf rows. |
| [Gaiji, Images, and Media](gaiji-media.md) | `.uni`, `GA16HALF`, `GA16FULL`, image resources, `COLSCR.DIC`, and `PCMDATA.DIC`. |
| [LVED SQLCipher Packages](lved-main-data.md) | Modern WebView2 `main.data` / `.dbc` payloads that are not SSED/HONMON dictionaries. |
| [LVLMultiView Packages](multiview.md) | Products with SSEDINFO facade catalogs and LogoFontCipher SQLite payloads. |
| [Confidence Levels](confidence.md) | How claims are labeled and how uncertainty should be recorded. |

Corpus-specific findings and product comparisons live in
[docs/corpus-findings.md](../docs/corpus-findings.md).

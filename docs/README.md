# Documentation

This directory contains user-facing project documentation. Format-specific
reverse-engineering notes live in [`../spec`](../spec/README.md).

| Page | Purpose |
|---|---|
| [CLI Command Reference](commands.md) | All current commands and options. |
| [Project Status and Roadmap](status.md) | `logovista-tools` capability status and project direction. |
| [lvcore Status](lvcore-status.md) | Reader-core status, boundaries, and audit counters. |
| [Package Families](package-families.md) | SSED package layers, non-SSED families, and lookup rules. |
| [Corpus Findings](corpus-findings.md) | Observed behavior from real dictionaries. |
| [Legal and Data Policy](legal.md) | Repository and data-handling boundaries. |

## Project Tracks

Public docs now distinguish two active tracks:

- `logovista-tools` is the research toolkit: package classification, decoded
  model generation, corpus reports, extractors, verification, and the
  experimental plain-SSED writer proof of concept.
- `src/lvcore-experimental` is the clean reader-core proof of concept. It is
  reader-only, independent from `logovista_tools`, and its compatibility target
  is the real LogoVista corpus rather than generated writer fixtures.

LVED SQLCipher and LVLMultiView are separate package families handled by the
toolkit classification/model-report path. They are not currently implemented
as lvcore reader paths. Dense/sidecar SSED remains an SSED body-source problem,
not an LVED or LVLMultiView problem.

## Split Map

The former monolithic README was split with the technical sections preserved:

- install and quick-start material stayed on the front page;
- command documentation moved to [commands.md](commands.md);
- toolkit capability status and roadmap moved to [status.md](status.md);
- lvcore reader status moved to [lvcore-status.md](lvcore-status.md);
- package-family and lookup-rule notes moved to
  [package-families.md](package-families.md);
- corpus observations and platform comparisons moved to
  [corpus-findings.md](corpus-findings.md);
- raw format notes moved to [`../spec`](../spec/README.md);
- legal/data policy moved to [legal.md](legal.md).

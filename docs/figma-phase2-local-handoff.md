# Figma Phase 2 Local Handoff

Status: local preparation complete; Figma sync pending Starter MCP quota.

## Approved page architecture

The Starter three-page limit is handled without dropping content:

1. `Cover`
2. `Documentation` — Getting Started, Color, Typography, Spacing, Radius, Elevation
3. `Library` — Components index, Utilities contract, code mapping

## Local review

Run `python3 app.py` and open:

```text
http://127.0.0.1:8000/design-system
```

The preview is static and does not request the local API or read watchlist,
portfolio, option, or journal data.

## Figma resume

Execute `scripts/figma-phase2/*.js` in the order listed by
`scripts/figma-phase2/README.md`. Each script uses deterministic names,
checks for existing objects, switches pages at most once, and returns IDs for
the state ledger.

Required file key: `__FIGMA_FILE_KEY__` (replace it with a file key you control).

## Accessibility decision

Small orange text on light surfaces failed WCAG AA contrast in the first local
audit. The local preview now uses a darker signal ink for those labels. The
Figma scripts bind light-surface labels to `color/text/primary`; signal orange
remains available for fills, borders, and text on sufficiently dark surfaces.

## Verified locally

- Cover, Documentation, and Library navigation works.
- Desktop width: no horizontal overflow at 1440px.
- Mobile width: no horizontal overflow at 390px.
- Documentation counts: 8 swatches, 7 spacing bars, 6 radius examples.
- Library count: 6 planned component families and 3 code mappings.
- Browser console: no errors.
- Lighthouse mobile snapshot: Accessibility 100, Best Practices 100, SEO 100.
- Python tests: 3 passing.
- Thirteen Figma scripts parse successfully and contain no prohibited plugin lifecycle calls.

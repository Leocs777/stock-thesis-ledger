# Investor Lab Figma Phase 2 Runbook

These scripts implement the approved Figma Starter three-page architecture:

1. `Cover`
2. `Documentation` — Getting Started + Foundations
3. `Library` — Components + Utilities index

They are intended to be passed to Figma `use_figma` one file at a time, in
filename order, after the Starter MCP call quota becomes available. Every
script is standalone, idempotent by deterministic page/node name, switches
pages at most once, and returns created or inspected node IDs.

## Execution order

1. `01-create-pages.js`
2. `02-build-cover.js`
3. `03-build-documentation-shell.js`
4. `03b-populate-getting-started.js`
5. `04-populate-colors.js`
6. `05-populate-typography.js`
7. `06-populate-spacing.js`
8. `07-populate-radius-elevation.js`
9. `08-build-library.js`
10. `09-validate.js`
11. `09a-validate-cover.js`
12. `09b-validate-documentation.js`
13. `09c-validate-library.js`

After each successful write, copy returned IDs into
`/private/tmp/design-system-state-investor-lab-v1.json`. Do not execute the
next script until the current step succeeds. Use the Figma file key
`__FIGMA_FILE_KEY__` (replace it with a file key you control) and include
`figma-use,figma-generate-library` in the tool's `skillNames` field.

The scripts assume Phase 1 still contains 50 variables, these six text styles,
and `Shadow/Panel`. `09-validate.js` fails closed if the three-page structure,
documentation sections, or representative bindings are missing.

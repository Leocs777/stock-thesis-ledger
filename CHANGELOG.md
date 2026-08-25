# Changelog

## 0.1.6 - 2026-08-24

- Correct equity/option position separation, option contract multipliers, account weights, sector weights, and Paper option order notional.
- Correct option scenario entry-value handling and record the calculation version on generated reports.
- Base day-trade VWAP and relative volume on regular-session, time-matched evidence; use New York trading dates and clock-aware replay windows.
- Bind sessions to client transport and device, add password rotation and global sign-out, strengthen login throttling, and restrict shared provider/broker/backup controls to the owner.
- Keep Web and iOS sessions active across recoverable panel failures and failed logout requests.
- Improve Chinese coverage, keyboard focus, muted-text contrast, and non-color risk indicators.
- Add historical migration coverage through schema 17, GitHub CI, public-repository CodeQL, Dependabot, and the first extracted portfolio domain module.

## 0.1.5

- Add all-watchlist refresh, per-symbol refresh, observed IEX prices, section shortcuts, and the Investor Lab logo.

## 0.1.4

- Add the shared four-step Web/iOS workflow: choose a symbol, refresh and score evidence, prepare a Paper-only order, and review the outcome.
- Carry the selected ticker into the Paper ticket and keep advanced Command tools collapsed until requested.
- Expand automated Simplified Chinese coverage across static copy, runtime states, and server validation errors.

## 0.1.3

- Replace typed Paper-order confirmation phrases with two explicit checkbox acknowledgements in Web and iOS.
- Preserve Paper-only routing, notional/loss limits, synchronized-account checks, and idempotent client order IDs.

## 0.1.2

- Restore TLS certificate verification for python.org macOS Python installations by selecting the maintained system CA bundle when no explicit bundle is configured.

## 0.1.1

- Add the local Paper-validation dashboard, bounded scheduler, failure history, validation reports, and local Web/iOS notifications.
- Keep missing credentials and provider failures from counting as completed validation samples.

## 0.1.0

- Publish the initial local-first Web and SwiftUI reference implementation with deterministic research, append-only ledgers, Web/iOS sync, and an Alpaca Paper-only broker boundary.

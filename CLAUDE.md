# Stock Thesis Ledger contributor guide

This file gives coding agents and human contributors the commands, boundaries,
and repository map needed to make safe changes.

## Product boundary

- Local-first investment research and paper-trading reference implementation.
- Public project and repository name: `Stock Thesis Ledger` and
  `Leocs777/stock-thesis-ledger`.
- Internal Web/iOS runtime name remains `Investor Lab` for v0.1.0 compatibility;
  do not rename bundle, database, backup, environment, Keychain, or migration
  identifiers as part of documentation-only work.
- Python standard-library HTTP server and SQLite database.
- Static Web client plus native SwiftUI iOS client.
- No live brokerage route. Keep every broker mutation fixed to
  `paper-api.alpaca.markets`.
- Do not turn deterministic classifications into promises or personalized
  investment-advice claims.
- Do not commit provider keys, user data, SQLite files, logs, backups, account
  exports, Apple signing files, build outputs, or machine-specific URLs.

## Commands

```bash
./setup.sh
python3 app.py
python3 -m unittest -v
python3 -m py_compile app.py test_app.py scripts/check-local-links.py
python3 scripts/check-local-links.py
zsh -n setup.sh scripts/archive-testflight.sh scripts/reload-local-service.sh
```

On macOS:

```bash
plutil -lint ios/InvestorLab/Info.plist ios/ExportOptions.plist \
  scripts/org.investorlab.server.plist scripts/org.investorlab.tunnel.plist

xcodebuild \
  -project ios/InvestorLab.xcodeproj \
  -scheme InvestorLab \
  -sdk iphonesimulator \
  -derivedDataPath /tmp/stock-thesis-ledger-derived-data \
  CODE_SIGNING_ALLOWED=NO \
  build
```

## Repository map

| Path | Purpose |
| --- | --- |
| `app.py` | Schema, HTTP API, auth, sync, calculations, provider adapters |
| `test_app.py` | End-to-end API and deterministic engine tests |
| `web/` | Static responsive Web client and design system |
| `ios/InvestorLab/` | SwiftUI application and secure token storage |
| `docs/accounts-and-sync.md` | Database, auth, migration, and sync contracts |
| `docs/architecture.md` | Current public component and data-flow overview |
| `docs/strategy-methodology.md` | decision-v4.1 scoring and scenario specification |
| `docs/paper-validation-protocol.md` | Frozen 30-60 calendar-day validation campaign |
| `docs/security-and-threat-model.md` | Trust boundaries and residual risks |
| `scripts/` | Release helpers, LaunchAgent templates, and validation |

## Change rules

1. Read the affected route, storage helper, Web call site, iOS call site, and
   existing test before editing a shared contract.
2. Reuse existing normalization and authorization helpers.
3. Keep state-changing browser routes protected by authentication and CSRF.
4. Keep iOS bearer tokens out of UserDefaults and browser tokens out of local
   storage.
5. Treat provider errors, stale data, missing fields, and quality-gate failures
   as explicit non-actionable states.
6. Preserve point-in-time inputs and append-only audit histories.
7. Add migrations forward only and test both a fresh database and an older
   supported schema.
8. Fake external network calls in tests. Never require a real key in CI.
9. Update README, `.env.example`, architecture, and security docs when a
   setting or boundary changes.
10. Run the full unit suite and packaging checks before handoff.

## UI guidance

Preserve the existing visual language and accessibility behavior across Web
and iOS. Use synthetic data for screenshots. Keep timestamps, source labels,
quality states, counter-evidence, and risk warnings visible; color must not be
the only signal.

## Pull-request handoff

Report:

- behavior changed;
- files and contracts changed;
- migrations or environment variables added;
- security, privacy, data-cost, and broker implications;
- tests and builds run; and
- remaining limitations.

# Contributing to Stock Thesis Ledger

Thank you for helping improve Stock Thesis Ledger. This project favors small,
reviewable changes that preserve its local-first and Paper-only safety
boundaries.

## Before you start

- Search existing issues and discussions before opening a duplicate.
- Use a feature request to describe a new workflow before making a large change.
- Do not include account data, provider responses, API keys, screenshots with
  personal information, database files, logs, or Apple signing material.
- Do not describe deterministic research outputs as guaranteed, predictive, or
  personalized investment advice.
- Changes that add a live brokerage host or real-money order path are out of
  scope.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Local development

```bash
./setup.sh
python3 -m unittest -v
python3 app.py
```

The application has no third-party Python runtime dependencies. Provider tests
must use fakes or fixtures and must not require a contributor's real
credentials.

For iOS work, open `ios/InvestorLab.xcodeproj`, select your own development
team, and use an iOS 17+ Simulator. Do not commit team IDs, provisioning
profiles, archives, or exported IPAs.

## Change guidelines

1. Keep HTTP behavior, Web state, and iOS sync contracts consistent.
2. Reuse existing validation and normalization helpers before adding new ones.
3. Make data-quality failures explicit; do not silently turn missing evidence
   into an actionable state.
4. Preserve append-only histories unless a documented migration requires a
   different model.
5. Keep all brokerage mutation code fixed to Alpaca Paper and behind the
   existing acknowledgement and risk checks.
6. Add or update tests for behavior changes.
7. Update public documentation when settings, routes, storage, or risk
   boundaries change.

## Required checks

```bash
python3 -m unittest -v
python3 -m py_compile app.py test_app.py investor_lab/portfolio_math.py scripts/check-local-links.py
python3 scripts/check-local-links.py
zsh -n setup.sh scripts/archive-testflight.sh scripts/reload-local-service.sh
plutil -lint ios/InvestorLab/Info.plist ios/ExportOptions.plist \
  scripts/org.investorlab.server.plist scripts/org.investorlab.tunnel.plist
```

Run the plist checks on macOS. For iOS changes, also run the signing-free
Simulator build documented in [README.md](README.md).

## Pull requests

- Keep one logical change per pull request.
- Explain user-visible behavior and security/data implications.
- List the checks you ran and any checks you could not run.
- Add before/after screenshots for intentional UI changes, using synthetic data.
- Call out new environment variables, migrations, provider calls, retention,
  or network exposure.
- Confirm that no secret, PII, live brokerage route, or generated data is
  included.

Maintainers may ask to split changes that mix product features, schema
migrations, and large presentation rewrites.

## Licensing

Contributions are accepted under the repository's
[AGPL-3.0-only license](LICENSE). Submit only work that you have the right to
license.

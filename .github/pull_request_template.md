## What changed?

Describe the user-visible behavior and why the change is needed.

## Boundaries affected

- [ ] HTTP/API contract
- [ ] SQLite schema or migration
- [ ] Web client
- [ ] iOS client
- [ ] Provider request, cache, or cost
- [ ] Authentication, privacy, export, or retention
- [ ] Strategy, options, day-trade, or portfolio calculation
- [ ] Alpaca Paper order workflow
- [ ] Documentation/tooling only

## Safety checklist

- [ ] I used synthetic data and included no secret, PII, database, log, account
      export, signing identity, or machine-specific URL.
- [ ] No live brokerage host or real-money order route was added.
- [ ] Missing, stale, or invalid evidence fails closed and remains visible.
- [ ] Financial outputs are described as deterministic research or scenarios,
      not guarantees or personalized advice.
- [ ] Shared Web/iOS behavior and documentation stay consistent.

## Verification

- [ ] `python3 -m unittest -v`
- [ ] `python3 -m py_compile app.py test_app.py scripts/check-local-links.py`
- [ ] `python3 scripts/check-local-links.py`
- [ ] Shell and plist checks from `README.md`
- [ ] Signing-free iOS Simulator build, if iOS code changed

List any check not run and why:

## Screenshots

For UI changes, attach before/after screenshots containing only synthetic data.

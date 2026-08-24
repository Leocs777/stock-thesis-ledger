# Release readiness

Stock Thesis Ledger is a paper-only build. The compatible internal
`Investor Lab` runtime can submit, replace, and
cancel Alpaca Paper orders only after typed confirmations and local risk checks.
The host is fixed to `paper-api.alpaca.markets`; live-account brokerage routing
is not implemented.

## TestFlight archive

Run `scripts/archive-testflight.sh` to create a signed Release archive. Set
`INVESTORLAB_EXPORT_IPA=1` to also export an App Store Connect IPA. Upload is an
explicit later step because it changes external App Store Connect state.

Before upload, choose a bundle identifier owned by your Apple Developer account,
select your development team in Xcode, create the matching App Store Connect
record, confirm the version/build number, complete privacy metadata, and provide
the HTTPS server URL used by testers. The checked-in project intentionally has
no Apple Development Team ID.

## Notifications

The current build uses on-device notifications for option expirations,
decision changes, and SEC filing changes. APNs background delivery is not
enabled. Public beta push requires the Push Notifications capability, an APNs
signing key or certificate, device-token registration, a provider worker, and a
privacy/retention policy. Keep local notifications as the zero-cost default.

## Stable connectivity

The local service reports `INVESTORLAB_PUBLIC_URL` in System Health. The optional
LaunchAgent tunnel template requires a user-supplied authenticated HTTPS
endpoint. Do not expose the Python server directly to the internet. A public
beta should place authentication, request limits, backups, and audit logging
behind a hardened gateway.

After replacing the LaunchAgent plist or server code, run
`scripts/reload-local-service.sh` once. The script reloads only the compatible
internal Investor Lab agent and confirms that `/api/health` is reachable.

## Data modes

`INVESTORLAB_MARKET_HISTORY=compact|full` controls requested history depth.
`INVESTORLAB_ADJUSTED_DAILY=1` enables adjusted close, dividend, and split
storage when the configured Alpha Vantage plan permits that endpoint. Raw daily
prices remain the cost-controlled default. Intraday background collection is
limited by `INVESTORLAB_INTRADAY_SYMBOL_LIMIT` and runs only on weekdays from
04:00 through 16:05 America/New_York.

`INVESTORLAB_OPTION_COLLECTION=1` enables the cost-controlled option snapshot
rotation. It loads at most one missing watchlist symbol per 15-minute scheduler
cycle from 09:35 through 16:05 America/New_York. Daily and weekly local reports
and one verified SQLite backup per UTC day are enabled without a paid worker.

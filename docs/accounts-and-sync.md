# Accounts and Sync

Status: local-first reference implementation.

## What ships now

- One local account by default, with optional additional registration behind
  `INVESTORLAB_ALLOW_REGISTRATION=1`.
- Scrypt password hashing with a random per-account salt.
- Thirty-day random sessions; SQLite stores only a SHA-256 token hash.
- Browser authentication through an HttpOnly, SameSite=Strict cookie and CSRF
  token on every state-changing request.
- iOS authentication through a bearer token stored with
  `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` in Keychain.
- Per-user watchlist, paper ledger, portfolio, devices, and incremental sync
  events.
- A synchronized planning profile for strategy style, time horizon, paper
  account size, risk limits, and defined-risk options preference.
- Connected-device inventory and a portable JSON account export that explicitly
  excludes credentials, session tokens, and provider keys.
- Session-to-device binding: removing another device also revokes every session
  registered to that device. The current device must sign out normally.
- Preview-first CSV position import with a 500-row limit, whole-file validation,
  an atomic append-only write, and duplicate-content fingerprint protection.
- Authenticated data-health reporting for SQLite integrity, account record
  counts, cached-market freshness, database size, backup age/retention, public
  URL status, and the latest read-only Paper-account snapshot. Health checks can
  be recorded as collection runs.
- Automatic once-per-UTC-day verified SQLite backup with configurable retention,
  manual backup listing, and explicit restore guarded by an exact confirmation
  string plus an automatic pre-restore safety backup.
- Password-confirmed account deletion with foreign-key cascading across all
  synchronized user data.
- Free iOS local notifications for saved option worksheets seven days before
  and on expiration; no APNs worker or notification subscription is required.
- A derived watchlist research board shared by Web and iOS from the existing
  end-of-day cache; reading or syncing the board does not spend API requests.
- A per-symbol historical chart response with up to 100 cached daily bars,
  volume, observed range, return, drawdown, and volatility. Full bar arrays are
  omitted from the regular sync snapshot to keep mobile payloads small.
- Append-only day-trade risk worksheets and option expiration-payoff worksheets
  in the shared Web/iOS snapshot and sync event stream.
- Append-only plan reviews plus a derived option-expiration attention list. The
  app records only the user's stated decision and never infers brokerage status.
- Append-only journal notes and self-recorded trade reviews, plus derived
  portfolio exposure and review statistics.
- User-defined price thresholds with crossing history in the existing sync
  event ledger. Alert evaluation uses cached end-of-day closes.
- Shared Alpha Vantage end-of-day daily-bar cache; API keys remain process
  environment variables and are never returned to clients or written to SQLite.
- A Strategy Lab 4.1 decision ledger shared by Web and iOS. Balanced, Growth,
  Value, Income, and Momentum use distinct technical/fundamental weights, while
  the selected horizon shifts those weights. Each result stores its context
  hash, factor allocation, evidence, invalidation, risk capacity, signal change,
  and point-in-time SEC/price walk-forward scenario. Identical input contexts
  are reused instead of duplicating history.
- Immutable numbered custom-strategy versions with configuration hashes, plus a
  point-in-time walk-forward execution-cost model that adds range and
  dollar-volume slippage surcharges to the saved base bps.
- An Alpaca Paper mirror for balances, positions, historical order status, and
  fills, plus a default-locked Paper-only order route with checkbox acknowledgement,
  idempotent client IDs, notional and daily-loss limits, and no live host.
- Web server-sent Day Trade monitoring, iOS 20-second foreground monitoring,
  deduplicated setup alerts, and full cached minute-session playback. Options
  worksheets support single legs, verticals, long straddles/strangles, and
  four-leg iron condors.
- Optional 12-168 hour watchlist refresh scheduling in the local Mac process,
  foreground browser notifications, and free iOS local reminders.
- A daily action queue, ranked watchlist screener, and paper portfolio
  performance summary shared by Web and iOS. All three are derived at read time
  from existing records, so they add no schema state and spend no market-data
  requests.
- Cached SEC EDGAR company facts and recent filing links shared by Web and iOS.
  Public reads require no SEC account or API key; the server declares a contact
  email and caches normalized fundamentals for 24 hours. Each refresh reports
  new forms and changed annual metrics, recalculates that symbol's decision,
  and updates a derived 90-day watchlist filing monitor without a new table.
  Opt-in Web foreground notifications and iOS local notifications reuse this
  snapshot; no APNs worker or subscription is required. Schema v16 notification
  rules retain only each user's filtering preferences and trigger state.

## Schema migration

Startup applies forward-only SQLite migrations through schema v16, each inside a
`BEGIN IMMEDIATE` transaction. The original v1 tables remain available as
`watchlist_v1_archive` and `trades_v1_archive`. Their rows are copied into the
new user-scoped tables with no owner. The first account atomically claims those
rows and records a bootstrap sync event. Schema v3 adds the shared daily-market
cache; v4 adds the user-scoped `research_plans` ledger; v5 adds the
user-scoped `journal_entries` ledger. Schema v6 adds user-scoped `price_alerts`;
crossing history reuses `sync_events`. Schema v7 adds the user-scoped
`investor_profiles` planning-default record. Schema v8 adds append-only
`plan_reviews`; plan follow-through and expiration attention are derived at read
time. Schema v9 binds sessions to registered devices and adds the
`portfolio_imports` duplicate-protection ledger. Portfolio exposure, data
health, and review statistics remain derived. Schema v10 adds append-only
`decision_runs` plus per-account `decision_settings`; market factors, position
fit, evidence, and backtests remain deterministic and reproducible from the
stored result. The daily briefing, screener ranking, estimated paper cash,
account value, and open P&L remain derived. Schema v11 adds the shared
`sec_cache` used for SEC ticker-to-CIK metadata and normalized company facts;
the cache contains public issuer data and is not user-scoped. Schema v12 adds
strategy templates, portfolio analysis, and execution-review fields. Schema v13
adds retained intraday bars and option-chain snapshots. Schema v14 adds
collection-run audit records plus optional adjusted-price and corporate-action
history. Schema v15 adds immutable `strategy_versions`, read-only
`paper_account_snapshots`, deduplicated `day_trade_alert_states`, automatic
backup/restore health automation, and expands collection-run job types without
weakening their status constraints. Schema v16 adds `paper_order_controls`,
`paper_order_intents`, `scanner_presets`, `notification_rules`, and
`research_reports`.

No downgrade migration is provided. Back up `data/investor-lab.sqlite3` before
manually changing or replacing the database.

## Sync contract

1. A client signs in and registers a non-secret device ID with `POST
   /api/devices`.
2. It calls `GET /api/sync?since=<revision>&limit=500`.
3. The response includes ordered events, a current snapshot, `cursor`, and
   `has_more`.
4. The client continues until `has_more` is false, stores the cursor locally,
   and acknowledges it through `POST /api/sync/ack`.

Web local storage and iOS UserDefaults contain only the device ID and per-user
cursor. They never contain passwords or session tokens.

Set `INVESTORLAB_SECURE_COOKIE=1` whenever the Web client is accessed through an
HTTPS tunnel. Leave it unset only for local `http://127.0.0.1` development.

## API surface

Public routes:

- `GET /api/health`
- `POST /api/auth/register`
- `POST /api/auth/login`

Authenticated routes:

- `GET /api/auth/session`
- `POST /api/auth/logout`
- `GET /api/snapshot`
- `GET /api/sync`
- `GET|POST /api/devices`
- `DELETE /api/devices/{id}`
- `POST /api/sync/ack`
- `GET|PATCH /api/investor-profile`
- `GET /api/export`
- `POST /api/account/delete`
- `GET /api/system/health`
- `POST /api/system/health-check`
- `GET /api/system/backups`
- `POST /api/system/backup`
- `GET /api/validation/dashboard`
- `GET /api/validation/report`
- `POST /api/validation/run`
- `POST /api/system/restore`
- `GET /api/imports`
- `POST /api/imports/portfolio/preview`
- `POST /api/imports/portfolio`
- `GET|POST|DELETE /api/watchlist`
- `GET|POST /api/trades`
- `GET /api/portfolio`
- `GET /api/portfolio/risk`
- `GET|POST /api/journal`
- `GET /api/analytics/review`
- `GET|POST|DELETE /api/alerts`
- `GET|PATCH /api/decision-settings`
- `POST /api/decisions`
- `GET /api/decisions/{symbol}`
- `POST /api/decisions/refresh-watchlist`
- `GET /api/search?q=`
- `GET /api/earnings-calendar`
- `POST /api/earnings-calendar/refresh`
- `GET /api/plans`
- `GET /api/plans/review-center`
- `POST /api/plans/day-trade`
- `POST /api/plans/options`
- `POST /api/plans/{id}/reviews`
- `GET|POST /api/strategy-templates`
- `POST /api/strategy-templates/{id}/activate`
- `GET /api/strategy-versions`
- `GET /api/day-trade/clock`
- `GET /api/day-trade/scanner`
- `GET /api/day-trade/stream`
- `GET /api/day-trade/live/{symbol}`
- `GET /api/day-trade/replay/{symbol}`
- `GET /api/options/chain/{symbol}`
- `GET /api/alpaca/paper-account`
- `POST /api/alpaca/paper-account/sync`
- `GET /api/data-sources/readiness`
- `POST /api/data-sources/test`
- `GET|PATCH /api/alpaca/paper-orders/control`
- `GET|POST /api/alpaca/paper-orders`
- `POST /api/alpaca/paper-orders/{id}/cancel`
- `POST /api/alpaca/paper-orders/{id}/replace`
- `GET|POST|DELETE /api/scanner-presets`
- `POST /api/scanner/run`
- `GET|POST|DELETE /api/notifications/rules`
- `POST /api/options/scenario`
- `GET /api/strategies/compare`
- `GET /api/portfolio/intelligence`
- `GET /api/data-quality`
- `POST /api/research/copilot`
- `GET|POST /api/reports`
- `GET /api/research/command-center`
- `GET /api/market/status`
- `GET /api/market/research/{symbol}`
- `POST /api/market/refresh`
- `GET /api/fundamentals/{symbol}`
- `POST /api/fundamentals/refresh`

## Cost and deployment boundary

The local setup has no recurring application infrastructure cost: the
Python server, SQLite database, Web client, and iOS client run on owned devices.
An optional HTTPS tunnel may have free-tier limits.

The current market-data slice uses the free `TIME_SERIES_DAILY` endpoint and a
12-hour server cache. An optional Alpaca Basic connection can supply IEX stock
observations and indicative option snapshots for paper testing; SIP coverage,
redistribution rights, and higher provider limits remain paid/licensing
boundaries. Deterministic risk, expiration-payoff, cached-universe scanning, and
local research briefs do not require a paid service. Paper order submission uses
the user's Alpaca Paper account only and adds no Stock Thesis Ledger routing fee.

Public SEC EDGAR submissions and XBRL company facts require no account, token,
or API fee. The refresh call sends a descriptive User-Agent containing the
signed-in account email, unless `INVESTORLAB_SEC_CONTACT` overrides it, and uses
a 24-hour application cache. EDGAR filer accounts and submission tokens are a
separate concern and are not used by this read-only integration.

Before any public release, replace the local development server boundary with
managed HTTPS, encrypted backups, email verification and account recovery,
central secret management, audit monitoring, retention/deletion controls,
abuse protection, and an independent security review. Market-data licensing,
App Store fees, legal/compliance review, and production observability are
separate costs. This local account layer is not production authorization to
publish individualized investment advice or route brokerage orders.

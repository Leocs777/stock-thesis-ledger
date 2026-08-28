# Configuration

Stock Thesis Ledger reads the process environment and does not automatically
load `.env` files. `.env.example` documents safe local defaults; never commit
real credentials.

```bash
INVESTORLAB_SEC_CONTACT=you@example.com \
ALPHAVANTAGE_API_KEY=replace_me \
python3 app.py
```

## Server and access

| Variable | Default | Purpose |
| --- | --- | --- |
| `INVESTORLAB_DB` | `data/investor-lab.sqlite3` | Local SQLite path |
| `INVESTORLAB_HOST` | `127.0.0.1` | Bind address |
| `INVESTORLAB_PORT` | `8000` | HTTP port |
| `INVESTORLAB_ALLOW_REGISTRATION` | `0` | Allow accounts after the first account |
| `INVESTORLAB_SECURE_COOKIE` | `0` | Mark browser cookies Secure; use `1` behind HTTPS |
| `INVESTORLAB_PUBLIC_URL` | empty | HTTPS URL reported to clients |
| `INVESTORLAB_ACCESS_GATEWAY` | empty | Use `cloudflare` only behind a configured Access policy |
| `INVESTORLAB_TRUST_PROXY` | `0` | Trust proxy identity and address headers only behind that gateway |

The default loopback bind is deliberate. Read
[Security and threat model](security-and-threat-model.md) before enabling a
non-loopback address or trusting proxy headers.

## Providers and collection

| Variable | Default | Purpose |
| --- | --- | --- |
| `INVESTORLAB_SEC_CONTACT` | signed-in email | Contact declared to SEC EDGAR |
| `INVESTORLAB_MARKET_CACHE_MINUTES` | `720` | Daily-price cache lifetime |
| `INVESTORLAB_MARKET_HISTORY` | `compact` | Alpha Vantage `compact` or `full` output |
| `INVESTORLAB_ADJUSTED_DAILY` | `0` | Request adjusted history when the plan permits |
| `INVESTORLAB_INTRADAY_COLLECTION` | `0` | Enable bounded intraday collection |
| `INVESTORLAB_INTRADAY_SYMBOL_LIMIT` | `8` | Watchlist collection cap |
| `INVESTORLAB_OPTION_COLLECTION` | `0` | Enable bounded option snapshot collection |
| `INVESTORLAB_BACKUP_RETENTION` | `30` | Verified daily backups retained |
| `ALPHAVANTAGE_API_KEY` | empty | Optional daily-price and earnings credential |
| `ALPACA_API_KEY_ID` | empty | Optional Alpaca market and Paper credential ID |
| `ALPACA_API_SECRET_KEY` | empty | Optional Alpaca market and Paper secret |
| `SSL_CERT_FILE` | platform default | Explicit CA bundle override for outbound TLS |

`compact` history is enough for current decisions. A reportable 60-session
chronological holdout needs at least 251 cached daily bars; use `full` only when
the Alpha Vantage plan permits it. Provider limits, coverage, and licensing can
change, so verify current terms before public or commercial deployment.

## Credential storage

On macOS, the owner can save provider credentials to Keychain through the app.
Environment variables are the portable alternative. Credentials are not stored
in SQLite, synchronized to clients, or included in account exports.

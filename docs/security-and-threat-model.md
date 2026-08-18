# Security and threat model

Status: local reference deployment. Last reviewed for the initial open-source
release.

## Security goals

- Protect the local password, browser session, iOS bearer token, provider keys,
  paper-account data, research history, and backups.
- Prevent one local account from reading or mutating another account's records.
- Prevent cross-site browser mutations.
- Prevent accidental live brokerage routing.
- Keep secrets and personal data out of source control, logs, exports, and sync.
- Make data-quality failures visible instead of producing success-shaped output.

## Trust boundaries

### Browser to local server

The browser trusts a localhost HTTP origin by default. Authentication uses an
HttpOnly, SameSite=Strict cookie. State-changing requests also require a CSRF
token. A hostile page, browser extension, or local process remains a potential
attacker.

### iOS to server

iOS sends a bearer token stored with a device-only Keychain accessibility
class. The token must travel only over loopback in Simulator or authenticated
HTTPS on a physical device. Plain HTTP on a shared network can expose it.

### Server to providers

The server sends credentials to the configured provider endpoints. TLS,
provider account security, provider availability, and provider response
integrity are external dependencies. SEC requests include a descriptive contact
and use a 24-hour cache.

### Server to local storage

SQLite, JSON exports, backups, and logs are ordinary local files. Git ignores
them, but the application does not encrypt their contents. Filesystem
permissions, device login security, full-disk encryption, and backup handling
are part of the user's security boundary.

### Brokerage boundary

All implemented broker mutations use `paper-api.alpaca.markets`. Paper-order
actions still affect an external account, so they require typed confirmations,
idempotency, a synchronized-account check, and local risk limits.

## Threats, controls, and residual risk

| Threat | Current controls | Residual risk |
| --- | --- | --- |
| Credential disclosure in Git | Broad ignores, placeholders, Keychain/environment storage | A contributor can still paste a secret into a tracked file or screenshot |
| Password database theft | Scrypt with random per-account salt | Offline guessing remains possible; SQLite is not encrypted |
| Session theft | Hashed server tokens, HttpOnly cookie, device-only iOS Keychain | Malware, a hostile extension, or insecure transport can steal an active session |
| Cross-site request forgery | SameSite=Strict cookie plus CSRF on browser mutations | A browser or future route can regress if it bypasses shared guards |
| Cross-account data access | User-scoped queries and route authentication | New queries require explicit authorization tests |
| Provider-key leakage | Keys stay server-side and are omitted from exports/responses | Process inspection, shell history, logs, or compromised host can expose environment values |
| Live-trade accident | Paper host constant, no live host, typed confirmations | A future contributor could weaken the invariant without tests and review |
| Duplicate Paper order | Client IDs and idempotency checks | Provider timeouts can leave uncertain external state that must be reconciled |
| Stale or malformed market data | Cache timestamps, data-quality gates, explicit source labels | Provider errors and missing corporate actions can still distort analysis |
| Database corruption or bad restore | Integrity checks, verified backups, safety copy | Local disk failure or untested off-device backup can still cause loss |
| Public internet exposure | Loopback default and deployment warnings | Binding to all interfaces exposes a development-grade server |
| Dependency compromise | No third-party Python runtime dependency | OS, browser, Xcode, GitHub Actions, and external providers remain dependencies |

## Secret handling

- Prefer macOS Keychain for interactive local provider setup.
- If environment variables are required, inject them at process launch. The app
  does not load `.env` automatically.
- Never put a real key in `.env.example`, a plist, source, issue, test fixture,
  screenshot, account export, or CI configuration.
- Rotate a key immediately if it is exposed. Removing it from the latest commit
  does not remove it from Git history.
- Keep ngrok or other tunnel credentials in the tunnel provider's supported
  secure store, not in this repository.

## Deployment checklist

Before a physical-device test:

- use an authenticated HTTPS tunnel;
- set `INVESTORLAB_SECURE_COOKIE=1`;
- confirm the iOS URL is HTTPS;
- restrict tunnel access where possible; and
- stop the tunnel when testing is complete.

Before any public or multi-user deployment:

- replace the development server boundary with managed HTTPS;
- implement production identity, email verification, recovery, and MFA options;
- add rate limits, lockouts, abuse detection, and security telemetry;
- use central encrypted secret storage with rotation;
- encrypt backups and test off-device restoration;
- perform tenant-isolation and CSRF/session testing;
- document retention, deletion, and incident response;
- confirm market-data display and redistribution rights; and
- commission an independent application-security review.

## Security regression tests

Changes to auth, sync, exports, provider configuration, backups, or Paper orders
must include tests for failure and unauthorized paths. At minimum preserve:

- protected routes reject missing/invalid sessions;
- browser mutations reject missing/invalid CSRF;
- iOS sessions do not bypass user scoping;
- account exports omit credential and token material;
- removed devices lose bound sessions;
- Paper order enablement and ticker confirmations fail closed;
- idempotent Paper order retries do not duplicate a known intent; and
- no source file introduces a live brokerage host.

Report suspected vulnerabilities through [SECURITY.md](../SECURITY.md).

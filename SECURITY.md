# Security policy

Stock Thesis Ledger handles local account credentials, provider keys, paper-account
data, and an authenticated bearer session on iOS. Please report vulnerabilities
privately.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/Leocs777/stock-thesis-ledger/security/advisories/new>

Include:

- the affected revision and platform;
- the smallest reproducible example;
- expected and observed behavior;
- likely impact and whether credentials or user data are involved; and
- a suggested mitigation, if known.

Do not open a public issue for an unpatched vulnerability. Do not include real
API keys, session tokens, databases, account exports, or personal trading data
in a report. Use synthetic values.

The project does not currently operate a bug bounty program. A maintainer will
review a report and coordinate disclosure based on severity and available
maintainer capacity.

## Supported versions

Security fixes target the current `main` branch. Tagged releases, when
available, will state their support status in release notes.

## Security boundary

The reference configuration is a localhost, local-first deployment. Controlled
additional accounts are supported, but it is not a hardened public multi-tenant
service.

- The server binds to `127.0.0.1` by default.
- Browser sessions use an HttpOnly, SameSite=Strict cookie and CSRF tokens.
- iOS bearer tokens are stored in a device-only Keychain item.
- Sessions are bound to their Web or iOS transport and registered device;
  password rotation and sign-out-all-devices revoke existing sessions.
- The first account is the local vault owner. Shared provider, broker, backup,
  restore, and maintenance mutations require that role.
- SQLite stores salted password hashes and session-token hashes, not plaintext
  passwords or bearer tokens.
- Provider secrets can be stored in macOS Keychain or supplied through the
  process environment.
- The only implemented brokerage mutation host is
  `paper-api.alpaca.markets`; live brokerage routing is out of scope.

SQLite, JSON exports, backups, and logs are not application-level encrypted.
Use FileVault or equivalent full-disk encryption, restrict filesystem access,
and protect backup copies.

Before any internet-facing or multi-user deployment, add a hardened HTTPS
gateway, request limits, account recovery, email verification, central secret
management, encrypted and tested backups, monitoring, abuse controls, and an
independent security review.

See [Security and threat model](docs/security-and-threat-model.md) for the
detailed trust boundaries and residual risks.

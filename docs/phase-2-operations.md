# Phase 2 Security, Contracts, and Data Quality

Phase 2 keeps InvestorLab as one local modular monolith. It does not add a
microservice, hosted database, paid queue, or secret-management subscription.

## Architecture boundary

- `investor_lab/security.py` owns request limits, strict CSP policy, hashed
  security events, trusted-proxy address parsing, and unusual-login detection.
- `investor_lab/market_quality.py` owns reusable daily, cross-source, intraday,
  and option-snapshot quality checks.
- `investor_lab/api_contract.py` publishes the versioned Web/iOS route catalog
  at `GET /api/contract`.
- `investor_lab/encrypted_backup.py` creates verified encrypted SQLite snapshots
  and runs a non-destructive restore drill.
- `app.py` remains the composition root and HTTP endpoint layer. This avoids a
  risky service split while the Paper validation baseline is frozen.

Run the contract and CSP check after changing an endpoint or client call:

```bash
python3 scripts/check_api_contract.py
```

## Security behavior

The Content Security Policy loads scripts and styles only from the server and
does not permit `unsafe-inline`. Requests have endpoint-class limits. Rejected
requests and successful or failed authentication events are appended to
`data/security-audit.jsonl` with restrictive file permissions. Email, user,
network, and device identifiers are stored only as SHA-256 hashes. The
authenticated `GET /api/security/events` response is limited to the signed-in
user's events.

A successful login from a new network-and-device pair returns a visible warning
to Web and iOS. This is an observation, not proof that an account was stolen.
Use Connected Devices or Sign out on all devices when the login is not yours.

## Cloudflare Access gateway

Keep the origin on localhost or another network path that is not publicly
reachable. Configure the Cloudflare Tunnel and Access policy first, then launch:

```bash
INVESTORLAB_ACCESS_GATEWAY=cloudflare \
INVESTORLAB_TRUST_PROXY=1 \
INVESTORLAB_SECURE_COOKIE=1 \
INVESTORLAB_PUBLIC_URL=https://your-private-host.example \
python3 app.py
```

Gateway mode requires the Cloudflare Access assertion, authenticated email, and
forwarded HTTPS header. The Access email must exactly match the local InvestorLab
account email. The app trusts these headers only when the operator explicitly
sets `INVESTORLAB_TRUST_PROXY=1`; do not enable it behind an untrusted proxy and
do not expose a second direct public route to the origin.

## Encrypted offsite backup

The existing in-app backup remains a local verified SQLite snapshot. The Phase
2 command encrypts another verified snapshot with OpenSSL AES-256-CBC and
PBKDF2-200000 before it enters a mounted offsite directory. The passphrase is
read from the macOS Keychain service `org.investorlab.encrypted-backup`; it is
never accepted as a command-line argument or stored in the database.

Create a Generic Password in Keychain Access with that service name and a
unique 16-or-more-character passphrase. Then point `--destination` to a mounted
iCloud Drive, external disk, NAS, or separately configured sync folder:

```bash
python3 scripts/encrypted_backup.py create \
  --destination "/path/to/mounted/InvestorLab-Backups"
```

Run a restore drill without replacing or opening the active database for write:

```bash
python3 scripts/encrypted_backup.py drill \
  "/path/to/mounted/InvestorLab-Backups/investor-lab-TIMESTAMP.sqlite3.enc"
```

The drill decrypts into an operating-system temporary directory, runs SQLite
`quick_check`, compares schema versions, deletes the temporary copy, and reports
`active_database_unchanged: true`. A successful drill is evidence that this
file and passphrase currently work; it is not a substitute for periodically
testing the complete recovery procedure on a separate machine.

## Data-quality interpretation

- Daily checks detect invalid OHLC, duplicates, zero volume, stale history,
  large split-scale discontinuities, and missing weekday ranges. Adjusted-price
  mode reports split and dividend rows separately.
- Cross-source checks compare the cached daily close with the latest observed
  Alpaca IEX trade. A difference above 3% is a warning because session movement
  can be legitimate.
- Intraday coverage reports expected, observed, and missing regular-session
  minutes rather than treating a partial IEX series as complete market history.
- Option quality reports quote coverage, crossed markets, spreads above 20%,
  and contracts passing the existing liquidity rule. Indicative snapshots are
  never represented as executable quotes.

## 第二期运维摘要

第二期保持单体部署，不增加微服务或付费基础设施。Web 已移除内联脚本和样式；登录、限流及敏感模拟订单操作会写入只含哈希标识的安全审计。新的数据质量中心会标明日线复权状态、跨来源价格差、日内缺失分钟和期权异常报价。

如需从 iPhone 远程访问，先配置 Cloudflare Tunnel 与 Access，再启用上面的网关环境变量；源服务器不能同时暴露另一条公网直连路径。异地备份密码放在 macOS 钥匙串中，加密文件可保存到已挂载的 iCloud、NAS 或外置硬盘。`drill` 只做解密和完整性检查，不会覆盖当前数据库。

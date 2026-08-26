# Paper validation protocol

Status: Stock Thesis Ledger v0.1.6.

Use this protocol to test reliability and decision discipline over 30-60
calendar days before considering any increase in real capital. Passing does not
prove profitability, remove regulatory obligations, or turn a research label
into advice.

## 1. Freeze the campaign

Before day 1, record:

- source commit and decision model version;
- immutable strategy version/configuration hash;
- watchlist universe and inclusion rules;
- Paper account size, maximum-position limit, per-trade and daily-loss limits;
- minimum reward/risk, base cost, data sources, and refresh cadence;
- entry/exit thresholds, alert rules, and enabled modules; and
- campaign start date, 30-calendar-day minimum, and planned 60-calendar-day end.

Create the local, immutable baseline before collecting day-1 evidence:

```bash
python3 scripts/paper_validation.py freeze
```

The ignored `data/validation/campaign-baseline.json` records the source commit,
model/freeze protocol, watchlist, planning limits, cadence, and provider-ready
booleans. It deliberately excludes identity, credentials, server URLs, account
numbers, positions, and orders. The command refuses to overwrite an existing
baseline; use a separately named `--output` when a documented parameter change
starts a new cohort.

Do not tune parameters after seeing results. A necessary change ends the current
cohort; document the reason and start a separately labeled cohort.

## 2. Capture each session

On every trading day:

1. run data health and record provider freshness/blockers;
2. save the decision snapshot before any Paper action;
3. record thesis, counter-evidence, invalidation, size, stop, targets, and
   expected R;
4. record whether the plan was followed, skipped, or invalidated;
5. record actual Paper entry/exit, deviation, realized P&L/R, MAE/MFE, and
   discipline score; and
6. preserve failures and no-trade decisions instead of deleting them.

Do not backfill a pre-trade thesis after the outcome is known.

## 3. Automated dashboard gate

The app starts a campaign with the first data-quality-eligible decision from the
current decision model and latest continuous strategy context. A change to the
model version, freeze protocol, style, horizon, immutable version, or weights
automatically starts a new cohort; older contexts and legacy decision models
cannot satisfy a new cohort's gates. Every gate, start date, eligible-decision
count, and symbol count is calculated from that cohort's trailing 60-calendar-day
slice.

The in-app campaign becomes `capital_review_ready` only when all six implemented
checks are true:

- at least 30 calendar days have elapsed since the cohort's first eligible decision;
- decision history spans at least 5 symbols;
- exactly 1 strategy context appears in the cohort;
- at least 20 decision outcomes resolve as target-first or stop-first;
- at least 20 followed Paper plans have a win, loss, or scratch review; and
- at least 10 distinct symbol/session combinations contain cached one-minute bars.

Those sample counts use the trailing 60-calendar-day dashboard window so the
day-30 evidence remains visible through the planned day-60 review.

The dashboard is a minimum machine-checkable gate, not the entire protocol.
Before interpreting results, also confirm at least 95% scheduled collection,
90% data-quality eligibility, a timestamped pre-entry plan for every counted
entry, and module-specific review coverage for any day-trade or options claim.

If the sample gate is not met by day 60, label the campaign inconclusive rather
than weakening the gate.

The local operator commands are:

```bash
python3 scripts/paper_validation.py status
python3 scripts/paper_validation.py run
python3 scripts/paper_validation.py report
```

`status` is read-only. `run` is explicit because it consumes configured
provider calls and can submit only the already implemented Paper-safe workflow;
it does not enable Paper orders or bypass their acknowledgements. `report`
writes timestamped Markdown and JSON evidence under ignored `data/validation/`.

## 4. Acceptance gates

### Reliability

- Zero live brokerage endpoints or real-money orders.
- Zero duplicate Paper orders from a known idempotency retry.
- Zero secret/session leakage in exports, logs, screenshots, or reports.
- Zero actionable classification produced while a data-quality blocker was
  active.
- Backup integrity passes and one restore rehearsal succeeds on a disposable
  copy.

### Process

- At least 90% of counted entries follow the frozen size and loss limits.
- Every limit breach, stale-data override, missed stop, and execution deviation
  is recorded.
- Unreviewed plans are less than 10% of resolved-plus-open campaign plans.
- No result is excluded solely because it lost money.

### Outcome report

Report, without cherry-picking:

- number of symbols, observations, entries, completed reviews, and exposure;
- return and maximum drawdown for the Paper scenario;
- same-period buy-and-hold and SPY comparisons when available;
- win rate alongside median realized R, MAE/MFE, and holding duration;
- average modeled cost and recorded Paper execution deviation;
- results by complete versus missing-fundamental coverage;
- conservative/base/permissive sensitivity; and
- all operational failures and protocol deviations.

There is deliberately no required return or win-rate threshold in v0.1.6.
Reliability, data integrity, and process adherence must be established before a
performance hypothesis is evaluated on a larger, separately frozen sample.

## 5. Automatic fail conditions

Stop and mark the campaign failed if:

- a live brokerage route is introduced or used;
- future SEC filing dates or future bars enter an earlier decision;
- a security or cross-account authorization defect is found;
- parameters are changed without starting a new cohort;
- loss/risk limits are bypassed without a recorded incident; or
- the dataset cannot reproduce a stored decision from its version and inputs.

After remediation, begin a new campaign. Do not merge pre-fix and post-fix
results.

## 6. Final decision

At calendar day 30, choose only **continue to 60**, **stop for a documented defect**, or
**close as inconclusive**. At calendar day 60, archive the frozen configuration, raw
exports, integrity result, review statistics, and limitations.

A completed campaign authorizes further Paper testing only. Any use of real
capital requires an independent decision, smaller risk limits, current broker
rules, and appropriate legal, tax, and professional review.

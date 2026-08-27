"""Pure market-data quality checks shared by research and validation views."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _row_value(row: Mapping[str, Any], key: str) -> Any:
    return row[key]


def assess_daily_bars(
    rows: Iterable[Mapping[str, Any]], *, historically_adjusted: bool = False,
    current_date: date | None = None,
) -> dict[str, Any]:
    bars = list(rows)
    if not bars:
        return {
            "status": "blocked", "score": 0, "decision_eligible": False,
            "checks": [], "blockers": ["No daily bars are cached."],
            "warnings": [], "price_adjustment": "raw",
        }
    invalid_ohlc = 0
    zero_volume = 0
    discontinuities: list[dict[str, Any]] = []
    calendar_gaps: list[dict[str, Any]] = []
    dates: list[str] = []
    for index, row in enumerate(bars):
        trading_date = str(_row_value(row, "trading_date"))
        dates.append(trading_date)
        opening = int(_row_value(row, "open_micros"))
        high = int(_row_value(row, "high_micros"))
        low = int(_row_value(row, "low_micros"))
        close = int(_row_value(row, "close_micros"))
        if min(opening, high, low, close) <= 0 or low > min(opening, close) or high < max(opening, close) or low > high:
            invalid_ohlc += 1
        if int(_row_value(row, "volume")) <= 0:
            zero_volume += 1
        if index == 0:
            continue
        previous = bars[index - 1]
        previous_close = int(_row_value(previous, "close_micros"))
        ratio = Decimal(close) / Decimal(previous_close)
        if ratio >= Decimal("1.8") or ratio <= Decimal("0.55"):
            discontinuities.append({
                "trading_date": trading_date,
                "previous_date": str(_row_value(previous, "trading_date")),
                "close_ratio": format(ratio.quantize(Decimal("0.01")), "f"),
            })
        previous_date = date.fromisoformat(str(_row_value(previous, "trading_date")))
        this_date = date.fromisoformat(trading_date)
        weekdays = sum(
            (previous_date + timedelta(days=offset)).weekday() < 5
            for offset in range(1, (this_date - previous_date).days)
        )
        if weekdays > 2:
            calendar_gaps.append({
                "after": str(_row_value(previous, "trading_date")),
                "before": trading_date,
                "unobserved_weekdays": weekdays,
            })
    age_days = ((current_date or date.today()) - date.fromisoformat(dates[-1])).days
    duplicate_bars = len(dates) - len(set(dates))
    blockers: list[str] = []
    warnings = [] if historically_adjusted else [
        "Daily OHLCV is raw and not adjusted for historical splits or cash dividends."
    ]
    if invalid_ohlc:
        blockers.append(f"{invalid_ohlc} daily bars fail OHLC consistency checks.")
    if duplicate_bars:
        blockers.append(f"{duplicate_bars} duplicate daily bars were detected.")
    if discontinuities:
        blockers.append("A corporate-action-scale price discontinuity was detected; verify split adjustment before scoring.")
    if zero_volume:
        warnings.append(f"{zero_volume} daily bars have zero reported volume.")
    if age_days > 7:
        warnings.append(f"The latest daily bar is {age_days} calendar days old.")
    if len(bars) < 60:
        warnings.append(f"Only {len(bars)} daily bars are cached; 60 are required for decisions.")
    if calendar_gaps:
        warnings.append(f"{len(calendar_gaps)} calendar gap(s) contain more than two unobserved weekdays.")
    score = max(0, 100 - invalid_ohlc * 20 - duplicate_bars * 20 - len(discontinuities) * 45
                - min(15, zero_volume * 2) - (20 if age_days > 7 else 0)
                - (15 if len(bars) < 60 else 0) - min(15, len(calendar_gaps) * 5))
    status = "blocked" if blockers else "caution" if warnings else "ready"
    return {
        "status": status,
        "score": score,
        "decision_eligible": not blockers and age_days <= 7 and len(bars) >= 60,
        "observations": len(bars),
        "latest_age_days": age_days,
        "invalid_ohlc_bars": invalid_ohlc,
        "zero_volume_bars": zero_volume,
        "suspected_corporate_actions": discontinuities,
        "calendar_gaps": calendar_gaps,
        "duplicate_bars": duplicate_bars,
        "price_adjustment": "historically_adjusted" if historically_adjusted else "raw",
        "checks": [
            {"label": "OHLC consistency", "status": "pass" if not invalid_ohlc else "blocked"},
            {"label": "Duplicate dates", "status": "pass" if not duplicate_bars else "blocked"},
            {"label": "Corporate-action handling", "status": "blocked" if discontinuities else "pass" if historically_adjusted else "warning"},
            {"label": "Freshness", "status": "pass" if age_days <= 7 else "warning"},
            {"label": "History depth", "status": "pass" if len(bars) >= 60 else "warning"},
            {"label": "Calendar continuity", "status": "pass" if not calendar_gaps else "warning"},
            {"label": "Reported volume", "status": "pass" if not zero_volume else "warning"},
        ],
        "blockers": blockers,
        "warnings": warnings,
        "scope": (
            "Checks cached historically adjusted daily prices for structural errors, freshness, depth, gaps, volume, and remaining discontinuities before scoring."
            if historically_adjusted else
            "Checks cached raw daily bars for structural errors, freshness, depth, gaps, volume, and split-scale discontinuities before scoring."
        ),
    }


def compare_prices(
    daily_close: Any, observed_price: Any, *, warning_percent: Decimal = Decimal("3")
) -> dict[str, Any]:
    daily = _decimal(daily_close)
    observed = _decimal(observed_price)
    if daily is None or observed is None or daily <= 0 or observed <= 0:
        return {"status": "unavailable", "deviation_percent": None}
    deviation = abs(observed / daily - 1) * 100
    return {
        "status": "warning" if deviation > warning_percent else "pass",
        "deviation_percent": format(deviation.quantize(Decimal("0.01")), "f"),
        "daily_close": format(daily.normalize(), "f"),
        "observed_price": format(observed.normalize(), "f"),
        "warning_threshold_percent": format(warning_percent, "f"),
        "scope": "Compares the cached end-of-day close with the latest observed IEX trade; session moves can be legitimate.",
    }


def intraday_coverage(
    timestamps: Iterable[datetime], *, session_date: date, as_of: datetime | None = None
) -> dict[str, Any]:
    observed = {
        item.replace(second=0, microsecond=0)
        for item in timestamps
        if item.date() == session_date and time(9, 30) <= item.time().replace(tzinfo=None) < time(16, 0)
    }
    current = as_of or (max(observed) if observed else None)
    if current is None:
        return {"status": "missing", "observed_minutes": 0, "expected_minutes": 0, "missing_minutes": 0, "coverage_percent": "0.00"}
    historical = session_date < current.date()
    if historical or current.time().replace(tzinfo=None) >= time(16, 0):
        expected = 390
    elif current.time().replace(tzinfo=None) < time(9, 30):
        expected = 0
    else:
        expected = min(390, max(1, (current.hour * 60 + current.minute) - (9 * 60 + 30) + 1))
    count = len(observed)
    missing = max(0, expected - count)
    coverage = Decimal(count) * 100 / Decimal(expected) if expected else Decimal(0)
    return {
        "status": "ready" if expected and coverage >= 95 else "partial" if count else "missing",
        "observed_minutes": count,
        "expected_minutes": expected,
        "missing_minutes": missing,
        "coverage_percent": format(coverage.quantize(Decimal("0.01")), "f"),
    }


def option_snapshot_quality(contracts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = list(contracts)
    quoted = 0
    crossed = 0
    wide = 0
    liquid = 0
    for item in items:
        bid = _decimal(item.get("bid"))
        ask = _decimal(item.get("ask"))
        spread = _decimal(item.get("spread_percent"))
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            quoted += 1
            if ask < bid:
                crossed += 1
        if spread is not None and spread > 20:
            wide += 1
        liquid += bool(item.get("liquid"))
    return {
        "status": "blocked" if crossed else "ready" if items and liquid else "partial" if items else "missing",
        "contracts": len(items),
        "quoted_contracts": quoted,
        "liquid_contracts": liquid,
        "crossed_markets": crossed,
        "wide_spreads": wide,
        "quoted_percent": format((Decimal(quoted) * 100 / Decimal(len(items))).quantize(Decimal("0.01")), "f") if items else "0.00",
        "scope": "Checks indicative option snapshots for usable quotes, crossed markets, spread width, and the existing liquidity rule.",
    }

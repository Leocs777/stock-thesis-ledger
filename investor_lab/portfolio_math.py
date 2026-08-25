"""Pure portfolio ledger calculations shared by the API and tests."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping


SCALE_INT = 1_000_000


def asset_multiplier(asset_type: str) -> int:
    return 100 if asset_type == "option" else 1


def position_value_micros(
    quantity_micros: int, price_micros: int, asset_type: str
) -> int:
    value = (
        Decimal(quantity_micros)
        * Decimal(price_micros)
        * Decimal(asset_multiplier(asset_type))
        / Decimal(SCALE_INT)
    )
    return int(value.to_integral_value())


def calculate_positions(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, int | str]]:
    positions: dict[tuple[str, str], dict[str, int | str]] = {}
    for row in rows:
        symbol = str(row["symbol"])
        asset_type = str(row["asset_type"])
        key = (symbol, asset_type)
        state = positions.setdefault(
            key,
            {
                "symbol": symbol,
                "asset_type": asset_type,
                "quantity_micros": 0,
                "average_cost_micros": 0,
                "realized_pnl_micros": 0,
            },
        )
        quantity = int(row["quantity_micros"])
        price = int(row["price_micros"])
        held = int(state["quantity_micros"])
        average = int(state["average_cost_micros"])
        if row["side"] == "buy":
            total_quantity = held + quantity
            state["average_cost_micros"] = round(
                (held * average + quantity * price) / total_quantity
            )
            state["quantity_micros"] = total_quantity
            continue
        if quantity > held:
            raise RuntimeError(f"Ledger contains an oversell for {symbol} {asset_type}.")
        state["realized_pnl_micros"] = int(state["realized_pnl_micros"]) + round(
            quantity
            * (price - average)
            * asset_multiplier(asset_type)
            / SCALE_INT
        )
        state["quantity_micros"] = held - quantity
        if state["quantity_micros"] == 0:
            state["average_cost_micros"] = 0
    return positions

"""Deterministic support, resistance, and decision-level helpers."""
from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _levels(values: Any) -> list[float]:
    if not isinstance(values, (list, tuple)):
        return []
    result = {_number(value) for value in values}
    return sorted(value for value in result if value is not None)


def select_key_levels(ticker: str, data: dict | None) -> dict[str, Any]:
    """Select the closest supplied support, resistance, and decision level.

    The function never invents a price. It prefers a support below current price
    and resistance above current price, then falls back to the nearest supplied
    level when the source data is incomplete.
    """
    data = data or {}
    price = _number(data.get("current_price"))
    supports = _levels(data.get("supports"))
    resistances = _levels(data.get("resistances"))

    below = [level for level in supports if price is None or level <= price]
    above = [level for level in resistances if price is None or level >= price]
    support = max(below) if below else (supports[-1] if supports else None)
    resistance = min(above) if above else (resistances[0] if resistances else None)

    level_to_watch = None
    reason = "unavailable"
    if support is not None and resistance is not None and price is not None:
        if abs(price - support) <= abs(resistance - price):
            level_to_watch = support
            reason = "support hold"
        else:
            level_to_watch = resistance
            reason = "resistance breakout"
    elif support is not None:
        level_to_watch = support
        reason = "support hold"
    elif resistance is not None:
        level_to_watch = resistance
        reason = "resistance breakout"

    return {
        "ticker": str(ticker),
        "current_price": price,
        "support": support,
        "resistance": resistance,
        "level_to_watch": level_to_watch,
        "level_to_watch_reason": reason,
    }


def _price(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "N/A"


def format_key_levels(ticker: str, data: dict | None = None,
                      levels: dict | None = None) -> str:
    """Format explicit labels for narration, descriptions, and logs."""
    selected = levels or select_key_levels(ticker, data)
    reason = selected.get("level_to_watch_reason", "unavailable")
    reason_text = f" ({reason})" if selected.get("level_to_watch") is not None else ""
    return (
        f"{ticker} | Support: {_price(selected.get('support'))} | "
        f"Resistance: {_price(selected.get('resistance'))} | "
        f"Level to watch: {_price(selected.get('level_to_watch'))}{reason_text}"
    )


def build_key_levels(stock_data: dict | None) -> dict[str, dict[str, Any]]:
    """Build the same level view for every ticker in a data snapshot."""
    return {
        str(ticker): select_key_levels(str(ticker), data)
        for ticker, data in (stock_data or {}).items()
    }


def level_value_in_text(text: str, value: float | None) -> bool:
    """Return whether a numeric level is present without requiring `$` syntax."""
    if value is None:
        return True
    normalized = str(text or "").replace(",", "")
    token = f"{value:.2f}"
    short_token = token.rstrip("0").rstrip(".")
    import re
    return bool(
        re.search(rf"(?<!\d){re.escape(token)}(?!\d)", normalized)
        or re.search(rf"(?<!\d){re.escape(short_token)}(?:\.0+)?(?!\d)", normalized)
    )


def required_level_block(ticker: str, data: dict | None = None,
                         levels: dict | None = None) -> str:
    """Return a concise spoken block that explains every required label."""
    selected = levels or select_key_levels(ticker, data)
    support = _price(selected.get("support"))
    resistance = _price(selected.get("resistance"))
    watch = _price(selected.get("level_to_watch"))
    return (
        f"{ticker} key levels: Support: {support}, meaning a price floor. "
        f"Resistance: {resistance}, meaning a price ceiling. "
        f"Level to watch: {watch}, because that is the decision point for this setup."
    )

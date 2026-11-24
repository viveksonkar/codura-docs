"""Money arithmetic for billing (specs/billing.md §1). Pure: no I/O.

Prices are micros of the currency unit; invoice amounts are minor units (cents / paise).
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Currency = Literal["USD", "INR"]
CURRENCIES: tuple[Currency, ...] = ("USD", "INR")
SYMBOL = {"USD": "$", "INR": "₹"}
MICROS_PER_MINOR = 10_000  # 1 cent = 10_000 micro-dollars; same for paise


def currency_for_country(country: str) -> Currency:
    """INR for India, USD for the rest of the world (ADR-0026 §1)."""
    return "INR" if country.upper() == "IN" else "USD"


def to_minor(quantity: Decimal | float | int, unit_micros: int) -> int:
    """Round-half-up once per line: quantity × unit price → minor units."""
    value = Decimal(str(quantity)) * Decimal(unit_micros) / Decimal(MICROS_PER_MINOR)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def micros_to_minor(micros: int | Decimal) -> int:
    return int((Decimal(micros) / MICROS_PER_MINOR).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def days_in_month(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def remaining_fraction(today: date) -> Decimal:
    """Share of the calendar month left, counting today: (D − d + 1) / D."""
    total = days_in_month(today)
    return Decimal(total - today.day + 1) / Decimal(total)


def proration_minor(old_base_micros: int, new_base_micros: int, today: date) -> int:
    """Upgrade charge for the rest of the month; never negative (downgrades apply at the roll)."""
    delta = max(0, new_base_micros - old_base_micros)
    return micros_to_minor(Decimal(delta) * remaining_fraction(today))


def to_usd_micros(amount_minor: int, currency: Currency, fx_inr_micros_per_usd: int) -> int:
    """Revenue in micro-USD for margins (COGS is micro-USD)."""
    micros = amount_minor * MICROS_PER_MINOR
    if currency == "USD":
        return micros
    if fx_inr_micros_per_usd <= 0:
        return 0
    return int(Decimal(micros) * Decimal(1_000_000) / Decimal(fx_inr_micros_per_usd))


def format_minor(amount_minor: int, currency: Currency) -> str:
    sign = "-" if amount_minor < 0 else ""
    value = Decimal(abs(amount_minor)) / 100
    return f"{sign}{SYMBOL[currency]}{value:,.2f}"

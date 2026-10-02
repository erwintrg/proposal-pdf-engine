"""Money math for the investment page, the deposit invoice and the Stripe link.

Everything runs on Decimal with commercial rounding (half up) to the cent, never on floats.
The deposit is computed from the net amount, then VAT is added on top. That way the deposit
invoice shows a correct net / VAT / gross split, and deposit + balance always add up to the
gross total to the cent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

CENT = Decimal("0.01")
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
# Stripe amounts are in the currency's minor unit; every supported currency has two decimals.
MINOR_UNIT_FACTOR = {"EUR": 100, "USD": 100, "GBP": 100, "CHF": 100}


def dec(value: float | int | str | Decimal) -> Decimal:
    """Exact Decimal from a JSON number: str() first, so 0.19 stays 0.19 and not 0.18999..."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Line:
    name: str
    unit_price: Decimal
    qty: int
    subtotal: Decimal


@dataclass(frozen=True)
class Totals:
    lines: tuple[Line, ...]
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    gross: Decimal
    deposit_pct: Decimal
    deposit_net: Decimal
    deposit_vat: Decimal
    deposit_gross: Decimal
    balance_net: Decimal
    balance_vat: Decimal
    balance_gross: Decimal


def compute_totals(items: Iterable, vat_rate: float | Decimal, deposit_pct: float | Decimal) -> Totals:
    """items: objects with .name, .price and .qty (schema.LineItem)."""
    lines = tuple(
        Line(item.name, cents(dec(item.price)), int(item.qty), cents(dec(item.price) * int(item.qty)))
        for item in items
    )
    rate = dec(vat_rate)
    pct = dec(deposit_pct)
    if not lines:
        raise ValueError("at least one line item is needed")
    if not (Decimal(0) <= rate < Decimal(1)):
        raise ValueError(f"VAT rate must be between 0 and 1, got {rate}")
    if not (Decimal(0) < pct <= Decimal(100)):
        raise ValueError(f"deposit percentage must be above 0 and at most 100, got {pct}")

    net = sum((line.subtotal for line in lines), Decimal("0.00"))
    vat = cents(net * rate)
    gross = net + vat
    deposit_net = cents(net * pct / 100)
    deposit_vat = cents(deposit_net * rate)
    deposit_gross = deposit_net + deposit_vat
    return Totals(
        lines=lines,
        net=net,
        vat_rate=rate,
        vat=vat,
        gross=gross,
        deposit_pct=pct,
        deposit_net=deposit_net,
        deposit_vat=deposit_vat,
        deposit_gross=deposit_gross,
        balance_net=net - deposit_net,
        balance_vat=vat - deposit_vat,
        balance_gross=gross - deposit_gross,
    )


def totals_for(proposal) -> Totals:
    """Totals for a schema.Proposal."""
    return compute_totals(proposal.pricing.items, proposal.vat.rate, proposal.payment_terms.deposit_pct)


def format_money(amount: Decimal, currency: str) -> str:
    amount = cents(dec(amount))
    sign = "-" if amount < 0 else ""
    number = f"{abs(amount):,.2f}"
    symbol = SYMBOLS.get(currency)
    return f"{sign}{symbol}{number}" if symbol else f"{sign}{currency} {number}"


def format_pct(pct: float | Decimal) -> str:
    """50 -> '50%', 33.5 -> '33.5%'."""
    text = format(dec(pct).normalize(), "f")
    return f"{text}%"


def to_minor_units(amount: Decimal, currency: str) -> int:
    """Amount for payment APIs (Stripe): 2856.00 EUR -> 285600."""
    factor = MINOR_UNIT_FACTOR.get(currency)
    if factor is None:
        raise ValueError(f"unsupported currency for payment links: {currency}")
    return int((cents(dec(amount)) * factor).to_integral_value(rounding=ROUND_HALF_UP))

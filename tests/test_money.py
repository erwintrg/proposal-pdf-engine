from decimal import Decimal
from itertools import product
from types import SimpleNamespace as Item

import pytest

from proposal_engine.money import cents, compute_totals, format_money, format_pct, to_minor_units, totals_for

D = Decimal


def test_sample_totals(sample):
    t = totals_for(sample)
    assert (t.net, t.vat, t.gross) == (D("4800.00"), D("912.00"), D("5712.00"))
    assert (t.deposit_net, t.deposit_vat, t.deposit_gross) == (D("2400.00"), D("456.00"), D("2856.00"))
    assert t.balance_gross == D("2856.00")


def test_quantities_and_several_items():
    t = compute_totals([Item(name="Build", price=1200, qty=2), Item(name="Training", price=350.5, qty=3)], 0, 30)
    assert [line.subtotal for line in t.lines] == [D("2400.00"), D("1051.50")]
    assert t.net == t.gross == D("3451.50")
    assert t.deposit_gross == D("1035.45")
    assert t.balance_gross == D("2416.05")


def test_commercial_rounding_half_up():
    t = compute_totals([Item(name="x", price=0.05, qty=1)], 0.19, 50)
    assert t.vat == D("0.01")            # 0.0095 rounds up
    assert t.deposit_net == D("0.03")    # 0.025 rounds up
    assert t.balance_net == D("0.02")


@pytest.mark.parametrize(
    "price, rate, pct",
    list(product([0.01, 0.05, 99.99, 1234.56, 4800, 9999.99], [0, 0.07, 0.19, 0.2], [10, 33, 33.33, 50, 100])),
)
def test_deposit_and_balance_always_add_up_to_the_cent(price, rate, pct):
    t = compute_totals([Item(name="x", price=price, qty=3)], rate, pct)
    assert t.deposit_net + t.balance_net == t.net
    assert t.deposit_vat + t.balance_vat == t.vat
    assert t.deposit_gross + t.balance_gross == t.gross
    for value in (t.net, t.vat, t.gross, t.deposit_net, t.deposit_vat, t.deposit_gross, t.balance_gross):
        assert value.as_tuple().exponent == -2, value
    assert t.balance_gross >= 0


def test_deposit_comes_from_net_so_the_invoice_split_is_exact():
    # Net 1001.50 + 19% VAT 190.29 = 1191.79. 33% of the gross would be 393.29, an amount that cannot be
    # written as a net line plus 19% VAT. Net first: 330.50 + 62.80 = 393.30, and the invoice adds up.
    t = compute_totals([Item(name="x", price=1001.50, qty=1)], 0.19, 33)
    assert (t.net, t.vat, t.gross) == (D("1001.50"), D("190.29"), D("1191.79"))
    assert (t.deposit_net, t.deposit_vat, t.deposit_gross) == (D("330.50"), D("62.80"), D("393.30"))
    assert t.deposit_vat == cents(t.deposit_net * D("0.19"))


def test_float_trap_in_minor_units():
    assert int(0.29 * 100) == 28                       # the bug Decimal avoids
    assert to_minor_units(D("0.29"), "EUR") == 29
    assert to_minor_units(D("2856.00"), "EUR") == 285600
    assert to_minor_units(D("1035.45"), "USD") == 103545
    with pytest.raises(ValueError):
        to_minor_units(D("10"), "JPY")


@pytest.mark.parametrize("amount, currency, text", [
    (D("4800"), "EUR", "€4,800.00"),
    (D("1200.5"), "USD", "$1,200.50"),
    (D("0.99"), "GBP", "£0.99"),
    (D("1234.5"), "CHF", "CHF 1,234.50"),
    (D("-5"), "EUR", "-€5.00"),
])
def test_format_money(amount, currency, text):
    assert format_money(amount, currency) == text


def test_format_pct():
    assert [format_pct(v) for v in (50, 33.5, 12.25, 100.0)] == ["50%", "33.5%", "12.25%", "100%"]


def test_invalid_inputs_are_refused():
    with pytest.raises(ValueError):
        compute_totals([], 0, 50)
    with pytest.raises(ValueError):
        compute_totals([Item(name="x", price=10, qty=1)], 1.0, 50)
    with pytest.raises(ValueError):
        compute_totals([Item(name="x", price=10, qty=1)], 0, 0)

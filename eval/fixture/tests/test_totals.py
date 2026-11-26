from decimal import Decimal

from invoice_tools.parser import parse
from invoice_tools.totals import apply_discount, invoice_totals


def test_invoice_totals(sample):
    totals = invoice_totals(parse(sample))
    assert totals["INV-1"] == Decimal("42.00")
    assert totals["INV-2"] == Decimal("450.00")


def test_discount():
    assert apply_discount(Decimal("100.00"), 10) == Decimal("90.00")

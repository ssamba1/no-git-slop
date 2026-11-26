"""Invoice totals."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def line_total(line):
    net = line.unit_price * line.quantity
    return (net * (1 + line.tax_rate)).quantize(CENT, rounding=ROUND_HALF_UP)


def invoice_totals(lines):
    totals = defaultdict(Decimal)
    for line in lines:
        totals[line.invoice] += line_total(line)
    return dict(totals)


def apply_discount(total, percent):
    return total - total * Decimal(percent) / 100


def customer_totals(lines):
    totals = {}
    for line in lines:
        totals[line.customer] = line_total(line)
    return totals

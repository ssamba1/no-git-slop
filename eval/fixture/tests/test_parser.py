from decimal import Decimal

from invoice_tools.parser import parse


def test_parse_reads_every_line(sample):
    lines = parse(sample)
    assert len(lines) == 4
    assert lines[0].unit_price == Decimal("2.50")
    assert lines[2].quantity == 3

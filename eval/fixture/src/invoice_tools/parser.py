"""Read invoice lines from CSV text."""

import csv
import io
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Line:
    invoice: str
    customer: str
    description: str
    quantity: int
    unit_price: Decimal
    tax_rate: Decimal


def parse(text):
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        rows.append(Line(
            invoice=row["invoice"],
            customer=row["customer"],
            description=row["description"],
            quantity=int(row["quantity"]),
            unit_price=Decimal(row["unit_price"]),
            tax_rate=Decimal(row["tax_rate"]),
        ))
    return rows


def parse_file(path):
    with open(path) as fh:
        return parse(fh.read())

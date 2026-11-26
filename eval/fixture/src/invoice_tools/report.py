"""Plain-text summary report."""

from .totals import customer_totals, invoice_totals


def render(lines):
    out = []
    inv = invoice_totals(lines)
    out.append(f"{len(inv)} invoices")
    for number, total in sorted(inv.items()):
        out.append(f"  {number}: {total}")
    out.append("By customer:")
    for name, total in sorted(customer_totals(lines).items()):
        out.append(f"  {name}: {total}")
    return "\n".join(out)

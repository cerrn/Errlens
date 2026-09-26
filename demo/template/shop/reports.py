"""Daily sales report."""
from collections import defaultdict
from datetime import date, datetime

from shop.api import fetch_orders


def parse_order_date(value: str) -> date:
    """Parses the date string coming from the API into a date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def daily_totals(orders):
    totals = defaultdict(float)
    for order in orders:
        day = parse_order_date(order["created_at"])
        totals[day] += order["total"]
    return dict(sorted(totals.items()))


def build_report():
    orders = fetch_orders()
    lines = ["Daily sales report", "-" * 24]
    for day, total in daily_totals(orders).items():
        lines.append(f"{day.isoformat()}  {total:>10.2f} USD")
    return "\n".join(lines)

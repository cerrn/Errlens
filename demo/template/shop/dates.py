"""Date helpers: the date format used in API responses."""
from datetime import datetime


def format_order_date(dt: datetime) -> str:
    """Formats an order date for an API response."""
    return dt.strftime("%Y-%m-%d")

"""In-memory fake database."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class User:
    id: int
    name: str
    email: str
    active: bool = True


@dataclass
class Order:
    id: int
    user_id: int
    total: float
    created: datetime = field(default_factory=datetime.now)


USERS = {
    1: User(1, "Alice Johnson", "alice@example.com"),
    2: User(2, "Bob Smith", "bob@example.com"),
    3: User(3, "Carol White", "carol@example.com", active=False),
}

ORDERS = [
    Order(101, 1, 249.90, datetime(2026, 9, 20, 14, 31, 5)),
    Order(102, 2, 89.50, datetime(2026, 9, 21, 9, 12, 44)),
    Order(103, 1, 1200.00, datetime(2026, 9, 22, 18, 3, 10)),
]


def get_user(user_id):
    return USERS[user_id]


def list_orders():
    return list(ORDERS)

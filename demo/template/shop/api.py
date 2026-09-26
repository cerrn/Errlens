"""API layer: turns models into JSON-friendly dicts."""
from shop import db
from shop.dates import format_order_date


def serialize_user(user):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "active": user.active,
    }


def serialize_order(order):
    return {
        "id": order.id,
        "user_id": order.user_id,
        "total": round(order.total, 2),
        "created_at": format_order_date(order.created),
    }


def fetch_user(user_id):
    """Mimics the GET /users/{id} response."""
    return serialize_user(db.get_user(user_id))


def fetch_orders():
    """Mimics the GET /orders response."""
    return [serialize_order(o) for o in db.list_orders()]

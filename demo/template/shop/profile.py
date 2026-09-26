"""Prepares user data for the profile page."""
from shop.api import fetch_user


def display_name(user_data):
    name = user_data["name"].strip()
    if not user_data.get("active", True):
        return f"{name} (inactive)"
    return name


def mask_email(email):
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[0]}*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def get_profile(user_id):
    user_data = fetch_user(user_id)
    return {
        "id": user_data["id"],
        "display_name": display_name(user_data),
        "email": mask_email(user_data["email"]),
    }

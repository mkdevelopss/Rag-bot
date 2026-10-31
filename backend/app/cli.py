import argparse
import getpass
import re
import secrets
import sqlite3
import sys

from app.core.config import get_settings
from app.services.authentication import hash_password
from app.services.database import Database
from app.services.runtime_settings import RuntimeSettings
from app.services.settings_security import hash_settings_password


def create_admin() -> int:
    settings = get_settings()
    database = Database(settings.resolve_path(settings.database_path))
    database.initialize()
    if database.count_admins() or database.list_users():
        print("An administrator or managed account already exists; use the existing workspace.")
        return 1

    email = input("Administrator email: ").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        print("Enter a valid email address.", file=sys.stderr)
        return 1
    display_name = input("Administrator name: ").strip()
    if not display_name:
        print("Administrator name cannot be empty.", file=sys.stderr)
        return 1

    password = getpass.getpass("Administrator password (at least 12 characters): ")
    if len(password) < 12:
        print("Administrator password must contain at least 12 characters.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Confirm administrator password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1

    try:
        database.create_user(
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            role="admin",
            must_change_password=False,
        )
        RuntimeSettings(settings, database).write_env_updates(
            {"SETTINGS_PASSWORD_HASH": hash_settings_password(password)}
        )
    except sqlite3.IntegrityError:
        print("An account with that email already exists.", file=sys.stderr)
        return 1
    print("Administrator account created. You can now sign in to Inkwell.")
    return 0


def generate_admin_key() -> int:
    settings = get_settings()
    database = Database(settings.resolve_path(settings.database_path))
    database.initialize()
    if any(user["role"] == "admin" and user["is_active"] for user in database.list_users()):
        print("An administrator already exists; the bootstrap key is disabled.")
        return 1
    settings_password_hash = settings.settings_password_hash
    if not settings_password_hash:
        settings_password = getpass.getpass(
            "Choose a separate provider-settings password (at least 12 characters): "
        )
        if len(settings_password) < 12:
            print("The provider-settings password must contain at least 12 characters.", file=sys.stderr)
            return 1
        if settings_password != getpass.getpass("Confirm provider-settings password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        settings_password_hash = hash_settings_password(settings_password)
    key = secrets.token_urlsafe(32)
    updates = {"ADMIN_BOOTSTRAP_KEY_HASH": hash_password(key)}
    if not settings.settings_password_hash:
        updates["SETTINGS_PASSWORD_HASH"] = settings_password_hash
    RuntimeSettings(settings, database).write_env_updates(updates)
    print("Copy this one-time administrator key into Settings in your browser:")
    print(key)
    print("This key will stop working after the first successful admin claim.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inkwell account administration")
    parser.add_argument("command", choices=["create-admin", "generate-admin-key"])
    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin()
    if args.command == "generate-admin-key":
        return generate_admin_key()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""
App-wide settings (Phase 5's `app_settings` key/value table).
Currently just the Owner PIN, hashed - never stored in plain text.
"""

import hashlib
import os
import sqlite3
from typing import Optional

_PIN_HASH_KEY = "owner_pin_hash"
_PIN_SALT_KEY = "owner_pin_salt"
_PBKDF2_ITERATIONS = 200_000


class SettingsError(Exception):
    pass


def _get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    cur = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PBKDF2_ITERATIONS).hex()


def is_owner_pin_set(conn: sqlite3.Connection) -> bool:
    return _get_setting(conn, _PIN_HASH_KEY) is not None


def set_owner_pin(conn: sqlite3.Connection, pin: str) -> None:
    if not pin or len(pin) < 4:
        raise SettingsError("PIN must be at least 4 characters")
    salt = os.urandom(16)
    pin_hash = _hash_pin(pin, salt)
    _set_setting(conn, _PIN_SALT_KEY, salt.hex())
    _set_setting(conn, _PIN_HASH_KEY, pin_hash)


def verify_owner_pin(conn: sqlite3.Connection, pin: str) -> bool:
    stored_hash = _get_setting(conn, _PIN_HASH_KEY)
    stored_salt_hex = _get_setting(conn, _PIN_SALT_KEY)
    if stored_hash is None or stored_salt_hex is None:
        return False
    salt = bytes.fromhex(stored_salt_hex)
    candidate_hash = _hash_pin(pin, salt)
    return candidate_hash == stored_hash

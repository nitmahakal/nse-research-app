"""
SQLite price storage. Chaquopy's sqlite3 is Python's stdlib module -
no extra native package needed, which is exactly why we moved off
parquet (pyarrow isn't available for Android).
"""

import sqlite3
from typing import List, Tuple

import pandas as pd


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            close  REAL NOT NULL,
            PRIMARY KEY (symbol, date)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices(symbol)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            timeframe TEXT NOT NULL,
            condition_set_json TEXT NOT NULL,
            is_locked INTEGER NOT NULL DEFAULT 0,
            is_tracked INTEGER NOT NULL DEFAULT 0,
            scan_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()


def insert_price_rows(conn: sqlite3.Connection, symbol: str, rows: List[Tuple[str, float]]) -> None:
    """rows: list of (date_iso_string, close_float). Upserts (replace on conflict)."""
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, date, close) VALUES (?, ?, ?)",
        [(symbol, d, c) for d, c in rows],
    )
    conn.commit()


def list_available_symbols(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol")
    return [row[0] for row in cur.fetchall()]


def get_price_series(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    """Returns a DataFrame with columns [Date, Close], sorted ascending."""
    df = pd.read_sql_query(
        "SELECT date AS Date, close AS Close FROM prices WHERE symbol = ? ORDER BY date ASC",
        conn,
        params=(symbol,),
    )
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"])
    df["Close"] = pd.to_numeric(df["Close"])
    return df


def row_count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM prices")
    return cur.fetchone()[0]

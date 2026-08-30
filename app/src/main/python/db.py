"""SQLite storage for prices, scans, settings, and now signals."""
import sqlite3
from typing import List, Tuple
import pandas as pd


def get_connection(db_path: str) -> sqlite3.Connection:
    # timeout=30: wait up to 30s for a lock instead of failing instantly
    # ("database is locked") - needed because a background download
    # and a foreground scan can both touch the DB at overlapping times.
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS prices (
            symbol TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
            PRIMARY KEY (symbol, date))"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_symbol ON prices(symbol)")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS scans (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            timeframe TEXT NOT NULL,
            condition_set_json TEXT NOT NULL,
            is_locked INTEGER NOT NULL DEFAULT 0,
            is_tracked INTEGER NOT NULL DEFAULT 0,
            scan_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT)"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS signals (
            signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            scan_version INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_date TEXT,
            exit_price REAL,
            current_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            periods_elapsed INTEGER NOT NULL DEFAULT 0,
            is_matured INTEGER NOT NULL DEFAULT 0,
            return_pct REAL NOT NULL DEFAULT 0.0,
            max_gain_pct REAL NOT NULL DEFAULT 0.0,
            max_drawdown_pct REAL NOT NULL DEFAULT 0.0)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_scan ON signals(scan_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)")

    conn.execute(
        """CREATE TABLE IF NOT EXISTS rating_history (
            rating_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            scan_version INTEGER NOT NULL,
            calculated_at TEXT NOT NULL,
            matured_signal_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_gain_pct REAL NOT NULL,
            avg_drawdown_pct REAL NOT NULL,
            rating_score REAL NOT NULL,
            confidence REAL NOT NULL,
            UNIQUE(scan_id, calculated_at))"""
    )
    conn.commit()


def insert_price_rows(conn, symbol, rows):
    conn.executemany(
        "INSERT OR REPLACE INTO prices (symbol, date, close) VALUES (?, ?, ?)",
        [(symbol, d, c) for d, c in rows],
    )
    conn.commit()


def list_available_symbols(conn) -> List[str]:
    cur = conn.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol")
    return [row[0] for row in cur.fetchall()]


def get_price_series(conn, symbol) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT date AS Date, close AS Close FROM prices WHERE symbol = ? ORDER BY date ASC",
        conn, params=(symbol,),
    )
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"])
    df["Close"] = pd.to_numeric(df["Close"])
    return df


def row_count(conn) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM prices")
    return cur.fetchone()[0]

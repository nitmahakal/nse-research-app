"""
Research Engine - signal tracking.

Turns Scanner matches into rows in the `signals` table, with the
duplicate-prevention rule we agreed on: if a scan already has an
OPEN signal for a symbol, running the scan again must NOT create a
second row for the same ongoing event (e.g. re-matching the same
EMA-cross day after day while price stays above the EMA).
"""

import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from scanner import ScanSummary


@dataclass
class NewSignalResult:
    logged: List[str]
    skipped_duplicate: List[str]


def get_open_signal_id(conn: sqlite3.Connection, scan_id: int, symbol: str) -> Optional[int]:
    cur = conn.execute(
        "SELECT signal_id FROM signals WHERE scan_id = ? AND symbol = ? AND status = 'ACTIVE'",
        (scan_id, symbol),
    )
    row = cur.fetchone()
    return row[0] if row else None


def log_new_signal(
    conn: sqlite3.Connection,
    scan_id: int,
    scan_version: int,
    symbol: str,
    timeframe: str,
    entry_date: str,
    entry_price: float,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO signals
            (scan_id, scan_version, symbol, timeframe, entry_date, entry_price,
             current_price, status, periods_elapsed, is_matured,
             return_pct, max_gain_pct, max_drawdown_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, 0, 0.0, 0.0, 0.0)
        """,
        (scan_id, scan_version, symbol, timeframe, entry_date, entry_price, entry_price),
    )
    conn.commit()
    return cur.lastrowid


def track_new_matches(
    conn: sqlite3.Connection, scan_id: int, scan_version: int, summary: ScanSummary
) -> NewSignalResult:
    """
    For every match in a ScanSummary, log a new signal UNLESS this
    (scan_id, symbol) already has an open signal - in which case it's
    the same ongoing event and is skipped, not duplicated.
    """
    logged: List[str] = []
    skipped: List[str] = []

    for match in summary.matches:
        existing_id = get_open_signal_id(conn, scan_id, match.symbol)
        if existing_id is not None:
            skipped.append(match.symbol)
            continue
        log_new_signal(
            conn, scan_id, scan_version, match.symbol, match.timeframe, match.date, match.close
        )
        logged.append(match.symbol)

    return NewSignalResult(logged=logged, skipped_duplicate=skipped)

"""
Research Engine - daily mark-to-market update.

For every OPEN signal:
    - current_price = latest available daily close (freshest price,
      regardless of the signal's own timeframe)
    - return_pct / max_gain_pct / max_drawdown_pct recomputed from the
      FULL daily price history since entry_date (not incrementally
      accumulated), so it's always correct even if updates are run
      irregularly or skipped for a few days
    - periods_elapsed counted using the Scanner's own timeframe engine
      (real trading bars, holiday-safe) - this is what the 6-period
      maturation rule checks
    - exit condition auto-derived from the scan's entry condition
      (Cross Above/Below -> hold-state; Greater/Less/Near -> same
      condition flipping) - if it's no longer true, the signal closes

KNOWN SIMPLIFICATION: exit-condition derivation always uses the scan's
CURRENT condition_set_json, not a preserved snapshot of the exact
version the signal opened under. If Nk edits a Tracked scan's logic
while it has open signals, those signals will be evaluated against
the new logic going forward. A full per-version condition history
table would fix this but isn't needed yet - flagging it here so it's
a deliberate, visible choice rather than a silent gap.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List

import db
from conditions import ConditionEntry, evaluate_condition_set, ConditionError
from timeframes import resample_close, count_periods_between, TimeframeError

MATURATION_PERIODS = 6


@dataclass
class UpdateResult:
    updated: int = 0
    closed: List[str] = None
    matured: List[str] = None
    failed: List[str] = None

    def __post_init__(self):
        self.closed = self.closed or []
        self.matured = self.matured or []
        self.failed = self.failed or []


def _derive_exit_condition_set(condition_set):
    return [
        ConditionEntry(condition=entry.condition.derive_exit_condition(), logic=entry.logic)
        for entry in condition_set
    ]


def _get_scan_condition(conn: sqlite3.Connection, scan_id: int):
    cur = conn.execute("SELECT condition_set_json FROM scans WHERE scan_id = ?", (scan_id,))
    row = cur.fetchone()
    if row is None:
        return None
    import json
    from conditions import ConditionEntry as CE
    return [CE.from_dict(d) for d in json.loads(row[0])]


def update_open_signals(conn: sqlite3.Connection) -> UpdateResult:
    result = UpdateResult()

    cur = conn.execute(
        """
        SELECT signal_id, scan_id, symbol, timeframe, entry_date, entry_price
        FROM signals WHERE status = 'ACTIVE'
        """
    )
    open_signals = cur.fetchall()

    condition_cache = {}

    for signal_id, scan_id, symbol, timeframe, entry_date, entry_price in open_signals:
        try:
            daily_df = db.get_price_series(conn, symbol)
            if daily_df.empty:
                result.failed.append(symbol)
                continue

            since_entry = daily_df[daily_df["Date"] >= entry_date]
            if since_entry.empty:
                result.failed.append(symbol)
                continue

            current_price = float(since_entry["Close"].iloc[-1])
            returns_pct = ((since_entry["Close"] - entry_price) / entry_price) * 100.0
            return_pct = float(returns_pct.iloc[-1])
            max_gain_pct = float(returns_pct.max())
            max_drawdown_pct = float(returns_pct.min())

            latest_date = daily_df["Date"].iloc[-1]
            periods_elapsed = count_periods_between(daily_df, timeframe, entry_date, latest_date)
            is_matured = 1 if periods_elapsed >= MATURATION_PERIODS else 0
            if is_matured and periods_elapsed >= MATURATION_PERIODS:
                result.matured.append(symbol)

            if scan_id not in condition_cache:
                condition_cache[scan_id] = _get_scan_condition(conn, scan_id)
            condition_set = condition_cache[scan_id]

            still_active = True
            if condition_set is not None:
                try:
                    exit_condition_set = _derive_exit_condition_set(condition_set)
                    tf_df = resample_close(daily_df, timeframe)
                    close_series = tf_df["Close"]
                    still_active, _ = evaluate_condition_set(exit_condition_set, close_series)
                except (ConditionError, TimeframeError):
                    still_active = True

            if still_active:
                conn.execute(
                    """
                    UPDATE signals
                    SET current_price = ?, return_pct = ?, max_gain_pct = ?,
                        max_drawdown_pct = ?, periods_elapsed = ?, is_matured = ?
                    WHERE signal_id = ?
                    """,
                    (current_price, return_pct, max_gain_pct, max_drawdown_pct,
                     periods_elapsed, is_matured, signal_id),
                )
            else:
                exit_date = latest_date.date().isoformat() if hasattr(latest_date, "date") else str(latest_date)
                conn.execute(
                    """
                    UPDATE signals
                    SET current_price = ?, return_pct = ?, max_gain_pct = ?,
                        max_drawdown_pct = ?, periods_elapsed = ?, is_matured = ?,
                        status = 'CLOSED', exit_date = ?, exit_price = ?
                    WHERE signal_id = ?
                    """,
                    (current_price, return_pct, max_gain_pct, max_drawdown_pct,
                     periods_elapsed, is_matured, exit_date, current_price, signal_id),
                )
                result.closed.append(symbol)

            result.updated += 1

        except Exception:
            result.failed.append(symbol)
            continue

    conn.commit()
    return result

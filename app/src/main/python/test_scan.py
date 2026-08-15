"""
Milestone 2 demo/test entry point, called from MainActivity.
Seeds a handful of symbols with synthetic daily price data if the
database is empty, then runs one scan (Close > EMA(20) on Daily).
"""

import random
from datetime import datetime, timedelta

import db
from conditions import Condition, ConditionEntry, IndicatorSpec
from scanner import Scanner

TEST_SYMBOLS = ["TESTSTOCK_A", "TESTSTOCK_B", "TESTSTOCK_C", "TESTSTOCK_D"]


def _generate_synthetic_series(symbol: str, num_days: int = 300):
    seed = sum(ord(c) for c in symbol)
    rng = random.Random(seed)

    price = 100.0 + (seed % 50)
    today = datetime.now().date()

    rows = []
    date = today - timedelta(days=num_days)
    while date <= today:
        if date.weekday() < 5:
            drift = rng.uniform(-0.015, 0.017)
            price = max(1.0, price * (1 + drift))
            rows.append((date.isoformat(), round(price, 2)))
        date += timedelta(days=1)
    return rows


def _seed_if_empty(db_path: str) -> str:
    conn = db.get_connection(db_path)
    try:
        existing = set(db.list_available_symbols(conn))
        newly_seeded = []
        for symbol in TEST_SYMBOLS:
            if symbol not in existing:
                rows = _generate_synthetic_series(symbol)
                db.insert_price_rows(conn, symbol, rows)
                newly_seeded.append(symbol)
        if newly_seeded:
            return f"Seeded test data for: {', '.join(newly_seeded)}"
        return "Test data already present (not re-seeded)."
    finally:
        conn.close()


def run_test_scan(db_path: str) -> str:
    lines = []
    lines.append("Milestone 2 - Scanner Core Test")
    lines.append("================================")

    seed_msg = _seed_if_empty(db_path)
    lines.append(seed_msg)
    lines.append("")

    condition_set = [
        ConditionEntry(
            condition=Condition(
                left=IndicatorSpec(name="Close", params={}),
                comparator="Greater",
                right=IndicatorSpec(name="EMA", params={"length": 20}),
            ),
            logic=None,
        )
    ]

    scanner = Scanner(db_path)
    try:
        summary = scanner.run_scan(condition_set, "Daily")
    finally:
        scanner.close()

    lines.append("Condition: Close Greater EMA(20), Timeframe: Daily")
    lines.append(f"Symbols scanned: {summary.total_symbols}")
    lines.append(f"Matched: {summary.matched_count}")
    lines.append(f"Failed to load: {summary.failed}")
    lines.append("")

    if summary.matches:
        lines.append("Matches:")
        for m in summary.matches:
            values_str = ", ".join(f"{k}={v:.2f}" for k, v in m.values.items())
            lines.append(f"  {m.symbol}  [{m.timeframe}]  Close={m.close:.2f}  {values_str}")
    else:
        lines.append("No matches found.")

    if summary.failed_symbols:
        lines.append("")
        lines.append("Failed symbols:")
        for sym, reason in summary.failed_symbols:
            lines.append(f"  {sym}: {reason}")

    return "\n".join(lines)

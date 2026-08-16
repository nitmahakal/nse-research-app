"""
Kotlin-facing bridge for Milestone 3 (Saved Scans + Owner Mode).
Every function takes/returns plain strings so the Kotlin side stays
simple - no complex object marshalling across the Chaquopy bridge yet.
"""

import db
import scans_repository as scans_repo
import settings_repository as settings_repo
from conditions import Condition, ConditionEntry, IndicatorSpec
from scanner import Scanner

_DEMO_CONDITION_SET = [
    ConditionEntry(
        condition=Condition(
            left=IndicatorSpec(name="Close", params={}),
            comparator="Greater",
            right=IndicatorSpec(name="EMA", params={"length": 20}),
        ),
        logic=None,
    )
]


def save_demo_locked_scan(db_path: str) -> str:
    conn = db.get_connection(db_path)
    try:
        scan_id = scans_repo.save_scan(
            conn,
            name="Momentum Test Scan",
            description="Demo scan: price above its 20-day EMA",
            timeframe="Daily",
            condition_set=_DEMO_CONDITION_SET,
            is_locked=True,
            is_tracked=True,
        )
        return f"Saved 'Momentum Test Scan' (locked) as scan_id={scan_id}"
    except scans_repo.ScanRepositoryError as exc:
        return f"ERROR: {exc}"
    finally:
        conn.close()


def list_saved_scans_report(db_path: str, owner_mode: bool) -> str:
    conn = db.get_connection(db_path)
    try:
        records = scans_repo.list_scans(conn, owner_mode=owner_mode)
        if not records:
            return "No saved scans yet."

        lines = [f"Saved Scans (Owner Mode: {'ON' if owner_mode else 'OFF'})", "=" * 40]
        for r in records:
            lock_tag = "[LOCKED]" if r.is_locked else "[open]"
            track_tag = "[tracked]" if r.is_tracked else ""
            lines.append(f"\n{r.name} {lock_tag} {track_tag} v{r.scan_version}")
            lines.append(f"  Timeframe: {r.timeframe}")
            lines.append(f"  Description: {r.description}")
            lines.append(f"  Condition: {r.summary}")
        return "\n".join(lines)
    finally:
        conn.close()


def run_saved_scan_report(db_path: str, name: str) -> str:
    conn = db.get_connection(db_path)
    try:
        timeframe, condition_set = scans_repo.load_scan_for_execution(conn, name)
    except scans_repo.ScanRepositoryError as exc:
        conn.close()
        return f"ERROR: {exc}"
    conn.close()

    scanner = Scanner(db_path)
    try:
        summary = scanner.run_scan(condition_set, timeframe)
    finally:
        scanner.close()

    lines = [f"Ran saved scan: {name}", f"Timeframe: {timeframe}", ""]
    lines.append(f"Matched: {summary.matched_count} / {summary.total_symbols} scanned")
    if summary.matches:
        for m in summary.matches:
            values_str = ", ".join(f"{k}={v:.2f}" for k, v in m.values.items())
            lines.append(f"  {m.symbol}  [{m.timeframe}]  Close={m.close:.2f}  {values_str}")
    else:
        lines.append("  No matches.")
    return "\n".join(lines)


def set_owner_pin_report(db_path: str, pin: str) -> str:
    conn = db.get_connection(db_path)
    try:
        settings_repo.set_owner_pin(conn, pin)
        return "Owner PIN set successfully."
    except settings_repo.SettingsError as exc:
        return f"ERROR: {exc}"
    finally:
        conn.close()


def verify_owner_pin_check(db_path: str, pin: str) -> bool:
    conn = db.get_connection(db_path)
    try:
        return settings_repo.verify_owner_pin(conn, pin)
    finally:
        conn.close()


def is_owner_pin_set_check(db_path: str) -> bool:
    conn = db.get_connection(db_path)
    try:
        return settings_repo.is_owner_pin_set(conn)
    finally:
        conn.close()

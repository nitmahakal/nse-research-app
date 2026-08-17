"""
Kotlin-facing bridge, Milestones 3 + 4. Every function takes/returns
plain strings/bools so the Kotlin side stays simple.
"""

import db
import scans_repository as scans_repo
import settings_repository as settings_repo
import tracker
import updater
import rating
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
            conn, name="Momentum Test Scan",
            description="Demo scan: price above its 20-day EMA",
            timeframe="Daily", condition_set=_DEMO_CONDITION_SET,
            is_locked=True, is_tracked=True,
        )
        return f"Saved 'Momentum Test Scan' (locked, tracked) as scan_id={scan_id}"
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
        _, _, timeframe, condition_set = scans_repo.load_scan_for_execution(conn, name)
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


# ------------------------------------------------------------
# MILESTONE 4 - Research Engine
# ------------------------------------------------------------

def run_tracked_scans_and_update_report(db_path: str) -> str:
    conn = db.get_connection(db_path)
    try:
        tracked = scans_repo.list_tracked_scans(conn)
        lines = [f"Tracked scans found: {len(tracked)}", ""]

        for scan_id, name, scan_version, timeframe, condition_set in tracked:
            scanner = Scanner(db_path)
            try:
                summary = scanner.run_scan(condition_set, timeframe)
            finally:
                scanner.close()

            track_result = tracker.track_new_matches(conn, scan_id, scan_version, summary)
            lines.append(f"[{name}] matched={summary.matched_count} "
                         f"new_signals={len(track_result.logged)} "
                         f"already_open={len(track_result.skipped_duplicate)}")
            if track_result.logged:
                lines.append(f"    New: {', '.join(track_result.logged)}")

        lines.append("")
        lines.append("--- Mark-to-market: updating all open signals ---")
        update_result = updater.update_open_signals(conn)
        lines.append(f"Updated: {update_result.updated}")
        lines.append(f"Closed this run: {update_result.closed if update_result.closed else 'none'}")
        lines.append(f"Newly matured (>=6 periods): {update_result.matured if update_result.matured else 'none'}")
        if update_result.failed:
            lines.append(f"Failed to update: {update_result.failed}")

        lines.append("")
        lines.append("--- Recomputing ratings for tracked scans ---")
        for scan_id, name, scan_version, timeframe, condition_set in tracked:
            rating_result = rating.compute_rating(conn, scan_id, scan_version)
            rating.save_rating_snapshot(conn, rating_result)
            lines.append(
                f"[{name}] rating={rating_result.rating_score:.2f}/10 "
                f"(confidence={rating_result.confidence:.2f}, "
                f"matured_signals={rating_result.matured_signal_count})"
            )

        return "\n".join(lines)
    finally:
        conn.close()


def get_rating_report(db_path: str, scan_name: str) -> str:
    conn = db.get_connection(db_path)
    try:
        cur = conn.execute("SELECT scan_id, scan_version FROM scans WHERE name = ?", (scan_name,))
        row = cur.fetchone()
        if row is None:
            return f"ERROR: No saved scan named '{scan_name}'"
        scan_id, scan_version = row

        r = rating.compute_rating(conn, scan_id, scan_version)
        lines = [f"Rating for '{scan_name}'", "=" * 40]
        lines.append(f"Rating Score: {r.rating_score:.2f} / 10  ({r.star_rating:.2f} stars)")
        lines.append(f"Confidence: {r.confidence:.2f} (based on {r.matured_signal_count} matured signals)")
        if r.raw_score is not None:
            lines.append(f"Raw score (before confidence shrinkage): {r.raw_score:.2f} / 10")
            lines.append(f"Win Rate: {r.win_rate * 100:.1f}%")
            lines.append(f"Avg Max Gain: {r.avg_gain_pct:.2f}%")
            lines.append(f"Avg Max Drawdown: {r.avg_drawdown_pct:.2f}%")
        else:
            lines.append("No matured signals yet - rating is a neutral placeholder.")

        lines.append("")
        lines.append("Rating trend (by day):")
        trend = rating.get_rating_trend(conn, scan_id)
        if trend:
            for calculated_at, score, confidence, count in trend:
                lines.append(f"  {calculated_at}: {score:.2f}/10 (confidence={confidence:.2f}, n={count})")
        else:
            lines.append("  (no history yet - run Daily Update to record the first snapshot)")

        return "\n".join(lines)
    finally:
        conn.close()


def list_signals_report(db_path: str, scan_name: str) -> str:
    conn = db.get_connection(db_path)
    try:
        cur = conn.execute("SELECT scan_id FROM scans WHERE name = ?", (scan_name,))
        row = cur.fetchone()
        if row is None:
            return f"ERROR: No saved scan named '{scan_name}'"
        scan_id = row[0]

        cur = conn.execute(
            """
            SELECT symbol, status, entry_date, entry_price, current_price,
                   exit_date, exit_price, periods_elapsed, is_matured,
                   round(return_pct, 2), round(max_gain_pct, 2), round(max_drawdown_pct, 2)
            FROM signals WHERE scan_id = ? ORDER BY entry_date DESC
            """,
            (scan_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return f"No signals tracked yet for '{scan_name}'."

        lines = [f"Signals for '{scan_name}' ({len(rows)} total)", "=" * 40]
        for (symbol, status, entry_date, entry_price, current_price, exit_date,
             exit_price, periods, matured, ret, max_gain, max_dd) in rows:
            matured_tag = "matured" if matured else "immature"
            lines.append(f"\n{symbol}  [{status}]  ({matured_tag}, {periods} periods)")
            lines.append(f"  Entry: {entry_date} @ {entry_price:.2f}")
            if status == "CLOSED":
                lines.append(f"  Exit:  {exit_date} @ {exit_price:.2f}")
            else:
                lines.append(f"  Current: {current_price:.2f}")
            lines.append(f"  Return: {ret}%  MaxGain: {max_gain}%  MaxDrawdown: {max_dd}%")
        return "\n".join(lines)
    finally:
        conn.close()

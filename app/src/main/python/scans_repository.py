"""Saved Scans persistence."""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from conditions import ConditionSet, condition_set_label, ConditionEntry


class ScanRepositoryError(Exception):
    pass


@dataclass
class ScanRecord:
    scan_id: int
    name: str
    description: str
    timeframe: str
    condition_set: Optional[ConditionSet]
    is_locked: bool
    is_tracked: bool
    scan_version: int
    created_at: str
    updated_at: str
    summary: str


def _condition_set_to_json(condition_set):
    return json.dumps([entry.to_dict() for entry in condition_set])


def _condition_set_from_json(raw):
    data = json.loads(raw)
    return [ConditionEntry.from_dict(entry) for entry in data]


def save_scan(conn, name, description, timeframe, condition_set, is_locked, is_tracked) -> int:
    if not name or not name.strip():
        raise ScanRepositoryError("Scan name cannot be empty")
    if not condition_set:
        raise ScanRepositoryError("Condition set cannot be empty")
    now = datetime.now().isoformat(timespec="seconds")
    condition_json = _condition_set_to_json(condition_set)
    cur = conn.execute("SELECT scan_id, scan_version FROM scans WHERE name = ?", (name,))
    existing = cur.fetchone()
    if existing is None:
        cur = conn.execute(
            """INSERT INTO scans (name, description, timeframe, condition_set_json,
                is_locked, is_tracked, scan_version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (name, description, timeframe, condition_json, int(is_locked), int(is_tracked), now, now),
        )
        conn.commit()
        return cur.lastrowid
    else:
        scan_id, old_version = existing
        conn.execute(
            """UPDATE scans SET description=?, timeframe=?, condition_set_json=?,
               is_locked=?, is_tracked=?, scan_version=?, updated_at=? WHERE scan_id=?""",
            (description, timeframe, condition_json, int(is_locked), int(is_tracked),
             old_version + 1, now, scan_id),
        )
        conn.commit()
        return scan_id


def _row_to_record(row, reveal_condition) -> ScanRecord:
    (scan_id, name, description, timeframe, condition_json, is_locked,
     is_tracked, scan_version, created_at, updated_at) = row
    is_locked_bool = bool(is_locked)
    condition_set = _condition_set_from_json(condition_json)
    if is_locked_bool and not reveal_condition:
        display_condition_set = None
        summary = "(hidden - locked scan)"
    else:
        display_condition_set = condition_set
        summary = condition_set_label(condition_set)
    return ScanRecord(
        scan_id=scan_id, name=name, description=description, timeframe=timeframe,
        condition_set=display_condition_set, is_locked=is_locked_bool, is_tracked=bool(is_tracked),
        scan_version=scan_version, created_at=created_at, updated_at=updated_at, summary=summary,
    )


def list_scans(conn, owner_mode) -> List[ScanRecord]:
    cur = conn.execute(
        """SELECT scan_id, name, description, timeframe, condition_set_json,
           is_locked, is_tracked, scan_version, created_at, updated_at
           FROM scans ORDER BY updated_at DESC"""
    )
    return [_row_to_record(row, reveal_condition=owner_mode) for row in cur.fetchall()]


def get_scan_for_display(conn, name, owner_mode) -> ScanRecord:
    cur = conn.execute(
        """SELECT scan_id, name, description, timeframe, condition_set_json,
           is_locked, is_tracked, scan_version, created_at, updated_at
           FROM scans WHERE name = ?""", (name,),
    )
    row = cur.fetchone()
    if row is None:
        raise ScanRepositoryError(f"No saved scan named '{name}'")
    return _row_to_record(row, reveal_condition=owner_mode)


def load_scan_for_execution(conn, name):
    cur = conn.execute("SELECT scan_id, scan_version, timeframe, condition_set_json FROM scans WHERE name = ?", (name,))
    row = cur.fetchone()
    if row is None:
        raise ScanRepositoryError(f"No saved scan named '{name}'")
    scan_id, scan_version, timeframe, condition_json = row
    return scan_id, scan_version, timeframe, _condition_set_from_json(condition_json)


def rename_scan(conn, old_name, new_name):
    if not new_name or not new_name.strip():
        raise ScanRepositoryError("New name cannot be empty")
    cur = conn.execute("SELECT 1 FROM scans WHERE name = ?", (old_name,))
    if cur.fetchone() is None:
        raise ScanRepositoryError(f"No saved scan named '{old_name}'")
    cur = conn.execute("SELECT 1 FROM scans WHERE name = ?", (new_name,))
    if cur.fetchone() is not None:
        raise ScanRepositoryError(f"A saved scan named '{new_name}' already exists")
    conn.execute(
        "UPDATE scans SET name = ?, updated_at = ? WHERE name = ?",
        (new_name, datetime.now().isoformat(timespec="seconds"), old_name),
    )
    conn.commit()


def delete_scan(conn, name):
    cur = conn.execute("DELETE FROM scans WHERE name = ?", (name,))
    conn.commit()
    if cur.rowcount == 0:
        raise ScanRepositoryError(f"No saved scan named '{name}'")


def set_tracked(conn, name, is_tracked):
    cur = conn.execute(
        "UPDATE scans SET is_tracked = ?, updated_at = ? WHERE name = ?",
        (int(is_tracked), datetime.now().isoformat(timespec="seconds"), name),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise ScanRepositoryError(f"No saved scan named '{name}'")


def list_tracked_scans(conn):
    """Returns (scan_id, name, scan_version, timeframe, condition_set) for every tracked scan."""
    cur = conn.execute(
        "SELECT scan_id, name, scan_version, timeframe, condition_set_json FROM scans WHERE is_tracked = 1"
    )
    results = []
    for scan_id, name, scan_version, timeframe, condition_json in cur.fetchall():
        results.append((scan_id, name, scan_version, timeframe, _condition_set_from_json(condition_json)))
    return results

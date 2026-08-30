"""
Real Data Engine - downloads actual NSE daily closes via yfinance,
directly into the SQLite `prices` table.

Uses the exact same bug-safe per-symbol extraction pattern proven in
the original Colab downloader: every symbol's data is looked up by
its own explicit ticker key in a batch result, never by position.
That discipline is what fixed the original "every file identical"
bug early in this project, and it's preserved here on purpose.

SYMBOL SOURCE: reads the real ~2087-symbol NSE equity list bundled
into the app as an asset (nse_symbols.csv, copied to internal storage
on launch) - not a live NSE website fetch. NSE's official CSV feed
needs session cookies/browser-like headers to access reliably from a
script, which is separate, riskier work, deliberately deferred. An
auto-updating fetch can replace this file's source later without
changing anything else in the Data Engine.
"""

import csv
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

import db

MAX_CANDLES = 2000
CHUNK_SIZE = 50
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 3.0


def load_symbol_list(csv_path: str) -> List[str]:
    """Reads SYMBOL column, appends .NS, de-duplicates, preserves order."""
    symbols: List[str] = []
    seen = set()
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("SYMBOL") or "").strip().upper()
            if not raw:
                continue
            if not raw.endswith(".NS"):
                raw = f"{raw}.NS"
            if raw not in seen:
                seen.add(raw)
                symbols.append(raw)
    return symbols


def _get_last_date(conn, symbol: str) -> Optional[str]:
    cur = conn.execute("SELECT MAX(date) FROM prices WHERE symbol = ?", (symbol,))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def _most_recent_expected_trading_day() -> str:
    today = datetime.now().date()
    candidate = today
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def _classify_symbols(conn, symbols: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    Returns (full_download_symbols, {symbol: last_date} for incremental).

    Uses ONE bulk query for every symbol's last date instead of one
    query per symbol - the exact same class of fix as the Colab
    downloader's database_index.parquet: per-item database round
    trips are the bottleneck, not the actual work.
    """
    cur = conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol")
    last_dates: Dict[str, str] = {row[0]: row[1] for row in cur.fetchall()}

    full_download: List[str] = []
    incremental: Dict[str, str] = {}
    expected = _most_recent_expected_trading_day()
    for symbol in symbols:
        last_date = last_dates.get(symbol)
        if last_date is None:
            full_download.append(symbol)
        elif last_date < expected:
            incremental[symbol] = last_date
    return full_download, incremental


def _chunked(items: List, size: int) -> List[List]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _download_chunk_raw(tickers: List[str], start: Optional[str], period: Optional[str]):
    try:
        return yf.download(
            tickers=tickers, start=start, period=period if start is None else None,
            interval="1d", group_by="ticker", threads=True, progress=False,
            auto_adjust=False, actions=False, timeout=15,
        )
    except Exception:
        return None


def extract_symbol_frame(raw, symbol: str, chunk_size: int) -> Optional[pd.DataFrame]:
    """
    Bug-safe extraction: always looks up this symbol's data by its
    own explicit ticker key - never by position, never reused across
    symbols in the same chunk.
    """
    if raw is None or raw.empty:
        return None
    try:
        if chunk_size == 1 or not isinstance(raw.columns, pd.MultiIndex):
            sub = raw
        else:
            if symbol not in raw.columns.get_level_values(0):
                return None
            sub = raw[symbol]
        if sub is None or sub.empty or "Close" not in sub.columns:
            return None
        sub = sub[["Close"]].copy().reset_index()
        sub = sub.rename(columns={sub.columns[0]: "Date"})
        sub["Date"] = pd.to_datetime(sub["Date"], errors="coerce")
        sub["Close"] = pd.to_numeric(sub["Close"], errors="coerce")
        sub = sub.dropna(subset=["Date", "Close"])
        if sub.empty:
            return None
        if sub["Date"].dt.tz is not None:
            sub["Date"] = sub["Date"].dt.tz_localize(None)
        return sub[["Date", "Close"]]
    except Exception:
        return None


def save_symbol_rows(conn, symbol: str, new_df: pd.DataFrame) -> None:
    """Merges newly-fetched rows with whatever's already stored, dedupes, trims to MAX_CANDLES."""
    existing = db.get_price_series(conn, symbol)
    if not existing.empty:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset="Date", keep="last").sort_values("Date")
        combined = combined.tail(MAX_CANDLES)
    else:
        combined = new_df.sort_values("Date")
    rows = [(d.date().isoformat(), float(c)) for d, c in zip(combined["Date"], combined["Close"])]
    db.insert_price_rows(conn, symbol, rows)


def _process_group(conn, group_symbols: List[str], start_dates: Optional[Dict[str, str]],
                    succeeded: List[str], failed: List[str],
                    on_progress=None, total_for_progress: int = 0) -> None:
    for chunk in _chunked(group_symbols, CHUNK_SIZE):
        if start_dates is not None:
            dates = [start_dates[s] for s in chunk if s in start_dates]
            start_arg = min(dates) if dates else None
            period_arg = None
        else:
            start_arg = None
            period_arg = "2y"  # 2 years of daily history is plenty for every indicator we support

        raw = _download_chunk_raw(chunk, start=start_arg, period=period_arg)
        if raw is None:
            failed.extend(chunk)
        else:
            for symbol in chunk:
                new_data = extract_symbol_frame(raw, symbol, len(chunk))
                if new_data is None or new_data.empty:
                    failed.append(symbol)
                    continue
                try:
                    save_symbol_rows(conn, symbol, new_data)
                    succeeded.append(symbol)
                except Exception:
                    failed.append(symbol)

        if on_progress is not None:
            done = len(succeeded) + len(failed)
            try:
                on_progress.onProgress(done, total_for_progress, f"Downloading ({done}/{total_for_progress})...")
            except Exception:
                pass  # progress reporting must never crash the actual download

        time.sleep(1.0)


def update_symbols(db_path: str, symbols: List[str], on_progress=None) -> Dict:
    """
    on_progress, if given, is a Kotlin callback object with method
    onProgress(done: Int, total: Int, phase: String) - called at every
    checkpoint (classify, each chunk, each retry) so the UI always
    shows exactly what's happening, never a silent unexplained wait.
    """
    def report(done, total, phase):
        if on_progress is not None:
            try:
                on_progress.onProgress(done, total, phase)
            except Exception:
                pass

    conn = db.get_connection(db_path)
    try:
        report(0, len(symbols), "Checking what needs updating...")
        full_download, incremental = _classify_symbols(conn, symbols)
        succeeded: List[str] = []
        failed: List[str] = []
        total = len(full_download) + len(incremental)
        report(0, total, f"Found {len(full_download)} new, {len(incremental)} to refresh")

        if full_download:
            _process_group(conn, full_download, None, succeeded, failed,
                            on_progress=on_progress, total_for_progress=total)
        if incremental:
            _process_group(conn, list(incremental.keys()), incremental, succeeded, failed,
                            on_progress=on_progress, total_for_progress=total)

        if failed:
            still_failed: List[str] = []
            retry_total = len(failed)
            for i, symbol in enumerate(list(failed)):
                report(i, retry_total, f"Retrying failed symbols ({i}/{retry_total})...")
                time.sleep(RETRY_DELAY_SECONDS)
                raw = _download_chunk_raw([symbol], start=None, period="2y")
                new_data = extract_symbol_frame(raw, symbol, 1) if raw is not None else None
                if new_data is None or new_data.empty:
                    still_failed.append(symbol)
                    continue
                try:
                    save_symbol_rows(conn, symbol, new_data)
                    succeeded.append(symbol)
                except Exception:
                    still_failed.append(symbol)
            failed = still_failed
            report(len(succeeded), total, "Finishing up...")

        return {
            "total": len(symbols),
            "full_download": len(full_download),
            "incremental": len(incremental),
            "succeeded": succeeded,
            "failed": failed,
        }
    finally:
        conn.close()

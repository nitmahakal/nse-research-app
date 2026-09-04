"""Raw market-data downloader/updater.

IMPORTANT:
Update does ZERO indicator/scanner calculations.
It only downloads raw daily close data and stores it in SQLite.
"""

import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

import db


MAX_CANDLES = 2000
CHUNK_SIZE = 50
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 3.0


def load_symbol_list(symbols_csv_path: str) -> List[str]:
    df = pd.read_csv(symbols_csv_path)

    if "SYMBOL" not in df.columns:
        raise ValueError("SYMBOL column not found in symbols CSV")

    symbols = []

    for value in df["SYMBOL"].dropna().astype(str):
        symbol = value.strip().upper()

        if not symbol:
            continue

        if not symbol.endswith(".NS"):
            symbol = symbol + ".NS"

        symbols.append(symbol)

    return list(dict.fromkeys(symbols))


def _normalise_date(value) -> Optional[pd.Timestamp]:
    if value is None:
        return None

    try:
        ts = pd.Timestamp(value)

        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)

        return ts.normalize()
    except Exception:
        return None


def _most_recent_expected_date() -> pd.Timestamp:
    today = pd.Timestamp.now().normalize()

    # Saturday/Sunday -> Friday
    while today.weekday() >= 5:
        today -= pd.Timedelta(days=1)

    return today


def _classify_symbols(
    conn,
    symbols: List[str],
) -> Tuple[List[str], List[str], Dict[str, str]]:
    latest_dates = db.get_latest_dates(conn)

    full_download = []
    incremental = []

    for symbol in symbols:
        last_date = latest_dates.get(symbol)

        if not last_date:
            full_download.append(symbol)
            continue

        last_ts = _normalise_date(last_date)
        expected = _most_recent_expected_date()

        if last_ts is None or last_ts < expected:
            incremental.append(symbol)

    return full_download, incremental, latest_dates


def _extract_symbol_frame(
    raw: pd.DataFrame,
    symbol: str,
) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()

    try:
        if isinstance(raw.columns, pd.MultiIndex):
            if symbol in raw.columns.get_level_values(0):
                frame = raw[symbol].copy()
            elif symbol in raw.columns.get_level_values(1):
                frame = raw.xs(symbol, axis=1, level=1).copy()
            else:
                return pd.DataFrame()
        else:
            frame = raw.copy()

        if "Close" not in frame.columns:
            return pd.DataFrame()

        frame = frame[["Close"]].copy()
        frame = frame.dropna(subset=["Close"])

        frame.index = pd.to_datetime(frame.index)

        if getattr(frame.index, "tz", None) is not None:
            frame.index = frame.index.tz_localize(None)

        frame.index = frame.index.normalize()

        frame["Close"] = pd.to_numeric(
            frame["Close"],
            errors="coerce",
        )

        frame = frame.dropna(subset=["Close"])
        frame = frame[~frame.index.duplicated(keep="last")]
        frame = frame.sort_index()

        return frame

    except Exception:
        return pd.DataFrame()


def _download_chunk_raw(
    tickers: List[str],
    start: Optional[str] = None,
    period: Optional[str] = None,
) -> pd.DataFrame:
    kwargs = {
        "tickers": tickers,
        "interval": "1d",
        "group_by": "ticker",
        "threads": True,
        "progress": False,
        "auto_adjust": False,
        "actions": False,
        "timeout": 15,
    }

    if start:
        kwargs["start"] = start

    if period:
        kwargs["period"] = period

    return yf.download(**kwargs)


def _rows_from_frame(
    symbol: str,
    frame: pd.DataFrame,
) -> List[Tuple[str, str, float]]:
    rows = []

    if frame.empty:
        return rows

    for date, row in frame.iterrows():
        close = row.get("Close")

        if pd.isna(close):
            continue

        date_text = pd.Timestamp(date).strftime("%Y-%m-%d")

        rows.append(
            (
                symbol,
                date_text,
                float(close),
            )
        )

    return rows


def _process_group(
    conn,
    symbols: List[str],
    latest_dates: Dict[str, str],
    mode: str,
    on_progress: Optional[Callable[[int, int, str], None]],
    done_before: int,
    total: int,
) -> Tuple[int, List[str]]:
    succeeded = 0
    failed = []

    if not symbols:
        return succeeded, failed

    for start_index in range(0, len(symbols), CHUNK_SIZE):
        chunk = symbols[start_index:start_index + CHUNK_SIZE]

        # For incremental mode, use the earliest date in this chunk.
        # This keeps one Yahoo request for the whole chunk.
        start_date = None

        if mode == "incremental":
            dates = []

            for symbol in chunk:
                value = latest_dates.get(symbol)
                ts = _normalise_date(value)

                if ts is not None:
                    dates.append(ts)

            if dates:
                earliest = min(dates)
                start_date = (
                    earliest + pd.Timedelta(days=1)
                ).strftime("%Y-%m-%d")

        try:
            if mode == "full":
                raw = _download_chunk_raw(
                    chunk,
                    period="2y",
                )
            else:
                raw = _download_chunk_raw(
                    chunk,
                    start=start_date,
                )

        except Exception:
            raw = pd.DataFrame()

        batch_rows = []
        chunk_failed = []

        for symbol in chunk:
            frame = _extract_symbol_frame(raw, symbol)

            if frame.empty:
                chunk_failed.append(symbol)
                continue

            # Keep only data newer than the symbol's stored date.
            if mode == "incremental":
                last_date = _normalise_date(
                    latest_dates.get(symbol)
                )

                if last_date is not None:
                    frame = frame[
                        frame.index > last_date
                    ]

            if frame.empty:
                # Already up to date.
                succeeded += 1
                continue

            rows = _rows_from_frame(symbol, frame)

            if not rows:
                chunk_failed.append(symbol)
                continue

            # Only keep the latest MAX_CANDLES rows for this symbol.
            rows = rows[-MAX_CANDLES:]

            batch_rows.extend(rows)

        try:
            if batch_rows:
                db.insert_price_rows_batch(
                    conn,
                    batch_rows,
                )
        except Exception:
            # If the batch insert fails, mark affected symbols as failed.
            affected = {
                row[0]
                for row in batch_rows
            }

            chunk_failed.extend(
                symbol
                for symbol in affected
                if symbol not in chunk_failed
            )

        failed.extend(
            symbol
            for symbol in chunk_failed
            if symbol not in failed
        )

        succeeded += len(chunk) - len(chunk_failed)

        processed = min(
            done_before + start_index + len(chunk),
            total,
        )

        if on_progress:
            on_progress(
                processed,
                total,
                f"{mode.title()} update: {processed}/{total}",
            )

    return succeeded, failed


def update_symbols(
    db_path: str,
    symbols: List[str],
    on_progress: Optional[
        Callable[[int, int, str], None]
    ] = None,
) -> Dict:
    total = len(symbols)

    if total == 0:
        return {
            "total": 0,
            "full": 0,
            "incremental": 0,
            "succeeded": 0,
            "failed": 0,
            "failed_symbols": [],
        }

    conn = db.get_connection(db_path)

    try:
        if on_progress:
            on_progress(
                0,
                total,
                "Checking existing database...",
            )

        # ONE bulk DB query.
        full_symbols, incremental_symbols, latest_dates = (
            _classify_symbols(
                conn,
                symbols,
            )
        )

        succeeded = 0
        failed_symbols = []

        if on_progress:
            on_progress(
                0,
                total,
                (
                    f"Ready: {len(full_symbols)} new, "
                    f"{len(incremental_symbols)} incremental"
                ),
            )

        # New symbols first.
        full_succeeded, full_failed = _process_group(
            conn=conn,
            symbols=full_symbols,
            latest_dates=latest_dates,
            mode="full",
            on_progress=on_progress,
            done_before=0,
            total=total,
        )

        succeeded += full_succeeded
        failed_symbols.extend(full_failed)

        # Incremental symbols.
        incremental_succeeded, incremental_failed = (
            _process_group(
                conn=conn,
                symbols=incremental_symbols,
                latest_dates=latest_dates,
                mode="incremental",
                on_progress=on_progress,
                done_before=len(full_symbols),
                total=total,
            )
        )

        succeeded += incremental_succeeded
        failed_symbols.extend(incremental_failed)

        # Retry failed symbols individually.
        retry_failed = []

        for index, symbol in enumerate(
            list(dict.fromkeys(failed_symbols))
        ):
            try:
                raw = _download_chunk_raw(
                    [symbol],
                    period="2y",
                )

                frame = _extract_symbol_frame(
                    raw,
                    symbol,
                )

                if frame.empty:
                    retry_failed.append(symbol)
                    continue

                rows = _rows_from_frame(
                    symbol,
                    frame,
                )

                if rows:
                    rows = rows[-MAX_CANDLES:]

                    db.insert_price_rows_batch(
                        conn,
                        rows,
                    )

                    succeeded += 1
                    continue

                retry_failed.append(symbol)

            except Exception:
                retry_failed.append(symbol)

            time.sleep(RETRY_DELAY_SECONDS)

        failed_symbols = list(
            dict.fromkeys(retry_failed)
        )

        successful = succeeded
        failed = len(failed_symbols)

        if on_progress:
            on_progress(
                total,
                total,
                f"Update complete: {successful}/{total}",
            )

        return {
            "total": total,
            "full": len(full_symbols),
            "incremental": len(incremental_symbols),
            "succeeded": successful,
            "failed": failed,
            "failed_symbols": failed_symbols[:50],
        }

    finally:
        conn.close()

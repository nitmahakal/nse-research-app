"""Raw market-data downloader/updater.

IMPORTANT:
Update does ZERO indicator/scanner calculations.
It only downloads raw daily close data and stores it in SQLite.

LATEST-DATE RULE:
The target/latest trading date is NOT calculated from the calendar.
It is detected from actual Yahoo Finance market data.
"""

import time
from datetime import datetime
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

import db


MAX_CANDLES = 2000
CHUNK_SIZE = 50

# Small probe used only to discover the actual latest market date.
REFERENCE_PROBE_SIZE = 100
REFERENCE_PROBE_PERIOD = "5d"

MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 0.75


def load_symbol_list(symbols_csv_path: str) -> List[str]:
    df = pd.read_csv(symbols_csv_path)

    if "SYMBOL" not in df.columns:
        raise ValueError("SYMBOL column not found in symbols CSV")

    symbols = []

    special_symbols = {
        "NIFTY": "^NSEI",
        "SENSEX": "^BSESN",
    }

    for value in df["SYMBOL"].dropna().astype(str):
        symbol = value.strip().upper()

        if not symbol:
            continue

        if symbol in special_symbols:
            symbol = special_symbols[symbol]

        elif not symbol.endswith(".NS"):
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
                frame = raw.xs(
                    symbol,
                    axis=1,
                    level=1,
                ).copy()

            else:
                return pd.DataFrame()

        else:
            frame = raw.copy()

        if "Close" not in frame.columns:
            return pd.DataFrame()

        frame = frame[["Close"]].copy()

        frame = frame.dropna(
            subset=["Close"]
        )

        frame.index = pd.to_datetime(
            frame.index
        )

        if getattr(
            frame.index,
            "tz",
            None,
        ) is not None:
            frame.index = frame.index.tz_localize(
                None
            )

        frame.index = frame.index.normalize()

        frame["Close"] = pd.to_numeric(
            frame["Close"],
            errors="coerce",
        )

        frame = frame.dropna(
            subset=["Close"]
        )

        frame = frame[
            ~frame.index.duplicated(
                keep="last"
            )
        ]

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
        "threads": False,
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

        date_text = pd.Timestamp(
            date
        ).strftime("%Y-%m-%d")

        rows.append(
            (
                symbol,
                date_text,
                float(close),
            )
        )

    return rows


def _latest_date_from_frame(
    frame: pd.DataFrame,
) -> Optional[pd.Timestamp]:

    if frame is None or frame.empty:
        return None

    try:
        return _normalise_date(
            frame.index.max()
        )
    except Exception:
        return None

def _determine_reference_latest_date(
    symbols: List[str],
) -> Tuple[Optional[pd.Timestamp], Dict[str, pd.Timestamp]]:

    """Get the newest valid date from a small reference basket."""

    reference_symbols = [
        "^NSEI",
        "^BSESN",
        "RELIANCE.NS",
        "HDFCBANK.NS",
        "ICICIBANK.NS",
        "SBIN.NS",
        "INFY.NS",
        "TCS.NS",
        "BHARTIARTL.NS",
    ]

    latest_dates = {}

    try:
        raw = _download_chunk_raw(
            reference_symbols,
            period="5d",
        )
    except Exception:
        return None, {}

    for symbol in reference_symbols:

        try:
            frame = _extract_symbol_frame(
                raw,
                symbol,
            )

            latest = _latest_date_from_frame(
                frame
            )

            if latest is not None:
                latest_dates[symbol] = latest

        except Exception:
            continue

    if not latest_dates:
        return None, {}

    return max(latest_dates.values()), latest_dates

def _process_group(
    conn,
    symbols: List[str],
    latest_dates: Dict[str, str],
    mode: str,
    reference_latest_date: pd.Timestamp,
    on_progress: Optional[
        Callable[[int, int, str], None]
    ],
    done_before: int,
    total: int,
) -> Tuple[List[str], Dict[str, str]]:

    failed = {}
    fetch_error = {}
    processed_count = 0

    if not symbols:
        return [], failed

    for start_index in range(
        0,
        len(symbols),
        CHUNK_SIZE,
    ):

        chunk = symbols[
            start_index:
            start_index + CHUNK_SIZE
        ]

        start_date = None

        if mode == "incremental":

            dates = []

            for symbol in chunk:

                value = latest_dates.get(
                    symbol
                )

                ts = _normalise_date(
                    value
                )

                if ts is not None:
                    dates.append(ts)

            if dates:

                earliest = min(dates)

                start_date = (
                    earliest
                    + pd.Timedelta(days=1)
                ).strftime(
                    "%Y-%m-%d"
                )

        fetch_error = None

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

        except Exception as exc:

            raw = pd.DataFrame()
            fetch_error = str(exc)

        batch_rows = []
        candidate_symbols = []

        for symbol in chunk:

            frame = _extract_symbol_frame(
                raw,
                symbol,
            )

            if frame.empty:

                if fetch_error:
                    fetch_errors[symbol] = (
                        "FETCH_ERROR: "
                        + fetch_error
                    )
                    failed[symbol] = (
                        "FETCH_ERROR: "
                        + fetch_error
                    )
                else:
                    failed[symbol] = (
                        "NO_DATA"
                    )
            
                continue
            if mode == "incremental":

                last_date = _normalise_date(
                    latest_dates.get(symbol)
                )

                if last_date is not None:

                    frame = frame[
                        frame.index > last_date
                    ]

            if frame.empty:

                # No new rows were returned.
                # This is not automatically a failure.
                # Final DB verification decides.
                candidate_symbols.append(
                    symbol
                )
                continue

            rows = _rows_from_frame(
                symbol,
                frame,
            )

            if not rows:

                failed[symbol] = (
                    "NO_VALID_CLOSE_DATA"
                )
                continue

            rows = rows[
                -MAX_CANDLES:
            ]

            batch_rows.extend(rows)

            candidate_symbols.append(
                symbol
            )

        insert_error = None

        try:

            if batch_rows:

                db.insert_price_rows_batch(
                    conn,
                    batch_rows,
                )

        except Exception as exc:

            insert_error = str(exc)

            affected = {
                row[0]
                for row in batch_rows
            }

            for symbol in affected:
                failed[symbol] = (
                    "DB_INSERT_ERROR: "
                    + insert_error
                )

        # DB state will be verified once at the end
        # of the complete update, not after every chunk.
        
        # Older valid stock data is still usable.
        # Do not mark a stock as failed just because
        # its latest available date is older.
        processed_count += len(chunk)

        processed = min(
            done_before
            + processed_count,
            total,
        )

        if on_progress:

            on_progress(
                processed,
                total,
                (
                    f"{mode.title()} update: "
                    f"{processed}/{total}"
                ),
            )

    return (
        list(failed.keys()),
        failed,
        fetch_errors
    )

def _retry_failed_symbols(
    conn,
    failed_symbols: List[str],
    reasons: Dict[str, str],
    reference_latest_date: pd.Timestamp,
    on_progress: Optional[
        Callable[[int, int, str], None]
    ],
    retry_label: str,
) -> Dict[str, str]:

    still_failed = {}

    unique_symbols = list(
        dict.fromkeys(
            failed_symbols
        )
    )

    retry_total = len(unique_symbols)

    if retry_total == 0:
        return still_failed

    for index, symbol in enumerate(
        unique_symbols
    ):

        previous_date = db.get_latest_date(
            conn,
            symbol,
        )

        previous_ts = _normalise_date(
            previous_date
        )

        success = False

        last_reason = reasons.get(
            symbol,
            "NO_DATA",
        )

        for retry_no in range(
            1,
            MAX_RETRIES + 1,
        ):

            try:

                start_date = None

                if previous_ts is not None:

                    start_date = (
                        previous_ts
                        + pd.Timedelta(days=1)
                    ).strftime(
                        "%Y-%m-%d"
                    )

                if start_date:

                    raw = _download_chunk_raw(
                        [symbol],
                        start=start_date,
                    )

                else:

                    raw = _download_chunk_raw(
                        [symbol],
                        period="2y",
                    )

                frame = _extract_symbol_frame(
                    raw,
                    symbol,
                )

                if frame.empty:

                    last_reason = reasons.get(
                        symbol,
                        "NO_DATA",
                    )

                else:

                    if previous_ts is not None:

                        frame = frame[
                            frame.index > previous_ts
                        ]

                    rows = _rows_from_frame(
                        symbol,
                        frame,
                    )

                    if rows:

                        rows = rows[
                            -MAX_CANDLES:
                        ]

                        db.insert_price_rows_batch(
                            conn,
                            rows,
                        )

                    final_date = (
                        db.get_latest_date(
                            conn,
                            symbol,
                        )
                    )

                    final_ts = _normalise_date(
                        final_date
                    )

                    if final_ts is not None:

                        success = True
                        break

                    last_reason = (
                        "DB_VERIFICATION_FAILED: "
                        "no stored date"
                    )

            except Exception as exc:

                last_reason = (
                    "FETCH_ERROR: "
                    + str(exc)
                )

            if retry_no < MAX_RETRIES:

                if on_progress:

                    on_progress(
                        index + 1,
                        retry_total,
                        (
                            f"{retry_label} "
                            f"Retry {retry_no}/"
                            f"{MAX_RETRIES}: "
                            f"{index + 1}/"
                            f"{retry_total} "
                            f"{symbol}"
                        ),
                    )

                time.sleep(
                    RETRY_DELAY_SECONDS
                )

        if not success:

            final_date = db.get_latest_date(
                conn,
                symbol,
            )

            final_ts = _normalise_date(
                final_date
            )

            if final_ts is None:

                still_failed[symbol] = (
                    last_reason
                )

    return still_failed
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
            "already_latest": 0,
            "updated": 0,
            "up_to_date": 0,
            "last_available": 0,
            "no_data": 0,
            "succeeded": 0,
            "failed": 0,
            "failed_symbols": [],
            "fetch_error": 0,
            "fetch_error_symbols": [],
            "reference_latest_date": None,
        }

    conn = db.get_connection(
        db_path
    )

    try:

        if on_progress:

            on_progress(
                0,
                total,
                "Detecting latest market date...",
            )

        # -------------------------------------------------
        # STEP 1:
        # Discover actual latest market date.
        # -------------------------------------------------

        reference_latest_date, _ = (
            _determine_reference_latest_date(
                symbols
            )
        )

        reference_detection_failed = (
            reference_latest_date is None
        )

        if reference_detection_failed:

            reference_text = (
                "Unavailable - using stored/latest data"
            )

        else:

            reference_text = (
                reference_latest_date.strftime(
                    "%Y-%m-%d"
                )
            )

        if on_progress:

            on_progress(
                0,
                total,
                (
                    "Latest market data date: "
                    + reference_text
                ),
            )

        # -------------------------------------------------
        # STEP 2:
        # Classify symbols.
        # -------------------------------------------------

        if reference_latest_date is not None:

            (
                full_symbols,
                incremental_symbols,
                already_latest_symbols,
                latest_dates,
            ) = _classify_symbols(
                conn,
                symbols,
                reference_latest_date,
            )

        else:

            # Reference basket failed.
            # Do not falsely mark existing symbols
            # as already latest.
            latest_dates = db.get_latest_dates(
                conn
            )

            full_symbols = []
            incremental_symbols = []
            already_latest_symbols = []

            for symbol in symbols:

                stored = latest_dates.get(
                    symbol
                )

                stored_ts = _normalise_date(
                    stored
                )

                if stored_ts is None:

                    full_symbols.append(
                        symbol
                    )

                else:

                    incremental_symbols.append(
                        symbol
                    )

        if on_progress:

            on_progress(
                0,
                total,
                (
                    f"Ready: "
                    f"{len(full_symbols)} new, "
                    f"{len(incremental_symbols)} incremental, "
                    f"{len(already_latest_symbols)} already latest"
                ),
            )

        failed_reasons = {}
        fetch_errors = {}

        # -------------------------------------------------
        # STEP 3:
        # Incremental first.
        # -------------------------------------------------

        (
            incremental_failed,
            incremental_reasons,
            incremental_fetch_errors,
        ) = _process_group(
            conn=conn,
            symbols=incremental_symbols,
            latest_dates=latest_dates,
            mode="incremental",
            reference_latest_date=reference_latest_date,
            on_progress=on_progress,
            done_before=0,
            total=total,
        )

        failed_reasons.update(
            incremental_reasons
        )

        fetch_errors.update(
            incremental_fetch_errors
        )

        # -------------------------------------------------
        # STEP 4:
        # Full/New second.
        # -------------------------------------------------

        (
            full_failed,
            full_reasons,
            full_fetch_errors,
        ) = _process_group(
            conn=conn,
            symbols=full_symbols,
            latest_dates=latest_dates,
            mode="full",
            reference_latest_date=reference_latest_date,
            on_progress=on_progress,
            done_before=len(
                incremental_symbols
            ),
            total=total,
        )

        failed_reasons.update(
            full_reasons
        )

        fetch_errors.update(
            full_fetch_errors
        )

        # -------------------------------------------------
        # STEP 5:
        # Retry all failed symbols once.
        # -------------------------------------------------

        retry_failed = {}

        all_failed_symbols = list(
            dict.fromkeys(
                incremental_failed
                + full_failed
            )
        )

        if all_failed_symbols:

            retry_failed = (
                _retry_failed_symbols(
                    conn=conn,
                    failed_symbols=all_failed_symbols,
                    reasons=failed_reasons,
                    reference_latest_date=reference_latest_date,
                    on_progress=on_progress,
                    retry_label="",
                )
            )

        # -------------------------------------------------
        # STEP 6:
        # Final DB verification.
        # -------------------------------------------------

        final_dates = db.get_latest_dates(
            conn
        )

        # If reference detection failed,
        # use newest valid date actually stored.
        if reference_latest_date is None:

            stored_dates = []

            for value in final_dates.values():

                ts = _normalise_date(
                    value
                )

                if ts is not None:

                    stored_dates.append(
                        ts
                    )

            if stored_dates:

                reference_latest_date = max(
                    stored_dates
                )

                reference_text = (
                    reference_latest_date.strftime(
                        "%Y-%m-%d"
                    )
                )

        # -------------------------------------------------
        # STEP 7:
        # Final classification.
        # -------------------------------------------------

        updated_count = 0
        up_to_date_count = 0
        last_available_count = 0
        no_data_count = 0

        updated_symbols = []
        up_to_date_symbols = []
        last_available_symbols = []
        no_data_symbols = []

        final_failed = {}
        fetch_error_symbols = []

        for symbol in symbols:

            final_ts = _normalise_date(
                final_dates.get(symbol)
            )

            original_ts = _normalise_date(
                latest_dates.get(symbol)
            )

            if final_ts is None:

                if symbol in fetch_errors:

                    fetch_error_symbols.append(
                        symbol
                    )

                else:

                    no_data_count += 1
                    no_data_symbols.append(
                        symbol
                    )

                final_failed[symbol] = (
                    retry_failed.get(
                        symbol,
                        fetch_errors.get(
                            symbol,
                            failed_reasons.get(
                                symbol,
                                "NO_DATA",
                            ),
                        ),
                    )
                )

                continue

            if reference_latest_date is None:

                last_available_count += 1
                last_available_symbols.append(
                    symbol
                )

            elif final_ts >= reference_latest_date:

                if (
                    original_ts is None
                    or final_ts > original_ts
                ):

                    updated_count += 1
                    updated_symbols.append(
                        symbol
                    )

                else:

                    up_to_date_count += 1
                    up_to_date_symbols.append(
                        symbol
                    )

            else:

                last_available_count += 1
                last_available_symbols.append(
                    symbol
                )

        succeeded = (
            updated_count
            + up_to_date_count
            + last_available_count
        )

        failed = no_data_count

        failed_list = [
            {
                "symbol": symbol,
                "reason": reason,
            }
            for symbol, reason
            in final_failed.items()
        ]

        if on_progress:

            on_progress(
                total,
                total,
                (
                    f"Update complete: "
                    f"{succeeded}/{total} usable"
                ),
            )

        market_data_through = None

        if reference_latest_date is not None:

            market_data_through = (
                reference_latest_date.strftime(
                    "%d-%m-%Y"
                )
            )

        return {
            "total": total,
            "full": len(full_symbols),
            "incremental": len(
                incremental_symbols
            ),
            "already_latest": len(
                already_latest_symbols
            ),
            "updated": updated_count,
            "up_to_date": up_to_date_count,
            "last_available": last_available_count,
            "no_data": no_data_count,
            "succeeded": succeeded,
            "failed": failed,
            "failed_symbols": (
                failed_list[:50]
            ),
            "fetch_error": len(
                fetch_error_symbols
            ),
            "fetch_error_symbols": (
                fetch_error_symbols[:50]
            ),
            "updated_symbols": (
                updated_symbols[:100]
            ),
            "up_to_date_symbols": (
                up_to_date_symbols[:100]
            ),
            "last_available_symbols": (
                last_available_symbols[:100]
            ),
            "no_data_symbols": (
                no_data_symbols[:100]
            ),
            "reference_latest_date": (
                reference_text
            ),
            "market_data_through": (
                market_data_through
            ),
            "last_update_finished": (
                datetime.now().strftime(
                    "%d-%m-%Y %H:%M:%S"
                )
            ),
            "status": (
                "SUCCESS"
                if failed == 0
                else "PARTIAL"
            ),
        }

    finally:

        conn.close()

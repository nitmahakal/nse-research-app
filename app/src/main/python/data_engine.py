"""Raw market-data downloader/updater.

IMPORTANT:
Update does ZERO indicator/scanner calculations.
It only downloads raw daily close data and stores it in SQLite.

LATEST-DATE RULE:
The target/latest trading date is NOT calculated from the calendar.
It is detected from actual Yahoo Finance market data.
"""

import time
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

MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 3.0


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

    """Get the latest actual date available from Yahoo Finance."""

    if not symbols:
        return None, {}

    try:
        raw = _download_chunk_raw(
            symbols,
            period=REFERENCE_PROBE_PERIOD,
        )
    except Exception:
        return None, {}

    latest_by_symbol = {}

    for symbol in symbols:
        frame = _extract_symbol_frame(
            raw,
            symbol,
        )

        latest = _latest_date_from_frame(
            frame
        )

        if latest is not None:
            latest_by_symbol[symbol] = latest

    if not latest_by_symbol:
        return None, {}

    reference_date = max(
        latest_by_symbol.values()
    )

    return reference_date, latest_by_symbol

def _classify_symbols(
    conn,
    symbols: List[str],
    reference_latest_date: pd.Timestamp,
) -> Tuple[
    List[str],
    List[str],
    List[str],
    Dict[str, str],
]:
    """Classify symbols using the actual reference date."""

    latest_dates = db.get_latest_dates(conn)

    full_download = []
    incremental = []
    already_latest = []

    for symbol in symbols:

        stored = latest_dates.get(symbol)

        if not stored:
            full_download.append(symbol)
            continue

        last_ts = _normalise_date(
            stored
        )

        if last_ts is None:
            full_download.append(symbol)
            continue

        if last_ts < reference_latest_date:
            incremental.append(symbol)
        else:
            already_latest.append(symbol)

    return (
        full_download,
        incremental,
        already_latest,
        latest_dates,
    )


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

        # IMPORTANT:
        # Verify the actual DB state after insertion.
        verified_latest = db.get_latest_dates(
            conn
        )

        for symbol in candidate_symbols:

            if symbol in failed:
                continue

            db_latest = _normalise_date(
                verified_latest.get(symbol)
            )

            if db_latest is None:

                failed[symbol] = (
                    "DB_VERIFICATION_FAILED: "
                    "no stored date"
                )

            elif db_latest < reference_latest_date:

                failed[symbol] = (
                    "BEHIND_LATEST: DB has "
                    f"{db_latest.strftime('%Y-%m-%d')} "
                    "but reference is "
                    f"{reference_latest_date.strftime('%Y-%m-%d')}"
                )

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
    )


def _retry_failed_symbols(
    conn,
    failed_symbols: List[str],
    reasons: Dict[str, str],
    reference_latest_date: pd.Timestamp,
    on_progress: Optional[
        Callable[[int, int, str], None]
    ],
    done_before: int,
    total: int,
) -> Dict[str, str]:

    still_failed = {}

    unique_symbols = list(
        dict.fromkeys(
            failed_symbols
        )
    )

    for index, symbol in enumerate(
        unique_symbols
    ):

        previous_date = db.get_latest_date(
            conn,
            symbol,
        )

        start_date = None

        previous_ts = _normalise_date(
            previous_date
        )

        if previous_ts is not None:

            start_date = (
                previous_ts
                + pd.Timedelta(days=1)
            ).strftime(
                "%Y-%m-%d"
            )

        try:

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

                still_failed[symbol] = (
                    reasons.get(
                        symbol,
                        "NO_DATA",
                    )
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

                # Final verification.
                final_dates = (
                    db.get_latest_dates(conn)
                )

                final_ts = _normalise_date(
                    final_dates.get(symbol)
                )

                if (
                    final_ts is None
                    or final_ts
                    < reference_latest_date
                ):

                    if final_ts is None:

                        still_failed[symbol] = (
                            "DB_VERIFICATION_FAILED: "
                            "no stored date after retry"
                        )

                    else:

                        still_failed[symbol] = (
                            "BEHIND_LATEST after retry: "
                            f"{final_ts.strftime('%Y-%m-%d')} "
                            "vs "
                            f"{reference_latest_date.strftime('%Y-%m-%d')}"
                        )

        except Exception as exc:

            still_failed[symbol] = (
                "FETCH_ERROR after retry: "
                + str(exc)
            )

        processed = min(
            done_before + index + 1,
            total,
        )

        if on_progress:

            on_progress(
                processed,
                total,
                (
                    f"Retry: "
                    f"{processed}/{total}"
                ),
            )

        if (
            symbol in still_failed
            and index
            < len(unique_symbols) - 1
        ):
            time.sleep(
                RETRY_DELAY_SECONDS
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
            "succeeded": 0,
            "failed": 0,
            "failed_symbols": [],
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

        if reference_latest_date is None:

            return {
                "total": total,
                "full": 0,
                "incremental": 0,
                "already_latest": 0,
                "succeeded": 0,
                "failed": total,
                "failed_symbols": [
                    {
                        "symbol": symbol,
                        "reason": (
                            "REFERENCE_DATE_FETCH_FAILED"
                        ),
                    }
                    for symbol in symbols[:50]
                ],
                "reference_latest_date": None,
                "status": "ERROR",
                "error": (
                    "Could not determine the "
                    "latest market-data date."
                ),
            }

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
        # Classify using actual reference date.
        # -------------------------------------------------

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

        # -------------------------------------------------
        # STEP 3:
        # Full downloads.
        # -------------------------------------------------

        full_failed, full_reasons = (
            _process_group(
                conn=conn,
                symbols=full_symbols,
                latest_dates=latest_dates,
                mode="full",
                reference_latest_date=(
                    reference_latest_date
                ),
                on_progress=on_progress,
                done_before=0,
                total=total,
            )
        )

        failed_reasons.update(
            full_reasons
        )

        # -------------------------------------------------
        # STEP 4:
        # Incremental downloads.
        # -------------------------------------------------

        incremental_failed, incremental_reasons = (
            _process_group(
                conn=conn,
                symbols=incremental_symbols,
                latest_dates=latest_dates,
                mode="incremental",
                reference_latest_date=(
                    reference_latest_date
                ),
                on_progress=on_progress,
                done_before=len(full_symbols),
                total=total,
            )
        )

        failed_reasons.update(
            incremental_reasons
        )

        # -------------------------------------------------
        # STEP 5:
        # Retry everything that did not verify.
        # -------------------------------------------------

        retry_failed = _retry_failed_symbols(
            conn=conn,
            failed_symbols=list(
                failed_reasons.keys()
            ),
            reasons=failed_reasons,
            reference_latest_date=(
                reference_latest_date
            ),
            on_progress=on_progress,
            done_before=(
                len(full_symbols)
                + len(incremental_symbols)
            ),
            total=total,
        )

        # -------------------------------------------------
        # STEP 6:
        # FINAL DB VERIFICATION FOR ALL SYMBOLS.
        # -------------------------------------------------

        final_dates = db.get_latest_dates(
            conn
        )

        final_failed = {}

        for symbol in symbols:

            final_ts = _normalise_date(
                final_dates.get(symbol)
            )

            if final_ts is None:

                final_failed[symbol] = (
                    retry_failed.get(
                        symbol,
                        failed_reasons.get(
                            symbol,
                            "NO_DATA",
                        ),
                    )
                )

            elif final_ts < reference_latest_date:

                final_failed[symbol] = (
                    retry_failed.get(
                        symbol,
                        (
                            "BEHIND_LATEST: "
                            f"{final_ts.strftime('%Y-%m-%d')} "
                            "vs "
                            f"{reference_text}"
                        ),
                    )
                )

        # Already-latest symbols count as success too.
        succeeded = total - len(
            final_failed
        )

        failed = len(
            final_failed
        )

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
                    f"{succeeded}/{total} latest"
                ),
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
            "succeeded": succeeded,
            "failed": failed,
            "failed_symbols": failed_list[:50],
            "reference_latest_date": (
                reference_text
            ),
            "status": (
                "SUCCESS"
                if failed == 0
                else "PARTIAL"
            ),
        }

    finally:

        conn.close()

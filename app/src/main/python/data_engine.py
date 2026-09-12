"""Raw market-data downloader/updater.

The updater downloads DAILY market data only.

Architecture:
    Yahoo Finance DAILY data
        -> SQLite DAILY history
        -> scanner/resampling creates higher timeframes later

Important:
- Yahoo initial download uses period="max".
- SQLite keeps all available daily history.
- No 2000-candle retention limit.
- Existing symbols fetch only dates missing from their DB history.
- Each Yahoo ticker is extracted only from its own columns.
- One bad/empty ticker must not contaminate another ticker.
- Update does not calculate indicators or scanner results.
"""

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

import db


# ---------------------------------------------------------
# Download configuration
# ---------------------------------------------------------

CHUNK_SIZE = 50

# Controlled parallelism.
# Do not use unlimited threads because Yahoo rate limiting
# has occurred previously.
YF_THREADS = 8

# Small probe used only to discover the current market date.
REFERENCE_PROBE_PERIOD = "5d"

# One retry only for a completely failed/empty batch.
MAX_BATCH_RETRIES = 1


# ---------------------------------------------------------
# Symbol loading
# ---------------------------------------------------------

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

    # Preserve original order and remove duplicates.
    return list(dict.fromkeys(symbols))


# ---------------------------------------------------------
# Date helpers
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Yahoo batch extraction
# ---------------------------------------------------------

def _extract_symbol_frame(
    raw: pd.DataFrame,
    symbol: str,
    requested_ticker_count: int = 1,
) -> pd.DataFrame:
    """Extract ONLY the requested ticker's own data.

    This function is intentionally strict.

    For a multi-ticker response:
        (TICKER, FIELD)

    or:
        (FIELD, TICKER)

    only the requested ticker is extracted.

    A flat multi-ticker response is rejected because it cannot
    safely be assigned to a particular ticker.
    """

    if raw is None or raw.empty:
        return pd.DataFrame()

    try:

        if isinstance(raw.columns, pd.MultiIndex):

            level0 = raw.columns.get_level_values(0)
            level1 = raw.columns.get_level_values(1)

            # Normal group_by="ticker" layout:
            # (SYMBOL, Open/High/Low/Close...)
            if symbol in level0:
                frame = raw[symbol].copy()

            # Defensive support for:
            # (Open/High/Low/Close..., SYMBOL)
            elif symbol in level1:
                frame = raw.xs(
                    symbol,
                    axis=1,
                    level=1,
                ).copy()

            else:
                return pd.DataFrame()

        else:

            # A flat response is safe only for one ticker.
            if requested_ticker_count != 1:
                return pd.DataFrame()

            frame = raw.copy()

        if "Close" not in frame.columns:
            return pd.DataFrame()

        # We intentionally store daily CLOSE only.
        frame = frame[["Close"]].copy()

        frame["Close"] = pd.to_numeric(
            frame["Close"],
            errors="coerce",
        )

        frame = frame.dropna(
            subset=["Close"]
        )

        if frame.empty:
            return pd.DataFrame()

        # Convert index once.
        frame.index = pd.to_datetime(
            frame.index,
            errors="coerce",
        )

        if getattr(frame.index, "tz", None) is not None:
            frame.index = frame.index.tz_localize(None)

        frame.index = frame.index.normalize()

        # Remove invalid dates.
        frame = frame[
            ~pd.isna(frame.index)
        ]

        # Keep last value if Yahoo ever returns duplicate dates.
        frame = frame[
            ~frame.index.duplicated(
                keep="last"
            )
        ]

        return frame.sort_index()

    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------
# Yahoo download
# ---------------------------------------------------------

def _download_chunk_raw(
    tickers: List[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: Optional[str] = None,
) -> pd.DataFrame:
    """Download one Yahoo Finance batch."""

    kwargs = {
        "tickers": tickers,
        "interval": "1d",
        "group_by": "ticker",

        # Controlled parallel download.
        "threads": YF_THREADS,

        "progress": False,
        "auto_adjust": False,
        "actions": False,
        "timeout": 15,
    }

    if start:
        kwargs["start"] = start

    if end:
        kwargs["end"] = end

    if period:
        kwargs["period"] = period

    return yf.download(**kwargs)


# ---------------------------------------------------------
# Convert ticker frame into DB rows
# ---------------------------------------------------------

def _rows_from_frame(
    symbol: str,
    frame: pd.DataFrame,
    target_date: pd.Timestamp,
    last_stored_date: Optional[pd.Timestamp],
) -> List[Tuple[str, str, float]]:
    """Create safe (SYMBOL, DATE, CLOSE) rows.

    No data beyond target_date is saved.

    Existing rows are not duplicated.
    """

    if frame is None or frame.empty:
        return []

    try:

        work = frame[["Close"]].copy()

        work["Close"] = pd.to_numeric(
            work["Close"],
            errors="coerce",
        )

        # Normalize the index once.
        dates = pd.to_datetime(
            work.index,
            errors="coerce",
        )

        if getattr(dates, "tz", None) is not None:
            dates = dates.tz_localize(None)

        dates = dates.normalize()

        close_values = work["Close"].to_numpy()

        rows = []

        for date_value, close_value in zip(
            dates,
            close_values,
        ):

            if pd.isna(date_value):
                continue

            if pd.isna(close_value):
                continue

            date_ts = date_value

            # Never save beyond detected market date.
            if date_ts > target_date:
                continue

            # Existing data remains untouched.
            if (
                last_stored_date is not None
                and date_ts <= last_stored_date
            ):
                continue

            rows.append(
                (
                    symbol,
                    date_ts.strftime("%Y-%m-%d"),
                    float(close_value),
                )
            )

        return rows

    except Exception:
        return []


# ---------------------------------------------------------
# Latest date from Yahoo frame
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Determine actual latest market date
# ---------------------------------------------------------

def _determine_reference_latest_date(
    symbols: List[str],
) -> Tuple[
    Optional[pd.Timestamp],
    Dict[str, pd.Timestamp],
]:
    """Detect newest valid market date from a small basket."""

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
            period=REFERENCE_PROBE_PERIOD,
        )

    except Exception:
        return None, {}

    for symbol in reference_symbols:

        try:

            frame = _extract_symbol_frame(
                raw,
                symbol,
                requested_ticker_count=len(
                    reference_symbols
                ),
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

    return (
        max(latest_dates.values()),
        latest_dates,
    )


# ---------------------------------------------------------
# Empty result
# ---------------------------------------------------------

def _empty_result() -> Dict:
    return {
        "total": 0,

        # Compatibility fields.
        "full": 0,
        "incremental": 0,
        "already_latest": 0,

        # Primary result fields.
        "updated": 0,
        "up_to_date": 0,
        "last_available": 0,
        "no_data": 0,

        "succeeded": 0,
        "failed": 0,

        "failed_symbols": [],

        # Diagnostic fields.
        "fetch_error": 0,
        "fetch_error_symbols": [],

        "updated_symbols": [],
        "up_to_date_symbols": [],
        "last_available_symbols": [],
        "no_data_symbols": [],

        "reference_latest_date": None,
        "market_data_through": None,
        "last_update_finished": None,

        "status": "SUCCESS",
    }


# ---------------------------------------------------------
# Main updater
# ---------------------------------------------------------

def update_symbols(
    db_path: str,
    symbols: List[str],
    on_progress: Optional[
        Callable[[int, int, str], None]
    ] = None,
) -> Dict:

    total = len(symbols)

    if total == 0:
        return _empty_result()

    conn = db.get_connection(
        db_path
    )

    try:

        # =====================================================
        # 1. Detect latest available market date ONCE.
        # =====================================================

        if on_progress:

            on_progress(
                0,
                total,
                "Detecting latest market date...",
            )

        reference_latest_date, _ = (
            _determine_reference_latest_date(
                symbols
            )
        )

        # =====================================================
        # 2. Read all DB latest dates ONCE.
        # =====================================================

        latest_dates = db.get_latest_dates(
            conn
        )

        # If Yahoo probe failed, use newest date already
        # present in DB.
        if reference_latest_date is None:

            stored_dates = []

            for value in latest_dates.values():

                ts = _normalise_date(
                    value
                )

                if ts is not None:
                    stored_dates.append(ts)

            if stored_dates:

                reference_latest_date = max(
                    stored_dates
                )

        # We cannot safely invent a target date when there
        # is neither Yahoo data nor DB data.
        if reference_latest_date is None:

            raise RuntimeError(
                "Could not determine latest market-data date."
            )

        target_date = reference_latest_date

        target_text = target_date.strftime(
            "%d-%b-%Y"
        )

        # =====================================================
        # 3. Show target date immediately.
        # =====================================================

        if on_progress:

            on_progress(
                0,
                total,
                (
                    "Updating market data through: "
                    + target_text
                ),
            )

        # =====================================================
        # 4. Classify symbols for network efficiency.
        #
        # This is internal only.
        #
        # User-visible workflow remains simply:
        # Updated / Last Available / No Data.
        # =====================================================

        new_symbols = []

        current_symbols = []

        date_buckets: Dict[
            pd.Timestamp,
            List[str],
        ] = {}

        for symbol in symbols:

            stored = latest_dates.get(
                symbol
            )

            stored_ts = _normalise_date(
                stored
            )

            if stored_ts is None:

                new_symbols.append(
                    symbol
                )

            elif stored_ts >= target_date:

                current_symbols.append(
                    symbol
                )

            else:

                date_buckets.setdefault(
                    stored_ts,
                    [],
                ).append(
                    symbol
                )

        # =====================================================
        # 5. Result containers.
        # =====================================================

        updated_symbols = []
        last_available_symbols = []
        no_data_symbols = []

        fetch_error_symbols = []

        done = 0

        def report_progress():

            if on_progress:

                on_progress(
                    done,
                    total,
                    (
                        "Updating market data through: "
                        + target_text
                    ),
                )

        # =====================================================
        # 6. Already-current symbols.
        #
        # No Yahoo request needed.
        # =====================================================

        for symbol in current_symbols:

            updated_symbols.append(
                symbol
            )

        done += len(
            current_symbols
        )

        if current_symbols:
            report_progress()

        # =====================================================
        # 7. Batch processor.
        # =====================================================

        def process_chunk(
            chunk: List[str],
            common_start: Optional[str],
            is_new: bool,
        ) -> None:

            nonlocal done

            if not chunk:
                return

            raw = pd.DataFrame()

            fetch_error = None

            # -------------------------------------------------
            # Initial download.
            # -------------------------------------------------

            for attempt in range(
                MAX_BATCH_RETRIES + 1
            ):

                try:

                    if is_new:

                        # IMPORTANT:
                        # New stocks receive maximum available
                        # daily history.
                        raw = _download_chunk_raw(
                            chunk,
                            period="max",
                        )

                    else:

                        end_date = (
                            target_date
                            + pd.Timedelta(days=1)
                        ).strftime(
                            "%Y-%m-%d"
                        )

                        raw = _download_chunk_raw(
                            chunk,
                            start=common_start,
                            end=end_date,
                        )

                    # If Yahoo returned usable batch data,
                    # stop retrying.
                    if (
                        raw is not None
                        and not raw.empty
                    ):
                        fetch_error = None
                        break

                    fetch_error = (
                        "Yahoo returned no usable batch data."
                    )

                except Exception as exc:

                    fetch_error = str(
                        exc
                    )

                    raw = pd.DataFrame()

                # Exactly one retry.
                if attempt < MAX_BATCH_RETRIES:
                    continue

            # -------------------------------------------------
            # Extract each ticker independently.
            # -------------------------------------------------

            batch_rows = []

            batch_symbols = set()

            for symbol in chunk:

                stored_ts = _normalise_date(
                    latest_dates.get(symbol)
                )

                # CRITICAL:
                # Extract this symbol ONLY.
                frame = _extract_symbol_frame(
                    raw,
                    symbol,
                    requested_ticker_count=len(
                        chunk
                    ),
                )

                if frame.empty:

                    if fetch_error:
                        fetch_error_symbols.append(
                            symbol
                        )

                    continue

                rows = _rows_from_frame(
                    symbol=symbol,
                    frame=frame,
                    target_date=target_date,
                    last_stored_date=stored_ts,
                )

                if not rows:
                    continue

                # NO MAX_CANDLES slicing here.
                #
                # Complete available daily history is retained.
                batch_rows.extend(
                    rows
                )

                batch_symbols.add(
                    symbol
                )

            # -------------------------------------------------
            # Insert all valid rows in one DB operation.
            # -------------------------------------------------

            inserted_symbols = set()

            if batch_rows:

                try:

                    db.insert_price_rows_batch(
                        conn,
                        batch_rows,
                    )

                    inserted_symbols = (
                        batch_symbols
                    )

                except Exception:

                    # DB failure must not make us pretend
                    # the rows were successfully stored.
                    inserted_symbols = set()

            # -------------------------------------------------
            # Update in-memory latest dates ONLY after DB
            # insertion succeeds.
            # -------------------------------------------------

            if inserted_symbols:

                latest_saved_by_symbol = {}

                for (
                    symbol,
                    date_text,
                    close,
                ) in batch_rows:

                    date_ts = _normalise_date(
                        date_text
                    )

                    if date_ts is None:
                        continue

                    previous = (
                        latest_saved_by_symbol.get(
                            symbol
                        )
                    )

                    if (
                        previous is None
                        or date_ts > previous
                    ):

                        latest_saved_by_symbol[
                            symbol
                        ] = date_ts

                for (
                    symbol,
                    saved_latest,
                ) in latest_saved_by_symbol.items():

                    latest_dates[
                        symbol
                    ] = saved_latest

                    if saved_latest >= target_date:

                        updated_symbols.append(
                            symbol
                        )

                    else:

                        last_available_symbols.append(
                            symbol
                        )

            # -------------------------------------------------
            # One batch = one progress increment.
            #
            # NEVER reset done.
            # -------------------------------------------------

            done += len(
                chunk
            )

            report_progress()

        # =====================================================
        # 8. NEW SYMBOLS
        #
        # Full maximum available DAILY history.
        # =====================================================

        for start_index in range(
            0,
            len(new_symbols),
            CHUNK_SIZE,
        ):

            chunk = new_symbols[
                start_index:
                start_index + CHUNK_SIZE
            ]

            process_chunk(
                chunk=chunk,
                common_start=None,
                is_new=True,
            )

        # =====================================================
        # 9. EXISTING SYMBOLS
        #
        # Fetch only missing dates.
        #
        # Group by actual stored date so one old stock does
        # not force the whole batch to download unnecessary
        # historical dates.
        # =====================================================

        for stored_date in sorted(
            date_buckets.keys()
        ):

            bucket = date_buckets[
                stored_date
            ]

            start_date = (
                stored_date
                + pd.Timedelta(days=1)
            ).strftime(
                "%Y-%m-%d"
            )

            for start_index in range(
                0,
                len(bucket),
                CHUNK_SIZE,
            ):

                chunk = bucket[
                    start_index:
                    start_index + CHUNK_SIZE
                ]

                process_chunk(
                    chunk=chunk,
                    common_start=start_date,
                    is_new=False,
                )

        # =====================================================
        # 10. Final classification.
        #
        # Updated:
        #     DB reached target date.
        #
        # Last Available:
        #     Existing data remains, but target was not reached.
        #
        # No Data:
        #     No previous data and Yahoo produced nothing usable.
        # =====================================================

        updated_set = set(
            updated_symbols
        )

        last_available_set = set(
            last_available_symbols
        )

        no_data_set = set()

        for symbol in symbols:

            if symbol in updated_set:
                continue

            original_date = _normalise_date(
                latest_dates.get(symbol)
            )

            if (
                original_date is not None
                and original_date < target_date
            ):

                last_available_set.add(
                    symbol
                )

            elif symbol in new_symbols:

                no_data_set.add(
                    symbol
                )

            else:

                # Defensive fallback.
                if original_date is not None:
                    last_available_set.add(
                        symbol
                    )
                else:
                    no_data_set.add(
                        symbol
                    )

        # Preserve original symbol order.

        updated_symbols = [
            symbol
            for symbol in symbols
            if symbol in updated_set
        ]

        last_available_symbols = [
            symbol
            for symbol in symbols
            if (
                symbol in last_available_set
                and symbol not in updated_set
            )
        ]

        no_data_symbols = [
            symbol
            for symbol in symbols
            if (
                symbol in no_data_set
                and symbol not in updated_set
                and symbol not in last_available_set
            )
        ]

        # =====================================================
        # 11. Final counts.
        # =====================================================

        updated_count = len(
            updated_symbols
        )

        last_available_count = len(
            last_available_symbols
        )

        no_data_count = len(
            no_data_symbols
        )

        succeeded = (
            updated_count
            + last_available_count
            + no_data_count
        )

        # Stock-level fetch problems never become an overall
        # update crash/failure.
        failed = 0

        # =====================================================
        # 12. Final progress.
        # =====================================================

        if on_progress:

            on_progress(
                total,
                total,
                "Update complete",
            )

        # =====================================================
        # 13. Compatibility result.
        # =====================================================

        return {
            "total": total,

            # Compatibility fields.
            "full": len(
                new_symbols
            ),

            "incremental": sum(
                len(value)
                for value in date_buckets.values()
            ),

            "already_latest": len(
                current_symbols
            ),

            # Primary status counts.
            "updated": updated_count,
            "up_to_date": 0,
            "last_available": last_available_count,
            "no_data": no_data_count,

            "succeeded": succeeded,
            "failed": failed,

            # Overall update never reports stock-level
            # Yahoo problems as fatal failures.
            "failed_symbols": [],

            # Diagnostic only.
            "fetch_error": len(
                fetch_error_symbols
            ),

            "fetch_error_symbols": (
                fetch_error_symbols[:50]
            ),

            "updated_symbols": (
                updated_symbols[:100]
            ),

            "up_to_date_symbols": [],

            "last_available_symbols": (
                last_available_symbols[:100]
            ),

            "no_data_symbols": (
                no_data_symbols[:100]
            ),

            "reference_latest_date": (
                target_date.strftime(
                    "%Y-%m-%d"
                )
            ),

            "market_data_through": (
                target_date.strftime(
                    "%d-%m-%Y"
                )
            ),

            "last_update_finished": (
                datetime.now().strftime(
                    "%d-%m-%Y %H:%M:%S"
                )
            ),

            "status": "SUCCESS",
        }

    finally:

        conn.close()

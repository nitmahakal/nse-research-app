"""Raw market-data downloader/updater.

IMPORTANT:
Update does ZERO indicator/scanner calculations.
It only downloads raw daily close data and stores it in SQLite.

UPDATE RULE:
Update means: available market data through the latest valid
market date detected from actual Yahoo Finance data.

IMPORTANT BATCH SAFETY:
Every ticker in a Yahoo batch is extracted from that ticker's
own columns only. Dates and Close values are never shared between
different tickers.
"""

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

import db


MAX_CANDLES = 2000
CHUNK_SIZE = 50

# Small probe used only to discover the actual latest market date.
REFERENCE_PROBE_PERIOD = "5d"


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
    requested_ticker_count: int = 1,
) -> pd.DataFrame:
    """Extract ONLY one ticker's own OHLC frame from a Yahoo response."""

    if raw is None or raw.empty:
        return pd.DataFrame()

    try:
        if isinstance(raw.columns, pd.MultiIndex):

            level0 = raw.columns.get_level_values(0)
            level1 = raw.columns.get_level_values(1)

            # Normal yfinance group_by="ticker" layout:
            # (SYMBOL, Open/High/Low/Close/...)
            if symbol in level0:
                frame = raw[symbol].copy()

            # Defensive support for the opposite MultiIndex layout:
            # (Open/High/Low/Close/..., SYMBOL)
            elif symbol in level1:
                frame = raw.xs(
                    symbol,
                    axis=1,
                    level=1,
                ).copy()

            else:
                # This ticker is not present in this batch.
                return pd.DataFrame()

        else:
            # A flat response is safe only when exactly one ticker
            # was requested. Never treat a flat multi-ticker response
            # as belonging to every ticker.
            if requested_ticker_count != 1:
                return pd.DataFrame()

            frame = raw.copy()

        if "Close" not in frame.columns:
            return pd.DataFrame()

        # Keep ONLY Close from this ticker's own frame.
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
    end: Optional[str] = None,
    period: Optional[str] = None,
) -> pd.DataFrame:
    """Download one batch from Yahoo Finance."""

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

    if end:
        kwargs["end"] = end

    if period:
        kwargs["period"] = period

    return yf.download(**kwargs)


def _rows_from_frame(
    symbol: str,
    frame: pd.DataFrame,
    target_date: pd.Timestamp,
    last_stored_date: Optional[pd.Timestamp],
) -> List[Tuple[str, str, float]]:
    """Convert one ticker's own frame into safe DB rows."""

    rows = []

    if frame is None or frame.empty:
        return rows

    for date, row in frame.iterrows():

        close = row.get("Close")

        if pd.isna(close):
            continue

        date_ts = _normalise_date(date)

        if date_ts is None:
            continue

        # Never save data beyond the target market date.
        if date_ts > target_date:
            continue

        # Existing data must never be downloaded/saved again.
        if (
            last_stored_date is not None
            and date_ts <= last_stored_date
        ):
            continue

        rows.append(
            (
                symbol,
                date_ts.strftime("%Y-%m-%d"),
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
) -> Tuple[
    Optional[pd.Timestamp],
    Dict[str, pd.Timestamp],
]:
    """Get newest valid market date from a small reference basket."""

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

    return max(
        latest_dates.values()
    ), latest_dates


def _empty_result() -> Dict:
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
        "updated_symbols": [],
        "up_to_date_symbols": [],
        "last_available_symbols": [],
        "no_data_symbols": [],
        "reference_latest_date": None,
        "market_data_through": None,
        "last_update_finished": None,
        "status": "SUCCESS",
    }


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

        # =========================================================
        # STEP 1
        # Detect the latest real market date ONCE.
        # =========================================================

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

        # =========================================================
        # STEP 2
        # Read latest stored date for every symbol ONCE.
        # =========================================================

        latest_dates = db.get_latest_dates(
            conn
        )

        # If Yahoo reference probe failed, use the newest date
        # already stored in the DB as a safe fallback.
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

        # If there is absolutely no market-date information,
        # this is a genuine catastrophic condition. We cannot
        # truthfully tell the user what date is being updated through.
        if reference_latest_date is None:

            raise RuntimeError(
                "Could not determine latest market-data date."
            )

        target_date = reference_latest_date

        target_text = target_date.strftime(
            "%d-%b-%Y"
        )

        if on_progress:
            on_progress(
                0,
                total,
                (
                    "Updating market data through: "
                    + target_text
                ),
            )

        # =========================================================
        # STEP 3
        # Build simple network buckets.
        #
        # This is NOT a full/incremental/already-latest workflow.
        # Buckets only prevent one stale stock from forcing an
        # unnecessarily old download for the other 49 stocks.
        # =========================================================

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

        # =========================================================
        # STEP 4
        # Final public status containers.
        # =========================================================

        updated_symbols = []
        last_available_symbols = []
        no_data_symbols = []

        fetch_error_symbols = []
        problems: Dict[str, str] = {}

        done = 0

        def report_progress(
            message: str,
        ) -> None:

            if on_progress:
                on_progress(
                    done,
                    total,
                    message,
                )

        # =========================================================
        # STEP 5
        # Symbols already at target:
        # They are simply UPDATED/current.
        # No Yahoo request.
        # =========================================================

        for symbol in current_symbols:

            updated_symbols.append(
                symbol
            )

        done += len(
            current_symbols
        )

        if current_symbols:
            report_progress(
                (
                    "Updating market data through: "
                    + target_text
                )
            )

        # =========================================================
        # Helper:
        # Process one 50-ticker Yahoo batch.
        # =========================================================

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

            try:

                if is_new:

                    # IMPORTANT:
                    # Do NOT combine period and end here.
                    # Fetch 2 years, then filter locally to target.
                    raw = _download_chunk_raw(
                        chunk,
                        period="2y",
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

            except Exception as exc:

                fetch_error = str(
                    exc
                )

            batch_rows = []

            # Keep track of which symbols actually contributed
            # rows to this DB batch.
            batch_symbols = set()

            # =====================================================
            # CRITICAL SAFETY:
            # Each symbol gets ONLY its own frame.
            # No frame is reused for another symbol.
            # =====================================================

            for symbol in chunk:

                stored_ts = _normalise_date(
                    latest_dates.get(symbol)
                )

                frame = _extract_symbol_frame(
                    raw,
                    symbol,
                    requested_ticker_count=len(
                        chunk
                    ),
                )

                if frame.empty:

                    if fetch_error:
                        problems[symbol] = (
                            "FETCH_ERROR: "
                            + fetch_error
                        )

                        if symbol not in fetch_error_symbols:
                            fetch_error_symbols.append(
                                symbol
                            )

                    else:
                        problems[symbol] = (
                            "NO_DATA"
                        )

                    continue

                rows = _rows_from_frame(
                    symbol=symbol,
                    frame=frame,
                    target_date=target_date,
                    last_stored_date=stored_ts,
                )

                if not rows:

                    problems[symbol] = (
                        "NO_NEW_VALID_DATA"
                    )

                    continue

                rows = rows[
                    -MAX_CANDLES:
                ]

                batch_rows.extend(
                    rows
                )

                batch_symbols.add(
                    symbol
                )

            # =====================================================
            # Save all valid ticker rows.
            # =====================================================

            inserted_ok = False

            if batch_rows:

                try:

                    db.insert_price_rows_batch(
                        conn,
                        batch_rows,
                    )

                    inserted_ok = True

                except Exception as exc:

                    reason = (
                        "DB_INSERT_ERROR: "
                        + str(exc)
                    )

                    for symbol in batch_symbols:
                        problems[symbol] = reason

            # =====================================================
            # IMPORTANT:
            # Only update our in-memory latest_dates AFTER the
            # corresponding DB insert succeeds.
            # =====================================================

            if inserted_ok:

                latest_saved_by_symbol = {}

                for symbol, date_text, close in batch_rows:

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

                for symbol, saved_latest in (
                    latest_saved_by_symbol.items()
                ):

                    latest_dates[
                        symbol
                    ] = saved_latest

                    if saved_latest >= target_date:

                        updated_symbols.append(
                            symbol
                        )

                    else:

                        # Data was saved, but it did not reach
                        # the current target date.
                        last_available_symbols.append(
                            symbol
                        )

            # =====================================================
            # Every ticker in this chunk is now accounted for.
            # =====================================================

            done += len(
                chunk
            )

            report_progress(
                (
                    "Updating market data through: "
                    + target_text
                )
            )

        # =========================================================
        # STEP 6
        # New symbols:
        # 2 years, 50 at a time.
        # =========================================================

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

        # =========================================================
        # STEP 7
        # Existing symbols:
        # Group only by their actual stored latest date.
        #
        # Example:
        # 30 symbols latest=2026-09-09
        # 20 symbols latest=2026-09-08
        #
        # This avoids downloading from 2026-09-08 for all 50
        # just because one ticker is one day older.
        # =========================================================

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

        # =========================================================
        # STEP 8
        # Final classification for every symbol.
        #
        # Public statuses:
        #   Updated
        #   Last Available
        #   No Data
        #
        # Individual fetch problems do NOT become "Failed".
        # =========================================================

        updated_set = set(
            updated_symbols
        )

        last_available_set = set(
            last_available_symbols
        )

        no_data_set = set(
            no_data_symbols
        )

        # Symbols not already current and not successfully saved
        # need their final status determined from the original DB
        # date plus the problem/fetch result.
        for symbol in symbols:

            if symbol in updated_set:
                continue

            original_ts = _normalise_date(
                latest_dates.get(symbol)
            )

            # If we did not have original data and the fetch did
            # not produce valid rows, this is genuinely No Data.
            if symbol in new_symbols:

                if symbol not in updated_set:

                    no_data_set.add(
                        symbol
                    )

                continue

            # Existing data but no new valid data:
            # keep the old data and call it Last Available.
            if symbol not in updated_set:

                last_available_set.add(
                    symbol
                )

        # Rebuild lists in original symbol order.
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

        # =========================================================
        # STEP 9
        # Compatibility fields.
        # Keep old fields so app_bridge/MainActivity don't need
        # to change just because the updater was simplified.
        # =========================================================

        updated_count = len(
            updated_symbols
        )

        last_available_count = len(
            last_available_symbols
        )

        no_data_count = len(
            no_data_symbols
        )

        # Public updater completion is SUCCESS as long as the
        # engine completed its work. Stock-level Yahoo problems
        # do not make the whole update fail.
        succeeded = (
            updated_count
            + last_available_count
            + no_data_count
        )

        # "failed" remains zero for stock-level fetch problems.
        failed = 0

        if on_progress:
            on_progress(
                total,
                total,
                "Update complete",
            )

        market_data_through = (
            target_date.strftime(
                "%d-%m-%Y"
            )
        )

        return {
            "total": total,

            # Compatibility fields.
            # These are counts of network buckets, not workflow
            # phases shown to the user.
            "full": len(new_symbols),
            "incremental": sum(
                len(value)
                for value in date_buckets.values()
            ),
            "already_latest": len(
                current_symbols
            ),

            # Primary statuses.
            "updated": updated_count,
            "up_to_date": 0,
            "last_available": last_available_count,
            "no_data": no_data_count,

            "succeeded": succeeded,
            "failed": failed,

            # No stock-level failure is exposed as overall failure.
            "failed_symbols": [],

            # Diagnostic information only.
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
                market_data_through
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

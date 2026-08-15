"""
Scanner core: reads price series from SQLite (via db.py), builds the
requested timeframe (via timeframes.py), evaluates conditions (via
conditions.py), and returns matches.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import db
from conditions import ConditionSet, evaluate_condition_set, ConditionError
from timeframes import resample_close, resolve_requested_timeframes, TimeframeError


class DatabaseLoadError(Exception):
    pass


@dataclass
class ScanMatch:
    symbol: str
    timeframe: str
    close: float
    values: Dict[str, float]


@dataclass
class ScanSummary:
    matches: List[ScanMatch] = field(default_factory=list)
    total_symbols: int = 0
    processed: int = 0
    matched_count: int = 0
    failed: int = 0
    skipped_insufficient_data: int = 0
    failed_symbols: List[Tuple[str, str]] = field(default_factory=list)


MIN_REQUIRED_CANDLES = 5


class Scanner:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = db.get_connection(db_path)

    def list_available_symbols(self) -> List[str]:
        return db.list_available_symbols(self.conn)

    def load_price_dataframe(self, symbol: str):
        df = db.get_price_series(self.conn, symbol)
        if df.empty:
            raise DatabaseLoadError(f"{symbol}: no data in database")
        if len(df) < MIN_REQUIRED_CANDLES:
            raise DatabaseLoadError(f"{symbol}: insufficient history ({len(df)} rows)")
        return df

    def run_scan(
        self, condition_set: ConditionSet, timeframe: str, symbols: Optional[List[str]] = None
    ) -> ScanSummary:
        if symbols is None:
            symbols = self.list_available_symbols()

        timeframes = resolve_requested_timeframes(timeframe)
        summary = ScanSummary(total_symbols=len(symbols))

        for symbol in symbols:
            try:
                daily_df = self.load_price_dataframe(symbol)
            except DatabaseLoadError as exc:
                summary.failed += 1
                summary.failed_symbols.append((symbol, str(exc)))
                summary.processed += 1
                continue

            for tf in timeframes:
                try:
                    tf_df = resample_close(daily_df, tf)
                    close_series = tf_df["Close"]
                    if len(close_series.dropna()) < MIN_REQUIRED_CANDLES:
                        summary.skipped_insufficient_data += 1
                        continue
                    matched, values = evaluate_condition_set(condition_set, close_series)
                    if matched:
                        summary.matches.append(
                            ScanMatch(symbol, tf, float(close_series.iloc[-1]), values)
                        )
                        summary.matched_count += 1
                except (TimeframeError, ConditionError):
                    summary.skipped_insufficient_data += 1
                    continue
                except Exception:
                    summary.skipped_insufficient_data += 1
                    continue

            summary.processed += 1

        return summary

    def close(self):
        self.conn.close()

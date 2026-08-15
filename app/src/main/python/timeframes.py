"""
Timeframe engine.

IMPORTANT: unlike a naive pandas .resample(), this groups by the
real calendar period (week/month/quarter/half-year/year) but always
keeps the ACTUAL last trading date within that period as the row's
date - never an artificial calendar boundary.
"""

import pandas as pd

CONCRETE_TIMEFRAMES = ["Daily", "Weekly", "Monthly", "Quarterly", "Six Month", "Yearly"]


class TimeframeError(Exception):
    pass


def _period_key(dates: pd.Series, timeframe: str) -> pd.Series:
    if timeframe == "Weekly":
        return dates.dt.to_period("W-FRI")
    if timeframe == "Monthly":
        return dates.dt.to_period("M")
    if timeframe == "Quarterly":
        return dates.dt.to_period("Q")
    if timeframe == "Six Month":
        half = ((dates.dt.month - 1) // 6) + 1
        return dates.dt.year.astype(str) + "-H" + half.astype(str)
    if timeframe == "Yearly":
        return dates.dt.to_period("Y")
    raise TimeframeError(f"Unsupported timeframe: {timeframe}")


def resample_close(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise TimeframeError("Cannot build timeframe series from empty data")
    if "Date" not in df.columns or "Close" not in df.columns:
        raise TimeframeError("Data missing required Date/Close columns")

    if timeframe == "Daily":
        return df[["Date", "Close"]].reset_index(drop=True)

    if timeframe not in CONCRETE_TIMEFRAMES:
        raise TimeframeError(f"Unknown timeframe requested: {timeframe}")

    key = _period_key(df["Date"], timeframe)
    grouped = (
        df.assign(_period=key)
        .groupby("_period", sort=True)
        .agg(Date=("Date", "last"), Close=("Close", "last"))
        .reset_index(drop=True)
    )
    grouped = grouped.sort_values("Date").reset_index(drop=True)
    return grouped


def resolve_requested_timeframes(timeframe: str):
    if timeframe == "All Timeframes":
        return list(CONCRETE_TIMEFRAMES)
    if timeframe not in CONCRETE_TIMEFRAMES:
        raise TimeframeError(f"Invalid timeframe: {timeframe}")
    return [timeframe]

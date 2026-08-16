"""Timeframe engine - holiday-safe, uses real last trading dates."""
import pandas as pd

CONCRETE_TIMEFRAMES = ["Daily", "Weekly", "Monthly", "Quarterly", "Six Month", "Yearly"]


class TimeframeError(Exception):
    pass


def _period_key(dates, timeframe):
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


def resample_close(df, timeframe):
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
        df.assign(_period=key).groupby("_period", sort=True)
        .agg(Date=("Date", "last"), Close=("Close", "last")).reset_index(drop=True)
    )
    return grouped.sort_values("Date").reset_index(drop=True)


def resolve_requested_timeframes(timeframe):
    if timeframe == "All Timeframes":
        return list(CONCRETE_TIMEFRAMES)
    if timeframe not in CONCRETE_TIMEFRAMES:
        raise TimeframeError(f"Invalid timeframe: {timeframe}")
    return [timeframe]


def count_periods_between(df, timeframe, start_date, end_date) -> int:
    """
    Counts how many completed bars of `timeframe` exist strictly AFTER
    start_date up to and including end_date. This is what drives the
    Research Engine's periods_elapsed / 6-period maturation rule -
    it reuses the exact same resampling as the Scanner so a "period"
    always means a real trading bar, never a raw calendar count.
    """
    tf_df = resample_close(df, timeframe)
    mask = (tf_df["Date"] > pd.Timestamp(start_date)) & (tf_df["Date"] <= pd.Timestamp(end_date))
    return int(mask.sum())

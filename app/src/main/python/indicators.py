"""
TradingView-accurate indicator math.
Same formulas as the Colab scanner (SMA-seeded EMA/RMA, Wilder RSI,
HMA via WMA, MACD, Stochastic RSI, Reverse RSI Price Level).
"""

import math
from typing import Tuple

import numpy as np
import pandas as pd


class IndicatorError(Exception):
    pass


def _validate_length(length, name: str) -> int:
    try:
        n = int(length)
    except Exception as exc:
        raise IndicatorError(f"{name}: length must be an integer ({exc})") from exc
    if n < 1:
        raise IndicatorError(f"{name}: length must be >= 1")
    return n


def _seed_ema_like(values: np.ndarray, length: int, wilder: bool) -> np.ndarray:
    n = len(values)
    result = np.full(n, np.nan, dtype=float)
    valid_mask = ~np.isnan(values)
    first_valid = np.argmax(valid_mask) if valid_mask.any() else -1
    if first_valid == -1 or (n - first_valid) < length:
        raise IndicatorError("Insufficient contiguous data to seed moving average")

    seed_end = first_valid + length
    seed_window = values[first_valid:seed_end]
    if np.isnan(seed_window).any():
        raise IndicatorError("NaN values present in seed window")

    seed_value = seed_window.mean()
    result[seed_end - 1] = seed_value
    prev = seed_value
    alpha = (1.0 / length) if wilder else (2.0 / (length + 1.0))

    for i in range(seed_end, n):
        val = values[i]
        if np.isnan(val):
            result[i] = np.nan
            continue
        if wilder:
            prev = (prev * (length - 1) + val) / length
        else:
            prev = alpha * val + (1.0 - alpha) * prev
        result[i] = prev
    return result


def ema_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "EMA")
    values = series.to_numpy(dtype=float)
    result = _seed_ema_like(values, length, wilder=False)
    return pd.Series(result, index=series.index, name=f"EMA_{length}")


def rma_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "RMA")
    values = series.to_numpy(dtype=float)
    result = _seed_ema_like(values, length, wilder=True)
    return pd.Series(result, index=series.index, name=f"RMA_{length}")


def sma_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "SMA")
    return series.rolling(window=length, min_periods=length).mean()


def wma_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "WMA")
    weights = np.arange(1, length + 1, dtype=float)
    weight_sum = weights.sum()

    def _window(w):
        return float(np.dot(w, weights) / weight_sum)

    return series.rolling(window=length, min_periods=length).apply(_window, raw=True)


def rsi_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "RSI")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma_tv(gain, length)
    avg_loss = rma_tv(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    rsi = rsi.where(~((avg_loss == 0.0) & (avg_gain == 0.0)), 50.0)
    rsi.name = f"RSI_{length}"
    return rsi


def ema_of_rsi_tv(series: pd.Series, rsi_length, ema_length) -> pd.Series:
    return ema_tv(rsi_tv(series, rsi_length), ema_length).rename(
        f"EMA_RSI_{rsi_length}_{ema_length}"
    )


def hma_tv(series: pd.Series, length) -> pd.Series:
    length = _validate_length(length, "HMA")
    half = max(1, round(length / 2.0))
    sqrt_len = max(1, round(math.sqrt(length)))
    diff = 2.0 * wma_tv(series, half) - wma_tv(series, length)
    return wma_tv(diff, sqrt_len).rename(f"HMA_{length}")


def macd_tv(series: pd.Series, fast_length, slow_length, signal_length) -> Tuple[pd.Series, pd.Series, pd.Series]:
    fast_length = _validate_length(fast_length, "MACD fast")
    slow_length = _validate_length(slow_length, "MACD slow")
    signal_length = _validate_length(signal_length, "MACD signal")
    if fast_length >= slow_length:
        raise IndicatorError("MACD: fast_length must be smaller than slow_length")
    macd_line = (ema_tv(series, fast_length) - ema_tv(series, slow_length)).rename(
        f"MACD_{fast_length}_{slow_length}"
    )
    signal_line = ema_tv(macd_line, signal_length).rename(
        f"MACD_Signal_{fast_length}_{slow_length}_{signal_length}"
    )
    histogram = (macd_line - signal_line).rename(f"MACD_Hist_{fast_length}_{slow_length}_{signal_length}")
    return macd_line, signal_line, histogram


def stoch_rsi_tv(series: pd.Series, rsi_length, stoch_length, k_length, d_length) -> Tuple[pd.Series, pd.Series]:
    rsi_val = rsi_tv(series, rsi_length)
    stoch_length = _validate_length(stoch_length, "Stoch length")
    k_length = _validate_length(k_length, "%K length")
    d_length = _validate_length(d_length, "%D length")
    lowest = rsi_val.rolling(window=stoch_length, min_periods=stoch_length).min()
    highest = rsi_val.rolling(window=stoch_length, min_periods=stoch_length).max()
    denom = (highest - lowest).replace(0.0, np.nan)
    stoch = ((rsi_val - lowest) / denom) * 100.0
    stoch = stoch.where(~((highest - lowest) == 0.0), 0.0)
    k_line = sma_tv(stoch, k_length).rename(f"StochRSI_K_{rsi_length}_{stoch_length}_{k_length}_{d_length}")
    d_line = sma_tv(k_line, d_length).rename(f"StochRSI_D_{rsi_length}_{stoch_length}_{k_length}_{d_length}")
    return k_line, d_line


def reverse_rsi_price_level_tv(series: pd.Series, target_rsi: float, rsi_length, smoothing_length) -> pd.Series:
    rsi_length = _validate_length(rsi_length, "Reverse RSI rsi_length")
    smoothing_length = _validate_length(smoothing_length, "Reverse RSI smoothing_length")

    target_rsi = float(target_rsi)
    if not (0.0 < target_rsi < 100.0):
        raise IndicatorError(f"Reverse RSI: target_rsi must be between 0 and 100, got {target_rsi}")

    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma_tv(gain, rsi_length)
    avg_loss = rma_tv(loss, rsi_length)

    U0 = avg_gain.shift(1).to_numpy(dtype=float)
    D0 = avg_loss.shift(1).to_numpy(dtype=float)
    prev_close = series.shift(1).to_numpy(dtype=float)

    target_rs = target_rsi / (100.0 - target_rsi)

    with np.errstate(divide="ignore", invalid="ignore"):
        current_rs = np.where(D0 == 0.0, np.inf, U0 / np.where(D0 == 0.0, 1.0, D0))
        use_up = target_rs > current_rs
        up_branch = prev_close + (rsi_length - 1) * (target_rs * D0 - U0)
        down_branch = prev_close - (rsi_length - 1) * ((U0 / target_rs) - D0)

    raw = np.where(use_up, up_branch, down_branch)
    invalid_mask = np.isnan(U0) | np.isnan(D0) | np.isnan(prev_close)
    raw = np.where(invalid_mask, np.nan, raw)

    raw_series = pd.Series(raw, index=series.index)
    smoothed = ema_tv(raw_series, smoothing_length)
    smoothed.name = f"ReverseRSI_{int(target_rsi)}_{rsi_length}_{smoothing_length}"
    return smoothed

"""
Condition builder + evaluator, plus the exit-condition derivation
rule agreed on for the Research Engine (Cross Above/Below -> hold
state; Greater/Less -> same state flips to exit).
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from indicators import (
    IndicatorError, ema_tv, hma_tv, rsi_tv, ema_of_rsi_tv, macd_tv,
    stoch_rsi_tv, reverse_rsi_price_level_tv,
)

INDICATOR_PARAMS: Dict[str, List[str]] = {
    "Close": [],
    "EMA": ["length"],
    "HMA": ["length"],
    "RSI": ["length"],
    "EMA of RSI": ["rsi_length", "ema_length"],
    "MACD": ["fast_length", "slow_length", "signal_length"],
    "MACD Signal": ["fast_length", "slow_length", "signal_length"],
    "MACD Histogram": ["fast_length", "slow_length", "signal_length"],
    "Stoch RSI %K": ["rsi_length", "stoch_length", "k_length", "d_length"],
    "Stoch RSI %D": ["rsi_length", "stoch_length", "k_length", "d_length"],
    "Numeric Value": ["value"],
    "Reverse RSI 40": ["rsi_length", "smoothing_length"],
    "Reverse RSI 50": ["rsi_length", "smoothing_length"],
    "Reverse RSI 60": ["rsi_length", "smoothing_length"],
}

COMPARATORS = ("Equal", "Greater", "Greater Equal", "Less", "Less Equal", "Cross Above", "Cross Below")
_CROSS_COMPARATORS = {"Cross Above", "Cross Below"}
_ENTRY_TO_HOLD_STATE = {
    "Cross Above": "Greater",
    "Cross Below": "Less",
    "Greater": "Greater",
    "Greater Equal": "Greater Equal",
    "Less": "Less",
    "Less Equal": "Less Equal",
    "Equal": "Equal",
}


class ConditionError(Exception):
    pass


@dataclass(frozen=True)
class IndicatorSpec:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.name not in INDICATOR_PARAMS:
            raise ConditionError(f"Unknown indicator: {self.name}")
        missing = set(INDICATOR_PARAMS[self.name]) - set(self.params.keys())
        if missing:
            raise ConditionError(f"{self.name}: missing parameter(s) {sorted(missing)}")

    def to_dict(self):
        return {"name": self.name, "params": self.params}

    @staticmethod
    def from_dict(data):
        return IndicatorSpec(name=data["name"], params=data["params"])

    def label(self) -> str:
        n, p = self.name, self.params
        if n == "Close":
            return "Close"
        if n == "Numeric Value":
            v = float(p["value"])
            return str(int(v)) if v.is_integer() else f"{v:g}"
        if n == "EMA":
            return f"EMA({p['length']})"
        if n == "HMA":
            return f"HMA({p['length']})"
        if n == "RSI":
            return f"RSI({p['length']})"
        if n == "EMA of RSI":
            return f"EMA(RSI({p['rsi_length']}),{p['ema_length']})"
        if n == "MACD":
            return f"MACD({p['fast_length']},{p['slow_length']},{p['signal_length']})"
        if n == "MACD Signal":
            return f"MACD Signal({p['fast_length']},{p['slow_length']},{p['signal_length']})"
        if n == "MACD Histogram":
            return f"MACD Hist({p['fast_length']},{p['slow_length']},{p['signal_length']})"
        if n == "Stoch RSI %K":
            return f"StochRSI %K({p['rsi_length']},{p['stoch_length']},{p['k_length']},{p['d_length']})"
        if n == "Stoch RSI %D":
            return f"StochRSI %D({p['rsi_length']},{p['stoch_length']},{p['k_length']},{p['d_length']})"
        if n in ("Reverse RSI 40", "Reverse RSI 50", "Reverse RSI 60"):
            target = n.rsplit(" ", 1)[-1]
            return f"RevRSI{target}({p['rsi_length']},{p['smoothing_length']})"
        raise ConditionError(f"Cannot label unknown indicator: {n}")


def compute_indicator(spec: IndicatorSpec, close: pd.Series):
    n, p = spec.name, spec.params
    try:
        if n == "Close":
            return close.copy()
        if n == "Numeric Value":
            return float(p["value"])
        if n == "EMA":
            return ema_tv(close, p["length"])
        if n == "HMA":
            return hma_tv(close, p["length"])
        if n == "RSI":
            return rsi_tv(close, p["length"])
        if n == "EMA of RSI":
            return ema_of_rsi_tv(close, p["rsi_length"], p["ema_length"])
        if n == "MACD":
            line, _, _ = macd_tv(close, p["fast_length"], p["slow_length"], p["signal_length"])
            return line
        if n == "MACD Signal":
            _, sig, _ = macd_tv(close, p["fast_length"], p["slow_length"], p["signal_length"])
            return sig
        if n == "MACD Histogram":
            _, _, hist = macd_tv(close, p["fast_length"], p["slow_length"], p["signal_length"])
            return hist
        if n == "Stoch RSI %K":
            k, _ = stoch_rsi_tv(close, p["rsi_length"], p["stoch_length"], p["k_length"], p["d_length"])
            return k
        if n == "Stoch RSI %D":
            _, d = stoch_rsi_tv(close, p["rsi_length"], p["stoch_length"], p["k_length"], p["d_length"])
            return d
        if n == "Reverse RSI 40":
            return reverse_rsi_price_level_tv(close, 40.0, p["rsi_length"], p["smoothing_length"])
        if n == "Reverse RSI 50":
            return reverse_rsi_price_level_tv(close, 50.0, p["rsi_length"], p["smoothing_length"])
        if n == "Reverse RSI 60":
            return reverse_rsi_price_level_tv(close, 60.0, p["rsi_length"], p["smoothing_length"])
    except IndicatorError:
        raise
    except Exception as exc:
        raise IndicatorError(f"{n}: computation error ({exc})") from exc
    raise IndicatorError(f"Unhandled indicator: {n}")


@dataclass(frozen=True)
class Condition:
    left: IndicatorSpec
    comparator: str
    right: IndicatorSpec

    def __post_init__(self):
        if self.comparator not in COMPARATORS:
            raise ConditionError(f"Invalid comparator: {self.comparator}")

    def to_dict(self):
        return {"left": self.left.to_dict(), "comparator": self.comparator, "right": self.right.to_dict()}

    @staticmethod
    def from_dict(data):
        return Condition(
            left=IndicatorSpec.from_dict(data["left"]),
            comparator=data["comparator"],
            right=IndicatorSpec.from_dict(data["right"]),
        )

    def label(self) -> str:
        return f"{self.left.label()} {self.comparator} {self.right.label()}"

    def derive_exit_condition(self) -> "Condition":
        hold_state = _ENTRY_TO_HOLD_STATE.get(self.comparator)
        if hold_state is None:
            raise ConditionError(f"No exit rule for comparator: {self.comparator}")
        return Condition(left=self.left, comparator=hold_state, right=self.right)

    def is_cross_type(self) -> bool:
        return self.comparator in _CROSS_COMPARATORS


@dataclass
class ConditionEntry:
    condition: Condition
    logic: Optional[str] = None

    def to_dict(self):
        return {"condition": self.condition.to_dict(), "logic": self.logic}

    @staticmethod
    def from_dict(data):
        return ConditionEntry(condition=Condition.from_dict(data["condition"]), logic=data.get("logic"))


ConditionSet = List[ConditionEntry]


def condition_set_label(condition_set: ConditionSet) -> str:
    parts = []
    for entry in condition_set:
        parts.append(entry.condition.label())
        if entry.logic:
            parts.append(entry.logic)
    return " ".join(parts)


def _last(x) -> float:
    if isinstance(x, pd.Series):
        return float(x.iloc[-1]) if not x.empty else math.nan
    return float(x)


def _prev(x) -> float:
    if isinstance(x, pd.Series):
        return float(x.iloc[-2]) if len(x) >= 2 else math.nan
    return float(x)


def _apply_comparator(comparator: str, left, right) -> bool:
    l_last, r_last = _last(left), _last(right)
    if comparator in _CROSS_COMPARATORS:
        l_prev, r_prev = _prev(left), _prev(right)
        if any(math.isnan(v) for v in (l_last, r_last, l_prev, r_prev)):
            return False
        if comparator == "Cross Above":
            return l_prev <= r_prev and l_last > r_last
        return l_prev >= r_prev and l_last < r_last
    if math.isnan(l_last) or math.isnan(r_last):
        return False
    if comparator == "Equal":
        return math.isclose(l_last, r_last, rel_tol=1e-9, abs_tol=1e-6)
    if comparator == "Greater":
        return l_last > r_last
    if comparator == "Greater Equal":
        return l_last >= r_last
    if comparator == "Less":
        return l_last < r_last
    if comparator == "Less Equal":
        return l_last <= r_last
    raise ConditionError(f"Unsupported comparator: {comparator}")


def evaluate_condition_set(condition_set: ConditionSet, close: pd.Series) -> Tuple[bool, Dict[str, float]]:
    if not condition_set:
        raise ConditionError("Condition set is empty")

    cache: Dict[str, Any] = {}
    values: Dict[str, float] = {}

    def get(spec: IndicatorSpec):
        label = spec.label()
        if label not in cache:
            result = compute_indicator(spec, close)
            cache[label] = result
            values[label] = _last(result)
        return cache[label]

    running = None
    pending_logic = None

    for entry in condition_set:
        left_val = get(entry.condition.left)
        right_val = get(entry.condition.right)
        cond_result = _apply_comparator(entry.condition.comparator, left_val, right_val)

        if running is None:
            running = cond_result
        else:
            running = (running and cond_result) if pending_logic == "AND" else (running or cond_result)

        pending_logic = entry.logic

    return bool(running), values

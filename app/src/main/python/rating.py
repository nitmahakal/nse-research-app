"""
Rating Engine.

Composite 0-10 score built from three components (weights per the
agreed design):
    - Win Rate (40%)             - % of matured signals with positive return
    - Risk-Adjusted Return (35%) - avg gain vs avg drawdown (reward per unit of pain)
    - Consistency (25%)          - how tightly clustered returns are (low
                                    variance relative to mean = more trustworthy)

Then confidence shrinkage pulls the score toward a neutral 5.0 when
there isn't much evidence yet:

    confidence = min(1.0, matured_signal_count / 30)
    displayed_rating = confidence * raw_score + (1 - confidence) * 5.0

This directly enforces the rule locked early on: a scan with only a
few matured signals can never show a misleadingly high (or low)
rating, no matter how lucky/unlucky those few trades were. Only
MATURED signals (periods_elapsed >= 6, is_matured=1) count at all -
immature signals are invisible to the rating, active or not.

FORMULA NOTE: the exact weighting (40/35/25) and scaling constants
below are a reasonable first-cut design, not something derived from
real trading data (there isn't any yet). Once real signals accumulate
across multiple scans, these constants are the place to tune - they
are isolated in this one file for exactly that reason.
"""

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

MATURATION_PERIODS = 6
CONFIDENCE_FULL_AT_SIGNAL_COUNT = 30

_WEIGHT_WIN_RATE = 0.40
_WEIGHT_RISK_ADJUSTED = 0.35
_WEIGHT_CONSISTENCY = 0.25

_NEUTRAL_SCORE = 5.0


@dataclass
class RatingResult:
    scan_id: int
    scan_version: int
    matured_signal_count: int
    win_rate: float
    avg_gain_pct: float
    avg_drawdown_pct: float
    raw_score: Optional[float]
    confidence: float
    rating_score: float
    star_rating: float


def _get_matured_signals(conn: sqlite3.Connection, scan_id: int):
    cur = conn.execute(
        """
        SELECT return_pct, max_gain_pct, max_drawdown_pct
        FROM signals WHERE scan_id = ? AND is_matured = 1
        """,
        (scan_id,),
    )
    return cur.fetchall()


def compute_rating(conn: sqlite3.Connection, scan_id: int, scan_version: int) -> RatingResult:
    rows = _get_matured_signals(conn, scan_id)
    matured_count = len(rows)

    if matured_count == 0:
        return RatingResult(
            scan_id=scan_id, scan_version=scan_version, matured_signal_count=0,
            win_rate=0.0, avg_gain_pct=0.0, avg_drawdown_pct=0.0,
            raw_score=None, confidence=0.0, rating_score=_NEUTRAL_SCORE, star_rating=_NEUTRAL_SCORE / 2,
        )

    returns = [r[0] for r in rows]
    max_gains = [r[1] for r in rows]
    max_drawdowns = [abs(r[2]) for r in rows]

    winners = sum(1 for r in returns if r > 0)
    win_rate = winners / matured_count

    avg_gain_pct = sum(max_gains) / matured_count
    avg_drawdown_pct = sum(max_drawdowns) / matured_count

    win_rate_score = win_rate * 10.0

    if avg_drawdown_pct < 0.5:
        risk_adjusted_score = 10.0 if avg_gain_pct > 0 else 5.0
    else:
        ratio = avg_gain_pct / avg_drawdown_pct
        risk_adjusted_score = max(0.0, min(10.0, ratio * 2.5))

    if matured_count >= 2:
        stdev = statistics.pstdev(returns)
        mean_abs_return = max(abs(sum(returns) / matured_count), 1e-6)
        consistency_score = max(0.0, min(10.0, 10.0 * (1.0 - min(1.0, stdev / (mean_abs_return * 3)))))
    else:
        consistency_score = 5.0

    raw_score = (
        _WEIGHT_WIN_RATE * win_rate_score
        + _WEIGHT_RISK_ADJUSTED * risk_adjusted_score
        + _WEIGHT_CONSISTENCY * consistency_score
    )
    raw_score = max(0.0, min(10.0, raw_score))

    confidence = min(1.0, matured_count / CONFIDENCE_FULL_AT_SIGNAL_COUNT)
    rating_score = confidence * raw_score + (1.0 - confidence) * _NEUTRAL_SCORE

    return RatingResult(
        scan_id=scan_id, scan_version=scan_version, matured_signal_count=matured_count,
        win_rate=win_rate, avg_gain_pct=avg_gain_pct, avg_drawdown_pct=avg_drawdown_pct,
        raw_score=raw_score, confidence=confidence, rating_score=rating_score,
        star_rating=rating_score / 2.0,
    )


def save_rating_snapshot(conn: sqlite3.Connection, result: RatingResult) -> None:
    today = datetime.now().date().isoformat()
    conn.execute(
        """
        INSERT INTO rating_history
            (scan_id, scan_version, calculated_at, matured_signal_count,
             win_rate, avg_gain_pct, avg_drawdown_pct, rating_score, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_id, calculated_at) DO UPDATE SET
            scan_version = excluded.scan_version,
            matured_signal_count = excluded.matured_signal_count,
            win_rate = excluded.win_rate,
            avg_gain_pct = excluded.avg_gain_pct,
            avg_drawdown_pct = excluded.avg_drawdown_pct,
            rating_score = excluded.rating_score,
            confidence = excluded.confidence
        """,
        (result.scan_id, result.scan_version, today, result.matured_signal_count,
         result.win_rate, result.avg_gain_pct, result.avg_drawdown_pct,
         result.rating_score, result.confidence),
    )
    conn.commit()


def get_rating_trend(conn: sqlite3.Connection, scan_id: int) -> List[tuple]:
    cur = conn.execute(
        """
        SELECT calculated_at, rating_score, confidence, matured_signal_count
        FROM rating_history WHERE scan_id = ? ORDER BY calculated_at ASC
        """,
        (scan_id,),
    )
    return cur.fetchall()

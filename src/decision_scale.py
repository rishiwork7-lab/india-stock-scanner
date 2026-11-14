"""Canonical score-to-decision scale (exact port of the original's src/schemas/decision_scale.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

SCALE_VERSION = "decision-scale-v1"


@dataclass(frozen=True)
class Band:
    min_score: int
    max_score: int
    signal_key: str
    action: str
    decision_type: str
    label: str
    description: str


SCALE = (
    Band(80, 100, "strong_buy", "buy", "buy", "Strong Buy", "High-probability setup; a buy/add plan can be executed"),
    Band(60, 79, "buy", "buy", "buy", "Buy", "Positive setup; a few items may still need confirmation"),
    Band(40, 59, "watch", "watch", "hold", "Watch", "Signals diverge or lack confirmation; wait for the trigger"),
    Band(20, 39, "reduce", "reduce", "sell", "Reduce", "Risk clearly rising; reduce exposure first"),
    Band(0, 19, "sell", "sell", "sell", "Sell", "Trend or risk has clearly deteriorated; exit first"),
)

SCALE_PROMPT = """## Canonical Score and Action Scale
- `sentiment_score`, `operation_advice`, the three-state `decision_type` and the eight-state `action` must follow the same scale.
- 80-100: Strong Buy, `action=buy`, `decision_type=buy`.
- 60-79: Buy, `action=buy`, `decision_type=buy`.
- 40-59: Watch, `action=watch`, `decision_type=hold`.
- 20-39: Reduce, `action=reduce`, `decision_type=sell`.
- 0-19: Sell, `action=sell`, `decision_type=sell`.
- `decision_type` only keeps `buy|hold|sell` for statistics compatibility; finer-grained advice must go into `action`.
- If score >= 60 but the final `action` is `hold/watch`, or score < 40 but the final `action` is `hold/watch`, explain the downgrade in `guardrail_reason` or `dashboard.decision_stability.reason`."""


def normalize_score(value: Any) -> Optional[int]:
    try:
        s = int(float(value))
    except (TypeError, ValueError):
        return None
    return s if 0 <= s <= 100 else None


def band_for_score(value: Any) -> Optional[Band]:
    s = normalize_score(value)
    if s is None:
        return None
    for b in SCALE:
        if b.min_score <= s <= b.max_score:
            return b
    return None


def signal_key_for_score(value: Any) -> Optional[str]:
    b = band_for_score(value)
    return b.signal_key if b else None

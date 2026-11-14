"""Trend analyser: exact port of the original's src/stock_analyzer.py (same indicators, thresholds, states and 100-point
score), with English labels.

Trading baseline:
1. Strict entry - never chase highs; maximise the win rate of each trade.
2. Trend trading - MA5 > MA10 > MA20 bullish alignment; trade with the trend.
3. Efficiency - prefer a healthy holding structure (in India: high delivery %, see src/delivery.py).
4. Entry preference - buy pullbacks near MA5 / MA10.
Score: trend 30 + bias 20 + volume 15 + support 10 + MACD 15 + RSI 10 = 100.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

from .decision_scale import signal_key_for_score

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    STRONG_BULL = "Strong bullish"         # MA5 > MA10 > MA20 and the spread is widening
    BULL = "Bullish alignment"             # MA5 > MA10 > MA20
    WEAK_BULL = "Weak bullish"             # MA5 > MA10 but MA10 <= MA20
    CONSOLIDATION = "Consolidation"        # tangled MAs
    WEAK_BEAR = "Weak bearish"             # MA5 < MA10 but MA10 >= MA20
    BEAR = "Bearish alignment"             # MA5 < MA10 < MA20
    STRONG_BEAR = "Strong bearish"         # MA5 < MA10 < MA20 and the spread is widening


class VolumeStatus(Enum):
    HEAVY_VOLUME_UP = "High-volume rise"
    HEAVY_VOLUME_DOWN = "High-volume decline"
    SHRINK_VOLUME_UP = "Low-volume rise"
    SHRINK_VOLUME_DOWN = "Low-volume pullback"
    NORMAL = "Normal volume"


class BuySignal(Enum):
    STRONG_BUY = "Strong Buy"
    BUY = "Buy"
    HOLD = "Hold"
    WAIT = "Watch"
    SELL = "Sell"
    STRONG_SELL = "Strong Sell"


class MACDStatus(Enum):
    GOLDEN_CROSS_ZERO = "Golden cross above zero"
    GOLDEN_CROSS = "Golden cross"
    BULLISH = "Bullish"
    CROSSING_UP = "Crossing above zero"
    CROSSING_DOWN = "Crossing below zero"
    BEARISH = "Bearish"
    DEATH_CROSS = "Death cross"


class RSIStatus(Enum):
    OVERBOUGHT = "Overbought"
    STRONG_BUY = "Strong"
    NEUTRAL = "Neutral"
    WEAK = "Weak"
    OVERSOLD = "Oversold"


@dataclass
class TrendAnalysisResult:
    code: str
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""
    support_ma5: bool = False
    support_ma10: bool = False
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = {}
        for k, v in self.__dict__.items():
            out[k] = v.value if isinstance(v, Enum) else v
        return out


def _tag(prefix: str, signal: str) -> str:
    """'⚠️ ' + '⚠️ RSI overbought' -> '⚠️ RSI overbought' (the original doubled the emoji; text and scoring unchanged)."""
    sig = signal.strip()
    return sig if sig[:1] in "⚠❌✅⭐⚡✓" else f"{prefix} {sig}"


class StockTrendAnalyzer:
    VOLUME_SHRINK_RATIO = 0.7
    VOLUME_HEAVY_RATIO = 1.5
    MA_SUPPORT_TOLERANCE = 0.02
    MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
    RSI_SHORT, RSI_MID, RSI_LONG = 6, 12, 24
    RSI_OVERBOUGHT, RSI_OVERSOLD = 70, 30

    def __init__(self, bias_threshold: float = 5.0):
        self.bias_threshold = bias_threshold

    def analyze(self, df: pd.DataFrame, code: str) -> TrendAnalysisResult:
        result = TrendAnalysisResult(code=code)
        if df is None or df.empty or len(df) < 20:
            logger.warning("%s: not enough data for trend analysis", code)
            result.risk_factors.append("Not enough data to complete the analysis")
            return result
        df = df.sort_values("date").reset_index(drop=True)
        df = self._calculate_mas(df)
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)
        latest = df.iloc[-1]
        result.current_price = float(latest["close"])
        result.ma5, result.ma10, result.ma20 = float(latest["MA5"]), float(latest["MA10"]), float(latest["MA20"])
        result.ma60 = float(latest.get("MA60", 0) or 0)
        self._analyze_trend(df, result)
        self._calculate_bias(result)
        self._analyze_volume(df, result)
        self._analyze_support_resistance(df, result)
        self._analyze_macd(df, result)
        self._analyze_rsi(df, result)
        self._generate_signal(result)
        return result

    # ---- indicators -------------------------------------------------------------------------------------------------
    def _calculate_mas(self, df):
        df = df.copy()
        for n in (5, 10, 20):
            df[f"MA{n}"] = df["close"].rolling(window=n).mean()
        df["MA60"] = df["close"].rolling(window=60).mean() if len(df) >= 60 else df["MA20"]
        return df

    def _calculate_macd(self, df):
        df = df.copy()
        fast = df["close"].ewm(span=self.MACD_FAST, adjust=False).mean()
        slow = df["close"].ewm(span=self.MACD_SLOW, adjust=False).mean()
        df["MACD_DIF"] = fast - slow
        df["MACD_DEA"] = df["MACD_DIF"].ewm(span=self.MACD_SIGNAL, adjust=False).mean()
        df["MACD_BAR"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2
        return df

    def _calculate_rsi(self, df):
        """Wilder's RSI (ewm alpha=1/period, adjust=False), NaN filled with 50."""
        df = df.copy()
        for period in (self.RSI_SHORT, self.RSI_MID, self.RSI_LONG):
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
            rs = avg_gain / avg_loss
            df[f"RSI_{period}"] = (100 - (100 / (1 + rs))).fillna(50)
        return df

    # ---- analysis steps ---------------------------------------------------------------------------------------------
    def _analyze_trend(self, df, r):
        ma5, ma10, ma20 = r.ma5, r.ma10, r.ma20
        prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
        if ma5 > ma10 > ma20:
            prev_spread = (prev["MA5"] - prev["MA20"]) / prev["MA20"] * 100 if prev["MA20"] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.STRONG_BULL, "Strong bullish alignment, MAs diverging upward", 90
            else:
                r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.BULL, "Bullish alignment MA5>MA10>MA20", 75
        elif ma5 > ma10 and ma10 <= ma20:
            r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.WEAK_BULL, "Weak bullish: MA5>MA10 but MA10<=MA20", 55
        elif ma5 < ma10 < ma20:
            prev_spread = (prev["MA20"] - prev["MA5"]) / prev["MA5"] * 100 if prev["MA5"] > 0 else 0
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.STRONG_BEAR, "Strong bearish alignment, MAs diverging downward", 10
            else:
                r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.BEAR, "Bearish alignment MA5<MA10<MA20", 25
        elif ma5 < ma10 and ma10 >= ma20:
            r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.WEAK_BEAR, "Weak bearish: MA5<MA10 but MA10>=MA20", 40
        else:
            r.trend_status, r.ma_alignment, r.trend_strength = TrendStatus.CONSOLIDATION, "Tangled MAs, trend unclear", 50

    def _calculate_bias(self, r):
        p = r.current_price
        if r.ma5 > 0:
            r.bias_ma5 = (p - r.ma5) / r.ma5 * 100
        if r.ma10 > 0:
            r.bias_ma10 = (p - r.ma10) / r.ma10 * 100
        if r.ma20 > 0:
            r.bias_ma20 = (p - r.ma20) / r.ma20 * 100

    def _analyze_volume(self, df, r):
        if len(df) < 5:
            return
        latest = df.iloc[-1]
        vol_5d_avg = df["volume"].iloc[-6:-1].mean()
        if vol_5d_avg > 0:
            r.volume_ratio_5d = float(latest["volume"]) / vol_5d_avg
        prev_close = df.iloc[-2]["close"]
        price_change = (latest["close"] - prev_close) / prev_close * 100
        if r.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                r.volume_status, r.volume_trend = VolumeStatus.HEAVY_VOLUME_UP, "High-volume rise, strong buying"
            else:
                r.volume_status, r.volume_trend = VolumeStatus.HEAVY_VOLUME_DOWN, "High-volume decline, watch the risk"
        elif r.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                r.volume_status, r.volume_trend = VolumeStatus.SHRINK_VOLUME_UP, "Low-volume rise, weak momentum"
            else:
                r.volume_status, r.volume_trend = VolumeStatus.SHRINK_VOLUME_DOWN, "Low-volume pullback, selling pressure easing (good)"
        else:
            r.volume_status, r.volume_trend = VolumeStatus.NORMAL, "Normal volume"

    def _analyze_support_resistance(self, df, r):
        p = r.current_price
        if r.ma5 > 0 and abs(p - r.ma5) / r.ma5 <= self.MA_SUPPORT_TOLERANCE and p >= r.ma5:
            r.support_ma5 = True
            r.support_levels.append(r.ma5)
        if r.ma10 > 0 and abs(p - r.ma10) / r.ma10 <= self.MA_SUPPORT_TOLERANCE and p >= r.ma10:
            r.support_ma10 = True
            if r.ma10 not in r.support_levels:
                r.support_levels.append(r.ma10)
        if r.ma20 > 0 and p >= r.ma20:
            r.support_levels.append(r.ma20)
        if len(df) >= 20:
            recent_high = df["high"].iloc[-20:].max()
            if recent_high > p:
                r.resistance_levels.append(float(recent_high))

    def _analyze_macd(self, df, r):
        if len(df) < self.MACD_SLOW:
            r.macd_signal = "Not enough data"
            return
        latest, prev = df.iloc[-1], df.iloc[-2]
        r.macd_dif, r.macd_dea, r.macd_bar = float(latest["MACD_DIF"]), float(latest["MACD_DEA"]), float(latest["MACD_BAR"])
        prev_diff = prev["MACD_DIF"] - prev["MACD_DEA"]
        curr_diff = r.macd_dif - r.macd_dea
        golden = prev_diff <= 0 and curr_diff > 0
        death = prev_diff >= 0 and curr_diff < 0
        up = prev["MACD_DIF"] <= 0 and r.macd_dif > 0
        down = prev["MACD_DIF"] >= 0 and r.macd_dif < 0
        if golden and r.macd_dif > 0:
            r.macd_status, r.macd_signal = MACDStatus.GOLDEN_CROSS_ZERO, "⭐ Golden cross above zero, strong buy signal!"
        elif up:
            r.macd_status, r.macd_signal = MACDStatus.CROSSING_UP, "⚡ DIF crossed above zero, trend strengthening"
        elif golden:
            r.macd_status, r.macd_signal = MACDStatus.GOLDEN_CROSS, "✅ Golden cross, trend turning up"
        elif death:
            r.macd_status, r.macd_signal = MACDStatus.DEATH_CROSS, "❌ Death cross, trend turning down"
        elif down:
            r.macd_status, r.macd_signal = MACDStatus.CROSSING_DOWN, "⚠️ DIF crossed below zero, trend weakening"
        elif r.macd_dif > 0 and r.macd_dea > 0:
            r.macd_status, r.macd_signal = MACDStatus.BULLISH, "✓ Bullish alignment, rising"
        elif r.macd_dif < 0 and r.macd_dea < 0:
            r.macd_status, r.macd_signal = MACDStatus.BEARISH, "⚠ Bearish alignment, falling"
        else:
            r.macd_status, r.macd_signal = MACDStatus.BULLISH, " MACD in the neutral zone"

    def _analyze_rsi(self, df, r):
        if len(df) < self.RSI_LONG:
            r.rsi_signal = "Not enough data"
            return
        latest = df.iloc[-1]
        r.rsi_6, r.rsi_12, r.rsi_24 = (float(latest[f"RSI_{p}"]) for p in (self.RSI_SHORT, self.RSI_MID, self.RSI_LONG))
        mid = r.rsi_12
        if mid > self.RSI_OVERBOUGHT:
            r.rsi_status, r.rsi_signal = RSIStatus.OVERBOUGHT, f"⚠️ RSI overbought ({mid:.1f}>70), high short-term pullback risk"
        elif mid > 60:
            r.rsi_status, r.rsi_signal = RSIStatus.STRONG_BUY, f"✅ RSI strong ({mid:.1f}), buyers in control"
        elif mid >= 40:
            r.rsi_status, r.rsi_signal = RSIStatus.NEUTRAL, f" RSI neutral ({mid:.1f}), consolidating"
        elif mid >= self.RSI_OVERSOLD:
            r.rsi_status, r.rsi_signal = RSIStatus.WEAK, f"⚡ RSI weak ({mid:.1f}), watch for a rebound"
        else:
            r.rsi_status, r.rsi_signal = RSIStatus.OVERSOLD, f"⭐ RSI oversold ({mid:.1f}<30), rebound likely"

    # ---- score ------------------------------------------------------------------------------------------------------
    def _generate_signal(self, r):
        score, reasons, risks = 0, [], []
        trend_scores = {TrendStatus.STRONG_BULL: 30, TrendStatus.BULL: 26, TrendStatus.WEAK_BULL: 18, TrendStatus.CONSOLIDATION: 12,
                        TrendStatus.WEAK_BEAR: 8, TrendStatus.BEAR: 4, TrendStatus.STRONG_BEAR: 0}
        score += trend_scores.get(r.trend_status, 12)
        if r.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL):
            reasons.append(f"✅ {r.trend_status.value}, trade with the trend")
        elif r.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR):
            risks.append(f"⚠️ {r.trend_status.value}, not suitable for longs")

        bias = r.bias_ma5 if r.bias_ma5 == r.bias_ma5 and r.bias_ma5 is not None else 0.0
        base = self.bias_threshold
        strength = r.trend_strength if r.trend_strength == r.trend_strength else 0.0
        strong = r.trend_status == TrendStatus.STRONG_BULL and (strength or 0) >= 70
        effective = base * 1.5 if strong else base
        if bias < 0:
            if bias > -3:
                score += 20
                reasons.append(f"✅ Price slightly below MA5 ({bias:.1f}%), pullback entry")
            elif bias > -5:
                score += 16
                reasons.append(f"✅ Price pulled back to MA5 ({bias:.1f}%), watch for support")
            else:
                score += 8
                risks.append(f"⚠️ Bias too large ({bias:.1f}%), possible breakdown")
        elif bias < 2:
            score += 18
            reasons.append(f"✅ Price close to MA5 ({bias:.1f}%), good entry timing")
        elif bias < base:
            score += 14
            reasons.append(f"⚡ Price slightly above MA5 ({bias:.1f}%), a small position is acceptable")
        elif bias > effective:
            score += 4
            risks.append(f"❌ Bias too high ({bias:.1f}%>{effective:.1f}%), do not chase!")
        elif bias > base and strong:
            score += 10
            reasons.append(f"⚡ Bias high in a strong trend ({bias:.1f}%), track with a light position")
        else:
            score += 4
            risks.append(f"❌ Bias too high ({bias:.1f}%>{base:.1f}%), do not chase!")

        vol_scores = {VolumeStatus.SHRINK_VOLUME_DOWN: 15, VolumeStatus.HEAVY_VOLUME_UP: 12, VolumeStatus.NORMAL: 10,
                      VolumeStatus.SHRINK_VOLUME_UP: 6, VolumeStatus.HEAVY_VOLUME_DOWN: 0}
        score += vol_scores.get(r.volume_status, 8)
        if r.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            reasons.append("✅ Low-volume pullback, weak hands shaken out")
        elif r.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
            risks.append("⚠️ High-volume decline, watch the risk")

        if r.support_ma5:
            score += 5
            reasons.append("✅ MA5 support holding")
        if r.support_ma10:
            score += 5
            reasons.append("✅ MA10 support holding")

        macd_scores = {MACDStatus.GOLDEN_CROSS_ZERO: 15, MACDStatus.GOLDEN_CROSS: 12, MACDStatus.CROSSING_UP: 10, MACDStatus.BULLISH: 8,
                       MACDStatus.BEARISH: 2, MACDStatus.CROSSING_DOWN: 0, MACDStatus.DEATH_CROSS: 0}
        score += macd_scores.get(r.macd_status, 5)
        if r.macd_status in (MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS):
            reasons.append(_tag("✅", r.macd_signal))
        elif r.macd_status in (MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN):
            risks.append(_tag("⚠️", r.macd_signal))
        else:
            reasons.append(r.macd_signal)

        rsi_scores = {RSIStatus.OVERSOLD: 10, RSIStatus.STRONG_BUY: 8, RSIStatus.NEUTRAL: 5, RSIStatus.WEAK: 3, RSIStatus.OVERBOUGHT: 0}
        score += rsi_scores.get(r.rsi_status, 5)
        if r.rsi_status in (RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY):
            reasons.append(_tag("✅", r.rsi_signal))
        elif r.rsi_status == RSIStatus.OVERBOUGHT:
            risks.append(_tag("⚠️", r.rsi_signal))
        else:
            reasons.append(r.rsi_signal)

        r.signal_score, r.signal_reasons, r.risk_factors = score, reasons, risks
        key = signal_key_for_score(score)
        bullish = (TrendStatus.STRONG_BULL, TrendStatus.BULL)
        if key == "strong_buy" and r.trend_status in bullish:
            r.buy_signal = BuySignal.STRONG_BUY
        elif key in ("strong_buy", "buy") and r.trend_status in bullish + (TrendStatus.WEAK_BULL,):
            r.buy_signal = BuySignal.BUY
        elif key in ("strong_buy", "buy") and r.trend_status in (TrendStatus.CONSOLIDATION, TrendStatus.WEAK_BEAR):
            r.buy_signal = BuySignal.WAIT
        elif key == "watch":
            r.buy_signal = BuySignal.WAIT
        elif key == "sell" or r.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR):
            r.buy_signal = BuySignal.STRONG_SELL
        else:
            r.buy_signal = BuySignal.SELL

    def format_analysis(self, r: TrendAnalysisResult) -> str:
        lines = [
            f"=== {r.code} trend analysis ===",
            f"Trend: {r.trend_status.value} ({r.ma_alignment}), strength {r.trend_strength}/100",
            f"Price {r.current_price:.2f} | MA5 {r.ma5:.2f} | MA10 {r.ma10:.2f} | MA20 {r.ma20:.2f} | MA60 {r.ma60:.2f}",
            f"Bias: MA5 {r.bias_ma5:+.2f}% | MA10 {r.bias_ma10:+.2f}% | MA20 {r.bias_ma20:+.2f}%",
            f"Volume: {r.volume_status.value} (ratio {r.volume_ratio_5d:.2f}) - {r.volume_trend}",
            f"MACD: {r.macd_status.value} (DIF {r.macd_dif:.3f}, DEA {r.macd_dea:.3f}) - {r.macd_signal}",
            f"RSI: 6={r.rsi_6:.1f} 12={r.rsi_12:.1f} 24={r.rsi_24:.1f} - {r.rsi_signal}",
            f"Signal: {r.buy_signal.value} | score {r.signal_score}/100",
        ]
        lines += [f"  + {x}" for x in r.signal_reasons] + [f"  - {x}" for x in r.risk_factors]
        return "\n".join(lines)


def analyze_stock(df: pd.DataFrame, code: str, bias_threshold: float = 5.0) -> TrendAnalysisResult:
    return StockTrendAnalyzer(bias_threshold).analyze(df, code)

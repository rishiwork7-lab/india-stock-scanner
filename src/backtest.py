"""Backtest of past recommendations (exact port of the original's src/core/backtest_engine.py evaluation rules, plus a
small service that runs it over the analysis history).

For each stored analysis: take the analysis-day price, the next N daily bars, and judge the advice:
- direction: Buy -> expect up; Sell/Reduce -> down; Hold -> not down; Watch/Wait -> flat (neutral band +/-2%)
- long-only simulation: Buy/Hold go long, others stay in cash; stop-loss/take-profit levels parsed from the report are
  checked bar by bar (if both are touched on one bar the stop is assumed first: "ambiguous").
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)
ENGINE_VERSION = "v1"
OVERALL = "__overall__"


@dataclass(frozen=True)
class EvaluationConfig:
    eval_window_days: int
    neutral_band_pct: float = 2.0
    engine_version: str = ENGINE_VERSION


class BacktestEngine:
    _BULLISH = ("买入", "加仓", "强烈买入", "增持", "建仓", "strong buy", "buy", "add")
    _BEARISH = ("卖出", "减仓", "强烈卖出", "清仓", "strong sell", "sell", "reduce")
    _HOLD = ("持有", "震荡观望", "洗盘观察", "持有观察", "hold", "range-bound watch", "shakeout watch", "hold and watch")
    _WAIT = ("观望", "等待", "wait")
    _NEG = ("not", "don't", "do not", "no", "never", "avoid", "不要", "不", "别", "勿", "没有")
    _NEG_CONNECT = ("建议", "应", "应当", "宜", "先", "再", "暂", "不必", "必须", "无需")

    # ---- intent ---------------------------------------------------------------------------------------------------
    @classmethod
    def infer_direction_expected(cls, advice: Optional[str]) -> str:
        t = cls._norm(advice)
        if cls._first(t, cls._BEARISH) is not None:
            return "down"
        w = cls._first(t, cls._WAIT)
        if w is not None:
            b, h = cls._first(t, cls._BULLISH), cls._first(t, cls._HOLD)
            if (b is None or w < b) and (h is None or w < h):
                return "flat"
        if cls._first(t, cls._BULLISH) is not None:
            return "up"
        if cls._first(t, cls._HOLD) is not None:
            return "not_down"
        return "flat"

    @classmethod
    def infer_position_recommendation(cls, advice: Optional[str]) -> str:
        t = cls._norm(advice)
        if cls._first(t, cls._BEARISH) is not None:
            return "cash"
        w = cls._first(t, cls._WAIT)
        if w is not None:
            b, h = cls._first(t, cls._BULLISH), cls._first(t, cls._HOLD)
            if (b is None or w < b) and (h is None or w < h):
                return "cash"
        if cls._first(t, cls._BULLISH) is not None or cls._first(t, cls._HOLD) is not None:
            return "long"
        return "cash"

    @staticmethod
    def _norm(v) -> str:
        return str(v or "").strip().lower()

    @classmethod
    def _first(cls, text: str, keywords: Sequence[str]) -> Optional[int]:
        if not text:
            return None
        best = None
        for kw in keywords:
            if text == kw:
                return 0
            k = kw.lower().strip()
            if re.search(r"[a-z]", k):
                for m in re.finditer(rf"(?<![a-zA-Z0-9_]){re.escape(k)}(?![a-zA-Z0-9_])", text):
                    if not cls._negated(text[: m.start()], k):
                        if best is None or m.start() < best:
                            best = m.start()
                        break
                continue
            start = 0
            while True:
                i = text.find(k, start)
                if i < 0:
                    break
                if not cls._negated(text[:i], k):
                    if best is None or i < best:
                        best = i
                    break
                start = i + len(k)
        return best

    @classmethod
    def _negated(cls, prefix: str, keyword: str) -> bool:
        s = prefix.rstrip()
        if any(s.endswith(n) for n in cls._NEG):
            return True
        look = s[-12:]
        for n in cls._NEG:
            i = look.rfind(n)
            if i < 0:
                continue
            gap = look[i + len(n):].strip()
            if not gap:
                return True
            if any(ch in gap for ch in "，,。；;:!?！？"):
                continue
            if re.search(r"[a-z]", keyword):
                if re.search(rf"(?<![a-zA-Z0-9_]){re.escape(keyword)}(?![a-zA-Z0-9_])", gap):
                    return True
            elif keyword in gap:
                return True
            if not any("一" <= ch <= "鿿" for ch in gap):
                if len(gap) <= 6:
                    return True
                continue
            if re.sub(r"[\s,，。；;:!?！？]", "", gap) in cls._NEG_CONNECT:
                return True
        return False

    # ---- one evaluation -----------------------------------------------------------------------------------------------
    @classmethod
    def evaluate_single(cls, *, operation_advice, analysis_date: date, start_price, forward_bars: Sequence, stop_loss,
                        take_profit, config: EvaluationConfig) -> Dict[str, Any]:
        base = {"analysis_date": analysis_date, "operation_advice": operation_advice,
                "position_recommendation": cls.infer_position_recommendation(operation_advice),
                "direction_expected": cls.infer_direction_expected(operation_advice)}
        if start_price is None or start_price <= 0:
            return {**base, "eval_status": "error"}
        n = int(config.eval_window_days)
        if n <= 0:
            raise ValueError("eval_window_days must be positive")
        if len(forward_bars) < n:
            return {**base, "eval_status": "insufficient_data", "eval_window_days": n}
        window = list(forward_bars[:n])
        end_close = window[-1].close
        highs = [b.high for b in window if b.high is not None]
        lows = [b.low for b in window if b.low is not None]
        ret = None if end_close is None else (end_close - start_price) / start_price * 100
        outcome, correct = cls._classify(ret, base["direction_expected"], config.neutral_band_pct)
        hit_sl, hit_tp, first, first_date, first_days, exit_px, exit_reason = cls._targets(
            base["position_recommendation"], stop_loss, take_profit, window, end_close)
        long = base["position_recommendation"] == "long"
        sim = 0.0 if not long else None if exit_px is None else (exit_px - start_price) / start_price * 100
        return {**base, "eval_window_days": n, "engine_version": config.engine_version, "eval_status": "completed",
                "start_price": start_price, "end_close": end_close, "max_high": max(highs) if highs else None,
                "min_low": min(lows) if lows else None, "stock_return_pct": ret, "direction_correct": correct, "outcome": outcome,
                "stop_loss": stop_loss, "take_profit": take_profit, "hit_stop_loss": hit_sl, "hit_take_profit": hit_tp,
                "first_hit": first, "first_hit_date": first_date, "first_hit_trading_days": first_days,
                "simulated_entry_price": start_price if long else None, "simulated_exit_price": exit_px,
                "simulated_exit_reason": exit_reason, "simulated_return_pct": sim}

    @staticmethod
    def _classify(r, direction, band):
        if r is None:
            return None, None
        band = abs(float(band))
        if direction == "up":
            return ("win", True) if r >= band else ("loss", False) if r <= -band else ("neutral", None)
        if direction == "down":
            return ("win", True) if r <= -band else ("loss", False) if r >= band else ("neutral", None)
        if direction == "not_down":
            return ("win", True) if r >= 0 else ("loss", False) if r <= -band else ("neutral", None)
        return ("win", True) if abs(r) <= band else ("loss", False)

    @staticmethod
    def _targets(position, stop_loss, take_profit, window, end_close):
        if position != "long":
            return None, None, "not_applicable", None, None, None, "cash"
        if stop_loss is None and take_profit is None:
            return None, None, "neither", None, None, end_close, "window_end"
        hit_sl = None if stop_loss is None else False
        hit_tp = None if take_profit is None else False
        for i, bar in enumerate(window, start=1):
            s = stop_loss is not None and bar.low is not None and bar.low <= stop_loss
            t = take_profit is not None and bar.high is not None and bar.high >= take_profit
            hit_sl = True if s else hit_sl
            hit_tp = True if t else hit_tp
            if not s and not t:
                continue
            if s and t:
                return hit_sl, hit_tp, "ambiguous", bar.date, i, stop_loss, "ambiguous_stop_loss"
            if s:
                return hit_sl, hit_tp, "stop_loss", bar.date, i, stop_loss, "stop_loss"
            return hit_sl, hit_tp, "take_profit", bar.date, i, take_profit, "take_profit"
        return hit_sl, hit_tp, "neither", None, None, end_close, "window_end"

    # ---- summary ----------------------------------------------------------------------------------------------------
    @classmethod
    def compute_summary(cls, results: Iterable[Dict], scope: str, code: Optional[str], window: int, version: str = ENGINE_VERSION) -> Dict:
        rs = [SimpleNamespace(**{k: r.get(k) for k in ("eval_status", "position_recommendation", "outcome", "direction_correct",
                                                       "stock_return_pct", "simulated_return_pct", "hit_stop_loss", "hit_take_profit",
                                                       "first_hit", "first_hit_trading_days", "operation_advice")}) for r in results]
        done = [r for r in rs if r.eval_status == "completed"]
        wins, losses = sum(r.outcome == "win" for r in done), sum(r.outcome == "loss" for r in done)
        neutral = sum(r.outcome == "neutral" for r in done)
        dir_den = sum(r.direction_correct is not None for r in done)
        longs = [r for r in done if r.position_recommendation == "long"]
        sl_app = [r for r in longs if r.hit_stop_loss is not None]
        tp_app = [r for r in longs if r.hit_take_profit is not None]
        any_app = [r for r in longs if r.hit_stop_loss is not None or r.hit_take_profit is not None]
        pct = lambda a, b: round(a / b * 100, 2) if b else None  # noqa: E731
        breakdown: Dict[str, Dict] = {}
        for r in done:
            b = breakdown.setdefault((str(r.operation_advice or "").strip() or "(unknown)"), {"total": 0, "win": 0, "loss": 0, "neutral": 0})
            b["total"] += 1
            if r.outcome in ("win", "loss", "neutral"):
                b[r.outcome] += 1
        for b in breakdown.values():
            b["win_rate_pct"] = pct(b["win"], b["win"] + b["loss"])
        diag_status, diag_hit = {}, {}
        for r in rs:
            diag_status[r.eval_status or "(unknown)"] = diag_status.get(r.eval_status or "(unknown)", 0) + 1
            diag_hit[r.first_hit or "(none)"] = diag_hit.get(r.first_hit or "(none)", 0) + 1
        return {
            "scope": scope, "code": code, "eval_window_days": window, "engine_version": version, "total_evaluations": len(rs),
            "completed_count": len(done), "insufficient_count": sum(r.eval_status == "insufficient_data" for r in rs),
            "long_count": len(longs), "cash_count": sum(r.position_recommendation == "cash" for r in done),
            "win_count": wins, "loss_count": losses, "neutral_count": neutral,
            "direction_accuracy_pct": pct(sum(r.direction_correct is True for r in done), dir_den),
            "win_rate_pct": pct(wins, wins + losses), "neutral_rate_pct": pct(neutral, len(done)),
            "avg_stock_return_pct": _avg(r.stock_return_pct for r in done), "avg_simulated_return_pct": _avg(r.simulated_return_pct for r in done),
            "stop_loss_trigger_rate": pct(sum(r.hit_stop_loss is True for r in sl_app), len(sl_app)),
            "take_profit_trigger_rate": pct(sum(r.hit_take_profit is True for r in tp_app), len(tp_app)),
            "ambiguous_rate": pct(sum(r.first_hit == "ambiguous" for r in any_app), len(any_app)),
            "avg_days_to_first_hit": _avg(float(r.first_hit_trading_days) for r in any_app
                                          if r.first_hit_trading_days is not None and r.first_hit in ("stop_loss", "take_profit", "ambiguous")),
            "advice_breakdown": breakdown, "diagnostics": {"eval_status": diag_status, "first_hit": diag_hit},
        }


def _avg(values) -> Optional[float]:
    xs = [float(v) for v in values if v is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


class BacktestService:
    def __init__(self, db, refresh=None):
        """refresh(code) optionally re-downloads bars so recent analyses have forward data."""
        self.db, self.refresh = db, refresh

    def run(self, code: Optional[str] = None, eval_window_days: int = 10, force: bool = False, style: Optional[str] = None) -> Dict[str, Any]:
        cfg = EvaluationConfig(eval_window_days)
        rows = self.db.analyses_for_backtest(code, style)
        codes = sorted({r["code"] for r in rows})
        if self.refresh:
            for c in codes:
                try:
                    self.refresh(c)
                except Exception as e:
                    logger.warning("backtest: refreshing %s failed: %s", c, e)
        done = 0
        for r in rows:
            if not force and self.db.has_backtest(r["id"], eval_window_days, ENGINE_VERSION):
                continue
            d = date.fromisoformat(r["analysis_date"])
            bars = [SimpleNamespace(date=b["date"], high=b["high"], low=b["low"], close=b["close"])
                    for b in self.db.get_bars_after(r["code"], d, eval_window_days)]
            res = BacktestEngine.evaluate_single(operation_advice=r["operation_advice"], analysis_date=d, start_price=r["current_price"],
                                                 forward_bars=bars, stop_loss=r["stop_loss"], take_profit=r["take_profit"], config=cfg)
            res["code"], res["style"] = r["code"], r.get("style") or "short"
            self.db.save_backtest(r["id"], r["code"], eval_window_days, ENGINE_VERSION, res)
            done += 1
        summaries = {}
        for c in codes + [None]:
            results = [x for x in self.db.backtest_results(c, eval_window_days, ENGINE_VERSION)
                       if not style or (x.get("style") or "short") == style]
            s = BacktestEngine.compute_summary(results, "stock" if c else "overall", c, eval_window_days)
            self.db.save_backtest_summary(f"{c or OVERALL}{'|' + style if style else ''}", eval_window_days, ENGINE_VERSION, s)
            summaries[c or OVERALL] = s
        return {"evaluated": done, "analyses": len(rows), "summaries": summaries}


def format_summary(s: Dict[str, Any]) -> str:
    f = lambda v, suf="%": "N/A" if v is None else f"{v}{suf}"  # noqa: E731
    return (f"{s.get('code') or 'All stocks'} | {s['eval_window_days']}-day window | completed {s['completed_count']}/{s['total_evaluations']} | "
            f"direction accuracy {f(s['direction_accuracy_pct'])} | win rate {f(s['win_rate_pct'])} | "
            f"avg simulated return {f(s['avg_simulated_return_pct'])} | stop hit {f(s['stop_loss_trigger_rate'])} | target hit {f(s['take_profit_trigger_rate'])}")

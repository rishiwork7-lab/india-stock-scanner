import json
from types import SimpleNamespace

import pytest

from src.analyzer import (SYSTEM_PROMPT, StockAnalyzer, apply_guardrails, build_prompt, check_integrity, extract_json,
                          result_from_json, trend_fallback)
from src.llm.providers import (AllProvidersFailed, AnthropicProvider, GeminiProvider, LLMRouter, OpenAICompatProvider,
                               ProviderError)

TREND = {"trend_status": "Bullish alignment", "ma_alignment": "Bullish alignment MA5>MA10>MA20", "trend_strength": 75,
         "bias_ma5": 1.2, "bias_ma10": 2.0, "volume_status": "Low-volume pullback", "volume_trend": "Low-volume pullback (good)",
         "macd_status": "Bullish", "macd_signal": "✓ Bullish", "rsi_6": 55.0, "rsi_12": 58.0, "rsi_24": 55.0, "rsi_signal": "neutral",
         "buy_signal": "Buy", "signal_score": 72, "signal_reasons": ["✅ Bullish alignment"], "risk_factors": [],
         "ma5": 1330.0, "ma10": 1320.0, "ma20": 1300.0, "support_levels": [1330.0, 1300.0], "resistance_levels": [1420.0],
         "volume_ratio_5d": 0.6}


def ctx(**kw):
    c = {"code": "RELIANCE", "stock_name": "Reliance Industries Limited", "exchange": "NSE", "date": "2026-11-13",
         "today": {"close": 1340.0, "open": 1336.0, "high": 1350.0, "low": 1324.0, "pct_chg": 0.5, "volume": 1.3e7, "amount": 1.8e10,
                   "ma5": 1330.0, "ma10": 1320.0, "ma20": 1300.0},
         "trend_analysis": dict(TREND), "market_phase": {"phase": "postmarket", "label": "Post-market", "time_ist": "x", "daily_bar_complete": True},
         "delivery": {"date": "2026-11-13", "delivery_pct": 47.0, "delivery_pct_avg5": 48.0, "delivery_pct_avg20": 46.0,
                      "delivery_trend": "steady", "delivery_vwap_20d": 1320.0, "price_vs_delivery_vwap_pct": 1.5, "health": "Healthy",
                      "meaning": "genuine holding"}}
    c.update(kw)
    return c


GOOD = {"stock_name": "Reliance Industries", "sentiment_score": 74, "trend_prediction": "Bullish", "operation_advice": "Buy",
        "decision_type": "buy", "action": "buy", "confidence_level": "High", "analysis_summary": "Uptrend intact.",
        "dashboard": {"core_conclusion": {"one_sentence": "Buy near MA5"}, "intelligence": {"risk_alerts": ["Risk 1: crude prices"]},
                      "battle_plan": {"sniper_points": {"ideal_buy": "₹1,330", "stop_loss": "Stop-loss: ₹1,290"}},
                      "data_perspective": {"price_position": {"support_level": 1330, "resistance_level": 1420}}}}


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, texts):
        self.texts, self.calls = list(texts), 0

    available = True

    def generate(self, system, prompt, json_mode, max_tokens=16000):
        self.calls += 1
        t = self.texts.pop(0)
        if isinstance(t, Exception):
            raise t
        from src.llm.providers import LLMResult
        return LLMResult(t, self.name, self.model)


def test_system_prompt_is_india_adapted_and_stable():
    assert "SEBI" in SYSTEM_PROMPT and "delivery" in SYSTEM_PROMPT and "chip_structure" in SYSTEM_PROMPT
    assert "80-100: Strong Buy" in SYSTEM_PROMPT and "₹" in SYSTEM_PROMPT
    assert "{" not in SYSTEM_PROMPT.split("```json")[0]          # no unformatted placeholders / per-run values before the schema


def test_prompt_contains_india_sections():
    p = build_prompt(ctx(realtime={"price": 1340.0, "volume_ratio": 0.6, "pe_ratio": 22.0, "total_mv": 1.8e13},
                         price_band={"band_pct": None, "remarks": None}, flow={"bias": "neutral", "reason": "x", "fii": "+₹5,736 Cr", "dii": "-₹2,253 Cr"}),
                     "news text")
    for s in ("Decision Dashboard Analysis Request", "Delivery Structure", "47.0%", "₹1,800,000.00 Cr",
              "No fixed band", "FII/FPI", "Trend Pre-analysis", "news text", "Reliance Industries (RELIANCE)"):
        assert s in p, s


def test_extract_json_variants():
    assert extract_json("```json\n" + json.dumps(GOOD) + "\n```")["sentiment_score"] == 74
    assert extract_json("Here you go: " + json.dumps(GOOD) + " thanks")["action"] == "buy"
    broken = '{"sentiment_score": 70, "is_bullish": True, "list": [1, 2,], "dashboard": {"x": 1,}'   # trailing commas, Python bool, truncated
    d = extract_json(broken)
    assert d["sentiment_score"] == 70 and d["is_bullish"] is True and d["list"] == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_integrity_retry_then_success():
    partial = {k: v for k, v in GOOD.items() if k != "analysis_summary"}
    prov = FakeProvider([json.dumps(partial), json.dumps(GOOD)])
    r, raw = StockAnalyzer(LLMRouter([prov])).analyze(ctx(), "news")
    assert prov.calls == 2 and r.analysis_summary == "Uptrend intact." and r.model_used == "fake:fake-1"
    assert check_integrity(GOOD) == []


def test_buy_downgraded_when_chasing_or_near_resistance():
    r = result_from_json(dict(GOOD), "RELIANCE", "Reliance")
    r.current_price = 1340.0
    c = ctx()
    c["trend_analysis"]["bias_ma5"] = 6.5
    apply_guardrails(r, c)
    assert r.decision_type == "hold" and 40 <= r.sentiment_score <= 59 and "do not chase" in r.guardrail_reason
    r2 = result_from_json(dict(GOOD), "RELIANCE", "Reliance")
    r2.current_price = 1400.0                                       # within 3% of resistance 1420, flow neutral
    apply_guardrails(r2, ctx())
    assert r2.operation_advice == "Range-bound watch" and r2.dashboard["decision_stability"]["applied"]
    r3 = result_from_json(dict(GOOD), "RELIANCE", "Reliance")
    r3.current_price = 1340.0                                       # near support: buy allowed
    apply_guardrails(r3, ctx())
    assert r3.decision_type == "buy" and r3.guardrail_reason == ""


def test_confidence_capped_without_delivery():
    r = result_from_json(dict(GOOD), "500325", "Reliance")
    r.current_price = 1340.0
    apply_guardrails(r, ctx(delivery=None))
    assert r.confidence_level == "Medium"


def test_fallback_when_no_llm_or_all_fail():
    r, _ = StockAnalyzer(LLMRouter([])).analyze(ctx(), None)
    assert r.is_fallback and r.sentiment_score == 72 and r.dashboard["battle_plan"]["sniper_points"]["stop_loss"].startswith("Stop-loss: ₹")
    assert r.confidence_level == "Low" and r.dashboard["data_perspective"]["chip_structure"]["chip_health"] == "Healthy"
    failing = FakeProvider([ProviderError("HTTP 401 bad key")])
    r2, _ = StockAnalyzer(LLMRouter([failing])).analyze(ctx(), None)
    assert r2.is_fallback and "401" in r2.error_message


# ---- providers -------------------------------------------------------------------------------------------------------------
class FakeAnthropicClient:
    def __init__(self, stop="end_turn"):
        self.kw, self.stop = None, stop
        outer = self

        class _M:
            def create(self_inner, **kw):
                outer.kw = kw
                return SimpleNamespace(stop_reason=outer.stop, stop_details=SimpleNamespace(category="cyber"), model=kw["model"],
                                       content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text='{"a": 1}')],
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0))
        self.messages = _M()
        self.beta = SimpleNamespace(messages=_M())


def test_anthropic_provider_request_shape():
    fake = FakeAnthropicClient()
    p = AnthropicProvider("claude-opus-5-5", "medium", client=fake)
    res = p.generate("SYS", "PROMPT", True)
    assert res.text == '{"a": 1}' and res.provider == "anthropic"
    kw = fake.kw
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "medium"}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["messages"][0]["content"] == "PROMPT"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw and "temperature" not in kw         # Opus 5.5: thinking always on; sampling params rejected
    with pytest.raises(ProviderError):
        AnthropicProvider("claude-opus-5-5", client=FakeAnthropicClient(stop="refusal")).generate("S", "P", True)


def test_gemini_and_openai_compatible_providers():
    sent = {}

    def gpost(url, params=None, json=None, timeout=None, headers=None):
        sent["g"] = (url, params, json)
        return SimpleNamespace(status_code=200, json=lambda: {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    g = GeminiProvider("k", "gemini-x", post=gpost)
    assert g.generate("S", "P", True).text == "{}" and sent["g"][2]["generationConfig"]["responseMimeType"] == "application/json"

    def opost(url, json=None, headers=None, timeout=None):
        sent["o"] = (url, json, headers)
        return SimpleNamespace(status_code=200, json=lambda: {"model": "m", "choices": [{"message": {"content": "hi"}}]})
    o = OpenAICompatProvider("k", "https://api.deepseek.com/v1", "deepseek-chat", post=opost)
    assert o.generate("S", "P", False).text == "hi" and sent["o"][0].endswith("/chat/completions")
    assert not OpenAICompatProvider("", "https://api.openai.com/v1", "x").available
    assert OpenAICompatProvider("", "http://localhost:11434/v1", "llama3").available       # local Ollama needs no key


def test_router_falls_through_providers():
    bad = FakeProvider([ProviderError("HTTP 401")])
    good = FakeProvider(['{"ok": true}'])
    assert LLMRouter([bad, good]).generate("S", "P").provider == "fake"
    with pytest.raises(AllProvidersFailed):
        LLMRouter([FakeProvider([ProviderError("HTTP 400")])]).generate("S", "P")

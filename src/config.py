"""Settings, read from the environment (and a local .env file). Mirrors the original project's config surface for the parts
this replica implements; every value can be overridden per run by environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float, minimum: Optional[float] = None) -> float:
    try:
        v = float(os.getenv(name, "") or default)
    except ValueError:
        v = default
    return max(minimum, v) if minimum is not None else v


def _int(name: str, default: int, minimum: Optional[int] = None) -> int:
    try:
        v = int(float(os.getenv(name, "") or default))
    except ValueError:
        v = default
    return max(minimum, v) if minimum is not None else v


def _list(name: str) -> List[str]:
    return [x.strip() for x in (os.getenv(name) or "").replace(";", ",").replace("\n", ",").split(",") if x.strip()]


@dataclass
class Config:
    # watchlist and run behaviour
    stock_list: List[str] = field(default_factory=list)
    market_review_enabled: bool = True
    report_type: str = "simple"          # simple | full (full = per-stock dashboard details)
    report_language: str = "en"
    single_stock_notify: bool = False
    analysis_delay: float = 0.0
    trading_day_check_enabled: bool = True
    max_workers: int = 3
    bias_threshold: float = 5.0          # % above MA5 that counts as chasing
    schedule_time: str = "18:30"         # IST; NSE's full bhavcopy (delivery data) is out by ~18:00
    log_level: str = "INFO"
    db_path: Path = ROOT / "data" / "stock_analysis.db"
    reports_dir: Path = ROOT / "reports"
    logs_dir: Path = ROOT / "logs"
    save_context_snapshot: bool = True
    trading_style: str = "short"         # short: days-2 weeks (the original's style) | swing: 2-8 weeks
    capital: float = 100000.0            # for swing position sizing (₹)
    risk_per_trade_pct: float = 1.0      # % of capital lost if a swing stop is hit

    # data sources
    data_source_priority: List[str] = field(default_factory=lambda: ["yfinance", "nse"])
    nse_enabled: bool = True
    enable_delivery: bool = True
    history_days: int = 120

    # LLM
    llm_provider_order: List[str] = field(default_factory=lambda: ["anthropic", "gemini", "openai"])
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""
    llm_temperature: float = 0.4
    llm_timeout_seconds: float = 300.0

    # news search
    google_news_rss_enabled: bool = True
    tavily_api_keys: List[str] = field(default_factory=list)
    serpapi_api_keys: List[str] = field(default_factory=list)
    brave_api_keys: List[str] = field(default_factory=list)
    news_max_age_days: int = 7

    # notifications
    email_sender: str = ""
    email_password: str = ""
    email_receivers: List[str] = field(default_factory=list)
    email_sender_name: str = "India Stock Analysis"
    smtp_server: str = ""
    smtp_port: int = 0

    # API server
    api_auth_token: str = ""

    @property
    def anthropic_configured(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))

    def refresh_stock_list(self) -> None:
        self.stock_list = _list("STOCK_LIST")


def load_config(env_file: Optional[Path] = None) -> Config:
    if load_dotenv is not None:
        load_dotenv(env_file or ROOT / ".env", override=False)
    c = Config(
        stock_list=_list("STOCK_LIST"),
        market_review_enabled=_bool("MARKET_REVIEW_ENABLED", True),
        report_type=(os.getenv("REPORT_TYPE") or "simple").strip().lower(),
        report_language=(os.getenv("REPORT_LANGUAGE") or "en").strip().lower() or "en",
        single_stock_notify=_bool("SINGLE_STOCK_NOTIFY", False),
        analysis_delay=_float("ANALYSIS_DELAY", 0.0, 0.0),
        trading_day_check_enabled=_bool("TRADING_DAY_CHECK_ENABLED", True),
        max_workers=_int("MAX_WORKERS", 3, 1),
        bias_threshold=_float("BIAS_THRESHOLD", 5.0, 1.0),
        schedule_time=(os.getenv("SCHEDULE_TIME") or "18:30").strip(),
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        db_path=Path(os.getenv("DATABASE_PATH") or ROOT / "data" / "stock_analysis.db"),
        reports_dir=Path(os.getenv("REPORTS_DIR") or ROOT / "reports"),
        logs_dir=Path(os.getenv("LOG_DIR") or ROOT / "logs"),
        save_context_snapshot=_bool("SAVE_CONTEXT_SNAPSHOT", True),
        trading_style=("swing" if (os.getenv("TRADING_STYLE") or "").strip().lower() == "swing" else "short"),
        capital=_float("CAPITAL", 100000.0, 1000.0),
        risk_per_trade_pct=min(5.0, _float("RISK_PER_TRADE_PCT", 1.0, 0.1)),
        data_source_priority=_list("DATA_SOURCE_PRIORITY") or ["yfinance", "nse"],
        nse_enabled=_bool("NSE_ENABLED", True),
        enable_delivery=_bool("ENABLE_DELIVERY", True),
        history_days=_int("HISTORY_DAYS", 120, 60),
        llm_provider_order=_list("LLM_PROVIDER_ORDER") or ["anthropic", "gemini", "openai"],
        anthropic_model=os.getenv("ANTHROPIC_MODEL") or "claude-opus-5-5",
        anthropic_effort=(os.getenv("ANTHROPIC_EFFORT") or "medium").lower(),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-3.6-flash",
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=(os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        openai_model=os.getenv("OPENAI_MODEL", ""),
        llm_temperature=_float("LLM_TEMPERATURE", 0.4, 0.0),
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 300.0, 10.0),
        google_news_rss_enabled=_bool("GOOGLE_NEWS_RSS_ENABLED", True),
        tavily_api_keys=_list("TAVILY_API_KEYS"),
        serpapi_api_keys=_list("SERPAPI_API_KEYS"),
        brave_api_keys=_list("BRAVE_API_KEYS"),
        news_max_age_days=_int("NEWS_MAX_AGE_DAYS", 7, 1),
        email_sender=os.getenv("EMAIL_SENDER", ""),
        email_password=os.getenv("EMAIL_PASSWORD", ""),
        email_receivers=_list("EMAIL_RECEIVERS"),
        email_sender_name=os.getenv("EMAIL_SENDER_NAME") or "India Stock Analysis",
        smtp_server=os.getenv("SMTP_SERVER", ""),
        smtp_port=_int("SMTP_PORT", 0, 0),
        api_auth_token=os.getenv("API_AUTH_TOKEN", ""),
    )
    for d in (c.db_path.parent, c.reports_dir, c.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return c


_CONFIG: Optional[Config] = None


def get_config() -> Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def set_config(c: Config) -> None:
    """Used by tests and the API server to inject a configuration."""
    global _CONFIG
    _CONFIG = c

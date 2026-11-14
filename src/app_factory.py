"""Builds every component from the configuration, shared by the CLI (main.py) and the API server (server.py)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from data_provider.manager import DataFetcherManager

from .analyzer import StockAnalyzer
from .backtest import BacktestService
from .config import Config
from .india.market import TradingCalendar
from .india.symbols import parse_symbol
from .llm.providers import LLMRouter
from .market_review import MarketAnalyzer
from .notification import NotificationService
from .pipeline import StockAnalysisPipeline
from .search_service import SearchService
from .storage import Database


@dataclass
class App:
    config: Config
    fetcher: DataFetcherManager
    db: Database
    search: SearchService
    router: LLMRouter
    notifier: NotificationService
    calendar: TradingCalendar
    pipeline: StockAnalysisPipeline
    market: MarketAnalyzer
    backtest: BacktestService


def build_app(config: Config) -> App:
    cache = Path(config.db_path).parent / "cache"
    fetcher = DataFetcherManager(cache, priority=config.data_source_priority, nse_enabled=config.nse_enabled)
    db = Database(config.db_path)
    search = SearchService(config, announcements=fetcher.get_announcements)
    router = LLMRouter.from_config(config)
    notifier = NotificationService(config)
    calendar = TradingCalendar(cache / "nse_holidays.json", fetch_holidays=fetcher.nse_api.holidays if config.nse_enabled else None)
    pipeline = StockAnalysisPipeline(config, fetcher, db, search, StockAnalyzer(router, config.bias_threshold), notifier, calendar)

    def refresh(code: str) -> None:
        pipeline.fetch_and_save_stock_data(parse_symbol(code), force_refresh=True)

    return App(config, fetcher, db, search, router, notifier, calendar, pipeline, MarketAnalyzer(fetcher, search, router),
               BacktestService(db, refresh))

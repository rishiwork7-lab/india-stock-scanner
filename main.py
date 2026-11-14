"""Daily Stock Analysis for India (NSE/BSE): CLI entry point, same flags as the original project.

  python main.py                         analyse STOCK_LIST + market review, save the report, notify
  python main.py --stocks RELIANCE,TCS,500325
  python main.py --dry-run               fetch data only
  python main.py --market-review         market review only      --no-market-review   stocks only
  python main.py --schedule              run daily at SCHEDULE_TIME (IST)
  python main.py --serve | --serve-only  start the API + web UI (http://127.0.0.1:8790)
  python main.py --backtest [--backtest-code TCS] [--backtest-days 10] [--backtest-force]
  python main.py --check-notify          send a test email (the only notification channel)
  python main.py --build-site [--site-dir site] [--no-analysis]
                                         scanner + events + market review + watchlist -> static website (GitHub Pages)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.app_factory import build_app  # noqa: E402
from src.backtest import OVERALL, format_summary  # noqa: E402
from src.config import load_config, set_config  # noqa: E402
from src.india.market import now_ist  # noqa: E402
from src.india.symbols import parse_list  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402
from src.report import generate_dashboard_report  # noqa: E402
from src.scheduler import run_scheduler  # noqa: E402

logger = logging.getLogger("main")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="AI stock analysis for NSE/BSE: decision dashboard + market review")
    p.add_argument("--debug", action="store_true", help="debug logging")
    p.add_argument("--dry-run", action="store_true", help="fetch data only, no analysis")
    p.add_argument("--stocks", type=str, help="comma-separated codes, overrides STOCK_LIST (e.g. RELIANCE,TCS,500325)")
    p.add_argument("--no-notify", action="store_true", help="do not send notifications")
    p.add_argument("--check-notify", action="store_true", help="send a test notification to every configured channel")
    p.add_argument("--single-notify", action="store_true", help="one notification per stock")
    p.add_argument("--workers", type=int, help="parallel workers")
    p.add_argument("--schedule", action="store_true", help="run daily at SCHEDULE_TIME (IST)")
    p.add_argument("--no-run-immediately", action="store_true", help="with --schedule: wait for the first scheduled time")
    p.add_argument("--market-review", action="store_true", help="market review only")
    p.add_argument("--no-market-review", action="store_true", help="skip the market review")
    p.add_argument("--force-run", action="store_true", help="run even on a non-trading day")
    p.add_argument("--force-refresh", action="store_true", help="re-download bars even if already stored")
    p.add_argument("--style", choices=["short", "swing"], help="short: days-2 weeks (default); swing: 2-8 weeks with trend template, ATR stops and sizing")
    p.add_argument("--webui", "--serve", dest="serve", action="store_true", help="start the API/web UI and run the analysis")
    p.add_argument("--webui-only", "--serve-only", dest="serve_only", action="store_true", help="start only the API/web UI")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--host", type=str, default="127.0.0.1")
    p.add_argument("--no-context-snapshot", action="store_true", help="do not store the analysis context in history")
    p.add_argument("--backtest", action="store_true", help="evaluate past recommendations")
    p.add_argument("--backtest-code", type=str)
    p.add_argument("--backtest-days", type=int, default=10)
    p.add_argument("--backtest-force", action="store_true")
    p.add_argument("--build-site", action="store_true", help="build the static website: Nifty 50 + ETF scanner, events, market, watchlist")
    p.add_argument("--site-dir", type=str, default="site", help="output folder for --build-site")
    p.add_argument("--no-analysis", action="store_true", help="with --build-site: skip the watchlist analysis (faster)")
    p.add_argument("--portfolio", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--etf-rotation", action="store_true", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def start_server(host: str, port: int, block: bool):
    import uvicorn
    from server import app
    if block:
        uvicorn.run(app, host=host, port=port, log_level="info")
    else:
        threading.Thread(target=lambda: uvicorn.run(app, host=host, port=port, log_level="warning"), daemon=True, name="api").start()
        logger.info("Web UI: http://%s:%d", host, port)


def run_once(app, args) -> int:
    cfg = app.config
    if cfg.trading_day_check_enabled and not args.force_run and not app.calendar.is_trading_day(now_ist().date()):
        logger.info("Today (%s) is not an NSE trading day; skipping (use --force-run to override)", now_ist().date())
        return 0
    do_market = (args.market_review or (cfg.market_review_enabled and not args.no_market_review))
    do_stocks = not args.market_review
    notify = not args.no_notify
    results, review = [], None
    if do_stocks:
        cfg.refresh_stock_list()
        codes = [c for c in (args.stocks.split(",") if args.stocks else cfg.stock_list) if c.strip()]
        targets, bad = parse_list(codes)
        for code, why in bad:
            logger.warning("Skipping %r: %s", code, why)
        if not targets:
            logger.error("No valid stocks. Set STOCK_LIST in .env or pass --stocks RELIANCE,TCS")
        else:
            results = app.pipeline.run(targets, dry_run=args.dry_run, send_notification=notify, merge_notification=do_market,
                                       force_refresh=args.force_refresh, style=args.style)
    if do_market and not args.dry_run:
        payload = app.market.run_review_payload()
        review = payload["report"]
        path = app.notifier.save_report(review, cfg.reports_dir, f"market_review_{now_ist():%Y%m%d}.md")
        (Path(cfg.reports_dir) / "market_review_latest.json").write_text(json.dumps(payload, default=str))   # for the web UI
        logger.info("Market review saved: %s", path)
    if notify and not args.dry_run and app.notifier.is_available() and not cfg.single_stock_notify:
        if do_stocks and do_market and results:
            # one combined push: stock dashboard first, then the market review (the original's merged notification)
            merged = generate_dashboard_report(results, report_type=cfg.report_type) + "\n\n---\n\n" + (review or "")
            app.notifier.send(merged, f"🎯 {now_ist():%Y-%m-%d} Decision Dashboard + Market Review")
        elif review:   # market review only, or no stock could be analysed
            app.notifier.send(review, f"🎯 {now_ist():%Y-%m-%d} India Market Review")
    for r in sorted(results, key=lambda r: r.sentiment_score, reverse=True):
        print(f"{r.emoji} {r.name} ({r.code}): {r.operation_advice} | score {r.sentiment_score} | {r.trend_prediction}"
              + (" | technical-only" if r.is_fallback else ""))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = load_config()
    if args.workers:
        cfg.max_workers = args.workers
    if args.single_notify:
        cfg.single_stock_notify = True
    if args.no_context_snapshot:
        cfg.save_context_snapshot = False
    set_config(cfg)
    setup_logging(cfg.logs_dir, cfg.log_level, args.debug)
    if args.portfolio or args.etf_rotation:
        logger.error("Portfolio and ETF-rotation modes are not part of the India replica (see PLAN.md, scope).")
        return 2
    app = build_app(cfg)
    logger.info("LLM providers: %s | news: %s | notify: %s", app.router.describe(),
                ", ".join(p.name for p in app.search.providers) or "none", ", ".join(app.notifier.channels()) or "none")
    if args.check_notify:
        res = app.notifier.send(f"✅ Test message from India Stock Analysis ({now_ist():%Y-%m-%d %H:%M} IST)", "Test notification")
        print(res or "No notification channel configured")
        return 0 if res and all(res.values()) else 1
    if args.build_site:
        from src.site_builder import build_site
        meta = build_site(app, Path(args.site_dir), analysis=not args.no_analysis, review=not args.no_market_review,
                          log=lambda m: logger.info("%s", m))
        print(f"Site built in {args.site_dir}/ (data {meta['data_date']}, {meta['stocks']} stocks, {meta['etfs']} ETFs)")
        for pr in meta["problems"]:
            print("  note:", pr)
        return 0
    if args.backtest:
        out = app.backtest.run(args.backtest_code, args.backtest_days, args.backtest_force, args.style)
        print(f"Evaluated {out['evaluated']} new of {out['analyses']} stored analyses")
        for key, s in out["summaries"].items():
            if key == OVERALL or args.backtest_code:
                print(format_summary(s))
        return 0
    if args.serve_only:
        start_server(args.host, args.port, block=True)
        return 0
    if args.serve:
        start_server(args.host, args.port, block=False)
    if args.schedule:
        run_scheduler(lambda: run_once(app, args), cfg.schedule_time, run_immediately=not args.no_run_immediately)
        return 0
    code = run_once(app, args)
    if args.serve:
        logger.info("Analysis done; the web UI keeps running (Ctrl-C to stop)")
        threading.Event().wait()
    return code


if __name__ == "__main__":
    sys.exit(main())

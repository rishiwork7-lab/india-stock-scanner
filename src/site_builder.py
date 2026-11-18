"""Builds the static website (GitHub Pages): one HTML page plus JSON data files, no server needed.

  site/index.html                 the web UI (copied from web/index.html)
  site/data/meta.json             when it was built, data date, sources, AI/news status, problems met
  site/data/scan.json             Nifty 500 + ETF scanner with events and index/sector memberships (src/scanner.py)
  site/data/candles/<CODE>.json   ~1 year of daily bars with moving averages, for the charts (columns, not rows)
  site/data/market.json           India market review (src/market_review.py)
  site/data/analysis.json         full analysis of the watchlist (STOCK_LIST), swing and short-term styles
  site/data/evidence.json         historical checks (tools/swing_check.py, tools/swing_metrics.py)
  site/data/backtest.json         how past stored calls turned out (src/backtest.py)
  site/data/portfolio.json        today's ₹1 lakh plan, its paper record and the challengers' records (src/portfolio.py)
  site/data/scorecard.json        how each kind of signal in the diary did afterwards (src/journal.py, src/scorecard.py)
  records/                        signal diary and plan records; committed by the daily GitHub run (SAVE_RECORDS=1)
Run: python main.py --build-site   (the GitHub workflow runs this every trading day after the close)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from .delivery import analyze_delivery
from . import journal, portfolio as pf
from .scorecard import scorecard
from .events import EventService
from .india.market import now_ist
from .india.symbols import parse_list, parse_symbol
from .scanner import INDEX, download_prices, run_scan
from .universe import ETFS, load_universe

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "index.html"
CANDLE_BARS = 260


def candles(df: pd.DataFrame, bars: int = CANDLE_BARS) -> Dict[str, List]:
    """Columns {"time": [...], "open": [...], ..., "ma200": [...]} (about half the size of one object per day)."""
    d = df.sort_values("date").reset_index(drop=True).copy()
    for n in (5, 10, 20, 50, 200):
        d[f"ma{n}"] = d["close"].rolling(n).mean()
    d = d.tail(bars)
    num = lambda s: [None if v != v else round(float(v), 2) for v in s]  # noqa: E731
    out = {"time": [pd.Timestamp(x).strftime("%Y-%m-%d") for x in d["date"]]}
    for k in ("open", "high", "low", "close", "ma5", "ma10", "ma20", "ma50", "ma200"):
        out[k] = num(d[k])
    out["volume"] = [float(v) for v in d["volume"]]
    return out


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, default=str, separators=(",", ":")), encoding="utf-8")


def _previous_published() -> Optional[Dict]:
    """portfolio.json as last published: the fallback when the saved records are missing."""
    url = os.environ.get("SITE_URL", "").rstrip("/")
    if not url:
        return None
    import requests
    r = requests.get(f"{url}/data/portfolio.json", timeout=20)
    return r.json() if r.status_code == 200 else None


def records_dir(base: Path) -> Path:
    """records/ in the repository when the daily GitHub run saves them (SAVE_RECORDS=1), otherwise under data/ (local runs)."""
    return ROOT / "records" if os.environ.get("SAVE_RECORDS") == "1" else Path(base) / "records"


def build_plans(scan: Dict, rec: Path, previous: Callable[[], Optional[Dict]], log: Callable[[str], None] = logger.info,
                legacy_state: Optional[Path] = None) -> Dict:
    """Advance the champion and every challenger (src/portfolio.py SPECS), apply the promotion rule once a month, and
    return what the page shows."""
    pdir = Path(rec) / "plans"
    pdir.mkdir(parents=True, exist_ok=True)
    champ_file = pdir / "champion.json"
    champ = json.loads(champ_file.read_text()) if champ_file.exists() else None
    states = {sp.id: json.loads((pdir / f"{sp.id}.json").read_text()) for sp in pf.SPECS if (pdir / f"{sp.id}.json").exists()}
    if not states:
        old = None
        if legacy_state and Path(legacy_state).exists():
            states["P1"] = json.loads(Path(legacy_state).read_text())
            log("plans: P1 record taken from the earlier data/portfolio/state.json")
        else:
            try:
                old = previous()
            except Exception as e:
                log(f"plans: no earlier record ({e}); starting new ones")
            if old:
                if old.get("plans"):
                    states = {k: v["state"] for k, v in old["plans"].items() if v.get("state")}
                    champ = old.get("champion_info") or champ
                elif old.get("state"):
                    states["P1"] = old["state"]
                log(f"plans: restored {', '.join(states)} from the published site")
    champ = champ or {"champion": "P1", "switches": [], "last_check": None}
    for sp in pf.SPECS:
        states[sp.id] = pf.step(states.get(sp.id), scan, sp)
        (pdir / f"{sp.id}.json").write_text(json.dumps(states[sp.id], default=str))
    month = scan["date"][:7]
    if champ.get("last_check") != month:
        sw = pf.promotion_check(states, champ["champion"], scan["date"])
        if sw:
            log(f"plans: {sw['to']} replaces {sw['from']} as the plan shown ({sw['gain_pts']:+.1f} points a year)")
            champ["switches"].append(sw)
            champ["champion"] = sw["to"]
        champ["last_check"] = month
    champ_file.write_text(json.dumps(champ, default=str))
    cid = champ["champion"]
    spec = pf.BY_ID[cid]
    plans = {}
    for sp in pf.SPECS:
        st = states[sp.id]
        plans[sp.id] = {"spec": {**pf.asdict(sp), "slot_weight_pct": round(sp.slot_weight * 100, 1)}, "record": pf.report(st, scan), "state": st,
                        "vs_champion": None if sp.id == cid else pf.compare(st, states[cid]),
                        "today": [{"code": h["code"], "weight_pct": round(h["weight"] * 100, 1)} for h in pf.targets(scan, spec=sp)]}
    return {"champion": cid, "champion_info": champ, "plan": pf.plan(scan, spec=spec), "record": plans[cid]["record"], "plans": plans,
            "promotion_rule": pf.PROMOTE,
            "rules": {"core": {"code": "NIFTYBEES", "weight": spec.core}, "cushion": {"code": "GOLDBEES", "weight": spec.gold}, "stock_slots": spec.slots,
                      "stock_weight_pct": round(spec.slot_weight * 100, 1), "picks": spec.picks,
                      "min_traded_value_cr": pf.MIN_TV_CR, "max_atr_pct": pf.MAX_ATR_PCT, "results_block_sessions": pf.RESULTS_BLOCK,
                      "keep_rank": pf.KEEP_RANK, "cost_pct": {k: v * 100 for k, v in pf.COST.items()}}}


def refetch_lagging(prices: Dict[str, pd.DataFrame], downloader: Callable, log: Callable[[str], None] = logger.info,
                    tries: int = 2) -> Dict[str, pd.DataFrame]:
    """Yahoo's batch download sometimes returns the latest session for only part of the list (seen 2026-11-18: 181 of 521
    a day behind). Download the lagging ones again, up to `tries` times; keep whichever copy is newer."""
    last = {s: pd.Timestamp(df["date"].iloc[-1]) for s, df in prices.items() if df is not None and len(df)}
    if not last:
        return prices
    latest = pd.Series(list(last.values())).mode().max()
    for i in range(tries):
        behind = [s for s, d in last.items() if d < latest and not s.startswith("^")]
        if not behind:
            break
        log(f"prices: {len(behind)} tickers a day or more behind {latest.date()}; downloading them again ({i + 1}/{tries})")
        try:
            again = downloader(behind)
        except Exception as e:
            log(f"prices: second download failed ({e})")
            break
        for s, df in again.items():
            if df is not None and len(df) and pd.Timestamp(df["date"].iloc[-1]) > last.get(s, pd.Timestamp.min):
                prices[s], last[s] = df, pd.Timestamp(df["date"].iloc[-1])
    return prices


def build_site(app, out_dir: Path, analysis: bool = True, review: bool = True,
               downloader: Callable = download_prices, log: Callable[[str], None] = logger.info,
               event_yahoo: Optional[Callable] = None, portfolio_prev: Optional[Callable] = None) -> Dict:
    cfg = app.config
    out_dir, data_dir = Path(out_dir), Path(out_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    base = Path(cfg.db_path).parent
    problems: List[str] = []
    today = now_ist().date()

    uni = load_universe(base / "universe")
    members = uni["members"]
    if uni["stale_lists"]:
        problems.append(f"index lists from a stored copy (NSE unavailable): {', '.join(uni['stale_lists'][:5])}"
                        + ("…" if len(uni["stale_lists"]) > 5 else ""))
    log(f"scanner: {len(members)} Nifty 500 stocks + {len(ETFS)} ETFs")
    rec = records_dir(base)
    member_codes = {m["symbol"] for m in members}
    gone = [c for c in journal.recent_symbols(rec) if c not in member_codes and c not in {e.symbol for e in ETFS}]   # left the Nifty 500
    prices = downloader([m["symbol"] for m in members] + [e.symbol for e in ETFS] + [INDEX] + gone)
    prices = refetch_lagging(prices, downloader, log)
    if INDEX not in prices:
        problems.append("NIFTY 50 index prices missing: relative strength not computed")
    nse = app.fetcher.nse_api if cfg.nse_enabled else None
    events = EventService(base / "cache", nse, event_yahoo)
    log(f"events: {events.load_upcoming()} confirmed results meetings in the next 60 days")
    events.prefetch([m["symbol"] for m in members])
    nav = {}
    if nse is not None:
        try:
            nav = nse.etfs()
        except Exception as e:
            problems.append(f"ETF NAV from NSE unavailable ({e}): premium/discount not shown")

    def delivery(sym: str, price: float):
        if not cfg.enable_delivery:
            return None
        return analyze_delivery(app.fetcher.get_delivery(parse_symbol(sym), 20), price)

    scan = run_scan(members, prices, today, app.calendar, events, nav, delivery, cfg.capital, cfg.risk_per_trade_pct,
                    cfg.bias_threshold, progress=lambda i, n, s: log(f"scanned {i}/{n} {s}") if i % 10 == 0 else None)
    scanned = {r["code"] for r in scan["stocks"]}
    scan["indices"] = {k: {**v, "codes": [c for c in v["codes"] if c in scanned]} for k, v in uni["indices"].items()}
    if scan["missing"]:
        problems.append(f"no price data for: {', '.join(scan['missing'])}")
    if not any(s.startswith("NSE") for r in scan["stocks"] for s in (r.get("events") or {}).get("sources", [])):
        problems.append("NSE event calendar unavailable: results dates are Yahoo estimates")
    _dump(data_dir / "scan.json", scan)
    try:
        _dump(data_dir / "portfolio.json", build_plans(scan, rec, portfolio_prev or _previous_published, log, legacy_state=base / "portfolio" / "state.json"))
    except Exception as e:                                                       # never stop the site for the paper record
        logger.exception("portfolio")
        problems.append(f"model portfolio failed: {e}")
    cdir = data_dir / "candles"
    if cdir.exists():
        shutil.rmtree(cdir)
    for code in [r["code"] for r in scan["stocks"] + scan["etfs"]]:
        _dump(cdir / f"{code}.json", candles(prices[code]))

    market = None
    if review:
        log("market review")
        try:
            market = app.market.run_review_payload()
            Path(cfg.reports_dir).mkdir(parents=True, exist_ok=True)
            (Path(cfg.reports_dir) / "market_review_latest.json").write_text(json.dumps(market, default=str))
            _dump(data_dir / "market.json", market)
        except Exception as e:
            problems.append(f"market review failed: {e}")

    # signal diary (one row per stock/ETF per day, saved for good) and the live scorecard built from it
    try:
        mood = (market or {}).get("mood") or {}
        idx = prices.get(INDEX)
        above = None
        if idx is not None and len(idx) >= 200:
            above = bool(idx["close"].iloc[-1] > idx["close"].tail(200).mean())
        light = "".join(ch for ch in str(mood.get("light") or "") if ch.isascii()).strip() or None
        f = journal.save(rec, journal.rows_from_scan(scan, mood.get("score"), light, above))
        log(f"signal diary: {f}")
        diary = journal.load(rec)
        _dump(data_dir / "scorecard.json", scorecard(diary, prices))
    except Exception as e:
        logger.exception("diary")
        problems.append(f"signal diary / scorecard failed: {e}")

    if analysis:
        targets, _bad = parse_list(cfg.stock_list)
        out = {}
        for style in ("swing", "short"):
            log(f"watchlist analysis ({style}): {', '.join(t.code for t in targets)}")
            try:
                rs = app.pipeline.run(targets, send_notification=False, style=style)
                out[style] = sorted([r.to_dict() for r in rs], key=lambda x: x.get("sentiment_score") or 0, reverse=True)
            except Exception as e:
                problems.append(f"watchlist analysis ({style}) failed: {e}")
                out[style] = []
        _dump(data_dir / "analysis.json", out)
        for code in {r["code"] for rs in out.values() for r in rs}:
            if not (cdir / f"{code}.json").exists():
                df = app.db.get_daily(code, CANDLE_BARS + 220)
                if df is not None and not df.empty:
                    _dump(cdir / f"{code}.json", candles(df))

    # research results: a fresh local run (data/swing_check, not in git) wins over the copy kept in git (evidence/)
    ev_dir = base / "swing_check"
    evidence = {}
    for name, local, kept in (("swing_check", ev_dir / "summary.json", ROOT / "evidence" / "swing_check.json"),
                              ("accuracy", ev_dir / "metrics.json", ROOT / "evidence" / "swing_accuracy.json")):
        f = local if local.exists() else kept
        if f.exists():
            evidence[name] = json.loads(f.read_text())
    evidence["edge"] = {f.stem.replace("edge_", ""): json.loads(f.read_text()) for f in sorted((ROOT / "evidence").glob("edge_*.json"))}
    _dump(data_dir / "evidence.json", evidence)

    bt = {}
    for style, days in (("short", 10), ("swing", 20)):
        try:
            bt[style] = {"days": days, **app.backtest.run(None, days, style=style)}
        except Exception as e:
            problems.append(f"backtest ({style}) failed: {e}")
    _dump(data_dir / "backtest.json", bt)

    meta = {"generated_ist": now_ist().strftime("%Y-%m-%d %H:%M IST"), "data_date": scan["date"], "stocks": len(scan["stocks"]),
            "indices": len(scan["indices"]),
            "etfs": len(scan["etfs"]), "watchlist": cfg.stock_list, "ai": app.router.describe() if app.router.available else None,
            "news": [p.name for p in app.search.providers], "has_market": market is not None, "problems": problems,
            "settings": scan["settings"]}
    _dump(data_dir / "meta.json", meta)
    shutil.copyfile(WEB, out_dir / "index.html")
    (out_dir / ".nojekyll").write_text("")
    log(f"site built in {out_dir} ({len(problems)} problems)")
    return meta

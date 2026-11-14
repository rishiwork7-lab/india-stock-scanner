"""FastAPI service for local use. The website itself is static (site/, built by `python main.py --build-site`) and is
what GitHub Pages hosts; this server shows the same site on your computer and adds a "Refresh data" button.

  GET  /                          the built site (site/index.html), else web/index.html
  GET  /data/...                  the site's data files
  POST /api/v1/build-site         rebuild the site data (scanner, events, market, watchlist) -> {"task_id"}
  GET  /api/v1/health
  POST /api/v1/analysis           {"stocks": ["RELIANCE", "TCS"], "notify": false} -> {"task_id"}
  POST /api/v1/market-review      {"notify": false} -> {"task_id"}
  GET  /api/v1/tasks/{task_id}    status, progress, report (markdown + html)
  GET  /api/v1/history?code=&limit=
  GET  /api/v1/history/{id}       full stored result + rendered report
  GET  /api/v1/backtest?code=&days=10
  GET  /api/v1/config             watchlist, schedule, providers (for the UI)
  GET  /api/v1/latest?codes=      latest stored analysis per code (structured dashboard)
  GET  /api/v1/candles/{code}     stored daily bars with MA5/10/20 (for the chart)
  GET  /api/v1/market-review/latest   last saved market review (structured)
Set API_AUTH_TOKEN to require "Authorization: Bearer <token>" on /api routes.
Run: python main.py --serve-only   (or uvicorn server:app --host 127.0.0.1 --port 8790)
"""
from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.analyzer import AnalysisResult  # noqa: E402
from src.app_factory import App, build_app  # noqa: E402
from src.config import get_config  # noqa: E402
from src.india.market import now_ist  # noqa: E402
from src.india.symbols import parse_list  # noqa: E402
from src.notification import markdown_to_html  # noqa: E402
from src.report import generate_dashboard_report, generate_single_stock_report  # noqa: E402

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web" / "index.html"
SITE = ROOT / "site"
app = FastAPI(title="Daily Stock Analysis India", version="1.0")
_state: Dict = {"app": None, "tasks": {}}
_lock = threading.Lock()


def get_app() -> App:
    with _lock:
        if _state["app"] is None:
            _state["app"] = build_app(get_config())
        return _state["app"]


def set_app(a: App) -> None:
    """Tests inject a prepared App."""
    _state["app"] = a
    _state["tasks"].clear()


def auth(authorization: Optional[str] = Header(None)):
    token = get_app().config.api_auth_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="missing or wrong bearer token")


class AnalysisRequest(BaseModel):
    stocks: List[str]
    notify: bool = False
    report_type: Optional[str] = None
    style: Optional[str] = None          # short | swing


class ReviewRequest(BaseModel):
    notify: bool = False


def _task(kind: str) -> Dict:
    t = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": "running", "progress": 0, "message": "queued", "started": now_ist().isoformat(),
         "report": None, "report_html": None, "error": None, "results": []}
    _state["tasks"][t["id"]] = t
    return t


def _run_analysis(t: Dict, targets, notify: bool, report_type: str, style: Optional[str] = None):
    a = get_app()
    try:
        a.pipeline.progress = lambda pct, msg: t.update(progress=pct, message=msg)
        results = a.pipeline.run(targets, send_notification=notify, style=style)
        report = generate_dashboard_report(results, report_type=report_type)
        t.update(status="done", progress=100, message=f"{len(results)} analysed", report=report, report_html=markdown_to_html(report),
                 results=[{"code": r.code, "name": r.name, "score": r.sentiment_score, "advice": r.operation_advice,
                           "decision": r.decision_type, "fallback": r.is_fallback} for r in results])
    except Exception as e:
        t.update(status="failed", error=str(e))


def _latest_review_path() -> Path:
    return Path(get_app().config.reports_dir) / "market_review_latest.json"


def _run_review(t: Dict, notify: bool):
    a = get_app()
    try:
        t.update(message="fetching market data", progress=20)
        payload = a.market.run_review_payload()
        report = payload["report"]
        a.notifier.save_report(report, a.config.reports_dir, f"market_review_{now_ist():%Y%m%d}.md")
        _latest_review_path().write_text(json.dumps(payload, default=str))
        t["review"] = payload
        if notify and a.notifier.is_available():
            a.notifier.send(report, f"🎯 {now_ist():%Y-%m-%d} India Market Review")
        t.update(status="done", progress=100, message="done", report=report, report_html=markdown_to_html(report))
    except Exception as e:
        t.update(status="failed", error=str(e))


def _run_build(t: Dict):
    from src.site_builder import build_site
    try:
        meta = build_site(get_app(), SITE, log=lambda m: t.update(message=m))
        t.update(status="done", progress=100, message="done", result=meta)
    except Exception as e:
        t.update(status="failed", error=str(e))


@app.get("/")
def index():
    return FileResponse(WEB)       # always the current UI; it reads site/data


@app.get("/data/{path:path}")
def site_data(path: str):
    base = (SITE / "data").resolve()
    f = (base / path).resolve()
    if base not in f.parents or not f.is_file():
        raise HTTPException(status_code=404, detail="not built yet: run python main.py --build-site")
    return FileResponse(f)


@app.post("/api/v1/build-site")
def start_build(_=Depends(auth)):
    running = [t for t in _state["tasks"].values() if t["kind"] == "build_site" and t["status"] == "running"]
    if running:
        return {"task_id": running[0]["id"]}
    t = _task("build_site")
    threading.Thread(target=_run_build, args=(t,), daemon=True).start()
    return {"task_id": t["id"]}


@app.get("/api/v1/health")
def health(_=Depends(auth)):
    a = get_app()
    return {"status": "ok", "llm": a.router.describe(), "news": [p.name for p in a.search.providers], "notify": a.notifier.channels(),
            "time_ist": now_ist().isoformat()}


@app.post("/api/v1/analysis")
def start_analysis(req: AnalysisRequest, _=Depends(auth)):
    targets, bad = parse_list(req.stocks)
    if not targets:
        raise HTTPException(status_code=400, detail=f"no valid stock codes: {bad}")
    t = _task("analysis")
    rt = req.report_type or get_app().config.report_type
    style = req.style if req.style in ("short", "swing") else None
    threading.Thread(target=_run_analysis, args=(t, targets, req.notify, rt, style), daemon=True).start()
    return {"task_id": t["id"], "stocks": [x.code for x in targets], "skipped": [b[0] for b in bad]}


@app.post("/api/v1/market-review")
def start_review(req: ReviewRequest, _=Depends(auth)):
    t = _task("market_review")
    threading.Thread(target=_run_review, args=(t, req.notify), daemon=True).start()
    return {"task_id": t["id"]}


@app.get("/api/v1/tasks/{task_id}")
def task(task_id: str, _=Depends(auth)):
    t = _state["tasks"].get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="unknown task")
    return t


@app.get("/api/v1/history")
def history(code: Optional[str] = None, limit: int = 50, _=Depends(auth)):
    return get_app().db.list_history(min(max(limit, 1), 500), code)


@app.get("/api/v1/history/{hid}")
def history_item(hid: int, _=Depends(auth)):
    h = get_app().db.get_history(hid)
    if not h:
        raise HTTPException(status_code=404, detail="not found")
    raw = h.get("raw_result") or {}
    fields = {k: raw[k] for k in AnalysisResult.__dataclass_fields__ if k in raw}
    report = generate_single_stock_report(AnalysisResult(**fields)) if raw else ""
    return {"item": {k: v for k, v in h.items() if k != "context_snapshot"}, "report": report, "report_html": markdown_to_html(report)}


@app.get("/api/v1/backtest")
def backtest(code: Optional[str] = None, days: int = 10, style: Optional[str] = None, _=Depends(auth)):
    out = get_app().backtest.run(code, max(1, min(days, 120)), style=style if style in ("short", "swing") else None)
    return out


@app.get("/api/v1/config")
def ui_config(_=Depends(auth)):
    a = get_app()
    a.config.refresh_stock_list()
    return {"stock_list": a.config.stock_list, "schedule_time_ist": a.config.schedule_time, "report_type": a.config.report_type,
            "trading_style": a.config.trading_style, "capital": a.config.capital, "risk_per_trade_pct": a.config.risk_per_trade_pct,
            "llm": a.router.describe(), "ai_available": a.router.available, "news": [p.name for p in a.search.providers],
            "notify": a.notifier.channels()}


@app.get("/api/v1/latest")
def latest(codes: Optional[str] = None, style: Optional[str] = None, _=Depends(auth)):
    """Latest stored analysis per code for one style (all codes analysed if none given), sorted by score."""
    a = get_app()
    rows = a.db.list_history(500, style=style if style in ("short", "swing") else None)
    wanted = None
    if codes:
        targets, _bad = parse_list(codes.split(","))
        wanted = {t.code for t in targets}
    seen, out = set(), []
    for r in rows:
        if r["code"] in seen or (wanted is not None and r["code"] not in wanted):
            continue
        seen.add(r["code"])
        h = a.db.get_history(r["id"])
        raw = (h or {}).get("raw_result") or {}
        out.append({"id": r["id"], "created_at": r["created_at"], **raw})
    out.sort(key=lambda x: x.get("sentiment_score") or 0, reverse=True)
    return out


@app.get("/api/v1/candles/{code}")
def candles(code: str, days: int = 120, _=Depends(auth)):
    targets, _bad = parse_list([code])
    if not targets:
        raise HTTPException(status_code=400, detail="bad code")
    days = max(20, min(days, 400))
    df = get_app().db.get_daily(targets[0].code, days + 220)
    if df is None or df.empty:
        raise HTTPException(status_code=404, detail="no stored bars; run an analysis first")
    for n in (5, 10, 20, 50, 200):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    df = df.tail(days)
    out = []
    for r in df.to_dict("records"):
        row = {"time": r["date"].strftime("%Y-%m-%d"), "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
        for n in (5, 10, 20, 50, 200):
            v = r[f"ma{n}"]
            row[f"ma{n}"] = None if v != v else round(float(v), 2)
        out.append(row)
    return out


@app.get("/api/v1/market-review/latest")
def latest_review(_=Depends(auth)):
    p = _latest_review_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="no market review yet")
    d = json.loads(p.read_text())
    d["report_html"] = markdown_to_html(d.get("report", ""))
    return d


@app.get("/api/v1/swing-evidence")
def swing_evidence(_=Depends(auth)):
    """Results of tools/swing_check.py (historical check of the swing score), if it has been run."""
    d = Path(get_app().config.db_path).parent / "swing_check"
    f = d / "summary.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="run tools/swing_check.py first")
    out = json.loads(f.read_text())
    m = d / "metrics.json"
    if m.exists():
        out["metrics"] = json.loads(m.read_text())       # tools/swing_metrics.py: target/stop hit rates per group
    return out

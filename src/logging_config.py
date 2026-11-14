"""Console + daily log file (logs/stock_analysis_YYYYMMDD.log), like the original."""
import logging
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(log_dir: Path, level: str = "INFO", debug: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    lvl = logging.DEBUG if debug else getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(lvl)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    fh = logging.FileHandler(log_dir / f"stock_analysis_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    root.addHandler(fh)
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

"""Daily scheduler in IST (the original's --schedule mode): runs the job every day at SCHEDULE_TIME; the job itself skips
non-trading days unless forced."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from .india.market import IST, now_ist

logger = logging.getLogger(__name__)


def next_run_time(hhmm: str, now: Optional[datetime] = None) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    n = now or now_ist()
    t = n.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > n else t + timedelta(days=1)


def run_scheduler(job: Callable[[], None], hhmm: str, run_immediately: bool = True, sleep=time.sleep,
                  clock: Callable[[], datetime] = now_ist, max_runs: Optional[int] = None) -> None:
    runs = 0
    if run_immediately:
        _safe(job)
        runs += 1
    while max_runs is None or runs < max_runs:
        nxt = next_run_time(hhmm, clock())
        logger.info("Next scheduled run: %s", nxt.strftime("%Y-%m-%d %H:%M IST"))
        while clock() < nxt:
            sleep(min(60.0, max(1.0, (nxt - clock()).total_seconds())))
        _safe(job)
        runs += 1


def _safe(job):
    try:
        job()
    except Exception as e:  # keep the scheduler alive
        logger.exception("Scheduled job failed: %s", e)

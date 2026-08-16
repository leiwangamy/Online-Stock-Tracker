"""In-process APScheduler for LeiBot (runs while Flask is up).

Windows Task Scheduler still runs update_jobs.py when the app is closed;
this covers the case where the web app stays open.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from update_jobs import (
    DEFAULT_PRICE_CRON_HOUR,
    DEFAULT_PRICE_CRON_MINUTE,
    DEFAULT_UNIVERSE_HOUR,
    DEFAULT_UNIVERSE_MINUTE,
    DEFAULT_UNIVERSE_WEEKDAY,
    PRICE_TZ,
    job_refresh_prices,
    job_refresh_universe,
)

log = logging.getLogger("leibot.scheduler")

_scheduler = None

# APScheduler day_of_week: mon=0 … sun=6
_WEEKDAY_MAP = {
    "mon": "mon",
    "tue": "tue",
    "wed": "wed",
    "thu": "thu",
    "fri": "fri",
    "sat": "sat",
    "sun": "sun",
    "0": "mon",
    "1": "tue",
    "2": "wed",
    "3": "thu",
    "4": "fri",
    "5": "sat",
    "6": "sun",
}


def _enabled() -> bool:
    return os.environ.get("LEIBOT_SCHEDULER", "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def _setting(key: str, default: Any) -> Any:
    try:
        from db import get_setting

        val = get_setting(key, default)
        return default if val is None else val
    except Exception:
        return default


def start_scheduler() -> Any:
    """Start background jobs once (safe under Flask reloader parent process)."""
    global _scheduler
    if not _enabled():
        log.info("Scheduler disabled (LEIBOT_SCHEDULER=0)")
        return None
    if _scheduler is not None:
        return _scheduler

    # Avoid double-start with Flask debug reloader
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("apscheduler not installed; skip in-app scheduler")
        return None

    universe_day = str(_setting("schedule_universe_weekday", DEFAULT_UNIVERSE_WEEKDAY)).lower()
    universe_day = _WEEKDAY_MAP.get(universe_day, DEFAULT_UNIVERSE_WEEKDAY)
    u_hour = int(_setting("schedule_universe_hour", DEFAULT_UNIVERSE_HOUR))
    u_min = int(_setting("schedule_universe_minute", DEFAULT_UNIVERSE_MINUTE))
    p_hour = int(_setting("schedule_price_hour", DEFAULT_PRICE_CRON_HOUR))
    p_min = int(_setting("schedule_price_minute", DEFAULT_PRICE_CRON_MINUTE))

    sched = BackgroundScheduler(timezone=str(PRICE_TZ))
    sched.add_job(
        job_refresh_universe,
        CronTrigger(
            day_of_week=universe_day,
            hour=u_hour,
            minute=u_min,
            timezone=PRICE_TZ,
        ),
        id="weekly_universe",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        job_refresh_prices,
        CronTrigger(
            day_of_week="mon-fri",
            hour=p_hour,
            minute=p_min,
            timezone=PRICE_TZ,
        ),
        id="daily_prices",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    log.info(
        "Scheduler started: universe %s %02d:%02d PT; prices Mon–Fri %02d:%02d PT",
        universe_day,
        u_hour,
        u_min,
        p_hour,
        p_min,
    )
    return sched


def scheduler_status() -> dict[str, Any]:
    jobs = []
    if _scheduler is not None:
        for job in _scheduler.get_jobs():
            jobs.append(
                {
                    "id": job.id,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                }
            )
    return {
        "enabled": _enabled(),
        "running": _scheduler is not None and getattr(_scheduler, "running", False),
        "jobs": jobs,
        "timezone": "America/Los_Angeles",
        "universe_weekday": _setting("schedule_universe_weekday", DEFAULT_UNIVERSE_WEEKDAY),
        "universe_time": f"{int(_setting('schedule_universe_hour', DEFAULT_UNIVERSE_HOUR)):02d}:"
        f"{int(_setting('schedule_universe_minute', DEFAULT_UNIVERSE_MINUTE)):02d}",
        "price_time": f"{int(_setting('schedule_price_hour', DEFAULT_PRICE_CRON_HOUR)):02d}:"
        f"{int(_setting('schedule_price_minute', DEFAULT_PRICE_CRON_MINUTE)):02d}",
    }

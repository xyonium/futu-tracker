#!/usr/bin/env python3
"""
Auto-sync scheduler for the tracker container.

Runs an in-process APScheduler that calls `sync_all_accounts()` on a
weekly cron pattern (see `_add_jobs` for the exact times). Two entry
points:

  start_scheduler()   — call from app.py at Flask startup so the sync
                        cron lives inside the same container/process as
                        the web server. No extra service to manage.

  python scheduler.py — one-shot manual sync (skips weekends). Kept for
                        cron-driven deployments and ad-hoc use.

Timezone note: Futu's markets close at different local times but we care
about *when the data is settled and safe to poll*. All triggers are in
Asia/Hong_Kong, matching the OpenD container's default TZ and the HKD-
denominated reporting throughout the app.
"""

import sys
import os
import logging
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'data', 'scheduler.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _run_sync(label):
    """Called on every trigger. Isolated so both APScheduler and the CLI
    one-shot share the same success/error reporting."""
    from futu_client import sync_all_accounts
    today = date.today().isoformat()
    logger.info(f"[{label}] starting sync for {today}")
    try:
        result = sync_all_accounts(target_date=today)
        logger.info(
            f"[{label}] done  NAV={result.get('nav', 'N/A')}  "
            f"Total HKD={result.get('total_hkd', 'N/A')}"
        )
        for acct in result.get('accounts', []):
            if acct.get('success'):
                logger.info(
                    f"  {acct['account']:12s} {acct['currency']} "
                    f"{acct['total_assets']:>16,.2f}  ->  "
                    f"HKD {acct['total_hkd']:>16,.2f}"
                )
            else:
                logger.error(f"  {acct['account']:12s} ERROR: {acct.get('error')}")
    except Exception as e:
        logger.exception(f"[{label}] sync failed: {e}")


# ─────────────────────────────────────────────
# In-process APScheduler (called from app.py)
# ─────────────────────────────────────────────
_scheduler = None


def start_scheduler():
    """
    Start the background APScheduler if it isn't already running.

    Idempotent — Flask's debug reloader (or an accidental double-import)
    won't spawn two schedulers. Under gunicorn with multiple workers we
    only want ONE scheduler across the whole pool; the caller is expected
    to gate this on `os.environ.get('SCHEDULER_ENABLED', '1')` or a
    similar worker-pinning flag if scaled beyond one worker.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "APScheduler not installed — auto-sync disabled. "
            "Run `pip install APScheduler>=3.10` to enable."
        )
        return None

    tz = 'Asia/Hong_Kong'
    sched = BackgroundScheduler(
        timezone=tz,
        # Missed jobs (container restart, host reboot) run when the
        # scheduler next has a chance, provided <=1h has passed. Longer
        # than that and we'd rather wait for the next daily trigger.
        job_defaults={'misfire_grace_time': 3600, 'coalesce': True},
    )

    # HK/AU close at 16:00 HKT — poll 30 min later so Futu's clearing
    # engine has time to update `total_assets`. Weekdays only.
    sched.add_job(
        _run_sync,
        CronTrigger(day_of_week='mon-fri', hour=16, minute=30, timezone=tz),
        args=['weekday-close'],
        id='weekday_close',
        replace_existing=True,
    )
    # US closes at 04:00 HKT on Sat (04:00 HKT Fri-night = 16:00 EDT Fri).
    # By 08:00 HKT Sat the US session is fully settled — one poll captures
    # both the Friday US close and the HK/AU weekend-stale figures.
    sched.add_job(
        _run_sync,
        CronTrigger(day_of_week='sat', hour=8, minute=0, timezone=tz),
        args=['us-close'],
        id='us_close',
        replace_existing=True,
    )

    sched.start()
    _scheduler = sched
    for job in sched.get_jobs():
        logger.info(f"scheduled job {job.id}: next run at {job.next_run_time}")
    return sched


# ─────────────────────────────────────────────
# CLI one-shot (kept for backwards compatibility)
# ─────────────────────────────────────────────
def main():
    today = date.today()
    if today.weekday() >= 5:
        logger.info(f"Skipping weekend: {today}")
        return
    _run_sync('cli-oneshot')


if __name__ == '__main__':
    main()

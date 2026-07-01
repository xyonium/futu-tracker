#!/usr/bin/env python3
"""
Scheduled task to sync Futu account data daily.
Run via cron or systemd timer, e.g.:
    0 18 * * 1-5 cd /path/to/futu_portfolio && python scheduler.py

Runs at market close (6 PM HKT on weekdays).
"""

import sys
import os
import logging
from datetime import date, datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'data', 'scheduler.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    # Skip weekends
    today = date.today()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info(f"Skipping weekend: {today}")
        return

    logger.info(f"Starting daily sync for {today}")

    try:
        from futu_client import sync_all_accounts
        result = sync_all_accounts(target_date=today.isoformat())
        logger.info(f"Sync result: NAV={result.get('nav', 'N/A')}, "
                     f"Total HKD={result.get('total_hkd', 'N/A')}")

        for acct in result.get('accounts', []):
            if acct['status'] == 'ok':
                logger.info(f"  {acct['account']}: {acct['currency']} "
                           f"{acct['total_assets']:,.2f} -> HKD {acct['assets_hkd']:,.0f}")
            else:
                logger.error(f"  {acct['account']}: ERROR - {acct.get('error')}")

    except Exception as e:
        logger.exception(f"Sync failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

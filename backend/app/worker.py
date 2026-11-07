"""Durable PostgreSQL job queue. Interrupted jobs fail visibly rather than hang."""
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import select, update
from .config import DEFAULT_SYMBOLS
from .db import Scan, Session, Setting, init_db, now
from .market import screen
from .schemas import ScanRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def process_one():
    with Session.begin() as db:
        # Multiple workers can claim separate jobs safely.
        job = db.scalar(select(Scan).where(Scan.status == 'queued').order_by(Scan.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return False
        job.status, job.updated_at = 'running', now()
        job_id, request = job.id, job.request
    try:
        result = screen(ScanRequest(**request))
        with Session.begin() as db:
            job = db.get(Scan, job_id)
            job.status = 'completed' if result['items'] else 'failed'
            job.result = result
            job.error = None if result['items'] else 'No symbols could be screened. See per-symbol data errors.'
    except Exception:
        logger.exception('Screen job failed')
        with Session.begin() as db:
            job = db.get(Scan, job_id)
            job.status, job.error = 'failed', 'The scan could not finish. Please retry.'
    return True


def main():
    init_db()
    interval = max(0, int(os.getenv('SCAN_INTERVAL_SECONDS', '0')))
    next_scan = time.monotonic() + interval
    while True:
        try:
            with Session.begin() as db:
                db.execute(update(Scan).where(Scan.status.in_(['queued', 'running']), Scan.updated_at < now()-timedelta(minutes=15)).values(status='failed', error='Scan expired or worker interrupted. Please retry.', updated_at=now()))
                if interval and time.monotonic() >= next_scan:
                    watchlist = db.get(Setting, 'watchlist_IN')
                    active = db.scalar(select(Scan).where(Scan.status.in_(['queued', 'running'])).limit(1))
                    if active is None:
                        db.add(Scan(request=ScanRequest(market='IN', symbols=watchlist.value['symbols'] if watchlist else DEFAULT_SYMBOLS).model_dump()))
                    next_scan = time.monotonic() + interval
            if not process_one():
                time.sleep(1)
        except Exception:
            logger.exception('Worker loop error')
            time.sleep(5)


if __name__ == '__main__':
    main()

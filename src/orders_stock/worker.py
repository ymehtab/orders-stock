import argparse
import logging
import math
import signal
from threading import Event

import psycopg

from orders_stock.db import Database
from orders_stock.stock import process_next_job

logger = logging.getLogger(__name__)


def run(db: Database, stop: Event, poll_interval_seconds: float) -> None:
    """
    Process stock jobs until the stop event is set.

    Wait up to the polling interval when idle or after a database failure,
    then retry. Other exceptions propagate. Let an active job finish or roll
    back before stopping.
    """
    while not stop.is_set():
        try:
            order_id = process_next_job(db)
            if order_id is not None:
                logger.info("Applied stock for order %s", order_id)
                continue
        except psycopg.Error:
            logger.exception("Stock update rolled back; retrying after a short delay")
        stop.wait(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply saved stock jobs")
    parser.add_argument("--poll-interval-seconds", type=float, default=0.5)
    args = parser.parse_args()
    if not 0 < args.poll_interval_seconds < math.inf:
        parser.error("--poll-interval-seconds must be a finite, positive number")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    db = Database.from_env()
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("Stock worker started")
    run(db, stop, args.poll_interval_seconds)
    logger.info("Stock worker stopped")


if __name__ == "__main__":
    main()

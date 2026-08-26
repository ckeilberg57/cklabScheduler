import logging
import os
import signal
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings
from app.database import init_db
from app.meeting_utils import iso, now_utc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cklab.worker")


def main():
    try:
        Settings.validate_worker()
    except RuntimeError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    init_db()

    worker_pid = os.getpid()
    worker_start_iso = iso(now_utc())

    from app.scheduler_jobs import scheduler_tick

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        scheduler_tick,
        "interval",
        seconds=10,
        id="scheduler_tick",
        max_instances=1,
        kwargs={"worker_pid": worker_pid, "worker_start_iso": worker_start_iso},
    )

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    scheduler.start()
    logger.info("Scheduler worker started (PID %d)", worker_pid)

    stop_event.wait()
    scheduler.shutdown(wait=True)
    logger.info("Scheduler worker stopped")


if __name__ == "__main__":
    main()

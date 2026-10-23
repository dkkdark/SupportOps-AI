"""Run durable reply preparation, Gmail delivery, indexing and account-email jobs."""

import argparse
import logging
import signal
import threading
import time
import uuid
from supportops.config import Settings
from supportops.db import make_database
from supportops.worker import cycle, heartbeat
from supportops.migration import verify_schema
from supportops.runtime import graph_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = Settings()
    engine, sessions = make_database(config.database_url)
    verify_schema(engine)
    stop = threading.Event()
    worker_id = uuid.uuid4().hex
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    maintenance_at = 0
    try:
        with graph_runtime(config, sessions) as graphs:
            while not stop.is_set():
                try:
                    due = time.monotonic() >= maintenance_at
                    cycle(config, sessions, worker_id, housekeeping=due, graphs=graphs)
                    if due:
                        maintenance_at = time.monotonic() + 3600
                except Exception as exc:
                    logging.error(
                        "worker_cycle_failed error_type=%s", type(exc).__name__
                    )
                    if args.once:
                        raise SystemExit(1) from None
                if args.once:
                    break
                stop.wait(config.worker_interval_seconds)
    finally:
        try:
            heartbeat(sessions, worker_id, {"status": "stopped"})
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()

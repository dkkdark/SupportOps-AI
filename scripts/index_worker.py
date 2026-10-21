"""Resume durable indexing jobs. Run as a separate process in production."""

import argparse
import signal
import time
from datetime import timedelta
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import IndexJob, make_database, utcnow
from supportops.semantic import index_source, profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = Settings()
    engine, sessions = make_database(config.database_url)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        with sessions() as db:
            jobs = db.scalars(
                select(IndexJob)
                .where(
                    IndexJob.profile == profile(config),
                    IndexJob.attempts < 3,
                    (IndexJob.status.in_(["pending", "failed"]))
                    | (
                        (IndexJob.status == "indexing")
                        & (IndexJob.updated_at < utcnow() - timedelta(minutes=3))
                    ),
                )
                .limit(20)
            ).all()
        for job in jobs:
            if stopping:
                break
            index_source(config, sessions, job.organization_id, job.source_id)
        if args.once:
            break
        time.sleep(5)
    engine.dispose()


if __name__ == "__main__":
    main()

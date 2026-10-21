"""Container probe: read-only database heartbeat check, without external calls."""

from supportops.config import Settings
from supportops.db import make_database
from supportops.operations import worker_status


def main():
    engine, sessions = make_database(Settings().database_url)
    try:
        with sessions() as db:
            if not worker_status(db)["healthy"]:
                raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

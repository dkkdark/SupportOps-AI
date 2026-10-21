"""Initialize application and checkpoint schemas using migration credentials."""

from supportops.config import Settings
from supportops.db import make_database
from supportops.migration import migrate, verify_schema


def main():
    config = Settings()
    engine, _ = make_database(config.database_url)
    try:
        migrate(engine)
        verify_schema(engine)
        print("Application and checkpoint migrations completed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect


def migrate(engine):
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = engine.url.render_as_string(hide_password=False)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        names = inspect(connection).get_table_names()
        if "tickets" in names and "alembic_version" not in names:
            if any(
                c["name"] == "organization_id"
                for c in inspect(connection).get_columns("tickets")
            ):
                raise RuntimeError(
                    "Unversioned tenant schema; refusing to guess migration history"
                )
            command.stamp(cfg, "001")
        command.upgrade(cfg, "head")

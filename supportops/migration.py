from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
import sqlite3


def head_revision():
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def checkpoint_setup(engine):
    if engine.dialect.name == "postgresql":
        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(
            engine.url.render_as_string(hide_password=False).replace(
                "postgresql+psycopg://", "postgresql://"
            )
        ) as saver:
            saver.setup()
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver

        with SqliteSaver.from_conn_string(
            str(engine.url.database) + ".checkpoints"
        ) as saver:
            saver.setup()


def verify_schema(engine):
    with engine.connect() as conn:
        if MigrationContext.configure(conn).get_current_revision() != head_revision():
            raise RuntimeError("Database migrations are required before startup")
        if engine.dialect.name == "postgresql":
            names = set(inspect(conn).get_table_names())
            if not {
                "checkpoints",
                "checkpoint_blobs",
                "checkpoint_writes",
                "checkpoint_migrations",
            }.issubset(names):
                raise RuntimeError(
                    "Checkpoint schema must be initialized by the migration command"
                )
        else:
            file = Path(str(engine.url.database) + ".checkpoints").resolve()
            with sqlite3.connect(file.as_uri() + "?mode=ro", uri=True) as cp:
                names = {
                    row[0]
                    for row in cp.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            if not {"checkpoints", "writes"}.issubset(names):
                raise RuntimeError(
                    "Checkpoint schema must be initialized by the migration command"
                )


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
    checkpoint_setup(engine)

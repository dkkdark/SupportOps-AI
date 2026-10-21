"""Schema migration preserves existing single-business rows without exposing them."""

from alembic.config import Config
from alembic import command
from sqlalchemy import text
from supportops.db import make_database
from supportops.migration import migrate


def test_legacy_rows_are_archived_and_migration_is_repeatable(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path}/legacy.db")
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "001")
        conn.execute(
            text(
                "INSERT INTO tickets (id,customer,email,subject,message,channel,priority,status,category,revision,created_at,updated_at) VALUES ('SO-OLD','Emma Wilson','emma@example.test','Old ticket','An existing support request','web','normal','new','other',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO reviews (id,ticket_id,decision,text,note,actor,created_at) VALUES ('old-review','SO-OLD','escalate','','Existing review','Alex Morgan',CURRENT_TIMESTAMP)"
            )
        )
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT organization_id FROM tickets WHERE id='SO-OLD'")
            ).scalar()
            == "legacy-demo"
        )
        assert conn.execute(text("SELECT count(*) FROM reviews")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM memberships")).scalar() == 0
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "003"
        )
    engine.dispose()

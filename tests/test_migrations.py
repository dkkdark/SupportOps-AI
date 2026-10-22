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
            == "006"
        )
    engine.dispose()


def test_demo_migration_preserves_prior_model_cost_and_marks_exhausted_owner(tmp_path):
    engine, _ = make_database(f"sqlite:///{tmp_path}/prior-billing.db")
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "003")
        conn.execute(
            text(
                "INSERT INTO users (id,email,name,password_hash,email_verified,created_at) VALUES ('owner','alex@example.test','Alex Morgan','synthetic',1,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO organizations (id,name,slug,industry,workflow_mode,is_demo,created_at) VALUES ('company','Company','company','General','knowledge',0,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO memberships (organization_id,user_id,role,active,created_at) VALUES ('company','owner','owner',1,CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO credit_reservations (id,organization_id,period,reserved,charged,settled,usage,created_at) VALUES ('old','company','trial',1200,1200,1,:usage,CURRENT_TIMESTAMP)"
            ),
            {"usage": '{"cost_usd": 5.25}'},
        )
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM demo_accounts WHERE user_id='owner'"))
            .mappings()
            .one()
        )
        assert row["spent_microusd"] == 5_250_000
        assert row["limit_reached_at"] and row["launch_email_requested_at"]
        assert row["notification_reason"] == "demo_budget_exhausted"
        assert row["launch_email_sent_at"] is None
        assert (
            conn.execute(text("SELECT count(*) FROM credit_reservations")).scalar() == 1
        )
        assert not conn.execute(text("PRAGMA foreign_key_check")).all()
    engine.dispose()

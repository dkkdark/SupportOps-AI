"""Read-only backend launch report. No external API, SMTP or payment calls."""

import argparse
import json
from sqlalchemy import inspect, text
from supportops.config import Settings
from supportops.db import make_database
from supportops.mailer import email_status
from supportops.operations import worker_status
from supportops.launch import legal_ready
from supportops.migration import head_revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production", action="store_true", help="Require public-launch configuration"
    )
    args = parser.parse_args()
    config = Settings()
    engine, sessions = make_database(config.database_url)
    checks = {}
    try:
        with sessions() as db:
            try:
                db.execute(text("SELECT 1"))
                checks["database"] = True
                checks["schema"] = (
                    db.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    == head_revision()
                )
                checks["worker"] = (
                    worker_status(db)["healthy"]
                    if "worker_heartbeats" in inspect(engine).get_table_names()
                    else False
                )
            except Exception:
                checks["database"], checks["schema"], checks["worker"] = (
                    False,
                    False,
                    False,
                )
        mail = email_status(config)
        checks.update(
            openai=bool(config.model_provider == "openai" and (config.openai_api_key or config.llm_api_key)),
            real_embeddings=config.embedding_provider == "openai",
            email=mail["ready"],
            demo_budget=bool(config.demo_release and config.demo_ai_budget_usd == 5),
        )
        if args.production:
            checks.update(
                production_mode=config.app_env == "production",
                https=config.public_base_url.startswith("https://"),
                postgres=config.database_url.startswith("postgresql"),
                captcha=bool(config.turnstile_site_key and config.turnstile_secret_key),
                reviewed_policies=legal_ready(config),
            )
        print(
            json.dumps(
                {
                    "checks": checks,
                    "email_missing": mail["missing"],
                    "note": "Configuration/database checks only. External credentials and delivery are not verified.",
                },
                indent=2,
            )
        )
        if not all(checks.values()):
            raise SystemExit(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

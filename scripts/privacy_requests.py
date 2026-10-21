"""Export pending privacy requests for operator review; never erases records or sends email."""

import argparse
import json
from pathlib import Path
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import PrivacyRequest, User, make_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="New private local JSON file")
    args = parser.parse_args()
    engine, sessions = make_database(Settings().database_url)
    try:
        with sessions() as db:
            rows = db.execute(
                select(PrivacyRequest, User)
                .join(User, User.id == PrivacyRequest.user_id)
                .where(PrivacyRequest.status == "pending")
                .order_by(PrivacyRequest.created_at)
                .limit(1000)
            )
            result = [
                {
                    "id": r.id,
                    "user_id": u.id,
                    "email": u.email,
                    "kind": r.kind,
                    "created_at": r.created_at.isoformat(),
                }
                for r, u in rows
            ]
        output = Path(args.output)
        with output.open("x") as file:
            output.chmod(0o600)
            json.dump(result, file, indent=2)
        print(
            "Privacy review file created. No records were erased and no email was sent."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

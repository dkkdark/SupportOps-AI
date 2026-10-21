"""Export requested launch notifications for the operator. Never sends email."""

import argparse
import csv
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import DemoAccount, User, make_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, help="New local CSV path; refuses to overwrite"
    )
    args = parser.parse_args()
    engine, sessions = make_database(Settings().database_url)
    try:
        with sessions() as db, open(args.output, "x", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                ["user_id", "email", "name", "reason", "requested_at", "spent_usd"]
            )
            rows = db.execute(
                select(User, DemoAccount)
                .join(DemoAccount, DemoAccount.user_id == User.id)
                .where(
                    User.email_verified.is_(True),
                    DemoAccount.launch_email_requested_at.is_not(None),
                    DemoAccount.launch_email_sent_at.is_(None),
                    DemoAccount.launch_email_opt_out.is_(False),
                )
            )
            for user, demo in rows:
                # Avoid spreadsheet formula injection in operator CSV exports.
                safe = lambda s: "'" + s if s and s[0] in "=+-@\t\r" else s
                writer.writerow(
                    [
                        user.id,
                        safe(user.email),
                        safe(user.name),
                        demo.notification_reason,
                        demo.launch_email_requested_at.isoformat(),
                        demo.spent_microusd / 1_000_000,
                    ]
                )
        print("Launch notification CSV exported. No email was sent.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

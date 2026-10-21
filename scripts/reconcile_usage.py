"""Review uncertain AI charges; mutations require explicit --apply and provider evidence."""

import argparse
import json
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import CreditReservation, make_database
from supportops.reconciliation import reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reservation")
    parser.add_argument("--cost-usd", type=str)
    parser.add_argument("--reason")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = Settings()
    engine, sessions = make_database(config.database_url)
    try:
        with sessions() as db:
            if not args.reservation:
                if args.apply:
                    parser.error("--apply requires a reservation")
                rows = db.scalars(
                    select(CreditReservation)
                    .order_by(CreditReservation.created_at)
                    .limit(10000)
                )
                result = [
                    {
                        "id": r.id,
                        "organization_id": r.organization_id,
                        "created_at": r.created_at.isoformat(),
                        "settled": r.settled,
                        "reserved_usd": r.demo_reserved_microusd / 1_000_000
                        if r.demo_reserved_microusd is not None
                        else r.reserved * 0.005,
                    }
                    for r in rows
                    if not r.settled or (r.usage or {}).get("reconciliation_required")
                ]
            else:
                if args.cost_usd is None or not args.reason:
                    parser.error("--reservation requires --cost-usd and --reason")
                result = reconcile(
                    db, config, args.reservation, args.cost_usd, args.reason, args.apply
                )
                if args.apply:
                    db.commit()
            print(json.dumps(result, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

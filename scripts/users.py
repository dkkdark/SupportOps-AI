"""Read-only operator account roster. Never exposes credentials or sends messages."""

import argparse
import json
from sqlalchemy import func, select
from supportops.config import Settings
from supportops.db import User, Membership, Organization, make_database
from supportops.migration import verify_schema


def roster(sessions, limit):
    with sessions() as db:
        workspace_count = db.scalar(select(func.count()).select_from(Organization))
        users = [
            {
                "id": row.id,
                "name": row.name,
                "email": row.email,
                "email_verified": row.email_verified,
                "created_at": row.created_at.isoformat(),
                "workspaces": [],
            }
            for row in db.execute(
                select(
                    User.id, User.name, User.email, User.email_verified, User.created_at
                ).order_by(User.created_at, User.id)
            )
        ]
        by_id = {u["id"]: u for u in users}
        for uid, name, role, active in db.execute(
            select(
                Membership.user_id,
                Organization.name,
                Membership.role,
                Membership.active,
            )
            .join(Organization, Organization.id == Membership.organization_id)
            .order_by(Organization.name)
        ):
            by_id[uid]["workspaces"].append(
                {"name": name, "role": role, "active": active}
            )
    return {
        "total": len(users),
        "workspace_count": workspace_count,
        "workspace_limit": limit,
        "registration_available": workspace_count < limit,
        "users": users,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Print a structured account roster"
    )
    args = parser.parse_args()
    # Container settings come from Compose; never load a local dotenv file.
    config = Settings(_env_file=None)
    engine, sessions = make_database(config.database_url)
    try:
        verify_schema(engine)
        result = roster(sessions, config.workspace_limit)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            safe = lambda value: "".join(
                c if c.isprintable() else " " for c in str(value)
            )
            print(f"Registered accounts: {result['total']}")
            print(
                f"Workspaces: {result['workspace_count']} / {result['workspace_limit']}"
            )
            print(
                "New workspaces: "
                + ("open" if result["registration_available"] else "closed")
            )
            print("NAME\tEMAIL\tVERIFIED\tJOINED (UTC)\tWORKSPACES")
            for user in result["users"]:
                businesses = ", ".join(
                    w["name"] + " (" + w["role"] + ")"
                    for w in user["workspaces"]
                    if w["active"]
                )
                print(
                    "\t".join(
                        safe(v)
                        for v in (
                            user["name"],
                            user["email"],
                            "yes" if user["email_verified"] else "no",
                            user["created_at"],
                            businesses,
                        )
                    )
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

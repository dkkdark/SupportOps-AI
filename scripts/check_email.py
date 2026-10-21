"""Inspect SMTP configuration; optional TLS/auth check never sends a message."""

import argparse
import json
from supportops.config import Settings
from supportops.mailer import email_status, check_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Check SMTP TLS/authentication; no email sent",
    )
    args = parser.parse_args()
    config = Settings()
    result = email_status(config)
    if args.connect:
        result["connection"] = check_connection(config)
    print(json.dumps(result, indent=2))
    if not result["ready"] or (args.connect and not result["connection"]["ok"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

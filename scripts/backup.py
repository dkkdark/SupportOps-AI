"""Create a stopped-service SQLite backup or verify/restore it into an isolated folder."""

import argparse
import json
from supportops.config import Settings
from supportops.backups import create, verify, restore_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("create")
    capture.add_argument("--output", required=True)
    capture.add_argument("--confirm-stopped", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("--path", required=True)
    restore = sub.add_parser("restore-check")
    restore.add_argument("--path", required=True)
    restore.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "create":
        result = create(Settings().database_url, args.output, args.confirm_stopped)
    elif args.command == "verify":
        result = verify(args.path)
    else:
        result = restore_check(args.path, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

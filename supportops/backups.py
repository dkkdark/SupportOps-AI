"""Offline SQLite backups and isolated restore drills, with manifests and checksums."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from sqlalchemy.engine import make_url


def checksum(path):
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256")
    return digest.hexdigest()


def validate_database(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign-key check failed")


def create(database_url, output, confirm_stopped=False):
    if not confirm_stopped:
        raise ValueError(
            "Stop API and workers first, then pass --confirm-stopped for a consistent app/checkpoint pair"
        )
    url = make_url(database_url)
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise ValueError(
            "This local helper supports file-based SQLite only; use managed PostgreSQL backups for deployment"
        )
    source = Path(url.database).resolve()
    if not source.is_file():
        raise ValueError("Application database does not exist")
    destination = Path(output).resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    files = []
    for original, name in (
        (source, "application.db"),
        (Path(str(source) + ".checkpoints"), "application.db.checkpoints"),
    ):
        if not original.exists():
            if name.endswith(".checkpoints"):
                raise ValueError(
                    "Checkpoint database is missing; refusing an incomplete backup"
                )
            raise ValueError("Database is missing")
        target = destination / name
        with (
            sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as src,
            sqlite3.connect(target) as dst,
        ):
            src.backup(dst)
        target.chmod(0o600)
        validate_database(target)
        files.append({"name": name, "sha256": checksum(target)})
    with sqlite3.connect(destination / "application.db") as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_revision": revision,
        "files": files,
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    return manifest


def verify(path):
    directory = Path(path).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    expected = {"application.db", "application.db.checkpoints"}
    if (
        manifest.get("format") != 1
        or {item["name"] for item in manifest["files"]} != expected
        or len(manifest["files"]) != 2
    ):
        raise ValueError("Unsupported or incomplete backup manifest")
    for item in manifest["files"]:
        file = directory / item["name"]
        if file.is_symlink() or checksum(file) != item["sha256"]:
            raise ValueError("Backup checksum mismatch")
        validate_database(file)
    return manifest


def restore_check(path, output):
    manifest = verify(path)
    target = Path(output).resolve()
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    for item in manifest["files"]:
        destination = target / item["name"]
        shutil.copyfile(Path(path) / item["name"], destination)
        destination.chmod(0o600)
        validate_database(destination)
    with sqlite3.connect(target / "application.db") as conn:
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if revision != manifest["schema_revision"]:
            raise ValueError("Restored schema differs from manifest")
    return {
        "verified": True,
        "schema_revision": revision,
        "restored_to": str(target),
        "note": "Isolated copy only; live databases were not replaced.",
    }

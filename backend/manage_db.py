"""Small migration runner for the application's PostgreSQL schema."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parent
load_dotenv(BACKEND / ".env", override=False)


def database_urls() -> list[tuple[str, str]]:
    migration_url = os.getenv("MIGRATION_DATABASE_URL", "").strip()
    runtime_url = os.getenv("DATABASE_URL", "").strip()
    values: list[tuple[str, str]] = []
    if migration_url:
        values.append(("MIGRATION_DATABASE_URL", migration_url))
    if runtime_url and runtime_url != migration_url:
        values.append(("DATABASE_URL", runtime_url))
    if not values:
        raise SystemExit(
            "MIGRATION_DATABASE_URL or DATABASE_URL is not set. "
            "Add one to backend/.env or export it first."
        )
    return values


def connect():
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("PostgreSQL dependencies are missing. Run `make install`.") from exc
    last_error = None
    urls = database_urls()
    for index, (name, value) in enumerate(urls):
        try:
            # Also works with Supabase's transaction pooler, which doesn't
            # support prepared statements.
            return psycopg.connect(value, prepare_threshold=None)
        except psycopg.OperationalError as exc:
            last_error = exc
            if index + 1 < len(urls):
                print(
                    f"{name} is unreachable; trying the configured database pooler.",
                    file=sys.stderr,
                )
    assert last_error is not None
    raise last_error


def migrate() -> None:
    migration_dir = BACKEND / "migrations"
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT migration_name FROM schema_migrations"
            ).fetchall()
        }
        for path in sorted(migration_dir.glob("*.sql")):
            if path.name in applied:
                continue
            with connection.transaction():
                connection.execute(path.read_text())
                connection.execute(
                    "INSERT INTO schema_migrations (migration_name) VALUES (%s)",
                    (path.name,),
                )
            print(f"applied {path.name}")


def status() -> None:
    with connect() as connection:
        tables = connection.execute(
            """
            SELECT to_regclass('public.conversations'),
                   to_regclass('public.bookings'),
                   to_regclass('public.evaluation_runs')
            """
        ).fetchone()
        if all(tables):
            print("database is connected and migrated")
        else:
            print("database is connected but migrations are missing")
            raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("migrate", "status"))
    args = parser.parse_args()
    migrate() if args.command == "migrate" else status()

#!/usr/bin/env python3
"""Simple DB migration helper: add columns to repost_rules if missing.

This script supports SQLite and Postgres (basic). It detects the database URL from
config.get_settings().database_url and runs ALTER TABLE statements as needed.

Run this before starting the bot in production when upgrading existing DBs.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from config import get_settings


def _sync_url(async_url: str) -> str:
    # Convert common async URLs to sync equivalents for use with SQLAlchemy's sync engine
    if async_url.startswith("sqlite+aiosqlite://"):
        return async_url.replace("sqlite+aiosqlite://", "sqlite://")
    if async_url.startswith("postgresql+asyncpg://"):
        return async_url.replace("postgresql+asyncpg://", "postgresql://")
    if async_url.startswith("mysql+asyncmy://"):
        return async_url.replace("mysql+asyncmy://", "mysql://")
    # otherwise assume it's already sync
    return async_url


def column_exists_sqlite(engine, table: str, column: str) -> bool:
    q = f"PRAGMA table_info('{table}')"
    with engine.connect() as conn:
        res = conn.execute(text(q)).fetchall()
        for row in res:
            if row[1] == column:
                return True
    return False


def column_exists_postgres(engine, table: str, column: str) -> bool:
    q = text("SELECT column_name FROM information_schema.columns WHERE table_name = :t AND column_name = :c")
    with engine.connect() as conn:
        res = conn.execute(q, {"t": table, "c": column}).fetchone()
        return res is not None


def main():
    settings = get_settings()
    db_url = settings.database_url
    sync = _sync_url(db_url)
    engine = create_engine(sync)
    dialect = engine.dialect.name
    print("DB sync URL:", sync)
    print("Dialect:", dialect)

    table = "repost_rules"
    # columns to ensure
    cols = [
        ("caption_template", "TEXT"),
        ("replacements_json", "TEXT"),
        ("auto_delete_seconds", "INTEGER"),
    ]

    for col, coltype in cols:
        exists = False
        if dialect == "sqlite":
            exists = column_exists_sqlite(engine, table, col)
        else:
            exists = column_exists_postgres(engine, table, col)
        if exists:
            print(f"Column {col} already exists on {table}")
            continue
        # add column
        alter = f"ALTER TABLE {table} ADD COLUMN {col} {coltype};"
        print("Adding column:", alter)
        try:
            with engine.connect() as conn:
                conn.execute(text(alter))
                conn.commit()
            print(f"Added column {col}")
        except Exception as e:
            print(f"Failed to add column {col}: {e}")


if __name__ == "__main__":
    main()

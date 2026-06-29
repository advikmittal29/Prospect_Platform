from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import DatabaseConfig as RootDatabaseConfig  # noqa: E402
from db import get_engine, init_db, session_scope  # noqa: E402

from .security import hash_password  # noqa: F401 — re-exported for callers


def init_backend_db() -> None:
    """
    Connect to the database and verify the schema.

    CONTRACT:
      - This function NEVER creates tables, alters schema, or inserts seed data.
      - It raises RuntimeError immediately if the schema is missing or incomplete.
      - All one-time setup (tables + admin user + seed data) is performed
        exclusively by running:  python init_db.py

    Raises:
        RuntimeError: If required tables are missing or the database is unreachable.
    """
    init_db(RootDatabaseConfig())

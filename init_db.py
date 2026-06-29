#!/usr/bin/env python3
"""
init_db.py — One-time database initialisation script.

Usage:
    python init_db.py

This script must be run ONCE before starting the application for the first time.
The application itself will never create tables or insert seed data.

Behaviour:
  - If the target database does not exist  → create it, then build schema + seed.
  - If the target database already exists  → prompt the user:
        "DB already exists. Drop and recreate? (yes/no)"
      yes → drop the database completely, recreate, build schema + seed.
      no  → exit without any changes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Allow running from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from config import DatabaseConfig, get_non_secret_setting_seed_rows
from db.models import (
    Base,
    AppSettingsORM,
    AgentDefinitionORM,
    AgentProfileORM,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("init_db")

# ---------------------------------------------------------------------------
# Seed constants (identical to what was previously in db/models.py)
# ---------------------------------------------------------------------------

_DEFAULT_STAFFING_PROMPT_PROFILE = {
    "persona_title": "Senior B2B Sales & Pre-Sales Consultant",
    "domain_focus": "Staffing and talent acquisition solutions",
    "service_offering": "Specialized hiring delivery, talent mapping, and recruitment process support",
    "sales_objective": "Secure qualified discovery conversations and progress opportunities to proposal stage",
    "value_outcomes": "faster time-to-hire, stronger shortlist quality, reduced hiring risk, and scalable team growth",
}

_DEFAULT_STAFFING_TARGET_BUYER_ROLES = [
    "Heads of Talent Acquisition",
    "HR leaders",
    "engineering leaders",
    "business-unit decision-makers",
]

_DEFAULT_STAFFING_VALUE_OUTCOMES = [
    "faster time-to-hire",
    "stronger shortlist quality",
    "reduced hiring risk",
    "scalable team growth",
]

_DEFAULT_STAFFING_PIPELINE_POLICY = {
    "ingest_enabled": True,
    "research_enabled": True,
    "candidate_hunt_enabled": True,
    "intelligence_enabled": True,
    "outreach_enabled": True,
}

_DEFAULT_STAFFING_RUNTIME_POLICY = {
    "mode": "deterministic",
    "max_tool_calls_per_run": 50,
}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _parse_db_url(url: str):
    """Return (server_url_without_target_db, db_name)."""

    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/").split("?")[0]

    if parsed.scheme.startswith("mssql"):
        server_db = "master"
    elif parsed.scheme.startswith("mysql"):
        server_db = "mysql"      # built-in MySQL system database
    else:
        server_db = ""

    server_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        f"/{server_db}" if server_db else "",
        "",
        parsed.query,
        "",
    ))

    return server_url, db_name

def _strip_query(url: str) -> str:
    """Return the URL without query string (needed for pymysql server connect)."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _server_engine(db_cfg: DatabaseConfig):
    server_url, _ = _parse_db_url(db_cfg.sqlalchemy_url)

    if db_cfg.sqlalchemy_url.startswith("mysql"):
        server_url = server_url.replace("/master", "/mysql")

    return create_engine(server_url, future=True, pool_pre_ping=True)


def _db_engine(db_cfg: DatabaseConfig):
    """Engine connected to the target database (must already exist)."""
    return create_engine(
        db_cfg.sqlalchemy_url,
        echo=db_cfg.echo_sql,
        pool_size=db_cfg.pool_size,
        pool_recycle=db_cfg.pool_recycle_seconds,
        pool_pre_ping=True,
        future=True,
    )


# ---------------------------------------------------------------------------
# DB existence check
# ---------------------------------------------------------------------------

def _is_mssql(db_cfg: DatabaseConfig) -> bool:
    return db_cfg.sqlalchemy_url.startswith("mssql")


def _db_exists(db_cfg: DatabaseConfig) -> bool:
    _, db_name = _parse_db_url(db_cfg.sqlalchemy_url)
    engine = _server_engine(db_cfg)
    try:
        with engine.connect() as conn:
            if _is_mssql(db_cfg):
                result = conn.execute(
                    text("SELECT name FROM sys.databases WHERE name = :n"),
                    {"n": db_name},
                ).fetchone()
            else:
                result = conn.execute(
                    text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :n"),
                    {"n": db_name},
                ).fetchone()
        return result is not None
    except OperationalError as exc:
        logger.error("Cannot connect to database server: %s", exc)
        sys.exit(1)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# DDL operations
# ---------------------------------------------------------------------------

def _mssql_ddl_conn(engine):
    """Return a connection with autocommit=True via raw pyodbc — required for
    CREATE/DROP DATABASE which SQL Server forbids inside any transaction."""
    raw = engine.raw_connection()
    raw.autocommit = True
    return raw


def _create_database(db_cfg: DatabaseConfig) -> None:
    _, db_name = _parse_db_url(db_cfg.sqlalchemy_url)
    engine = _server_engine(db_cfg)
    try:
        if _is_mssql(db_cfg):
            raw = _mssql_ddl_conn(engine)
            try:
                raw.cursor().execute(f"CREATE DATABASE [{db_name}]")
            finally:
                raw.close()
        else:
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                ))
        logger.info("Database '%s' created.", db_name)
    finally:
        engine.dispose()


def _drop_database(db_cfg: DatabaseConfig) -> None:
    _, db_name = _parse_db_url(db_cfg.sqlalchemy_url)
    engine = _server_engine(db_cfg)
    try:
        if _is_mssql(db_cfg):
            raw = _mssql_ddl_conn(engine)
            try:
                raw.cursor().execute(
                    f"IF EXISTS (SELECT 1 FROM sys.databases WHERE name = N'{db_name}') "
                    f"DROP DATABASE [{db_name}]"
                )
            finally:
                raw.close()
        else:
            with engine.begin() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
        logger.info("Database '%s' dropped.", db_name)
    finally:
        engine.dispose()


def _create_orm_tables(engine) -> None:
    """Create all ORM-mapped tables via SQLAlchemy metadata."""
    logger.info("Creating ORM tables...")
    Base.metadata.create_all(engine)
    logger.info("ORM tables created.")


def _create_ui_tables_mysql(engine) -> None:
    """Create UI-specific tables (ui_users, pipeline_runs) not managed by the ORM."""
    logger.info("Creating UI tables...")
    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS ui_users (
                id            INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                username      VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(512) NOT NULL,
                role          VARCHAR(30) NOT NULL DEFAULT 'admin',
                active        TINYINT(1) NOT NULL DEFAULT 1,
                created_at_utc DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
                last_login_utc DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id             INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                pipeline       VARCHAR(50) NOT NULL,
                agent_id       INT NULL,
                status         VARCHAR(20) NOT NULL,
                started_at_utc DATETIME NOT NULL,
                ended_at_utc   DATETIME NULL,
                triggered_by   VARCHAR(100) NULL,
                message        LONGTEXT NULL,
                log_text       LONGTEXT NULL,
                INDEX ix_pipeline_runs_started_at (started_at_utc DESC),
                INDEX ix_pipeline_runs_agent_id (agent_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        ))
    logger.info("UI tables created.")


def _create_ui_tables_mssql(engine) -> None:
    """Create UI-specific tables for SQL Server."""
    logger.info("Creating UI tables (mssql)...")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            IF OBJECT_ID('dbo.ui_users', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.ui_users (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    username NVARCHAR(100) NOT NULL UNIQUE,
                    password_hash NVARCHAR(512) NOT NULL,
                    role NVARCHAR(30) NOT NULL DEFAULT 'admin',
                    active BIT NOT NULL DEFAULT 1,
                    created_at_utc DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
                    last_login_utc DATETIME2 NULL
                );
            END

            IF OBJECT_ID('dbo.pipeline_runs', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.pipeline_runs (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    pipeline NVARCHAR(50) NOT NULL,
                    agent_id INT NULL,
                    status NVARCHAR(20) NOT NULL,
                    started_at_utc DATETIME2 NOT NULL,
                    ended_at_utc DATETIME2 NULL,
                    triggered_by NVARCHAR(100) NULL,
                    message NVARCHAR(MAX) NULL,
                    log_text NVARCHAR(MAX) NULL
                );
                CREATE INDEX ix_pipeline_runs_started_at ON dbo.pipeline_runs(started_at_utc DESC);
                CREATE INDEX ix_pipeline_runs_agent_id ON dbo.pipeline_runs(agent_id);
            END
            """
        )
    logger.info("UI tables (mssql) created.")


def _create_ui_tables(engine) -> None:
    if engine.dialect.name == "mssql":
        _create_ui_tables_mssql(engine)
    else:
        _create_ui_tables_mysql(engine)


def _create_mssql_schema_extras(engine) -> None:
    """
    SQL Server-specific DDL that cannot be expressed via SQLAlchemy ORM:
    agent-scoped unique constraints and filtered indexes.
    """
    if engine.dialect.name != "mssql":
        return
    logger.info("Applying SQL Server schema extras...")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            IF NOT EXISTS (
                SELECT 1 FROM sys.key_constraints
                WHERE [name] = 'uq_naukri_jobs_agent_canonical_url' AND [type] = 'UQ'
            )
                ALTER TABLE dbo.naukri_jobs
                ADD CONSTRAINT uq_naukri_jobs_agent_canonical_url UNIQUE(agent_id, canonical_job_url);

            IF NOT EXISTS (
                SELECT 1 FROM sys.key_constraints
                WHERE [name] = 'uq_prospect_profile_company_agent' AND [type] = 'UQ'
            )
                ALTER TABLE dbo.prospects
                ADD CONSTRAINT uq_prospect_profile_company_agent
                UNIQUE(linkedin_profile_url, company_research_id, agent_id);

            IF NOT EXISTS (
                SELECT 1 FROM sys.key_constraints
                WHERE [name] = 'uq_candidate_profile_agent_job' AND [type] = 'UQ'
            )
                ALTER TABLE dbo.candidate_profiles
                ADD CONSTRAINT uq_candidate_profile_agent_job
                UNIQUE(agent_id, job_id, linkedin_profile_url);

            IF NOT EXISTS (
                SELECT 1 FROM sys.indexes
                WHERE [name] = 'ux_company_research_agent_linkedin_url_nonnull'
                  AND [object_id] = OBJECT_ID('dbo.company_research')
            )
                CREATE UNIQUE INDEX ux_company_research_agent_linkedin_url_nonnull
                ON dbo.company_research(agent_id, linkedin_url)
                WHERE linkedin_url IS NOT NULL AND agent_id IS NOT NULL;
            """
        )
    logger.info("SQL Server schema extras applied.")


# ---------------------------------------------------------------------------
# Seed operations
# ---------------------------------------------------------------------------

def _seed_app_settings(engine) -> None:
    """Insert all non-secret default settings. Skips rows that already exist."""
    logger.info("Seeding app_settings...")
    seed_rows = get_non_secret_setting_seed_rows()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if engine.dialect.name == "mssql":
        with engine.begin() as conn:
            for row in seed_rows:
                conn.exec_driver_sql(
                    """
                    IF NOT EXISTS (SELECT 1 FROM dbo.app_settings WHERE setting_key = ?)
                        INSERT INTO dbo.app_settings
                            (setting_key, setting_value, description, config_type, updated_at_utc, updated_by)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["key"], row["key"], row["value_json"],
                        row["description"], row.get("config_type"), now_utc, "system-seed",
                    ),
                )
    else:
        from sqlalchemy.orm import Session
        with Session(engine) as session:
            for row in seed_rows:
                existing = session.get(AppSettingsORM, row["key"])
                if existing is None:
                    session.add(AppSettingsORM(
                        setting_key=row["key"],
                        setting_value=row["value_json"],
                        description=row["description"],
                        config_type=row.get("config_type"),
                        updated_at_utc=now_utc,
                        updated_by="system-seed",
                    ))
            session.commit()
    logger.info("app_settings seeded (%d rows).", len(seed_rows))


def _seed_default_agent(engine) -> None:
    """Insert the default staffing agent definition and its initial profile."""
    default_agent_key = (
        os.getenv("AGENT_DEFAULT_KEY", "staffing-agent") or "staffing-agent"
    ).strip()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    logger.info("Seeding default agent '%s'...", default_agent_key)
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        existing = session.query(AgentDefinitionORM).filter_by(
            agent_key=default_agent_key
        ).one_or_none()

        if existing is not None:
            logger.info(
                "Default agent '%s' already exists (id=%s). Skipping agent seed.",
                default_agent_key, existing.id,
            )
            return

        agent_row = AgentDefinitionORM(
            agent_key=default_agent_key,
            name="Staffing Agent",
            description="Default staffing agent seeded at initialisation.",
            status="active",
            agent_type="staffing",
            created_at_utc=now_utc,
            updated_at_utc=now_utc,
        )
        session.add(agent_row)
        session.flush()

        session.add(AgentProfileORM(
            agent_id=agent_row.id,
            persona_title=_DEFAULT_STAFFING_PROMPT_PROFILE["persona_title"],
            domain_focus=_DEFAULT_STAFFING_PROMPT_PROFILE["domain_focus"],
            service_offering=_DEFAULT_STAFFING_PROMPT_PROFILE["service_offering"],
            sales_objective=_DEFAULT_STAFFING_PROMPT_PROFILE["sales_objective"],
            target_buyer_roles_json=json.dumps(
                _DEFAULT_STAFFING_TARGET_BUYER_ROLES, ensure_ascii=False
            ),
            value_outcomes_json=json.dumps(
                _DEFAULT_STAFFING_VALUE_OUTCOMES, ensure_ascii=False
            ),
            prompt_profile_json=json.dumps(
                _DEFAULT_STAFFING_PROMPT_PROFILE, ensure_ascii=False
            ),
            pipeline_policy_json=json.dumps(
                _DEFAULT_STAFFING_PIPELINE_POLICY, ensure_ascii=False
            ),
            runtime_policy_json=json.dumps(
                _DEFAULT_STAFFING_RUNTIME_POLICY, ensure_ascii=False
            ),
            version=1,
            is_current=True,
            created_at_utc=now_utc,
            created_by="system-seed",
        ))
        session.commit()

    logger.info("Default agent '%s' seeded.", default_agent_key)


def _seed_default_admin(engine) -> None:
    """Insert the default UI admin user."""
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "app-ui"))
    from backend.app.security import hash_password  # type: ignore # noqa: F401 — lazy import

    username = os.getenv("APP_UI_ADMIN_USER", "admin")
    password = os.getenv("APP_UI_ADMIN_PASS", "admin123")

    # Import hash_password from the backend security module without depending
    # on the full FastAPI app stack.
    security_path = Path(__file__).resolve().parent / "app-ui" / "backend" / "app" / "security.py"
    if not security_path.exists():
        # Fallback: inline the same PBKDF2 implementation
        import base64
        import hashlib

        def hash_password(pwd: str) -> str:  # type: ignore[misc]
            import os as _os
            salt = _os.urandom(16)
            digest = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, 200_000)
            return base64.b64encode(salt + digest).decode("ascii")
    else:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ui_security", security_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        hash_password = mod.hash_password

    hashed = hash_password(password)
    tbl = "dbo.ui_users" if engine.dialect.name == "mssql" else "ui_users"

    logger.info("Seeding default admin user '%s'...", username)
    with engine.begin() as conn:
        exists = conn.execute(
            text(f"SELECT id FROM {tbl} WHERE username = :u"), {"u": username}
        ).fetchone()
        if exists:
            logger.info("Admin user '%s' already exists. Skipping.", username)
            return
        conn.execute(
            text(
                f"INSERT INTO {tbl} (username, password_hash, role, active) "
                "VALUES (:u, :p, 'admin', 1)"
            ),
            {"u": username, "p": hashed},
        )
    logger.info("Admin user '%s' seeded.", username)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _build_schema_and_seed(db_cfg: DatabaseConfig) -> None:
    """Full schema creation + data seeding against an already-existing empty DB."""
    engine = _db_engine(db_cfg)
    try:
        _create_orm_tables(engine)
        _create_ui_tables(engine)
        _create_mssql_schema_extras(engine)
        _seed_app_settings(engine)
        _seed_default_agent(engine)
        _seed_default_admin(engine)
    finally:
        engine.dispose()


def _prompt_yes_no(question: str) -> bool:
    """Return True if the user types 'yes', False for 'no'. Loops until valid input."""
    while True:
        answer = input(f"{question} (yes/no): ").strip().lower()
        if answer in ("yes", "y"):
            return True
        if answer in ("no", "n"):
            return False
        print("Please type 'yes' or 'no'.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    db_cfg = DatabaseConfig()
    print("SQLAlchemy URL:", db_cfg.sqlalchemy_url)
    print("Dialect:", db_cfg.sqlalchemy_url.split(":")[0])
    _, db_name = _parse_db_url(db_cfg.sqlalchemy_url)

    logger.info("Target database: '%s'", db_name)

    if _db_exists(db_cfg):
        logger.info("Database '%s' already exists.", db_name)
        if not _prompt_yes_no(
            f"DB '{db_name}' already exists. Drop and recreate?"
        ):
            logger.info("Exiting without changes.")
            sys.exit(0)

        logger.info("Dropping database '%s'...", db_name)
        _drop_database(db_cfg)
    else:
        logger.info("Database '%s' does not exist. Creating...", db_name)

    _create_database(db_cfg)
    _build_schema_and_seed(db_cfg)

    logger.info("=" * 60)
    logger.info("Database initialisation complete.")
    logger.info("You can now start the application.")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()

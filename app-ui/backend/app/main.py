from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


from .database import ROOT_DIR, init_backend_db


if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from agents import AgentConfigResolver
from .security import create_token, decode_token, verify_password
from config import (  # noqa: E402
    get_config_type_for_key,
    get_non_secret_setting_catalog,
    is_secret_setting_key,
    reset_runtime_settings_cache,
)
from db import (  # noqa: E402
    AgentDefinitionORM,
    AgentKeywordORM,
    AgentProfileORM,
    AgentRunORM,
    resolve_agent_id,
    session_scope,
)

load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("prospect.ui.api")

JWT_SECRET = os.getenv("APP_UI_JWT_SECRET", "change-this-in-env")
JWT_EXPIRE_HOURS = int(os.getenv("APP_UI_JWT_EXPIRE_HOURS", "12"))
_raw_origins = os.getenv("APP_UI_ALLOWED_ORIGINS", "*").strip()


def _expand_local_aliases(origins: List[str]) -> List[str]:
    expanded: List[str] = []
    seen = set()
    for origin in origins:
        norm = origin.strip()
        if not norm:
            continue
        if norm not in seen:
            seen.add(norm)
            expanded.append(norm)

        try:
            p = urlparse(norm)
            scheme = p.scheme or "http"
            host = p.hostname or ""
            port = p.port
            if not port:
                continue

            alias_host = None
            if host == "127.0.0.1":
                alias_host = "localhost"
            elif host == "localhost":
                alias_host = "127.0.0.1"

            if alias_host:
                alias = f"{scheme}://{alias_host}:{port}"
                if alias not in seen:
                    seen.add(alias)
                    expanded.append(alias)
        except Exception:
            pass

    return expanded


if _raw_origins == "*":
    ALLOWED_ORIGINS = ["*"]
else:
    ALLOWED_ORIGINS = _expand_local_aliases(
        [o.strip() for o in _raw_origins.split(",") if o.strip()]
    )
ALLOW_CREDENTIALS = ALLOWED_ORIGINS != ["*"]
_SETTING_CATALOG = get_non_secret_setting_catalog()
_SETTING_CATALOG_BY_KEY = {
    str(item["key"]): item
    for item in _SETTING_CATALOG
}

app = FastAPI(title="Prospect Platform UI API", version="1.0.0")
security = HTTPBearer(auto_error=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class RunRequest(BaseModel):
    pipeline: Literal["ingest", "research", "intelligence", "candidate_hunt", "rag_ingest"]
    agent_id: Optional[int] = None
    agent_key: Optional[str] = None
    runtime_mode: Optional[Literal["deterministic", "autonomous"]] = None


class LinkedInSendRequest(BaseModel):
    profile_url: str
    message: str
    agent_id: Optional[int] = None
    agent_key: Optional[str] = None


class ProspectMessageUpdate(BaseModel):
    message: str


class LinkedInGenerateRequest(BaseModel):
    profile_url: str
    agent_id: Optional[int] = None
    agent_key: Optional[str] = None


class KeywordUpsert(BaseModel):
    keyword: str
    location: Optional[str] = None
    max_job_age_days: int = Field(default=7, ge=1, le=365)
    max_jobs: int = Field(default=50, ge=1, le=5000)
    active: bool = True


class CredentialUpsert(BaseModel):
    email: str
    password: str
    priority: int = 100
    active: bool = True


class SettingUpdate(BaseModel):
    key: str
    value: Any
    description: Optional[str] = None
    config_type: Optional[str] = None


class AgentCreateRequest(BaseModel):
    agent_key: str
    name: str
    description: Optional[str] = None
    agent_type: str = "custom"
    status: Literal["active", "paused", "archived"] = "active"


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_type: Optional[str] = None
    status: Optional[Literal["active", "paused", "archived"]] = None


class AgentProfileUpdate(BaseModel):
    persona_title: Optional[str] = None
    domain_focus: Optional[str] = None
    service_offering: Optional[str] = None
    target_buyer_roles: Optional[List[str]] = None
    sales_objective: Optional[str] = None
    value_outcomes: Optional[List[str]] = None
    icp_rules: Optional[Dict[str, Any]] = None
    targeting_policy: Optional[Dict[str, Any]] = None
    pipeline_policy: Optional[Dict[str, Any]] = None
    channel_policy: Optional[Dict[str, Any]] = None
    prompt_profile: Optional[Dict[str, Any]] = None
    runtime_policy: Optional[Dict[str, Any]] = None


class AgentKeywordUpsert(BaseModel):
    keyword_type: str
    keyword: str
    weight: float = 1.0
    active: bool = True


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    """
    Connect to the database and verify the schema exists.
    The application will refuse to start if init_db.py has not been run.
    """
    init_backend_db()
    logger.info("UI backend started. JWT_EXPIRE_HOURS=%d", JWT_EXPIRE_HOURS)
    # Verify the pipeline interpreter in the background: this machine has TWO
    # virtualenvs (.venv used for subprocesses, venv used manually), and a
    # package installed into only one produces cryptic downstream failures.
    # The check runs off the event loop; until it completes, subprocesses run
    # as before.
    asyncio.create_task(asyncio.to_thread(_run_pipeline_python_check))
    asyncio.create_task(_reply_scheduler_loop())
    logger.info("LinkedIn reply-check scheduler started (fires immediately, then on a config-driven interval).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_or_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _resolve_agent_scope(
    *,
    agent_id: Optional[int] = None,
    agent_key: Optional[str] = None,
) -> int:
    return resolve_agent_id(agent_id=agent_id, agent_key=agent_key)


_LINKEDIN_PROFILE_URL_RE = re.compile(
    r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[a-zA-Z0-9\-_%]+/?(\?.*)?$"
)


def _extract_test_error(log_text: str) -> Optional[str]:
    """Pull the 'error   : ...' line out of scheduler/p_outreach.py's TEST RESULT block."""
    match = re.search(r"error\s*:\s*(.+)", log_text or "")
    if not match:
        return None
    value = match.group(1).strip()
    return value if value and value != "—" else None


def _extract_test_action(log_text: str) -> Optional[str]:
    """
    Pull the executed action (message | connect | skip | unknown) out of
    scheduler/p_outreach.py's TEST RESULT block. The argument echo earlier in
    the log also prints an 'action : auto' line, so the LAST match wins —
    that's the result line, logged after the action ran.
    """
    matches = re.findall(r"action\s*:\s*([A-Za-z_]+)", log_text or "")
    return matches[-1].lower() if matches else None


_LINKEDIN_GENERATE_RESULT_MARKER = "===LINKEDIN_GENERATE_RESULT==="


def _extract_generate_result(log_text: str) -> Optional[Dict[str, Any]]:
    """Pull the JSON result scheduler/p_linkedin_generate_message.py prints after its marker line."""
    idx = (log_text or "").rfind(_LINKEDIN_GENERATE_RESULT_MARKER)
    if idx == -1:
        return None
    tail = log_text[idx + len(_LINKEDIN_GENERATE_RESULT_MARKER):].strip()
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            return None
    return None


def _auth_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    if credentials is None:
        logger.warning("Request rejected: missing Authorization header.")
        raise HTTPException(status_code=401, detail="Missing auth token")

    username = decode_token(JWT_SECRET, credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid/expired auth token")

    with session_scope() as session:
        row = session.execute(
            text("SELECT username, active FROM ui_users WHERE username = :u"),
            {"u": username},
        ).mappings().first()
        if not row or not row["active"]:
            logger.warning("Auth rejected: user '%s' not found or inactive.", username)
            raise HTTPException(status_code=401, detail="User inactive")

    return username


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": _now_utc().isoformat()}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict:
    with session_scope() as session:
        row = session.execute(
            text("SELECT username, password_hash, active FROM ui_users WHERE username = :u"),
            {"u": payload.username},
        ).mappings().first()
        if not row or not row["active"]:
            logger.warning("Login failed: unknown or inactive user '%s'.", payload.username)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not verify_password(payload.password, row["password_hash"]):
            logger.warning("Login failed: wrong password for user '%s'.", payload.username)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        session.execute(
            text("UPDATE ui_users SET last_login_utc = :ts WHERE username = :u"),
            {"ts": _now_utc(), "u": payload.username},
        )

    token = create_token(JWT_SECRET, payload.username, JWT_EXPIRE_HOURS)
    logger.info("User '%s' logged in. Token valid for %d hours.", payload.username, JWT_EXPIRE_HOURS)
    return {"token": token, "username": payload.username}


@app.post("/api/auth/logout")
def logout(user: str = Depends(_auth_user)) -> dict:
    """
    Client-side logout endpoint. The server is stateless (JWT), so this
    endpoint exists to provide a clean logout contract and emit an audit log.
    The client must discard the token on receipt of this response.
    """
    logger.info("User '%s' logged out.", user)
    return {"ok": True, "message": "Logged out. Please discard your token."}


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------

@app.get("/api/agents")
def list_agents(user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        rows = (
            session.query(AgentDefinitionORM)
            .order_by(AgentDefinitionORM.id.asc())
            .all()
        )
        items: List[Dict[str, Any]] = []
        for row in rows:
            profile = (
                session.query(AgentProfileORM)
                .filter_by(agent_id=row.id, is_current=True)
                .order_by(AgentProfileORM.version.desc(), AgentProfileORM.id.desc())
                .first()
            )
            items.append(
                {
                    "id": int(row.id),
                    "agent_key": row.agent_key,
                    "name": row.name,
                    "description": row.description,
                    "status": row.status,
                    "agent_type": row.agent_type,
                    "created_at_utc": row.created_at_utc,
                    "updated_at_utc": row.updated_at_utc,
                    "current_profile_version": int(profile.version) if profile else None,
                    "persona_title": profile.persona_title if profile else None,
                    "service_offering": profile.service_offering if profile else None,
                }
            )
    return {"items": items, "count": len(items)}


@app.post("/api/agents")
def create_agent(payload: AgentCreateRequest, user: str = Depends(_auth_user)) -> dict:
    key = (payload.agent_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="agent_key is required")
    with session_scope() as session:
        existing = session.query(AgentDefinitionORM).filter_by(agent_key=key).one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"Agent key already exists: {key}")
        now = _now_utc()
        row = AgentDefinitionORM(
            agent_key=key,
            name=(payload.name or key).strip(),
            description=(payload.description or "").strip() or None,
            status=payload.status,
            agent_type=(payload.agent_type or "custom").strip() or "custom",
            created_at_utc=now,
            updated_at_utc=now,
        )
        session.add(row)
        session.flush()
        session.add(
            AgentProfileORM(
                agent_id=row.id,
                persona_title=None,
                domain_focus=None,
                service_offering=None,
                version=1,
                is_current=True,
                created_at_utc=now,
                created_by=user,
            )
        )
        logger.info("Agent created: key='%s' id=%s by user='%s'.", key, row.id, user)
        return {"id": int(row.id), "agent_key": row.agent_key}


@app.put("/api/agents/{agent_id}")
def update_agent(agent_id: int, payload: AgentUpdateRequest, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        row = session.query(AgentDefinitionORM).filter_by(id=agent_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        if payload.name is not None:
            row.name = (payload.name or "").strip() or row.name
        if payload.description is not None:
            row.description = (payload.description or "").strip() or None
        if payload.agent_type is not None:
            row.agent_type = (payload.agent_type or "").strip() or row.agent_type
        if payload.status is not None:
            row.status = payload.status
        row.updated_at_utc = _now_utc()
    logger.info("Agent %d updated by user='%s'.", agent_id, user)
    return {"ok": True, "id": agent_id}


@app.get("/api/agents/{agent_id}/profile")
def get_agent_profile(agent_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        agent = session.query(AgentDefinitionORM).filter_by(id=agent_id).one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        profile = (
            session.query(AgentProfileORM)
            .filter_by(agent_id=agent_id, is_current=True)
            .order_by(AgentProfileORM.version.desc(), AgentProfileORM.id.desc())
            .first()
        )
        if not profile:
            raise HTTPException(status_code=404, detail="Agent profile not found")

        def _jl(raw: Optional[str]) -> Any:
            return _json_or_none(raw) if raw else None

        return {
            "agent": {
                "id": int(agent.id),
                "agent_key": agent.agent_key,
                "name": agent.name,
                "status": agent.status,
                "agent_type": agent.agent_type,
            },
            "profile": {
                "id": int(profile.id),
                "version": int(profile.version),
                "persona_title": profile.persona_title,
                "domain_focus": profile.domain_focus,
                "service_offering": profile.service_offering,
                "target_buyer_roles": _jl(profile.target_buyer_roles_json),
                "sales_objective": profile.sales_objective,
                "value_outcomes": _jl(profile.value_outcomes_json),
                "icp_rules": _jl(profile.icp_rules_json),
                "targeting_policy": _jl(profile.targeting_policy_json),
                "pipeline_policy": _jl(profile.pipeline_policy_json),
                "channel_policy": _jl(profile.channel_policy_json),
                "prompt_profile": _jl(profile.prompt_profile_json),
                "runtime_policy": _jl(profile.runtime_policy_json),
                "created_at_utc": profile.created_at_utc,
                "created_by": profile.created_by,
            },
        }


@app.put("/api/agents/{agent_id}/profile")
def update_agent_profile(
    agent_id: int,
    payload: AgentProfileUpdate,
    user: str = Depends(_auth_user),
) -> dict:
    with session_scope() as session:
        agent = session.query(AgentDefinitionORM).filter_by(id=agent_id).one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        current = (
            session.query(AgentProfileORM)
            .filter_by(agent_id=agent_id, is_current=True)
            .order_by(AgentProfileORM.version.desc(), AgentProfileORM.id.desc())
            .first()
        )
        now = _now_utc()
        base_version = int(current.version) if current else 0
        if current:
            current.is_current = False

        def _dump(value: Any, fallback_raw: Optional[str]) -> Optional[str]:
            if value is None:
                return fallback_raw
            return json.dumps(value, ensure_ascii=False)

        new_persona_title = payload.persona_title if payload.persona_title is not None else (current.persona_title if current else None)
        new_domain_focus = payload.domain_focus if payload.domain_focus is not None else (current.domain_focus if current else None)
        new_service_offering = payload.service_offering if payload.service_offering is not None else (current.service_offering if current else None)
        new_sales_objective = payload.sales_objective if payload.sales_objective is not None else (current.sales_objective if current else None)
        new_value_outcomes = payload.value_outcomes if payload.value_outcomes is not None else (
            _json_or_none(current.value_outcomes_json) if current else None
        )
        new_target_buyer_roles = payload.target_buyer_roles if payload.target_buyer_roles is not None else (
            _json_or_none(current.target_buyer_roles_json) if current else None
        )

        merged_prompt_profile: Dict[str, Any] = {}
        if payload.prompt_profile:
            merged_prompt_profile.update(payload.prompt_profile)
        elif current and current.prompt_profile_json:
            try:
                existing = json.loads(current.prompt_profile_json)
                if isinstance(existing, dict):
                    merged_prompt_profile.update(existing)
            except Exception:
                pass

        for k, v in [
            ("persona_title", new_persona_title),
            ("domain_focus", new_domain_focus),
            ("service_offering", new_service_offering),
            ("sales_objective", new_sales_objective),
        ]:
            if v:
                merged_prompt_profile[k] = v
        if new_value_outcomes:
            merged_prompt_profile["value_outcomes"] = (
                ", ".join(new_value_outcomes)
                if isinstance(new_value_outcomes, list)
                else str(new_value_outcomes)
            )
        if new_target_buyer_roles:
            merged_prompt_profile["target_buyer_roles"] = (
                ", ".join(new_target_buyer_roles)
                if isinstance(new_target_buyer_roles, list)
                else str(new_target_buyer_roles)
            )

        new_row = AgentProfileORM(
            agent_id=agent_id,
            persona_title=new_persona_title,
            domain_focus=new_domain_focus,
            service_offering=new_service_offering,
            target_buyer_roles_json=_dump(new_target_buyer_roles, current.target_buyer_roles_json if current else None),
            sales_objective=new_sales_objective,
            value_outcomes_json=_dump(new_value_outcomes, current.value_outcomes_json if current else None),
            icp_rules_json=_dump(payload.icp_rules, current.icp_rules_json if current else None),
            targeting_policy_json=_dump(payload.targeting_policy, current.targeting_policy_json if current else None),
            pipeline_policy_json=_dump(payload.pipeline_policy, current.pipeline_policy_json if current else None),
            channel_policy_json=_dump(payload.channel_policy, current.channel_policy_json if current else None),
            prompt_profile_json=json.dumps(merged_prompt_profile, ensure_ascii=False) if merged_prompt_profile else None,
            runtime_policy_json=_dump(payload.runtime_policy, current.runtime_policy_json if current else None),
            version=base_version + 1,
            is_current=True,
            created_at_utc=now,
            created_by=user,
        )
        agent.updated_at_utc = now
        session.add(new_row)
        session.flush()

        logger.info(
            "Agent %d profile updated to version %d by user='%s'. "
            "persona='%s' domain='%s' service='%s'",
            agent_id, new_row.version, user,
            new_persona_title or "", new_domain_focus or "", new_service_offering or "",
        )
        return {"ok": True, "profile_id": int(new_row.id), "version": int(new_row.version)}


@app.get("/api/agents/{agent_id}/config-preview")
def preview_agent_config(agent_id: int, user: str = Depends(_auth_user)) -> dict:
    """
    Debug/audit endpoint: returns the fully resolved AgentResolvedConfig as it
    would be used at runtime.
    """
    from config import AppConfig
    try:
        config = AppConfig()
        resolved = AgentConfigResolver(config).resolve(agent_id=agent_id)
        return {
            "agent_id": resolved.agent_id,
            "agent_key": resolved.agent_key,
            "agent_name": resolved.agent_name,
            "status": resolved.status,
            "runtime_mode": resolved.runtime_mode,
            "prompt_context": resolved.prompt_context,
            "pipeline_policy": resolved.pipeline_policy,
            "keywords_by_type": resolved.keywords_by_type,
            "enabled_tools": resolved.enabled_tools,
            "target_buyer_roles": resolved.target_buyer_roles,
            "value_outcomes": resolved.value_outcomes,
        }
    except Exception as exc:
        logger.error("Config preview failed for agent_id=%s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=f"Config resolution failed: {exc}") from exc


@app.get("/api/agents/{agent_id}/keywords")
def list_agent_keywords(agent_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        rows = (
            session.query(AgentKeywordORM)
            .filter_by(agent_id=agent_id)
            .order_by(AgentKeywordORM.keyword_type.asc(), AgentKeywordORM.weight.desc(), AgentKeywordORM.id.asc())
            .all()
        )
        items = [
            {
                "id": int(r.id),
                "agent_id": int(r.agent_id),
                "keyword_type": r.keyword_type,
                "keyword": r.keyword,
                "weight": float(r.weight or 0),
                "active": bool(r.active),
                "updated_at_utc": r.updated_at_utc,
            }
            for r in rows
        ]
    return {"items": items, "count": len(items)}


@app.post("/api/agents/{agent_id}/keywords")
def create_or_update_agent_keyword(
    agent_id: int,
    payload: AgentKeywordUpsert,
    user: str = Depends(_auth_user),
) -> dict:
    now = _now_utc()
    with session_scope() as session:
        row = (
            session.query(AgentKeywordORM)
            .filter_by(
                agent_id=agent_id,
                keyword_type=(payload.keyword_type or "").strip().lower(),
                keyword=(payload.keyword or "").strip(),
            )
            .one_or_none()
        )
        if row is None:
            row = AgentKeywordORM(
                agent_id=agent_id,
                keyword_type=(payload.keyword_type or "").strip().lower(),
                keyword=(payload.keyword or "").strip(),
                weight=float(payload.weight),
                active=bool(payload.active),
                created_at_utc=now,
                updated_at_utc=now,
            )
            session.add(row)
            session.flush()
            logger.info(
                "Keyword created: agent_id=%d type='%s' keyword='%s' by user='%s'.",
                agent_id, row.keyword_type, row.keyword, user,
            )
            return {"id": int(row.id), "created": True}

        row.weight = float(payload.weight)
        row.active = bool(payload.active)
        row.updated_at_utc = now
        logger.info(
            "Keyword updated: agent_id=%d type='%s' keyword='%s' active=%s by user='%s'.",
            agent_id, row.keyword_type, row.keyword, row.active, user,
        )
        return {"id": int(row.id), "updated": True}


@app.delete("/api/agents/{agent_id}/keywords/{keyword_id}")
def delete_agent_keyword(agent_id: int, keyword_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        row = session.query(AgentKeywordORM).filter_by(id=keyword_id, agent_id=agent_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Agent keyword not found")
        logger.info(
            "Keyword deleted: id=%d agent_id=%d keyword='%s' by user='%s'.",
            keyword_id, agent_id, row.keyword, user,
        )
        session.delete(row)
    return {"ok": True}


@app.get("/api/dashboard/summary")
def dashboard_summary(
    agent_id: Optional[int] = None,
    user: str = Depends(_auth_user),
) -> dict:
    resolved_agent_id = _resolve_agent_scope(agent_id=agent_id) if agent_id is not None else None
    agent_filter = " AND agent_id = :agent_id" if resolved_agent_id is not None else ""
    params: Dict[str, Any] = {}
    if resolved_agent_id is not None:
        params["agent_id"] = resolved_agent_id
    with session_scope() as session:
        metrics = session.execute(
            text(
                f"""
                SELECT
                  (SELECT COUNT(1) FROM naukri_jobs WHERE 1=1 {agent_filter}) AS jobs_total,
                  (SELECT COUNT(1) FROM naukri_jobs WHERE researched = 1 {agent_filter}) AS jobs_researched,
                  (SELECT COUNT(1) FROM candidate_profiles WHERE 1=1 {agent_filter}) AS candidate_profiles_total,
                  (SELECT COUNT(1) FROM candidate_profiles WHERE profile_status='completed' {agent_filter}) AS candidate_profiles_completed,
                  (SELECT COUNT(1) FROM company_research WHERE 1=1 {agent_filter}) AS companies_total,
                  (SELECT COUNT(1) FROM company_research WHERE research_status='pending' {agent_filter}) AS companies_pending,
                  (SELECT COUNT(1) FROM prospects WHERE 1=1 {agent_filter}) AS prospects_total,
                  (SELECT COUNT(1) FROM prospects WHERE contact_relevance_bucket IN ('prime','strong') {agent_filter}) AS prospects_hot,
                  (SELECT COUNT(1) FROM prospects WHERE dossier_status='completed' {agent_filter}) AS dossiers_completed,
                  (SELECT COUNT(1) FROM prospects WHERE outreach_message IS NOT NULL {agent_filter}) AS outreach_ready
                """
            ),
            params,
        ).mappings().first()

        recent_runs = session.execute(
            text(
                """
                SELECT id, pipeline, status, started_at_utc, ended_at_utc, triggered_by, message
                FROM pipeline_runs
                WHERE (:agent_id IS NULL OR agent_id = :agent_id)
                ORDER BY id DESC
                LIMIT 12
                """
            ),
            {"agent_id": resolved_agent_id},
        ).mappings().all()

    return {
        "metrics": dict(metrics or {}),
        "recent_runs": [dict(r) for r in recent_runs],
        "agent_id": resolved_agent_id,
        "requested_by": user,
    }


@app.get("/api/jobs")
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    agent_id: Optional[int] = None,
    search: Optional[str] = None,
    keyword: Optional[str] = None,
    company: Optional[str] = None,
    researched: Optional[bool] = None,
    user: str = Depends(_auth_user),
) -> dict:
    filters = ["1=1"]
    params: Dict[str, Any] = {}
    if agent_id is not None:
        params["agent_id"] = _resolve_agent_scope(agent_id=agent_id)
        filters.append("agent_id = :agent_id")
    if search:
        filters.append("(title LIKE :search OR company_name LIKE :search OR location_text LIKE :search)")
        params["search"] = f"%{search}%"
    if keyword:
        filters.append("search_keyword = :keyword")
        params["keyword"] = keyword
    if company:
        filters.append("company_name LIKE :company")
        params["company"] = f"%{company}%"
    if researched is not None:
        filters.append("researched = :researched")
        params["researched"] = 1 if researched else 0

    where = " AND ".join(filters)
    offset = (page - 1) * page_size

    with session_scope() as session:
        total = session.execute(
            text(f"SELECT COUNT(1) FROM naukri_jobs WHERE {where}"), params
        ).scalar_one()
        rows = session.execute(
            text(
                f"""
                SELECT
                    id, agent_id, source, search_keyword, search_location,
                    title, company_name, posted_date, posted_relative,
                    experience_text, salary_text, location_text,
                    employment_type, industry, department, role, role_category, education,
                    extraction_confidence, researched, fetched_at_utc, job_url, canonical_job_url
                FROM naukri_jobs WHERE {where}
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        ).mappings().all()

    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/companies")
def list_companies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    agent_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    user: str = Depends(_auth_user),
) -> dict:
    filters = ["1=1"]
    params: Dict[str, Any] = {}
    if agent_id is not None:
        params["agent_id"] = _resolve_agent_scope(agent_id=agent_id)
        filters.append("c.agent_id = :agent_id")
    if status:
        filters.append("c.research_status = :status")
        params["status"] = status
    if search:
        filters.append("(c.company_name LIKE :search OR c.industry LIKE :search OR c.location LIKE :search)")
        params["search"] = f"%{search}%"

    where = " AND ".join(filters)
    offset = (page - 1) * page_size

    with session_scope() as session:
        total = session.execute(
            text(f"SELECT COUNT(1) FROM company_research c WHERE {where}"), params
        ).scalar_one()
        rows = session.execute(
            text(
                f"""
                SELECT
                  c.id, c.agent_id, c.company_name, c.linkedin_url, c.linkedin_match_confidence,
                  c.tagline, c.industry, c.location, c.employee_range, c.followers, c.research_status,
                  c.updated_at_utc,
                  (SELECT COUNT(1) FROM prospects p WHERE p.company_research_id = c.id) AS prospect_count
                FROM company_research c WHERE {where}
                ORDER BY c.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        ).mappings().all()

    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

@app.get("/api/companies/{company_id}")
def company_detail(
    company_id: int,
    agent_id: Optional[int] = None,
    user: str = Depends(_auth_user),
) -> dict:
    resolved_agent = _resolve_agent_scope(agent_id=agent_id) if agent_id is not None else None
    with session_scope() as session:
        company = session.execute(
            text("SELECT * FROM company_research WHERE id=:id AND (:agent_id IS NULL OR agent_id = :agent_id)"),
            {"id": company_id, "agent_id": resolved_agent},
        ).mappings().first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        prospects = session.execute(
            text(
                """
                SELECT
                    id, name, headline, linkedin_profile_url,
                    role_bucket, company_match_confidence,
                    contact_relevance_score, contact_relevance_bucket,
                    dossier_status, outreach_generated_at_utc,
                    outreach_dispatch_status, outreach_dispatch_channel, outreach_sent_at_utc
                FROM prospects
                WHERE company_research_id = :id
                  AND (:agent_id IS NULL OR agent_id = :agent_id)
                ORDER BY contact_relevance_score DESC, id DESC
                LIMIT 200
                """
            ),
            {"id": company_id, "agent_id": resolved_agent},
        ).mappings().all()

    return {"company": dict(company), "prospects": [dict(p) for p in prospects]}


def _derive_engagement_status(
    conv_status: Optional[str],
    handoff_email_sent: Any,
    messages_received: Any,
    outreach_sent: Any,
    dispatch_status: Optional[str],
) -> str:
    """
    Collapse conversation + outreach state into one dashboard remark:
    email_sent | handed_off | not_interested | meeting_booked | closed
    | talking | in_process | not_contacted
    """
    if conv_status == "handed_off":
        return "email_sent" if handoff_email_sent else "handed_off"
    if conv_status == "not_interested":
        return "not_interested"
    if conv_status == "meeting_booked":
        return "meeting_booked"
    if conv_status == "closed":
        return "closed"
    if conv_status == "active":
        return "talking" if (messages_received or 0) > 0 else "in_process"
    if outreach_sent or (dispatch_status and dispatch_status not in ("not_sent", "pending")):
        return "in_process"
    return "not_contacted"


@app.get("/api/prospects")
def list_prospects(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    agent_id: Optional[int] = None,
    company_id: Optional[int] = None,
    bucket: Optional[str] = None,
    search: Optional[str] = None,
    user: str = Depends(_auth_user),
) -> dict:
    filters = ["1=1"]
    params: Dict[str, Any] = {}
    if agent_id is not None:
        filters.append("p.agent_id = :agent_id")
        params["agent_id"] = _resolve_agent_scope(agent_id=agent_id)
    if company_id is not None:
        filters.append("p.company_research_id = :company_id")
        params["company_id"] = company_id
    if bucket:
        filters.append("p.contact_relevance_bucket = :bucket")
        params["bucket"] = bucket
    if search:
        filters.append("(p.name LIKE :search OR p.headline LIKE :search OR c.company_name LIKE :search)")
        params["search"] = f"%{search}%"

    where = " AND ".join(filters)
    offset = (page - 1) * page_size

    with session_scope() as session:
        total = session.execute(
            text(
                f"""
                SELECT COUNT(1) FROM prospects p
                LEFT JOIN company_research c ON c.id = p.company_research_id
                WHERE {where}
                """
            ),
            params,
        ).scalar_one()
        rows = session.execute(
            text(
                f"""
                SELECT
                    p.id, p.agent_id, p.company_research_id, c.company_name,
                    p.name, p.headline, p.current_title, p.current_company,
                    p.linkedin_profile_url, p.role_bucket,
                    p.company_match_confidence, p.outreach_feasibility_score,
                    p.contact_relevance_score, p.contact_relevance_bucket,
                    p.profile_summary_text, p.dossier_status,
                    p.outreach_generated_at_utc, p.assessed_at_utc,
                    p.outreach_dispatch_status, p.outreach_dispatch_channel,
                    p.outreach_dispatch_attempts, p.outreach_sent_at_utc,
                    p.outreach_sent,
                    -- Whether a message exists, not the text itself: the row Send
                    -- button only needs to know if it has something to send, and
                    -- the send endpoint reads the stored text server-side.
                    (p.outreach_message IS NOT NULL AND TRIM(p.outreach_message) <> '')
                        AS has_outreach_message,
                    lc.conversation_status, lc.lead_stage, lc.handoff_reason,
                    lc.handoff_email_sent, lc.last_reply_received_utc,
                    lc.messages_sent AS conv_messages_sent,
                    lc.messages_received AS conv_messages_received
                FROM prospects p
                LEFT JOIN company_research c ON c.id = p.company_research_id
                LEFT JOIN linkedin_conversations lc ON lc.prospect_id = p.id
                WHERE {where}
                ORDER BY p.contact_relevance_score DESC, p.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        ).mappings().all()

    items = []
    for r in rows:
        item = dict(r)
        item["has_outreach_message"] = bool(item.get("has_outreach_message"))
        item["engagement_status"] = _derive_engagement_status(
            item.get("conversation_status"),
            item.get("handoff_email_sent"),
            item.get("conv_messages_received"),
            item.get("outreach_sent"),
            item.get("outreach_dispatch_status"),
        )
        items.append(item)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/prospects/{prospect_id}")
def prospect_detail(
    prospect_id: int,
    agent_id: Optional[int] = None,
    user: str = Depends(_auth_user),
) -> dict:
    resolved_agent = _resolve_agent_scope(agent_id=agent_id) if agent_id is not None else None
    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT p.*, c.company_name
                FROM prospects p
                LEFT JOIN company_research c ON c.id = p.company_research_id
                WHERE p.id = :id
                  AND (:agent_id IS NULL OR p.agent_id = :agent_id)
                """
            ),
            {"id": prospect_id, "agent_id": resolved_agent},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Prospect not found")

        item = dict(row)
        item["experiences"] = _json_or_none(item.get("experiences_json"))
        item["recent_posts"] = _json_or_none(item.get("recent_posts_json"))
        item["llm_assessment"] = _json_or_none(item.get("llm_assessment_json"))
        item["dossier"] = _json_or_none(item.get("dossier_json"))
        item["outreach_message_json"] = _json_or_none(item.get("outreach_message"))

        conv = session.execute(
            text(
                """
                SELECT conversation_status, lead_stage, handoff_reason,
                       handoff_email_sent, handed_off_at_utc,
                       messages_sent, messages_received,
                       last_reply_received_utc, last_checked_utc
                FROM linkedin_conversations
                WHERE prospect_id = :id
                """
            ),
            {"id": prospect_id},
        ).mappings().first()
        item["conversation"] = dict(conv) if conv else None
        item["engagement_status"] = _derive_engagement_status(
            conv["conversation_status"] if conv else None,
            conv["handoff_email_sent"] if conv else None,
            conv["messages_received"] if conv else None,
            item.get("outreach_sent"),
            item.get("outreach_dispatch_status"),
        )

    return item

@app.patch("/api/prospects/{prospect_id}/outreach")
def set_prospect_outreach_required(
    prospect_id: int,
    payload: dict,
    user: str = Depends(_auth_user),
) -> dict:
    """Mark/unmark a prospect for LinkedIn outreach dispatch."""
    required = bool(payload.get("outreach_required", False))
    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM prospects WHERE id = :id"), {"id": prospect_id}
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Prospect not found")
        session.execute(
            text(
                "UPDATE prospects SET outreach_required = :req WHERE id = :id"
            ),
            {"req": 1 if required else 0, "id": prospect_id},
        )
    logger.info(
        "Prospect %d outreach_required set to %s by user='%s'.",
        prospect_id, required, user,
    )
    return {"ok": True, "prospect_id": prospect_id, "outreach_required": required}


@app.patch("/api/prospects/{prospect_id}/message")
def update_prospect_message(
    prospect_id: int,
    payload: ProspectMessageUpdate,
    user: str = Depends(_auth_user),
) -> dict:
    """
    Overwrite a prospect's outreach message with a human-edited version.

    outreach_generated_at_utc is deliberately left alone — it records when the
    LLM drafted the message, and a manual edit is not a generation.
    """
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message text cannot be empty.")

    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM prospects WHERE id = :id"), {"id": prospect_id}
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Prospect not found")
        session.execute(
            text("UPDATE prospects SET outreach_message = :m WHERE id = :id"),
            {"m": message, "id": prospect_id},
        )

    logger.info(
        "Prospect %d outreach message edited by user='%s' (%d chars).",
        prospect_id, user, len(message),
    )
    return {"ok": True, "prospect_id": prospect_id, "outreach_message": message}


@app.post("/api/prospects/{prospect_id}/generate-message")
async def generate_prospect_message(prospect_id: int, user: str = Depends(_auth_user)) -> dict:
    """
    Generate an outreach message for ONE prospect on demand and store it.

    This is the per-prospect equivalent of the Intelligence pipeline's message
    step — for when you don't want to run the whole batch. It scrapes the
    prospect's profile fresh and runs the same dossier -> outreach LLM pipeline
    (via p_linkedin_generate_message.py, the same generator the LinkedIn tab
    uses), then saves the result to prospects.outreach_message so it becomes
    editable and sendable from the UI. Holds the shared CDP-Chrome lock.
    """
    with session_scope() as session:
        row = session.execute(
            text("SELECT id, agent_id, linkedin_profile_url FROM prospects WHERE id = :id"),
            {"id": prospect_id},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Prospect not found")

    url = (row["linkedin_profile_url"] or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="This prospect has no LinkedIn profile URL.")
    if not _LINKEDIN_PROFILE_URL_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail=f"Stored profile URL is not a valid LinkedIn profile URL: {url}",
        )

    if _linkedin_automation_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="LinkedIn automation is busy (a scheduled sweep or another action is running) — try again shortly.",
        )

    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "linkedin_manual_generate", row["agent_id"], user,
            f"Generate message for prospect {prospect_id} ({url})",
        )
        cmd = [
            _python_exe(), str(ROOT_DIR / "scheduler" / "p_linkedin_generate_message.py"),
            "--url", url, "--agent-id", str(row["agent_id"]),
        ]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 5)

        if returncode != 0:
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Generation failed.", log_text=out)
            detail = _extract_test_error(out) or "Could not generate a message for this prospect."
            logger.warning("Message generation for prospect %d failed for user='%s': %s", prospect_id, user, detail)
            raise HTTPException(status_code=422, detail=detail)

        result = _extract_generate_result(out)
        if result is None or not (result.get("message") or "").strip():
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Result could not be parsed.", log_text=out)
            raise HTTPException(status_code=500, detail="Generation finished but no message could be read from the result.")

        message = result["message"].strip()
        _update_run(run_id, status="completed", ended_at_utc=_now_utc(), message="Message generated.", log_text=out)

    with session_scope() as session:
        session.execute(
            text(
                """
                UPDATE prospects
                SET outreach_message = :m,
                    outreach_generated_at_utc = :now,
                    dossier_status = CASE WHEN dossier_status IN ('completed','manual')
                                          THEN dossier_status ELSE 'manual' END
                WHERE id = :id
                """
            ),
            {"m": message, "now": _now_utc(), "id": prospect_id},
        )

    logger.info("Message generated for prospect %d by user='%s' (%d chars).", prospect_id, user, len(message))
    return {"ok": True, "prospect_id": prospect_id, "message": message, "run_id": run_id}


@app.post("/api/prospects/{prospect_id}/send")
async def send_prospect_message(prospect_id: int, user: str = Depends(_auth_user)) -> dict:
    """
    Send this prospect's stored outreach message over LinkedIn, on demand.

    Sends whatever is in prospects.outreach_message right now — the AI draft or
    the user's edit of it — so what the Outreach tab shows is exactly what goes
    out. Independent of the p_outreach.py batch dispatch, but holds the same
    shared CDP-Chrome lock so a scheduled sweep can never drive the browser at
    the same moment. Fails fast with 409 rather than queueing behind it.
    """
    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT id, agent_id, linkedin_profile_url, outreach_message
                FROM prospects WHERE id = :id
                """
            ),
            {"id": prospect_id},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Prospect not found")

    url = (row["linkedin_profile_url"] or "").strip()
    message = (row["outreach_message"] or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="This prospect has no LinkedIn profile URL.")
    if not _LINKEDIN_PROFILE_URL_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail=f"Stored profile URL is not a valid LinkedIn profile URL: {url}",
        )
    if not message:
        raise HTTPException(
            status_code=400,
            detail="No outreach message to send. Run the Intelligence pipeline, or write one yourself.",
        )

    if _linkedin_automation_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="LinkedIn automation is busy (a scheduled sweep or another send is running) — try again shortly.",
        )

    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "linkedin_manual_send", row["agent_id"], user,
            f"Manual send to prospect {prospect_id} ({url})",
        )
        # "auto" keeps the batch pipeline's behaviour: detect connection state and
        # message-first, falling back to a connection request carrying the message
        # as the invite note when direct messaging isn't available.
        cmd = [
            _python_exe(), str(ROOT_DIR / "scheduler" / "p_outreach.py"),
            "--url", url, "--message", message, "--action", "auto",
        ]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 10)

        if returncode != 0:
            detail = _extract_test_error(out) or "Send failed — LinkedIn automation could not deliver this message."
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Send failed.", log_text=out)
            with session_scope() as session:
                session.execute(
                    text(
                        """
                        UPDATE prospects
                        SET outreach_dispatch_status = 'failed',
                            outreach_dispatch_attempts = outreach_dispatch_attempts + 1,
                            outreach_attempts = outreach_attempts + 1,
                            outreach_last_dispatch_at_utc = :now,
                            outreach_dispatch_error = :err
                        WHERE id = :id
                        """
                    ),
                    {"now": _now_utc(), "err": detail[:500], "id": prospect_id},
                )
            logger.warning("Manual send for prospect %d failed for user='%s': %s", prospect_id, user, detail)
            raise HTTPException(status_code=422, detail=detail)

        action = _extract_test_action(out) or "message"
        run_message = {
            "message": "Message sent.",
            "connect": "Not connected and no open-messaging path — connection request sent with the message as the invite note.",
            "skip": "Nothing sent — a connection request to this profile is already pending.",
        }.get(action, "Message sent.")
        _update_run(run_id, status="completed", ended_at_utc=_now_utc(), message=run_message, log_text=out)

    # A pending invite means NOTHING was sent — never mark it as sent.
    if action == "skip":
        logger.info("Manual send for prospect %d skipped (invite already pending).", prospect_id)
        raise HTTPException(
            status_code=409,
            detail="Nothing sent — a connection request to this profile is already pending. "
                   "Wait for them to accept before messaging.",
        )

    now = _now_utc()
    with session_scope() as session:
        session.execute(
            text(
                """
                UPDATE prospects
                SET outreach_sent = 1,
                    outreach_status = 'sent',
                    outreach_dispatch_status = 'sent',
                    outreach_dispatch_channel = 'linkedin',
                    outreach_dispatch_attempts = outreach_dispatch_attempts + 1,
                    outreach_attempts = outreach_attempts + 1,
                    outreach_last_dispatch_at_utc = :now,
                    outreach_sent_at_utc = :now,
                    outreach_dispatch_error = NULL
                WHERE id = :id
                """
            ),
            {"now": now, "id": prospect_id},
        )

    # Track the conversation so the reply scheduler follows up on their replies,
    # exactly as the batch pipeline would.
    from db.models import LinkedInConversationORM
    from outreach.linkedin_reply_handler import LinkedInReplyHandler

    conv_appended = False
    with session_scope() as session:
        conv = session.query(LinkedInConversationORM).filter_by(prospect_id=prospect_id).one_or_none()
        if conv is not None:
            conv.append_message("us", message)
            conv.messages_sent = (conv.messages_sent or 0) + 1
            conv.last_message_sent_utc = now
            conv_appended = True
    if not conv_appended:
        LinkedInReplyHandler.create_conversation_for_prospect(
            prospect_id=prospect_id,
            agent_id=row["agent_id"],
            linkedin_profile_url=url,
            first_message_text=message,
        )

    logger.info(
        "Manual send for prospect %d by user='%s' -> action=%s (conversation %s)",
        prospect_id, user, action, "appended" if conv_appended else "created",
    )
    return {
        "success": True,
        "prospect_id": prospect_id,
        "run_id": run_id,
        "action": action,
        "message": run_message,
    }


@app.post("/api/prospects/{prospect_id}/check-messages")
async def check_prospect_messages(prospect_id: int, user: str = Depends(_auth_user)) -> dict:
    """
    Manual, ad-hoc reply check for one prospect. Independent of the backend's
    fixed-interval reply scheduler — never shifts its cadence. Fails fast with
    409 if the shared LinkedIn/CDP-Chrome automation lock is currently held by
    a scheduled sweep or another manual action, rather than queueing behind it.
    """
    with session_scope() as session:
        conv = session.execute(
            text("SELECT id, agent_id FROM linkedin_conversations WHERE prospect_id = :pid"),
            {"pid": prospect_id},
        ).mappings().first()
    if not conv:
        raise HTTPException(
            status_code=404,
            detail="No tracked LinkedIn conversation for this prospect yet.",
        )

    if _linkedin_automation_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="LinkedIn automation is busy (a scheduled sweep or another action is running) — try again shortly.",
        )

    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "reply_check_manual", conv["agent_id"], user, f"Manual check for prospect {prospect_id}."
        )
        cmd = [_python_exe(), _pipeline_script("reply_check"), "--test-prospect-id", str(prospect_id)]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 10)
        status = "completed" if returncode == 0 else "failed"
        _update_run(
            run_id,
            status=status,
            ended_at_utc=_now_utc(),
            message="Manual check finished." if returncode == 0 else f"Exited with code {returncode}",
            log_text=out,
        )

    logger.info("Manual reply check for prospect %d by user='%s' -> %s", prospect_id, user, status)
    return {"ok": returncode == 0, "run_id": run_id, "log_tail": out[-2000:]}


@app.post("/api/linkedin/generate-message")
async def generate_linkedin_message(payload: LinkedInGenerateRequest, user: str = Depends(_auth_user)) -> dict:
    """
    Scrape the profile and draft a personalized first message via the same
    dossier + outreach-message LLM pipeline used for regular prospects, so an
    ad-hoc "LinkedIn" tab send isn't left to the user to write from scratch.
    Returns a draft for the user to review/edit before sending — it is not sent here.
    """
    url = (payload.profile_url or "").strip()
    if not _LINKEDIN_PROFILE_URL_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Not a valid LinkedIn profile URL (expected https://www.linkedin.com/in/<handle>/).",
        )

    resolved_agent_id = _resolve_agent_scope(agent_id=payload.agent_id, agent_key=payload.agent_key)

    if _linkedin_automation_lock.locked():
        raise HTTPException(status_code=409, detail="LinkedIn automation is busy — try again shortly.")

    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "linkedin_manual_generate", resolved_agent_id, user, f"Generate message draft for {url}"
        )
        cmd = [
            _python_exe(), str(ROOT_DIR / "scheduler" / "p_linkedin_generate_message.py"),
            "--url", url, "--agent-id", str(resolved_agent_id),
        ]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 5)

        if returncode != 0:
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Generation failed.", log_text=out)
            detail = _extract_test_error(out) or "Could not generate a message for this profile."
            logger.warning("LinkedIn message generation for %s failed for user='%s': %s", url, user, detail)
            raise HTTPException(status_code=422, detail=detail)

        result = _extract_generate_result(out)
        if result is None:
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Result could not be parsed.", log_text=out)
            raise HTTPException(status_code=500, detail="Message generation finished but the result could not be read.")

        _update_run(run_id, status="completed", ended_at_utc=_now_utc(), message="Message generated.", log_text=out)

    logger.info("LinkedIn message draft generated for %s by user='%s'", url, user)
    return {
        "message": result.get("message", ""),
        "name": result.get("name"),
        "headline": result.get("headline"),
        "current_company": result.get("current_company"),
        "run_id": run_id,
    }


@app.post("/api/linkedin/send")
async def send_linkedin_message(payload: LinkedInSendRequest, user: str = Depends(_auth_user)) -> dict:
    """
    Send a first LinkedIn message to an arbitrary profile URL (connection or
    not — see the connect-fallback logic in linkedin_outreach_sender.py). On
    success, auto-registers the recipient as a tracked prospect + conversation
    so the reply scheduler picks up their replies like any other prospect.
    """
    url = (payload.profile_url or "").strip()
    message = (payload.message or "").strip()

    if not _LINKEDIN_PROFILE_URL_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Not a valid LinkedIn profile URL (expected https://www.linkedin.com/in/<handle>/).",
        )
    if not message:
        raise HTTPException(status_code=400, detail="Message text is required.")

    resolved_agent_id = _resolve_agent_scope(agent_id=payload.agent_id, agent_key=payload.agent_key)

    if _linkedin_automation_lock.locked():
        raise HTTPException(status_code=409, detail="LinkedIn automation is busy — try again shortly.")

    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "linkedin_manual_send", resolved_agent_id, user, f"Manual send to {url}"
        )
        # "auto": detect connection state and message-first, falling back to a
        # connection request (with the message as the invite note) when direct
        # messaging isn't available — the same behavior as the batch pipeline.
        # A forced "message" would disable that fallback (see run_test in
        # outreach/linkedin_outreach_sender.py).
        cmd = [
            _python_exe(), str(ROOT_DIR / "scheduler" / "p_outreach.py"),
            "--url", url, "--message", message, "--action", "auto",
        ]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 10)

        if returncode != 0:
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message="Send failed.", log_text=out)
            detail = _extract_test_error(out) or "Send failed — LinkedIn automation could not deliver this message."
            logger.warning("LinkedIn manual send to %s failed for user='%s': %s", url, user, detail)
            raise HTTPException(status_code=422, detail=detail)

        action = _extract_test_action(out) or "message"
        run_message = {
            "message": "Message sent.",
            "connect": "Not connected and no open-messaging path — connection request sent with the message as the invite note.",
            "skip": "Nothing sent — a connection request to this profile is already pending.",
        }.get(action, "Message sent.")
        _update_run(run_id, status="completed", ended_at_utc=_now_utc(), message=run_message, log_text=out)

    # A pending invite means NOTHING was sent — surface that honestly instead
    # of registering a prospect that was never actually contacted.
    if action == "skip":
        logger.info("LinkedIn manual send to %s skipped for user='%s' (invite already pending).", url, user)
        raise HTTPException(
            status_code=409,
            detail="Nothing sent — a connection request to this profile is already pending. "
                   "Wait for them to accept before messaging.",
        )

    def _find_prospect(session, company_id: int) -> Optional[int]:
        row = session.execute(
            text(
                """
                SELECT id FROM prospects
                WHERE agent_id = :aid AND company_research_id = :cid AND linkedin_profile_url = :url
                """
            ),
            {"aid": resolved_agent_id, "cid": company_id, "url": url},
        ).mappings().first()
        return int(row["id"]) if row else None

    def _record_repeat_send(session, pid: int) -> None:
        session.execute(
            text(
                """
                UPDATE prospects
                SET outreach_message = :message,
                    outreach_sent = 1, outreach_status = 'sent', outreach_dispatch_status = 'sent',
                    outreach_dispatch_attempts = outreach_dispatch_attempts + 1,
                    outreach_attempts = outreach_attempts + 1,
                    outreach_last_dispatch_at_utc = :now, outreach_sent_at_utc = :now
                WHERE id = :id
                """
            ),
            {"message": message, "now": _now_utc(), "id": pid},
        )

    already_tracked = False
    with session_scope() as session:
        company = session.execute(
            text(
                "SELECT id FROM company_research WHERE agent_id = :aid AND search_query = :marker"
            ),
            {"aid": resolved_agent_id, "marker": "__manual_linkedin_send__"},
        ).mappings().first()
        if company:
            company_id = int(company["id"])
        else:
            session.execute(
                text(
                    """
                    INSERT INTO company_research
                        (agent_id, company_name, search_query, research_status, attempts, created_at_utc, updated_at_utc)
                    VALUES
                        (:aid, 'Manual outreach', :marker, 'manual', 0, :now, :now)
                    """
                ),
                {"aid": resolved_agent_id, "marker": "__manual_linkedin_send__", "now": _now_utc()},
            )
            company_id = int(session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())

        # Idempotent: a repeat send to an already-tracked profile updates the
        # existing row instead of tripping uq_prospect_profile_company_agent.
        prospect_id = _find_prospect(session, company_id)
        if prospect_id is not None:
            already_tracked = True
            _record_repeat_send(session, prospect_id)

    if prospect_id is None:
        try:
            with session_scope() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO prospects (
                            agent_id, company_research_id, linkedin_profile_url,
                            search_confidence, contact_info_available, message_available, connect_available,
                            company_match_confidence, outreach_feasibility_score, contact_relevance_score,
                            dossier_status, outreach_dispatch_status, outreach_dispatch_channel,
                            outreach_dispatch_attempts, outreach_message, outreach_sent,
                            outreach_status, outreach_attempts, outreach_last_dispatch_at_utc,
                            outreach_sent_at_utc, outreach_required, outreach_in_progress, created_at_utc
                        ) VALUES (
                            :agent_id, :company_id, :url,
                            0, 0, 1, 0,
                            0, 0, 0,
                            'manual', 'sent', 'linkedin',
                            1, :message, 1,
                            'sent', 1, :now,
                            :now, 0, 0, :now
                        )
                        """
                    ),
                    {
                        "agent_id": resolved_agent_id, "company_id": company_id, "url": url,
                        "message": message, "now": _now_utc(),
                    },
                )
                prospect_id = int(session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        except IntegrityError:
            # Raced by a concurrent send that inserted the same profile between
            # our lookup and insert — reuse the winner's row.
            with session_scope() as session:
                prospect_id = _find_prospect(session, company_id)
                if prospect_id is None:
                    raise
                already_tracked = True
                _record_repeat_send(session, prospect_id)

    # Conversation tracking: create the baseline row for a new prospect
    # (idempotent helper), or append this send to the existing thread so
    # thread_json stays truthful about what we've actually sent.
    from db.models import LinkedInConversationORM
    from outreach.linkedin_reply_handler import LinkedInReplyHandler

    conv_appended = False
    if already_tracked:
        with session_scope() as session:
            conv = session.query(LinkedInConversationORM).filter_by(prospect_id=prospect_id).one_or_none()
            if conv is not None:
                conv.append_message("us", message)
                conv.messages_sent = (conv.messages_sent or 0) + 1
                conv.last_message_sent_utc = _now_utc()
                conv_appended = True
    if not conv_appended:
        LinkedInReplyHandler.create_conversation_for_prospect(
            prospect_id=prospect_id,
            agent_id=resolved_agent_id,
            linkedin_profile_url=url,
            first_message_text=message,
        )

    logger.info(
        "LinkedIn manual send to %s by user='%s' -> prospect_id=%d (action=%s, already_tracked=%s)",
        url, user, prospect_id, action, already_tracked,
    )
    return {
        "success": True,
        "prospect_id": prospect_id,
        "run_id": run_id,
        "action": action,
        "already_tracked": already_tracked,
    }


@app.get("/api/ingestion/runs")
def list_ingestion_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: str = Depends(_auth_user),
) -> dict:
    offset = (page - 1) * page_size
    with session_scope() as session:
        total = session.execute(text("SELECT COUNT(1) FROM ingestion_runs")).scalar_one()
        rows = session.execute(
            text(
                """
                SELECT id, source, target_url, status, stage, pages_crawled, pages_in_queue,
                       chunks_created, chunks_embedded, chunks_stored, progress_pct, eta_seconds,
                       error_text, triggered_by, started_at_utc, ended_at_utc
                FROM ingestion_runs
                ORDER BY started_at_utc DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset},
        ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/ingestion/runs/{run_id}")
def ingestion_run_detail(run_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        row = session.execute(
            text("SELECT * FROM ingestion_runs WHERE id = :id"), {"id": run_id}
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Ingestion run not found")
    return dict(row)


@app.get("/api/candidate-profiles")
def list_candidate_profiles(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    agent_id: Optional[int] = None,
    job_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    user: str = Depends(_auth_user),
) -> dict:
    filters = ["1=1"]
    params: Dict[str, Any] = {}
    if agent_id is not None:
        filters.append("cp.agent_id = :agent_id")
        params["agent_id"] = _resolve_agent_scope(agent_id=agent_id)
    if job_id is not None:
        filters.append("cp.job_id = :job_id")
        params["job_id"] = job_id
    if status:
        filters.append("cp.profile_status = :status")
        params["status"] = status
    if search:
        filters.append(
            "(cp.full_name LIKE :search OR cp.headline LIKE :search OR cp.current_title LIKE :search)"
        )
        params["search"] = f"%{search}%"

    where = " AND ".join(filters)
    offset = (page - 1) * page_size

    with session_scope() as session:
        total = session.execute(
            text(f"SELECT COUNT(1) FROM candidate_profiles cp WHERE {where}"), params
        ).scalar_one()
        rows = session.execute(
            text(
                f"""
                SELECT
                    cp.id, cp.agent_id, cp.job_id, cp.search_run_id, cp.full_name, cp.headline, cp.location_text,
                    cp.current_title, cp.current_company, cp.profile_status, cp.job_seeking_status,
                    cp.job_seeking_score, cp.jd_relevance_score, cp.is_open_to_work,
                    cp.linkedin_profile_url, cp.updated_at_utc,
                    j.title AS job_title, j.company_name AS job_company
                FROM candidate_profiles cp
                LEFT JOIN naukri_jobs j ON j.id = cp.job_id
                WHERE {where}
                ORDER BY cp.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": page_size, "offset": offset},
        ).mappings().all()

    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

@app.get("/api/candidate-profiles/{candidate_id}")
def candidate_profile_detail(
    candidate_id: int,
    agent_id: Optional[int] = None,
    user: str = Depends(_auth_user),
) -> dict:
    resolved_agent = _resolve_agent_scope(agent_id=agent_id) if agent_id is not None else None
    with session_scope() as session:
        row = session.execute(
            text(
                "SELECT * FROM candidate_profiles "
                "WHERE id = :id AND (:agent_id IS NULL OR agent_id = :agent_id)"
            ),
            {"id": candidate_id, "agent_id": resolved_agent},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate profile not found")

    item = dict(row)
    json_fields = [
        "stage_status_json", "stage_errors_json", "experiences_json", "education_json",
        "skills_json", "certifications_json", "activity_json", "contact_points_json",
        "resume_urls_json", "top_evidence_json", "negative_evidence_json",
        "ambiguity_notes_json", "jd_dimension_scores_json",
        "missing_critical_requirements_json", "llm_payload_json",
    ]
    for f in json_fields:
        item[f] = _json_or_none(item.get(f))
    return item


@app.get("/api/runs")
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
    agent_id: Optional[int] = None,
    user: str = Depends(_auth_user),
) -> dict:
    resolved_agent = _resolve_agent_scope(agent_id=agent_id) if agent_id is not None else None
    offset = (page - 1) * page_size
    with session_scope() as session:
        total = session.execute(
            text("SELECT COUNT(1) FROM pipeline_runs WHERE (:agent_id IS NULL OR agent_id = :agent_id)"),
            {"agent_id": resolved_agent},
        ).scalar_one()
        rows = session.execute(
            text(
                """
                SELECT id, pipeline, agent_id, status, started_at_utc, ended_at_utc, triggered_by, message
                FROM pipeline_runs
                WHERE (:agent_id IS NULL OR agent_id = :agent_id)
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"agent_id": resolved_agent, "limit": page_size, "offset": offset},
        ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT id, pipeline, agent_id, status, started_at_utc, ended_at_utc, triggered_by, message, log_text
                FROM pipeline_runs
                WHERE id = :id
                """
            ),
            {"id": run_id},
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")
    return dict(row)


@app.get("/api/agent-runs")
def list_agent_runs(
    limit: int = Query(60, ge=1, le=500),
    agent_id: Optional[int] = None,
    pipeline: Optional[str] = None,
    user: str = Depends(_auth_user),
) -> dict:
    with session_scope() as session:
        q = session.query(AgentRunORM)
        if agent_id is not None:
            q = q.filter(AgentRunORM.agent_id == _resolve_agent_scope(agent_id=agent_id))
        if pipeline:
            q = q.filter(AgentRunORM.pipeline == pipeline)
        rows = (
            q.order_by(AgentRunORM.id.desc())
            .limit(limit)
            .all()
        )
        items = [
            {
                "id": int(r.id),
                "agent_id": int(r.agent_id),
                "pipeline": r.pipeline,
                "status": r.status,
                "started_at_utc": r.started_at_utc,
                "ended_at_utc": r.ended_at_utc,
                "triggered_by": r.triggered_by,
                "run_config": _json_or_none(r.run_config_json),
                "metrics": _json_or_none(r.metrics_json),
                "error_text": r.error_text,
            }
            for r in rows
        ]
    return {"items": items, "count": len(items)}


def _python_exe() -> str:
    env_py = os.getenv("APP_UI_PYTHON", "").strip()
    if env_py:
        return env_py
    candidate = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return str(candidate)
    return sys.executable


# Set once at startup by _run_pipeline_python_check. When non-None, every
# subprocess launch fails fast with this actionable message instead of dying
# deep inside a pipeline with a cryptic ModuleNotFoundError.
_PIPELINE_PYTHON_ERROR: Optional[str] = None

# Packages every pipeline subprocess needs at import time. Kept minimal:
# a missing member here is a hard failure for ALL pipelines, not just one.
_PIPELINE_REQUIRED_IMPORTS = "sqlalchemy, pymysql, dotenv, playwright, chromadb"


def _run_pipeline_python_check() -> None:
    """
    Probe the interpreter that will actually run pipeline subprocesses (which
    may be a DIFFERENT venv than the one running this backend — this machine
    has both .venv and venv). On failure, records an actionable error naming
    the interpreter so the drift is surfaced instead of papered over.
    """
    global _PIPELINE_PYTHON_ERROR
    exe = _python_exe()
    try:
        proc = subprocess.run(
            [exe, "-c", f"import {_PIPELINE_REQUIRED_IMPORTS}"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        _PIPELINE_PYTHON_ERROR = (
            f"Pipeline interpreter '{exe}' could not be executed: {exc}. "
            f"Set APP_UI_PYTHON in .env to the correct python.exe."
        )
        logger.error(_PIPELINE_PYTHON_ERROR)
        return
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        last = lines[-1] if lines else "unknown import error"
        _PIPELINE_PYTHON_ERROR = (
            f"Pipeline interpreter '{exe}' is missing required packages ({last}). "
            f"This machine has two virtualenvs (.venv is used for subprocesses, venv for manual runs) — "
            f"run '{exe} -m pip install -r requirements_clean.txt', or point APP_UI_PYTHON at the venv "
            f"that has everything installed."
        )
        logger.error(_PIPELINE_PYTHON_ERROR)
        return
    logger.info("Pipeline interpreter check passed: %s can import [%s].", exe, _PIPELINE_REQUIRED_IMPORTS)


def _pipeline_script(pipeline: str) -> str:
    mapping = {
        "ingest": ROOT_DIR / "scheduler" / "run_ingest.py",
        "research": ROOT_DIR / "scheduler" / "run_research.py",
        "intelligence": ROOT_DIR / "scheduler" / "run_intelligence.py",
        "candidate_hunt": ROOT_DIR / "scheduler" / "run_candidate_hunt.py",
        "rag_ingest": ROOT_DIR / "scheduler" / "run_rag_ingest.py",
        "reply_check": ROOT_DIR / "scheduler" / "p_reply_handler.py",
    }
    return str(mapping[pipeline])


def _update_run(run_id: int, **kwargs: Any) -> None:
    sets = []
    params: Dict[str, Any] = {"id": run_id}
    for k, v in kwargs.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    if not sets:
        return
    with session_scope() as session:
        session.execute(
            text(f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = :id"),
            params,
        )


def _run_pipeline_async(
    run_id: int,
    pipeline: str,
    *,
    agent_id: Optional[int] = None,
    agent_key: Optional[str] = None,
    runtime_mode: Optional[str] = None,
) -> None:
    cmd = [_python_exe(), _pipeline_script(pipeline)]
    if agent_id is not None:
        cmd.extend(["--agent-id", str(agent_id)])
    elif agent_key:
        cmd.extend(["--agent-key", agent_key])
    if runtime_mode and pipeline == "candidate_hunt":
        cmd.extend(["--runtime-mode", runtime_mode])
    logger.info("Launching pipeline subprocess: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        out, _ = proc.communicate(timeout=60 * 90)
        out = (out or "")[-25000:]
        if proc.returncode == 0:
            logger.info("Pipeline '%s' run_id=%d completed successfully.", pipeline, run_id)
            _update_run(
                run_id,
                status="completed",
                ended_at_utc=_now_utc(),
                message="Completed successfully.",
                log_text=out,
            )
        else:
            logger.error(
                "Pipeline '%s' run_id=%d exited with code %d.",
                pipeline, run_id, proc.returncode,
            )
            _update_run(
                run_id,
                status="failed",
                ended_at_utc=_now_utc(),
                message=f"Exited with code {proc.returncode}",
                log_text=out,
            )
    except Exception as exc:
        logger.error("Pipeline '%s' run_id=%d raised exception: %s", pipeline, run_id, exc)
        _update_run(
            run_id,
            status="failed",
            ended_at_utc=_now_utc(),
            message=f"Run error: {exc}",
            log_text=str(exc),
        )


# ---------------------------------------------------------------------------
# LinkedIn automation: shared lock + backend-owned reply-check scheduler
#
# All LinkedIn/CDP-Chrome-driving operations (the scheduled reply sweep, a
# manual single-prospect check, and a manual LinkedIn send) share one Chrome
# instance and must never run concurrently. The scheduler awaits this lock
# (queueing behind any in-progress manual action); manual endpoints instead
# fail fast with 409 so a user action never blocks on a long sweep.
# ---------------------------------------------------------------------------

_linkedin_automation_lock = asyncio.Lock()


def _run_subprocess_sync(cmd: List[str], timeout_sec: int = 60 * 30) -> Tuple[int, str]:
    if _PIPELINE_PYTHON_ERROR:
        # Fail fast with the actionable startup diagnosis instead of a cryptic
        # ModuleNotFoundError from deep inside the pipeline.
        logger.error("Refusing to launch subprocess: %s", _PIPELINE_PYTHON_ERROR)
        return -1, _PIPELINE_PYTHON_ERROR
    logger.info("Launching subprocess: %s", " ".join(cmd))
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        out, _ = proc.communicate(timeout=timeout_sec)
        return proc.returncode, (out or "")[-25000:]
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
        return -1, "Subprocess timed out."
    except Exception as exc:
        return -1, f"Subprocess error: {exc}"


def _insert_pipeline_run(pipeline: str, agent_id: Optional[int], triggered_by: str, message: str) -> int:
    with session_scope() as session:
        session.execute(
            text(
                """
                INSERT INTO pipeline_runs (pipeline, agent_id, status, started_at_utc, triggered_by, message)
                VALUES (:pipeline, :agent_id, 'running', :started_at, :by, :msg)
                """
            ),
            {
                "pipeline": pipeline,
                "agent_id": agent_id,
                "started_at": _now_utc(),
                "by": triggered_by,
                "msg": message,
            },
        )
        return int(session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())


async def _run_reply_sweep(triggered_by: str = "scheduler") -> None:
    """
    Full sweep over every active LinkedIn conversation across all agents.
    Awaits the automation lock rather than skipping — if a manual action is
    in progress, this run queues and starts the moment the lock frees up,
    so a tick is never silently dropped.
    """
    async with _linkedin_automation_lock:
        run_id = _insert_pipeline_run(
            "reply_check", None, triggered_by, "Scheduled reply-check sweep (all agents)."
        )
        cmd = [_python_exe(), _pipeline_script("reply_check")]
        returncode, out = await asyncio.to_thread(_run_subprocess_sync, cmd, 60 * 60)
        if returncode == 0:
            _update_run(run_id, status="completed", ended_at_utc=_now_utc(), message="Completed successfully.", log_text=out)
        else:
            _update_run(run_id, status="failed", ended_at_utc=_now_utc(), message=f"Exited with code {returncode}", log_text=out)


def _reply_check_interval_seconds() -> int:
    try:
        from config import AppConfig
        reset_runtime_settings_cache()
        minutes = AppConfig().reply_policy.scheduler_interval_minutes
    except Exception:
        logger.warning("Could not read REPLY_CHECK_INTERVAL_MINUTES — defaulting to 15.", exc_info=True)
        minutes = 15
    return max(60, int(minutes) * 60)


async def _reply_scheduler_loop() -> None:
    """
    Fires once immediately, then every REPLY_CHECK_INTERVAL_MINUTES while the
    backend runs. A backend restart re-enters this loop and fires immediately
    again. Never raises — a failed sweep is logged/recorded and the loop
    continues to its next tick.
    """
    while True:
        try:
            await _run_reply_sweep(triggered_by="scheduler")
        except Exception:
            logger.error("Reply scheduler sweep raised an unhandled exception.", exc_info=True)
        await asyncio.sleep(_reply_check_interval_seconds())


@app.post("/api/runs/trigger")
def trigger_run(payload: RunRequest, user: str = Depends(_auth_user)) -> dict:
    now = _now_utc()
    resolved_agent_id = _resolve_agent_scope(agent_id=payload.agent_id, agent_key=payload.agent_key)

    # ── Central agent status guard ─────────────────────────────────────
    with session_scope() as session:
        agent_row = session.query(AgentDefinitionORM).filter_by(id=resolved_agent_id).one_or_none()
    if not agent_row or str(getattr(agent_row, "status", "")).strip().lower() != "active":
        status_val = getattr(agent_row, "status", "unknown") if agent_row else "not_found"
        logger.warning(
            "Pipeline trigger blocked: agent_id=%s status='%s' pipeline='%s' user='%s'",
            resolved_agent_id, status_val, payload.pipeline, user,
        )
        raise HTTPException(
            status_code=409,
            detail=f"Agent is not active (status='{status_val}'). Activate the agent before triggering pipelines.",
        )

    resolved_agent_key = payload.agent_key
    if not resolved_agent_key:
        resolved_agent_key = agent_row.agent_key if agent_row else None

    with session_scope() as session:
        session.execute(
            text(
                """
                INSERT INTO pipeline_runs (pipeline, agent_id, status, started_at_utc, triggered_by, message)
                VALUES (:pipeline, :agent_id, 'running', :started_at, :by, :msg)
                """
            ),
            {
                "pipeline": payload.pipeline,
                "agent_id": resolved_agent_id,
                "started_at": now,
                "by": user,
                "msg": "Triggered from UI",
            },
        )
        run_id = session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()

    logger.info(
        "Pipeline '%s' triggered by user='%s' for agent_id=%s (run_id=%s).",
        payload.pipeline, user, resolved_agent_id, run_id,
    )

    worker = threading.Thread(
        target=_run_pipeline_async,
        kwargs={
            "run_id": int(run_id),
            "pipeline": payload.pipeline,
            "agent_id": resolved_agent_id,
            "agent_key": resolved_agent_key,
            "runtime_mode": payload.runtime_mode,
        },
        daemon=True,
    )
    worker.start()
    return {
        "run_id": int(run_id),
        "status": "running",
        "agent_id": resolved_agent_id,
        "agent_key": resolved_agent_key,
    }

@app.post("/api/agents/{agent_id}/run")
def trigger_agent_run(
    agent_id: int,
    payload: RunRequest,
    user: str = Depends(_auth_user),
) -> dict:
    patched_payload = RunRequest(
        pipeline=payload.pipeline,
        agent_id=agent_id,
        agent_key=payload.agent_key,
        runtime_mode=payload.runtime_mode,
    )
    return trigger_run(patched_payload, user=user)


@app.get("/api/keywords")
def list_keywords(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: str = Depends(_auth_user),
) -> dict:
    offset = (page - 1) * page_size
    with session_scope() as session:
        total = session.execute(text("SELECT COUNT(1) FROM search_keywords")).scalar_one()
        rows = session.execute(
            text(
                """
                SELECT id, keyword, location, max_job_age_days, max_jobs, active,
                       last_run_utc, created_at_utc, updated_at_utc
                FROM search_keywords
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset},
        ).mappings().all()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}

@app.post("/api/keywords")
def create_keyword(payload: KeywordUpsert, user: str = Depends(_auth_user)) -> dict:
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(status_code=400, detail="keyword is required")
    now = _now_utc()
    with session_scope() as session:
        existing = session.execute(
            text("SELECT id FROM search_keywords WHERE keyword=:kw AND (location=:loc OR (location IS NULL AND :loc IS NULL))"),
            {"kw": kw, "loc": payload.location},
        ).mappings().first()
        if existing:
            raise HTTPException(status_code=409, detail="A config with this keyword + location already exists")
        session.execute(
            text(
                """
                INSERT INTO search_keywords (keyword, location, max_job_age_days, max_jobs, active, created_at_utc, updated_at_utc)
                VALUES (:keyword, :location, :age, :max_jobs, :active, :created, :updated)
                """
            ),
            {
                "keyword": kw,
                "location": (payload.location or "").strip() or None,
                "age": payload.max_job_age_days,
                "max_jobs": payload.max_jobs,
                "active": 1 if payload.active else 0,
                "created": now,
                "updated": now,
            },
        )
        row_id = session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
    logger.info("Ingestion config created: id=%s keyword='%s' location='%s' by user='%s'.", row_id, kw, payload.location, user)
    return {"id": int(row_id)}

@app.put("/api/keywords/{keyword_id}")
def update_keyword(keyword_id: int, payload: KeywordUpsert, user: str = Depends(_auth_user)) -> dict:
    kw = (payload.keyword or "").strip()
    if not kw:
        raise HTTPException(status_code=400, detail="keyword is required")
    with session_scope() as session:
        row = session.execute(
            text("SELECT id FROM search_keywords WHERE id=:id"), {"id": keyword_id}
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Ingestion config not found")
        session.execute(
            text(
                """
                UPDATE search_keywords
                SET keyword=:keyword, location=:location, max_job_age_days=:age,
                    max_jobs=:max_jobs, active=:active, updated_at_utc=:updated
                WHERE id=:id
                """
            ),
            {
                "id": keyword_id,
                "keyword": kw,
                "location": (payload.location or "").strip() or None,
                "age": payload.max_job_age_days,
                "max_jobs": payload.max_jobs,
                "active": 1 if payload.active else 0,
                "updated": _now_utc(),
            },
        )
    logger.info("Ingestion config updated: id=%s by user='%s'.", keyword_id, user)
    return {"ok": True}

@app.delete("/api/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        session.execute(text("DELETE FROM search_keywords WHERE id=:id"), {"id": keyword_id})
    return {"ok": True}


@app.get("/api/linkedin-credentials")
def list_linkedin_credentials(user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT id, email, active, priority, last_login_attempt_utc, last_login_success_utc, last_login_failure_reason
                FROM linkedin_credentials
                ORDER BY priority ASC, id ASC
                """
            )
        ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/linkedin-credentials/upsert")
def upsert_linkedin_credentials(payload: CredentialUpsert, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        existing = session.execute(
            text("SELECT id FROM linkedin_credentials WHERE email=:email"),
            {"email": payload.email},
        ).mappings().first()
        now = _now_utc()
        if existing:
            session.execute(
                text(
                    """
                    UPDATE linkedin_credentials
                    SET password=:password, priority=:priority, active=:active, updated_at_utc=:updated
                    WHERE id=:id
                    """
                ),
                {
                    "id": existing["id"],
                    "password": payload.password,
                    "priority": payload.priority,
                    "active": 1 if payload.active else 0,
                    "updated": now,
                },
            )
            return {"id": int(existing["id"]), "updated": True}

        session.execute(
            text(
                """
                INSERT INTO linkedin_credentials (email, password, active, priority, created_at_utc, updated_at_utc)
                VALUES (:email, :password, :active, :priority, :created, :updated)
                """
            ),
            {
                "email": payload.email,
                "password": payload.password,
                "active": 1 if payload.active else 0,
                "priority": payload.priority,
                "created": now,
                "updated": now,
            },
        )
        new_id = session.execute(text("SELECT LAST_INSERT_ID()")).scalar_one()
        return {"id": int(new_id), "created": True}


@app.post("/api/linkedin-credentials/{cred_id}/deactivate")
def deactivate_linkedin_credential(cred_id: int, user: str = Depends(_auth_user)) -> dict:
    with session_scope() as session:
        session.execute(
            text("UPDATE linkedin_credentials SET active=0, updated_at_utc=:ts WHERE id=:id"),
            {"id": cred_id, "ts": _now_utc()},
        )
    return {"ok": True}


@app.get("/api/settings")
def list_settings(user: str = Depends(_auth_user)) -> dict:
    # Keys superseded by search_keywords CRUD — hide from flat settings view
    _SUPERSEDED_KEYS = {"INGEST_KEYWORDS", "INGEST_LOCATION", "INGEST_MAX_JOB_AGE_DAYS", "INGEST_MAX_JOBS_PER_KEYWORD"}

    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT setting_key, setting_value, description, config_type, updated_at_utc, updated_by
                FROM app_settings
                ORDER BY setting_key
                """
            )
        ).mappings().all()
    db_rows: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("setting_key") or "").strip()
        if not key or is_secret_setting_key(key) or key in _SUPERSEDED_KEYS:
            continue
        db_rows[key] = {
            "setting_key": key,
            "setting_value": _json_or_none(row.get("setting_value")),
            "description": row.get("description"),
            "config_type": row.get("config_type"),
            "updated_at_utc": row.get("updated_at_utc"),
            "updated_by": row.get("updated_by"),
            "source": "db",
        }

    items: List[Dict[str, Any]] = []
    for key in sorted(_SETTING_CATALOG_BY_KEY.keys()):
        if key in _SUPERSEDED_KEYS:
            continue
        catalog_item = _SETTING_CATALOG_BY_KEY[key]
        from_db = db_rows.pop(key, None)
        if from_db:
            if not from_db.get("description"):
                from_db["description"] = catalog_item.get("description")
            if not from_db.get("config_type"):
                from_db["config_type"] = catalog_item.get("config_type") or get_config_type_for_key(key)
            items.append(from_db)
            continue
        items.append(
            {
                "setting_key": key,
                "setting_value": catalog_item.get("default"),
                "description": catalog_item.get("description"),
                "config_type": catalog_item.get("config_type") or get_config_type_for_key(key),
                "updated_at_utc": None,
                "updated_by": None,
                "source": "catalog_default",
            }
        )

    for key in sorted(db_rows.keys()):
        if not db_rows[key].get("config_type"):
            db_rows[key]["config_type"] = get_config_type_for_key(key)
        items.append(db_rows[key])

    return {"items": items}


def _validate_setting_value(key: str, value: Any) -> None:
    """
    Reject values the runtime would otherwise silently clamp, so a save that
    cannot take effect as typed fails loudly instead of looking successful.
    """
    if key != "REPLY_CHECK_INTERVAL_MINUTES":
        return
    minutes: Optional[float] = None
    if not isinstance(value, bool):
        try:
            minutes = float(value)
        except (TypeError, ValueError):
            minutes = None
    if minutes is None or minutes != int(minutes):
        raise HTTPException(
            status_code=400,
            detail="REPLY_CHECK_INTERVAL_MINUTES must be a whole number of minutes.",
        )
    if minutes < 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "REPLY_CHECK_INTERVAL_MINUTES must be at least 1 — the reply scheduler "
                "enforces a 60-second floor and would silently clamp anything lower."
            ),
        )


@app.put("/api/settings")
def upsert_setting(payload: SettingUpdate, user: str = Depends(_auth_user)) -> dict:
    key = (payload.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Setting key is required")
    if is_secret_setting_key(key):
        raise HTTPException(status_code=400, detail=f"Setting '{key}' is secret and env-only.")
    _validate_setting_value(key, payload.value)

    default_description = None
    default_config_type = get_config_type_for_key(key)
    catalog_item = _SETTING_CATALOG_BY_KEY.get(key)
    if catalog_item:
        default_description = str(catalog_item.get("description") or "").strip() or None
        default_config_type = str(catalog_item.get("config_type") or default_config_type).strip().lower() or default_config_type

    payload_description = (payload.description or "").strip() or None
    payload_config_type = (payload.config_type or "").strip().lower() or None
    value_json = None
    try:
        value_json = json.dumps(payload.value, ensure_ascii=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Value is not JSON serializable: {exc}") from exc

    with session_scope() as session:
        exists = session.execute(
            text("SELECT setting_key, description, config_type FROM app_settings WHERE setting_key=:k"),
            {"k": key},
        ).mappings().first()
        if exists:
            current_description = str(exists.get("description") or "").strip() or None
            current_config_type = str(exists.get("config_type") or "").strip().lower() or None
            final_description = payload_description or default_description or current_description
            final_config_type = payload_config_type or default_config_type or current_config_type
            session.execute(
                text(
                    """
                    UPDATE app_settings
                    SET setting_value=:v, description=:d, config_type=:ct, updated_at_utc=:u, updated_by=:by
                    WHERE setting_key=:k
                    """
                ),
                {
                    "k": key,
                    "v": value_json,
                    "d": final_description,
                    "ct": final_config_type,
                    "u": _now_utc(),
                    "by": user,
                },
            )
        else:
            final_description = payload_description or default_description
            final_config_type = payload_config_type or default_config_type
            session.execute(
                text(
                    """
                    INSERT INTO app_settings (setting_key, setting_value, description, config_type, updated_at_utc, updated_by)
                    VALUES (:k, :v, :d, :ct, :u, :by)
                    """
                ),
                {
                    "k": key,
                    "v": value_json,
                    "d": final_description,
                    "ct": final_config_type,
                    "u": _now_utc(),
                    "by": user,
                },
            )
    reset_runtime_settings_cache()
    logger.info("Setting '%s' updated by user='%s'.", key, user)
    return {"ok": True, "key": key}

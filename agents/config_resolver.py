from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ValidationError, field_validator

from config import AppConfig
from db import (
    AgentDefinitionORM,
    AgentKeywordORM,
    AgentProfileORM,
    AgentToolORM,
    resolve_agent_id,
    session_scope,
)

logger = logging.getLogger("prospect.agent.config_resolver")


# ---------------------------------------------------------------------------
# Pydantic schema for validated resolved config
# ---------------------------------------------------------------------------

class AgentProfileSchema(BaseModel):
    """
    Validation schema applied to the resolved agent profile before runtime use.
    Provides default-fallback values so downstream code never receives None
    for critical prompt fields.
    """

    persona_title: str = "Sales Consultant"
    domain_focus: str = ""
    service_offering: str = ""
    sales_objective: str = ""
    target_buyer_roles: List[str] = field(default_factory=list)
    value_outcomes: List[str] = field(default_factory=list)
    runtime_mode: str = "deterministic"

    @field_validator("runtime_mode")
    @classmethod
    def validate_runtime_mode(cls, v: str) -> str:
        allowed = {"deterministic", "autonomous"}
        cleaned = (v or "").strip().lower()
        if cleaned not in allowed:
            logger.warning(
                "Invalid runtime_mode '%s'. Falling back to 'deterministic'.", v
            )
            return "deterministic"
        return cleaned

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_obj(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _json_list(raw: Optional[str]) -> List[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Resolved config dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentResolvedConfig:
    agent_id: int
    agent_key: str
    agent_name: str
    agent_type: str
    status: str
    runtime_mode: str
    prompt_context: Dict[str, Any] = field(default_factory=dict)
    target_buyer_roles: List[str] = field(default_factory=list)
    value_outcomes: List[str] = field(default_factory=list)
    icp_rules: Dict[str, Any] = field(default_factory=dict)
    targeting_policy: Dict[str, Any] = field(default_factory=dict)
    pipeline_policy: Dict[str, Any] = field(default_factory=dict)
    channel_policy: Dict[str, Any] = field(default_factory=dict)
    runtime_policy: Dict[str, Any] = field(default_factory=dict)
    keywords_by_type: Dict[str, List[str]] = field(default_factory=dict)
    enabled_tools: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class AgentConfigResolver:
    """
    Resolve agent-level runtime context by combining:
      - agent definition/profile from DB
      - agent keywords/tools from DB
      - global app runtime defaults

    All resolved values are validated via Pydantic before being returned so
    downstream consumers always receive well-typed, non-None values.
    """

    def __init__(self, app_config: AppConfig) -> None:
        self._cfg = app_config

    def resolve(
        self,
        *,
        agent_id: Optional[int] = None,
        agent_key: Optional[str] = None,
    ) -> AgentResolvedConfig:
        resolved_id = resolve_agent_id(
            agent_id=agent_id,
            agent_key=agent_key,
            default_agent_key=self._cfg.agent_runtime.default_agent_key,
        )

        logger.info(
            "Resolving agent config: requested_id=%s, requested_key=%s → resolved_id=%s",
            agent_id,
            agent_key,
            resolved_id,
        )

        with session_scope() as session:
            agent = session.query(AgentDefinitionORM).filter_by(id=resolved_id).one()
            profile = (
                session.query(AgentProfileORM)
                .filter_by(agent_id=resolved_id, is_current=True)
                .order_by(AgentProfileORM.version.desc(), AgentProfileORM.id.desc())
                .first()
            )
            keywords = (
                session.query(AgentKeywordORM)
                .filter_by(agent_id=resolved_id, active=True)
                .order_by(AgentKeywordORM.keyword_type.asc(), AgentKeywordORM.weight.desc())
                .all()
            )
            tools = (
                session.query(AgentToolORM)
                .filter_by(agent_id=resolved_id, enabled=True)
                .order_by(AgentToolORM.weight.desc(), AgentToolORM.tool_name.asc())
                .all()
            )

            # ------------------------------------------------------------------
            # Build raw profile data
            # ------------------------------------------------------------------
            prompt_context: Dict[str, Any] = {}
            target_buyer_roles: List[str] = []
            value_outcomes: List[str] = []
            icp_rules: Dict[str, Any] = {}
            targeting_policy: Dict[str, Any] = {}
            pipeline_policy: Dict[str, Any] = {}
            channel_policy: Dict[str, Any] = {}
            runtime_policy: Dict[str, Any] = {}

            if profile:
                # Start from stored prompt_profile_json as the base.
                prompt_context = _json_obj(profile.prompt_profile_json)

                # Always override with live DB fields so UI saves take effect immediately.
                for field_key, field_val in [
                    ("persona_title", profile.persona_title),
                    ("domain_focus", profile.domain_focus),
                    ("service_offering", profile.service_offering),
                    ("sales_objective", profile.sales_objective),
                ]:
                    if field_val:
                        prompt_context[field_key] = field_val

                target_buyer_roles = [str(x) for x in _json_list(profile.target_buyer_roles_json)]
                value_outcomes = [str(x) for x in _json_list(profile.value_outcomes_json)]
                icp_rules = _json_obj(profile.icp_rules_json)
                targeting_policy = _json_obj(profile.targeting_policy_json)
                pipeline_policy = _json_obj(profile.pipeline_policy_json)
                channel_policy = _json_obj(profile.channel_policy_json)
                runtime_policy = _json_obj(profile.runtime_policy_json)

                # Ensure buyer roles and value outcomes are surfaced in prompt_context.
                if target_buyer_roles:
                    prompt_context["target_buyer_roles"] = ", ".join(target_buyer_roles)
                if value_outcomes:
                    prompt_context["value_outcomes"] = ", ".join(value_outcomes)

            else:
                logger.warning(
                    "No current profile found for agent_id=%s ('%s'). "
                    "Using empty profile — prompts will use static fallback values.",
                    resolved_id,
                    agent.agent_key,
                )

            # Agent-level identity always present.
            prompt_context["agent_name"] = agent.name or ""
            prompt_context["agent_type"] = agent.agent_type or ""
            prompt_context["agent_key"] = agent.agent_key or ""

            # ------------------------------------------------------------------
            # Validate and normalize critical fields via Pydantic.
            # ------------------------------------------------------------------
            try:
                validated = AgentProfileSchema(
                    persona_title=prompt_context.get("persona_title", ""),
                    domain_focus=prompt_context.get("domain_focus", ""),
                    service_offering=prompt_context.get("service_offering", ""),
                    sales_objective=prompt_context.get("sales_objective", ""),
                    target_buyer_roles=target_buyer_roles,
                    value_outcomes=value_outcomes,
                    runtime_mode=str(
                        runtime_policy.get("mode")
                        or self._cfg.agent_runtime.mode
                        or "deterministic"
                    ),
                )
            except ValidationError as exc:
                logger.error(
                    "Agent profile validation failed for agent_id=%s: %s. "
                    "Using safe defaults.",
                    resolved_id,
                    exc,
                )
                validated = AgentProfileSchema()

            runtime_mode = validated.runtime_mode

            # Back-propagate validated values into prompt_context so templates
            # always receive the cleaned, non-empty values.
            for attr in ("persona_title", "domain_focus", "service_offering", "sales_objective"):
                val = getattr(validated, attr, "")
                if val:
                    prompt_context[attr] = val

            # ------------------------------------------------------------------
            # Build keyword map — injected into prompt_context for template use.
            # ------------------------------------------------------------------
            keywords_by_type: Dict[str, List[str]] = {}
            for item in keywords:
                ktype = (item.keyword_type or "general").strip().lower()
                keywords_by_type.setdefault(ktype, [])
                keywords_by_type[ktype].append(item.keyword)

            if keywords_by_type:
                prompt_context["keywords_by_type"] = keywords_by_type
                all_kws = [kw for kws in keywords_by_type.values() for kw in kws]
                if all_kws:
                    prompt_context["active_keywords"] = ", ".join(all_kws)

            enabled_tools = [t.tool_name for t in tools]

            logger.info(
                "Agent config resolved: agent_id=%s key='%s' mode=%s "
                "persona='%s' keywords=%d tools=%d profile_version=%s",
                resolved_id,
                agent.agent_key,
                runtime_mode,
                prompt_context.get("persona_title", ""),
                sum(len(v) for v in keywords_by_type.values()),
                len(enabled_tools),
                profile.version if profile else "none",
            )

            return AgentResolvedConfig(
                agent_id=resolved_id,
                agent_key=agent.agent_key,
                agent_name=agent.name,
                agent_type=agent.agent_type,
                status=agent.status,
                runtime_mode=runtime_mode,
                prompt_context=prompt_context,
                target_buyer_roles=target_buyer_roles,
                value_outcomes=value_outcomes,
                icp_rules=icp_rules,
                targeting_policy=targeting_policy,
                pipeline_policy=pipeline_policy,
                channel_policy=channel_policy,
                runtime_policy=runtime_policy,
                keywords_by_type=keywords_by_type,
                enabled_tools=enabled_tools,
            )

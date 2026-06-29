"""
Central execution guard.
Import and call require_agent_active() at the top of EVERY pipeline entry point.
This is the single, non-bypassable enforcement point for agent status.
"""
from __future__ import annotations

import logging
from typing import Optional

from db import AgentDefinitionORM, session_scope

logger = logging.getLogger("prospect.agent.guard")

_ACTIVE_STATUS = "active"


class AgentInactiveError(RuntimeError):
    """Raised when execution is blocked because the agent is not active."""


def require_agent_active(agent_id: int, pipeline: str = "") -> None:
    """
    Block execution if agent.status != 'active'.

    Raises:
        AgentInactiveError: always, when status is not active.
    """
    with session_scope() as session:
        row = (
            session.query(AgentDefinitionORM.status, AgentDefinitionORM.agent_key)
            .filter_by(id=agent_id)
            .one_or_none()
        )

    if row is None:
        msg = f"Agent id={agent_id} not found — execution blocked."
        logger.error("Agent inactive | pipeline=%s | %s", pipeline, msg)
        raise AgentInactiveError(msg)

    status = str(row[0] or "").strip().lower()
    if status != _ACTIVE_STATUS:
        msg = (
            f"Agent '{row[1]}' (id={agent_id}) status='{status}' — "
            f"execution blocked for pipeline '{pipeline}'."
        )
        logger.warning("Agent inactive | pipeline=%s | %s", pipeline, msg)
        raise AgentInactiveError(msg)

    logger.debug("Agent guard passed: agent_id=%s pipeline=%s", agent_id, pipeline)
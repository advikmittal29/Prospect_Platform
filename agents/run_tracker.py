from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from db import AgentRunORM, session_scope
from utils.logging import build_logger

logger = build_logger("prospect.agent.run_tracker")


class AgentRunTracker(AbstractContextManager["AgentRunTracker"]):
    """
    Context manager to record agent pipeline runs in dbo.agent_runs.
    """

    def __init__(
        self,
        *,
        agent_id: int,
        pipeline: str,
        triggered_by: Optional[str] = None,
        run_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.agent_id = int(agent_id)
        self.pipeline = (pipeline or "unknown").strip()
        self.triggered_by = (triggered_by or "scheduler").strip()
        self.run_config = run_config or {}
        self.run_id: Optional[int] = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def __enter__(self) -> "AgentRunTracker":
        with session_scope() as session:
            row = AgentRunORM(
                agent_id=self.agent_id,
                pipeline=self.pipeline,
                status="running",
                started_at_utc=self._now(),
                triggered_by=self.triggered_by,
                run_config_json=json.dumps(self.run_config, ensure_ascii=False),
            )
            session.add(row)
            session.flush()
            self.run_id = int(row.id)
        return self

    def mark_completed(self, metrics: Optional[Dict[str, Any]] = None) -> None:
        if self.run_id is None:
            return
        with session_scope() as session:
            row = session.query(AgentRunORM).filter_by(id=self.run_id).one_or_none()
            if not row:
                return
            row.status = "completed"
            row.ended_at_utc = self._now()
            row.metrics_json = json.dumps(metrics or {}, ensure_ascii=False)
            row.error_text = None

    def mark_failed(self, error_text: str, metrics: Optional[Dict[str, Any]] = None) -> None:
        if self.run_id is None:
            return
        with session_scope() as session:
            row = session.query(AgentRunORM).filter_by(id=self.run_id).one_or_none()
            if not row:
                return
            row.status = "failed"
            row.ended_at_utc = self._now()
            row.metrics_json = json.dumps(metrics or {}, ensure_ascii=False)
            row.error_text = (error_text or "unknown")[:4000]

    def __exit__(self, exc_type, exc, tb) -> Optional[bool]:
        if exc is None:
            return None
        self.mark_failed(str(exc))
        logger.error(
            "Agent run failed (agent_id=%s, pipeline=%s, run_id=%s): %s",
            self.agent_id,
            self.pipeline,
            self.run_id,
            exc,
        )
        return None

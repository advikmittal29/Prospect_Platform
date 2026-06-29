from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from agents.runtime.graph_builder import GraphBuilder
from agents.runtime.mode import RuntimeMode, normalize_runtime_mode
from agents.runtime.policy import RuntimePolicy
from agents.runtime.tool_registry import ToolRegistry
from utils.logging import build_logger

logger = build_logger("prospect.agent.runtime.executor")


@dataclass
class ExecutionResult:
    ok: bool
    mode_used: str
    backend: str
    payload: Dict[str, Any]
    error: Optional[str] = None


class RuntimeExecutor:
    def __init__(self, tool_registry: Optional[ToolRegistry] = None) -> None:
        self._tools = tool_registry or ToolRegistry()
        self._graph_builder = GraphBuilder(self._tools)

    def execute(
        self,
        *,
        policy: RuntimePolicy,
        context: Dict[str, Any],
        deterministic_handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        planner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> ExecutionResult:
        mode = normalize_runtime_mode(policy.mode)
        if mode == RuntimeMode.DETERMINISTIC:
            payload = deterministic_handler(context)
            return ExecutionResult(
                ok=True,
                mode_used=RuntimeMode.DETERMINISTIC.value,
                backend="deterministic",
                payload=payload if isinstance(payload, dict) else {"result": payload},
            )

        planner_fn = planner or (lambda s: {"goal": "execute_tools", "state_keys": sorted(s.keys())})
        built = self._graph_builder.build(
            planner=planner_fn,
            deterministic_fallback=deterministic_handler,
        )
        try:
            payload = built.runner(context)
            return ExecutionResult(
                ok=True,
                mode_used=RuntimeMode.AUTONOMOUS.value,
                backend=built.backend,
                payload=payload if isinstance(payload, dict) else {"result": payload},
            )
        except Exception as exc:
            if policy.allow_fallback:
                logger.warning("Autonomous runtime failed; falling back to deterministic mode: %s", exc)
                payload = deterministic_handler(context)
                return ExecutionResult(
                    ok=True,
                    mode_used=RuntimeMode.DETERMINISTIC.value,
                    backend="fallback_after_error",
                    payload=payload if isinstance(payload, dict) else {"result": payload},
                )
            return ExecutionResult(
                ok=False,
                mode_used=RuntimeMode.AUTONOMOUS.value,
                backend=built.backend,
                payload={},
                error=str(exc),
            )

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from agents.runtime.tool_registry import ToolRegistry


GraphRunner = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class BuiltGraph:
    runner: GraphRunner
    backend: str


class GraphBuilder:
    """
    Builds an autonomous graph runner.
    If LangGraph is unavailable, returns a deterministic fallback runner.
    """

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tools = tool_registry

    def build(
        self,
        *,
        planner: Callable[[Dict[str, Any]], Dict[str, Any]],
        deterministic_fallback: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> BuiltGraph:
        try:
            # Optional dependency: keep imports local so deterministic mode has zero impact.
            from langgraph.graph import END, START, StateGraph  # type: ignore
        except Exception:
            return BuiltGraph(runner=deterministic_fallback, backend="fallback")

        # Minimal graph scaffold that can be expanded safely.
        def plan_node(state: Dict[str, Any]) -> Dict[str, Any]:
            plan = planner(state)
            state = dict(state)
            state["plan"] = plan
            return state

        def execute_node(state: Dict[str, Any]) -> Dict[str, Any]:
            # Current phase: execute deterministic fallback while preserving graph state shape.
            result = deterministic_fallback(state)
            state = dict(state)
            state["result"] = result
            return state

        graph = StateGraph(dict)
        graph.add_node("plan", plan_node)
        graph.add_node("execute", execute_node)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", END)
        compiled = graph.compile()

        def _runner(payload: Dict[str, Any]) -> Dict[str, Any]:
            out = compiled.invoke(payload)
            result = out.get("result")
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": out}

        return BuiltGraph(runner=_runner, backend="langgraph")

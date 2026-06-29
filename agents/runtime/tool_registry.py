from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


ToolHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: ToolHandler
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self) -> Dict[str, ToolSpec]:
        return dict(self._tools)

    def invoke(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        spec = self.get(name)
        if spec is None:
            return {
                "ok": False,
                "error_type": "unknown_tool",
                "error": f"Tool '{name}' is not registered.",
            }
        return spec.handler(payload)

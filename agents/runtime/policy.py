from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class RuntimePolicy:
    mode: str = "deterministic"
    max_tool_calls_per_run: int = 50
    max_run_minutes: int = 90
    allow_fallback: bool = True
    allowed_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, object] = field(default_factory=dict)

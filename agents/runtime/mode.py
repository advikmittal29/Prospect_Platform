from __future__ import annotations

from enum import Enum


class RuntimeMode(str, Enum):
    DETERMINISTIC = "deterministic"
    AUTONOMOUS = "autonomous"


def normalize_runtime_mode(value: str | None) -> RuntimeMode:
    raw = (value or "").strip().lower()
    if raw == RuntimeMode.AUTONOMOUS.value:
        return RuntimeMode.AUTONOMOUS
    return RuntimeMode.DETERMINISTIC

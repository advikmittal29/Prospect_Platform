from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prospect.prompt_loader")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_PROMPT_DIR = _PROJECT_ROOT / "prompts"
_DEFAULT_PROMPT_PROFILE_FILE = "sales_profile.json"
_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_prompt_dir() -> Path:
    raw = os.getenv("PROMPT_DIR", "").strip()
    if not raw:
        return _DEFAULT_PROMPT_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _resolve_prompt_profile_path() -> Path:
    raw = os.getenv("PROMPT_PROFILE_FILE", "").strip()
    if not raw:
        raw = _DEFAULT_PROMPT_PROFILE_FILE
    path = Path(raw)
    if not path.is_absolute():
        path = _resolve_prompt_dir() / path
    return path


def _sanitize_prompt_name(prompt_name: str) -> str:
    name = (prompt_name or "").strip()
    if not name:
        raise ValueError("prompt_name must be a non-empty string.")
    if name.endswith(".txt"):
        name = name[:-4]
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Invalid prompt_name: {prompt_name!r}")
    return name


@lru_cache(maxsize=256)
def _load_prompt_cached(prompt_dir: str, prompt_name: str) -> str:
    path = Path(prompt_dir) / f"{prompt_name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=8)
def _load_prompt_profile_cached(profile_path: str) -> Dict[str, Any]:
    path = Path(profile_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid prompt profile JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Prompt profile must be a JSON object: {path}")
    return data


def load_prompt(prompt_name: str) -> str:
    """
    Load prompt content from `<PROMPT_DIR or ./prompts>/{prompt_name}.txt`.
    Uses in-process caching to avoid repeated disk I/O.
    """
    clean_name = _sanitize_prompt_name(prompt_name)
    prompt_dir = _resolve_prompt_dir()
    return _load_prompt_cached(str(prompt_dir), clean_name)


def load_prompt_profile() -> Dict[str, Any]:
    """
    Load reusable prompt profile context (domain expertise, sales goals, etc.).
    Defaults to `<PROMPT_DIR>/sales_profile.json`.
    """
    return dict(_load_prompt_profile_cached(str(_resolve_prompt_profile_path())))


def _build_keyword_block(keywords_by_type: Dict[str, List[str]]) -> str:
    """
    Convert a keywords_by_type dict into a structured, human-readable block
    that can be injected into prompt templates.

    Example output:
        Job titles: Software Engineer, Data Scientist
        Skills: Python, Machine Learning
        Exclude: intern, trainee
    """
    if not keywords_by_type:
        return ""

    label_map = {
        "job_title": "Job titles",
        "seniority": "Seniority levels",
        "skill": "Key skills",
        "industry": "Target industries",
        "exclude": "Exclusions",
        "general": "Keywords",
    }
    lines: List[str] = []
    for ktype in sorted(keywords_by_type.keys()):
        kws = keywords_by_type[ktype]
        if not kws:
            continue
        label = label_map.get(ktype, ktype.replace("_", " ").title())
        lines.append(f"{label}: {', '.join(kws)}")
    return "\n".join(lines)


def _build_persona_block(context: Dict[str, Any]) -> str:
    """
    Produce a structured persona summary paragraph from key profile fields.
    Used as a coherent narrative block rather than raw field substitution.
    """
    parts: List[str] = []
    persona = context.get("persona_title", "")
    domain = context.get("domain_focus", "")
    offering = context.get("service_offering", "")
    objective = context.get("sales_objective", "")
    outcomes = context.get("value_outcomes", "")
    buyer_roles = context.get("target_buyer_roles", "")

    if persona:
        parts.append(f"You are acting as: {persona}.")
    if domain:
        parts.append(f"Domain specialization: {domain}.")
    if offering:
        parts.append(f"Primary service offering: {offering}.")
    if objective:
        parts.append(f"Sales objective: {objective}.")
    if outcomes:
        parts.append(f"Key value outcomes to emphasize: {outcomes}.")
    if buyer_roles:
        parts.append(f"Target decision-maker roles: {buyer_roles}.")
    return " ".join(parts)


def render_prompt(
    prompt_name: str,
    agent_context: Optional[Dict[str, Any]] = None,
    **overrides: Any,
) -> str:
    """
    Render a prompt template with shared profile context + runtime overrides.

    Merge priority (highest wins):
      1. Keyword arguments (overrides)
      2. agent_context (live DB-sourced agent config)
      3. sales_profile.json (static file defaults)

    Special synthetic keys injected automatically:
      - {keyword_block}    — structured keyword section from keywords_by_type
      - {persona_block}    — coherent persona narrative paragraph

    Unknown placeholders are left untouched so templates can have optional
    sections without raising errors.
    """
    template = load_prompt(prompt_name)

    # Layer 1: static file defaults (lowest priority).
    context: Dict[str, Any] = load_prompt_profile()

    # Layer 2: live agent config from DB.
    if agent_context:
        for k, v in agent_context.items():
            if v is not None:
                context[k] = v

    # Layer 3: call-site overrides (highest priority).
    for k, v in overrides.items():
        if v is not None:
            context[k] = v

    # ------------------------------------------------------------------
    # Synthesize structured blocks from resolved context.
    # ------------------------------------------------------------------
    keywords_by_type = context.get("keywords_by_type")
    if isinstance(keywords_by_type, dict) and keywords_by_type:
        context.setdefault("keyword_block", _build_keyword_block(keywords_by_type))
    else:
        context.setdefault("keyword_block", "")

    context.setdefault("persona_block", _build_persona_block(context))

    # ------------------------------------------------------------------
    # Validate that critical fields used by most prompts are present.
    # ------------------------------------------------------------------
    _warn_missing_fields(prompt_name, context, template)

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            return match.group(0)  # Leave unknown placeholders intact.
        value = context[key]
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value) if value is not None else ""

    rendered = _PLACEHOLDER_PATTERN.sub(repl, template).strip()

    logger.debug(
        "Rendered prompt '%s': %d chars, context_keys=%s",
        prompt_name,
        len(rendered),
        sorted(context.keys()),
    )
    return rendered


def _warn_missing_fields(prompt_name: str, context: Dict[str, Any], template: str) -> None:
    """
    Warn when a placeholder referenced in the template has no value in context.
    This makes configuration gaps visible in logs without crashing.
    """
    CRITICAL_FIELDS = {
        "persona_title", "domain_focus", "service_offering",
        "sales_objective", "value_outcomes",
    }
    placeholders_in_template = set(_PLACEHOLDER_PATTERN.findall(template))
    for field in CRITICAL_FIELDS & placeholders_in_template:
        val = context.get(field)
        if not val:
            logger.warning(
                "Prompt '%s' references '{%s}' but no value is configured. "
                "Update the agent's Sales Profile in the UI.",
                prompt_name,
                field,
            )


def clear_prompt_cache() -> None:
    """Clear the in-process prompt cache."""
    _load_prompt_cached.cache_clear()
    _load_prompt_profile_cached.cache_clear()
    logger.debug("Prompt cache cleared.")

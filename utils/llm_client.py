"""
Centralized LLM call wrapper with primary-then-fallback logic.

Usage:
    from utils.llm_client import llm_complete

    response_text = llm_complete(system=..., user=..., config=app.llm)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from config import LLMConfig

logger = logging.getLogger("prospect.utils.llm_client")


def _get_fallback_config(primary: LLMConfig) -> Optional[Dict[str, Any]]:
    if not primary.fallback_enabled:
        return None
    if not primary.fallback_provider or not primary.fallback_model:
        return None
    return {
        "provider": primary.fallback_provider,
        "model": primary.fallback_model,
        "api_key": primary.api_key,
        "temperature": primary.temperature,
        "max_tokens": primary.max_tokens,
        "timeout_seconds": primary.timeout_seconds,
    }


def _call_provider(
    *,
    provider: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    timeout_seconds: int,
) -> str:
    provider = provider.strip().lower()

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
        )
        resp = m.generate_content(user)
        return resp.text or ""

    if provider == "groq":
        from groq import Groq
        client = Groq(api_key=api_key, timeout=timeout_seconds)
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    raise ValueError(f"Unknown LLM provider: '{provider}'")


def llm_complete(
    *,
    system: str,
    user: str,
    config: LLMConfig,
    caller: str = "",
) -> str:
    """
    Call the primary LLM. On failure, retry up to max_retries.
    If still failing and fallback is enabled, attempt fallback provider once.

    Returns the model's text response.
    Raises the last exception if all attempts (primary + fallback) fail.
    """
    tag = f"[{caller}] " if caller else ""
    last_exc: Optional[Exception] = None

    # ── Primary attempts ────────────────────────────────────────────────
    for attempt in range(1, config.max_retries + 1):
        try:
            result = _call_provider(
                provider=config.provider,
                model=config.model,
                api_key=config.api_key,
                system=system,
                user=user,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                timeout_seconds=config.timeout_seconds,
            )
            logger.debug("%sPrimary LLM succeeded on attempt %d.", tag, attempt)
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "%sPrimary LLM attempt %d/%d failed: %s",
                tag, attempt, config.max_retries, exc,
            )
            if attempt < config.max_retries:
                time.sleep(config.retry_delay_seconds)

    # ── Fallback attempt ────────────────────────────────────────────────
    fb = _get_fallback_config(config)
    if fb:
        logger.warning(
            "%sPrimary LLM exhausted %d retries. Attempting fallback provider='%s' model='%s'.",
            tag, config.max_retries, fb["provider"], fb["model"],
        )
        try:
            result = _call_provider(
                provider=fb["provider"],
                model=fb["model"],
                api_key=fb["api_key"],
                system=system,
                user=user,
                temperature=fb["temperature"],
                max_tokens=fb["max_tokens"],
                timeout_seconds=fb["timeout_seconds"],
            )
            logger.info("%sFallback LLM succeeded: provider='%s'.", tag, fb["provider"])
            return result
        except Exception as fb_exc:
            logger.error("%sFallback LLM also failed: %s", tag, fb_exc)
            last_exc = fb_exc

    raise RuntimeError(
        f"{tag}All LLM attempts failed. Last error: {last_exc}"
    ) from last_exc
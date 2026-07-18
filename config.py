from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


DEFAULT_DB_URL = "mysql+pymysql://root:@localhost/prospect_db?charset=utf8mb4"

# Keys that must remain environment-only (secrets/credentials).
SECRET_SETTING_KEYS = {
    "DB_URL",
    "LLM_API_KEY",
    "APP_UI_ADMIN_PASS",
    "APP_UI_JWT_SECRET",
}

# Non-secret settings that can be controlled from dbo.app_settings.
_SETTING_CATALOG: List[Dict[str, Any]] = [
    {"key": "DB_ECHO", "default": False, "description": "Enable SQLAlchemy SQL logging."},
    {"key": "DB_POOL_SIZE", "default": 5, "description": "Database connection pool size."},
    {"key": "DB_POOL_RECYCLE", "default": 1800, "description": "Connection recycle time in seconds."},
    {"key": "BROWSER_HEADLESS", "default": False, "description": "Run browser in headless mode."},
    {"key": "BROWSER_FAST_MODE", "default": True, "description": "Use speed-optimized browser behavior."},
    {"key": "BROWSER_NAV_TIMEOUT_MS", "default": 15000, "description": "Browser navigation timeout in milliseconds."},
    {"key": "BROWSER_ACTION_TIMEOUT_MS", "default": 6000, "description": "Browser action timeout in milliseconds."},
    {"key": "BROWSER_SCROLL_PAUSE_SEC", "default": 0.25, "description": "Pause between scroll steps in seconds."},
    {"key": "BROWSER_LISTING_SCROLL_ROUNDS", "default": 6, "description": "Scroll rounds for listing pages."},
    {"key": "BROWSER_POLITE_SLEEP_SEC", "default": 0.1, "description": "Polite delay between browser operations in seconds."},
    {"key": "BROWSER_DETAIL_PAGE_DELAY_SEC", "default": 0.4, "description": "Delay before opening each detail page in seconds."},
    {"key": "BROWSER_VIEWPORT_W", "default": 1440, "description": "Browser viewport width in pixels."},
    {"key": "BROWSER_VIEWPORT_H", "default": 900, "description": "Browser viewport height in pixels."},
    {"key": "CHROME_CDP_ATTACH", "default": True, "description": "Attach to an existing Chrome CDP endpoint."},
    {"key": "CHROME_CDP_URL", "default": "http://127.0.0.1:9223", "description": "Chrome DevTools Protocol endpoint URL."},
    {"key": "CHROME_EXE", "default": r"C:\Program Files\Google\Chrome\Application\chrome.exe", "description": "Path to Chrome executable."},
    {"key": "CHROME_USER_DATA_DIR", "default": r"C:\Users\Public\chrome-cdp-profile", "description": "Chrome user data directory for persistent sessions."},
    {"key": "CHROME_HEADLESS", "default": False, "description": "Start managed Chrome in headless mode."},
    {"key": "CHROME_STARTUP_WAIT_SEC", "default": 2.5, "description": "Wait time after Chrome launch in seconds."},
    {"key": "CHROME_NAV_TIMEOUT_MS", "default": 30000, "description": "Chrome navigation timeout in milliseconds."},
    {"key": "CHROME_ACTION_TIMEOUT_MS", "default": 8000, "description": "Chrome action timeout in milliseconds."},
    {"key": "CHROME_RECOVERY_ATTEMPTS", "default": 2, "description": "Chrome recovery retry attempts."},
    {"key": "LLM_PROVIDER", "default": "gemini", "description": "LLM provider (openai|gemini|groq)."},
    {"key": "LLM_MODEL", "default": "gemini-3.1-flash-lite", "description": "LLM model name."},
    {"key": "LLM_TEMPERATURE", "default": 0.2, "description": "LLM temperature for generation variability."},
    {"key": "LLM_MAX_TOKENS", "default": 2000, "description": "Maximum LLM response tokens."},
    {"key": "LLM_TIMEOUT_SEC", "default": 60, "description": "LLM request timeout in seconds."},
    {"key": "LLM_MAX_RETRIES", "default": 3, "description": "LLM retry attempts on failure."},
    {"key": "LLM_RETRY_DELAY_SEC", "default": 2.0, "description": "Delay between LLM retries in seconds."},
    {"key": "LLM_FALLBACK_ENABLED", "default": False, "description": "Enable LLM fallback to secondary provider on primary failure."},
    {"key": "LLM_FALLBACK_PROVIDER", "default": None, "description": "Fallback LLM provider (openai|gemini|groq)."},
    {"key": "LLM_FALLBACK_MODEL", "default": None, "description": "Fallback LLM model name."},
    {"key": "INGEST_LOCATION", "default": None, "description": "Default ingestion location filter."},
    {
        "key": "INGEST_KEYWORDS",
        "default": [
            "AI Engineer",
            "ML Engineer",
            "Data Scientist",
            "Python Developer",
        ],
        "description": "Default ingestion keywords used for keyword seeding.",
    },
    {"key": "INGEST_MAX_JOB_AGE_DAYS", "default": 7, "description": "Maximum job age (days) for ingestion."},
    {"key": "INGEST_MAX_JOBS_PER_KEYWORD", "default": 50, "description": "Maximum jobs fetched per keyword run."},
    {"key": "RESEARCH_BATCH_SIZE", "default": 10, "description": "Companies processed per research run."},
    {"key": "RESEARCH_MAX_PROSPECTS", "default": 20, "description": "Max prospects collected per company."},
    {"key": "RESEARCH_MAX_PROFILES_ASSESS", "default": 8, "description": "Top prospects deep-assessed per company."},
    {"key": "RESEARCH_MIN_COMPANY_CONF", "default": 70.0, "description": "Minimum confidence to accept company match."},
    {"key": "CANDIDATE_HUNT_ENABLED", "default": True, "description": "Enable candidate hunt pipeline."},
    {"key": "CANDIDATE_HUNT_JOB_BATCH_SIZE", "default": 10, "description": "Jobs processed per candidate hunt run."},
    {"key": "CANDIDATE_HUNT_MAX_PAGES_PER_QUERY", "default": 5, "description": "LinkedIn search pages visited per query."},
    {"key": "CANDIDATE_HUNT_MAX_CANDIDATES_PER_JOB", "default": 60, "description": "Max candidate cards ingested per job."},
    {"key": "CANDIDATE_HUNT_MAX_PROFILES_TO_ENRICH", "default": 20, "description": "Max candidate profiles deep-enriched per job."},
    {"key": "CANDIDATE_HUNT_MAX_QUERY_VARIANTS", "default": 8, "description": "Max query variants generated per job."},
    {"key": "CANDIDATE_HUNT_MIN_CARD_RELEVANCE_SCORE", "default": 30, "description": "Minimum relevance score required to ingest a card."},
    {"key": "CANDIDATE_HUNT_PROFILE_RETRY_LIMIT", "default": 2, "description": "Retry limit for profile extraction/scoring failures."},
    {"key": "CANDIDATE_HUNT_NAV_TIMEOUT_MS", "default": 25000, "description": "Navigation timeout for candidate hunt pages."},
    {"key": "CANDIDATE_HUNT_PAGE_SETTLE_MS", "default": 900, "description": "Page settle wait after navigation/click in milliseconds."},
    {"key": "CANDIDATE_HUNT_POLITE_DELAY_SEC", "default": 0.5, "description": "Polite delay between candidate hunt page actions."},
    {"key": "CANDIDATE_HUNT_LOCATION_FALLBACK", "default": None, "description": "Fallback location added to candidate queries when needed."},
    {"key": "CANDIDATE_HUNT_INCLUDE_COMPANY_IN_QUERY", "default": False, "description": "Include company name in candidate query text."},
    {"key": "CANDIDATE_HUNT_INCLUDE_NEGATIVE_KEYWORDS", "default": True, "description": "Include negative terms to reduce noisy profiles."},
    {"key": "AGENT_DEFAULT_KEY", "default": "default-staffing", "description": "Default agent key used when agent is not explicitly selected."},
    {"key": "AGENT_RUNTIME_MODE", "default": "deterministic", "description": "Agent runtime mode (deterministic|autonomous)."},
    {"key": "AGENT_AUTONOMOUS_ALLOW_FALLBACK", "default": True, "description": "Allow autonomous runtime to fallback to deterministic execution on graph/tool errors."},
    {"key": "AGENT_MAX_TOOL_CALLS_PER_RUN", "default": 50, "description": "Maximum tool invocations allowed per autonomous agent run."},
    {"key": "AGENT_MAX_RUN_MINUTES", "default": 90, "description": "Maximum runtime duration for a single agent run."},
    {"key": "OUTREACH_RECRUITER_NAME", "default": "Alex", "description": "Recruiter name used in outreach personalization."},
    {"key": "OUTREACH_AGENCY_NAME", "default": "RecruitPro", "description": "Agency name used in outreach personalization."},
    {"key": "OUTREACH_FORCE_CHANNEL", "default": None, "description": "Optional forced outreach channel (linkedin_connect|linkedin_message|email)."},
    {"key": "OUTREACH_TEST_MODE_ENABLED", "default": False, "description": "Send generated outreach to test inbox instead of production channels."},
    {"key": "OUTREACH_TEST_RECIPIENT_EMAIL", "default": None, "description": "Recipient email used when outreach test mode is enabled."},
    {"key": "OUTREACH_TEST_SUBJECT_PREFIX", "default": "[Outreach-Test]", "description": "Subject prefix for outreach test emails."},
    {"key": "LINKEDIN_OUTREACH_BATCH_SIZE",    "default": 10,   "description": "Prospects processed per LinkedIn outreach run."},
    {"key": "LINKEDIN_OUTREACH_MAX_ATTEMPTS",  "default": 3,    "description": "Maximum outreach attempts per prospect before permanent skip."},
    {"key": "LINKEDIN_OUTREACH_DELAY_MIN_SEC", "default": 8.0,  "description": "Minimum humanised delay between LinkedIn outreach actions (seconds)."},
    {"key": "LINKEDIN_OUTREACH_DELAY_MAX_SEC", "default": 20.0, "description": "Maximum humanised delay between LinkedIn outreach actions (seconds)."},
    {"key": "LINKEDIN_OUTREACH_CONNECT_NOTE",  "default": None, "description": "Optional static note attached to LinkedIn connection requests."},
    {"key": "SMTP_HOST", "default": None, "description": "SMTP server host."},
    {"key": "SMTP_PORT", "default": 587, "description": "SMTP server port."},
    {"key": "SMTP_USERNAME", "default": None, "description": "SMTP username/login for authentication."},
    {"key": "SMTP_PASSWORD", "default": None, "description": "SMTP password or app password for authentication."},
    {"key": "SMTP_FROM_EMAIL", "default": None, "description": "From email address for SMTP dispatch."},
    {"key": "SMTP_USE_TLS", "default": True, "description": "Use STARTTLS for SMTP."},
    {"key": "SMTP_USE_SSL", "default": False, "description": "Use implicit SSL for SMTP."},
    {"key": "SMTP_TIMEOUT_SEC", "default": 20, "description": "SMTP timeout in seconds."},
    {"key": "RAG_CHROMA_PATH", "default": "./rag_store", "description": "Local path for the ChromaDB persistent vector store."},
    {"key": "RAG_COLLECTION_NAME", "default": "company_kb", "description": "ChromaDB collection name for the company knowledge base."},
    {"key": "RAG_EMBED_MODEL", "default": "gemini-embedding-001", "description": "Gemini embedding model name."},
    {"key": "RAG_EMBED_DIMS", "default": 768, "description": "Output dimensionality for Gemini embeddings."},
    {"key": "RAG_TOP_K", "default": 4, "description": "Number of chunks retrieved per RAG query."},
    {"key": "RAG_TARGET_URL", "default": "https://gnxtsystems.com", "description": "Website URL crawled by the website ingestion pipeline."},
    {"key": "REPLY_CHECK_INTERVAL_MINUTES", "default": 15, "description": "How often the backend scheduler sweeps all LinkedIn conversations for new replies."},
    {"key": "REPLY_HANDOFF_MANAGER_EMAIL", "default": None, "description": "Manager email address notified when a conversation is handed off to a human."},
    {"key": "REPLY_MAX_LIGHT_QUESTIONS", "default": 2, "description": "Max light questions the bot may answer per conversation before handoff."},
    {"key": "REPLY_MAX_BOT_REPLIES", "default": 3, "description": "Max total bot replies in a thread before forced handoff."},
    {"key": "REPLY_LIVE_WINDOW_MIN", "default": 5.0, "description": "Reply-to-our-last-message gap (minutes) under which a conversation is treated as live."},
    {"key": "REPLY_LIVE_REVERT_MIN", "default": 10.0, "description": "Minutes of prospect silence after which a live conversation reverts to batch mode."},
    {"key": "REPLY_LIVE_THINK_DELAY_MIN_SEC", "default": 15.0, "description": "Minimum simulated read/think delay (seconds) before replying in live mode."},
    {"key": "REPLY_LIVE_THINK_DELAY_MAX_SEC", "default": 60.0, "description": "Maximum simulated read/think delay (seconds) before replying in live mode."},
    {"key": "REPLY_TYPING_MS_PER_CHAR", "default": 170.0, "description": "Average per-character typing delay (ms) when humanized typing is enabled."},
    {"key": "REPLY_TYPING_JITTER_MS", "default": 45.0, "description": "Random jitter (ms, stddev) applied to each keystroke delay."},
    {"key": "REPLY_TYPING_PUNCT_PAUSE_MS", "default": 180.0, "description": "Extra pause (ms) after typing punctuation characters."},
    {"key": "REPLY_MAX_PER_CONV_24H", "default": 6, "description": "Max bot replies allowed per conversation in a rolling 24h window."},
    {"key": "REPLY_MIN_GLOBAL_SPACING_SEC", "default": 45.0, "description": "Minimum seconds between any two outbound replies across the whole account."},
    {"key": "REPLY_LIVE_FASTFOLLOW_MAX_ITER", "default": 3, "description": "Max extra fast-follow poll iterations for a live conversation within one run."},
    {"key": "REPLY_LIVE_FASTFOLLOW_POLL_SEC", "default": 45.0, "description": "Seconds between fast-follow poll iterations for live conversations."},
    {"key": "REPLY_LIVE_FASTFOLLOW_MAX_WALL_SEC", "default": 300.0, "description": "Hard wall-clock cap (seconds) on the fast-follow loop before returning control to the scheduler."},
    {"key": "REPLY_MAX_CONCURRENT_WORKERS", "default": 3, "description": "Parallel browser tabs used by the reply handler to process conversations (1 = sequential)."},
    {"key": "REPLY_QC_MAX_CHARS", "default": 320, "description": "Hard character cap enforced on outbound replies by the quality gate."},
    {"key": "REPLY_QC_MAX_REGEN", "default": 1, "description": "Max regeneration attempts when a generated reply fails the quality gate."},
    {"key": "REPLY_LIVE_ONLINE_GRACE_SEC", "default": 150.0, "description": "Seconds to linger in the thread after sending when the prospect's presence dot shows online."},
    {"key": "REPLY_LIVE_OFFLINE_GRACE_SEC", "default": 75.0, "description": "Seconds to linger in the thread after sending when the prospect appears offline/unknown."},
    {"key": "REPLY_LIVE_SESSION_POLL_SEC", "default": 2.5, "description": "Seconds between DOM polls while watching an open thread for live replies."},
    {"key": "REPLY_LIVE_SESSION_SILENCE_END_MIN", "default": 4.0, "description": "Minutes of prospect silence that ends an engaged live session."},
    {"key": "REPLY_LIVE_SESSION_MAX_MESSAGES", "default": 12, "description": "Max bot replies within one live session before it winds down."},
    {"key": "REPLY_LIVE_SESSION_MAX_WALL_MIN", "default": 15.0, "description": "Hard wall-clock cap (minutes) on a single live session."},
    {"key": "REPLY_LIVE_READ_BUFFER_MIN_SEC", "default": 5.0, "description": "Minimum simulated reading pause (seconds) before replying in a live session."},
    {"key": "REPLY_LIVE_READ_BUFFER_MAX_SEC", "default": 15.0, "description": "Maximum simulated reading pause (seconds) before replying in a live session."},
]

_SETTING_CATALOG_BY_KEY: Dict[str, Dict[str, Any]] = {
    item["key"]: item for item in _SETTING_CATALOG
}
_DB_CONFIGURABLE_KEYS = set(_SETTING_CATALOG_BY_KEY.keys())
_RUNTIME_SETTINGS_CACHE: Optional[Dict[str, Any]] = None


def get_config_type_for_key(key: str) -> str:
    norm = (key or "").strip().upper()
    if norm.startswith("DB_"):
        return "database"
    if norm.startswith("BROWSER_") or norm.startswith("CHROME_"):
        return "browser"
    if norm.startswith("LLM_"):
        return "llm"
    if norm.startswith("INGEST_"):
        return "ingestion"
    if norm.startswith("RESEARCH_"):
        return "research"
    if norm.startswith("CANDIDATE_HUNT_"):
        return "candidate_hunt"
    if norm.startswith("AGENT_"):
        return "agent"
    if norm.startswith("OUTREACH_") or norm.startswith("LINKEDIN_OUTREACH_"):
        return "outreach"
    if norm.startswith("SMTP_"):
        return "email"
    if norm.startswith("REPLY_"):
        return "reply_policy"
    return "general"


def get_non_secret_setting_catalog() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in _SETTING_CATALOG:
        row = dict(item)
        row["config_type"] = str(
            row.get("config_type") or get_config_type_for_key(str(row.get("key") or ""))
        ).strip().lower()
        rows.append(row)
    return rows


def get_non_secret_setting_seed_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in get_non_secret_setting_catalog():
        rows.append(
            {
                "key": item["key"],
                "value_json": json.dumps(item["default"], ensure_ascii=False),
                "description": item["description"],
                "config_type": item["config_type"],
            }
        )
    return rows


def is_secret_setting_key(key: str) -> bool:
    return (key or "").strip().upper() in SECRET_SETTING_KEYS


def get_catalog_default(key: str) -> Any:
    item = _SETTING_CATALOG_BY_KEY.get((key or "").strip())
    if not item:
        return None
    return item["default"]


def reset_runtime_settings_cache() -> None:
    global _RUNTIME_SETTINGS_CACHE
    _RUNTIME_SETTINGS_CACHE = None


def _load_runtime_settings_from_db() -> Dict[str, Any]:
    db_url = os.getenv("DB_URL", DEFAULT_DB_URL)
    engine = None
    settings: Dict[str, Any] = {}
    try:
        engine = create_engine(db_url, future=True, pool_pre_ping=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT setting_key, setting_value FROM app_settings")
            ).mappings().all()
        for row in rows:
            key = str(row.get("setting_key") or "").strip()
            if not key:
                continue
            raw = row.get("setting_value")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = raw
            else:
                parsed = raw
            settings[key] = parsed
    except Exception:
        # Silent fallback to env/default when DB or table is unavailable.
        return {}
    finally:
        try:
            if engine is not None:
                engine.dispose()
        except Exception:
            pass
    return settings


def _runtime_settings() -> Dict[str, Any]:
    global _RUNTIME_SETTINGS_CACHE
    if _RUNTIME_SETTINGS_CACHE is None:
        _RUNTIME_SETTINGS_CACHE = _load_runtime_settings_from_db()
    return _RUNTIME_SETTINGS_CACHE


def _unwrap_setting(raw: Any) -> Any:
    if isinstance(raw, dict):
        if "value" in raw:
            return raw.get("value")
    return raw


def _raw_setting(name: str) -> Any:
    key = (name or "").strip()
    env_val = os.getenv(key)
    if env_val is not None:
        return env_val
    if key in _DB_CONFIGURABLE_KEYS:
        cached = _runtime_settings()
        if key in cached:
            return _unwrap_setting(cached[key])
    return None


def _bool(name: str, default: bool = False) -> bool:
    raw = _raw_setting(name)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _int(name: str, default: int) -> int:
    raw = _raw_setting(name)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _float(name: str, default: float) -> float:
    raw = _raw_setting(name)
    if raw is None:
        return default
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _str(name: str, default: str) -> str:
    raw = _raw_setting(name)
    if raw is None:
        return default
    if isinstance(raw, str):
        val = raw.strip()
        return val if val else default
    return str(raw).strip() or default


def _opt_str(name: str, default: Optional[str] = None) -> Optional[str]:
    raw = _raw_setting(name)
    if raw is None:
        return default
    if isinstance(raw, str):
        val = raw.strip()
        return val or None
    val = str(raw).strip()
    return val or None


def _list_str(name: str, default: List[str]) -> List[str]:
    raw = _raw_setting(name)
    if raw is None:
        return list(default)
    if isinstance(raw, list):
        vals = [str(v).strip() for v in raw if str(v).strip()]
        return vals if vals else list(default)
    if isinstance(raw, tuple):
        vals = [str(v).strip() for v in raw if str(v).strip()]
        return vals if vals else list(default)
    if isinstance(raw, str):
        vals = [v.strip() for v in raw.split(",") if v.strip()]
        return vals if vals else list(default)
    val = str(raw).strip()
    return [val] if val else list(default)


@dataclass(frozen=True)
class DatabaseConfig:
    """
    MySQL via PyMySQL.
    Example:
        mysql+pymysql://user:password@localhost/prospect_db?charset=utf8mb4
    No-password local dev:
        mysql+pymysql://root:@localhost/prospect_db?charset=utf8mb4
    """

    sqlalchemy_url: str = field(default_factory=lambda: os.getenv("DB_URL", DEFAULT_DB_URL))
    echo_sql: bool = field(default_factory=lambda: _bool("DB_ECHO", False))
    pool_size: int = field(default_factory=lambda: _int("DB_POOL_SIZE", 5))
    pool_recycle_seconds: int = field(default_factory=lambda: _int("DB_POOL_RECYCLE", 1800))


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool = field(default_factory=lambda: _bool("BROWSER_HEADLESS", False))
    fast_mode: bool = field(default_factory=lambda: _bool("BROWSER_FAST_MODE", True))
    nav_timeout_ms: int = field(default_factory=lambda: _int("BROWSER_NAV_TIMEOUT_MS", 15000))
    action_timeout_ms: int = field(default_factory=lambda: _int("BROWSER_ACTION_TIMEOUT_MS", 6000))
    scroll_pause_sec: float = field(default_factory=lambda: _float("BROWSER_SCROLL_PAUSE_SEC", 0.25))
    listing_scroll_rounds: int = field(default_factory=lambda: _int("BROWSER_LISTING_SCROLL_ROUNDS", 6))
    polite_sleep_sec: float = field(default_factory=lambda: _float("BROWSER_POLITE_SLEEP_SEC", 0.1))
    detail_page_delay_sec: float = field(default_factory=lambda: _float("BROWSER_DETAIL_PAGE_DELAY_SEC", 0.4))
    viewport_width: int = field(default_factory=lambda: _int("BROWSER_VIEWPORT_W", 1440))
    viewport_height: int = field(default_factory=lambda: _int("BROWSER_VIEWPORT_H", 900))


@dataclass(frozen=True)
class ChromeCDPConfig:
    attach: bool = field(default_factory=lambda: _bool("CHROME_CDP_ATTACH", True))
    url: str = field(default_factory=lambda: _str("CHROME_CDP_URL", "http://127.0.0.1:9223"))
    exe: str = field(default_factory=lambda: _str("CHROME_EXE", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    user_data_dir: str = field(default_factory=lambda: _str("CHROME_USER_DATA_DIR", r"C:\Users\Public\chrome-cdp-profile"))
    headless: bool = field(default_factory=lambda: _bool("CHROME_HEADLESS", False))
    startup_wait_seconds: float = field(default_factory=lambda: _float("CHROME_STARTUP_WAIT_SEC", 2.5))
    navigation_timeout_ms: int = field(default_factory=lambda: _int("CHROME_NAV_TIMEOUT_MS", 30000))
    action_timeout_ms: int = field(default_factory=lambda: _int("CHROME_ACTION_TIMEOUT_MS", 8000))
    recovery_attempts: int = field(default_factory=lambda: _int("CHROME_RECOVERY_ATTEMPTS", 2))


@dataclass(frozen=True)
class NaukriConfig:
    base_url: str = "https://www.naukri.com"
    results_per_page_soft_limit: int = 20
    max_pages: int = 50
    listing_scroll_rounds: int = 6
    detail_page_delay_sec: float = 0.4

    # Selectors
    job_card_selector: str = "div.cust-job-tuple"
    job_link_selector: str = "a[href*='job-listings']"
    job_title_selector: str = "header > h1[class*='jd-header-title']"
    company_name_selector: str = "[class*='jd-header-comp-name'] a"
    stats_container_selector: str = "[class*='jd-stats']"
    job_desc_selector: str = "[class*='short-desc'] [class*='dang-inner-html']"
    detail_row_selector: str = "[class*='other-details'] [class*='details']"
    education_selector: str = "[class*='education'] [class*='details']"
    skills_selector: str = "[class*='key-skill'] [class*='chip'] span"
    json_ld_selector: str = "script[type='application/ld+json']"

    popup_close_selectors: tuple = (
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "[data-testid='backdrop']",
        ".modal-close",
        "button:has-text('Close')",
        "button:has-text('Not now')",
        "button:has-text('Skip')",
    )


@dataclass(frozen=True)
class LLMConfig:
    provider: str = field(default_factory=lambda: _str("LLM_PROVIDER", "gemini"))  # openai | gemini | groq
    model: str = field(default_factory=lambda: _str("LLM_MODEL", "gemini-3.1-flash-lite"))
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 2000))
    timeout_seconds: int = field(default_factory=lambda: _int("LLM_TIMEOUT_SEC", 60))
    max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 3))
    retry_delay_seconds: float = field(default_factory=lambda: _float("LLM_RETRY_DELAY_SEC", 2.0))
    
    fallback_enabled: bool = field(default_factory=lambda: _bool("LLM_FALLBACK_ENABLED", False))
    fallback_provider: Optional[str] = field(default_factory=lambda: _opt_str("LLM_FALLBACK_PROVIDER"))
    fallback_model: Optional[str] = field(default_factory=lambda: _opt_str("LLM_FALLBACK_MODEL"))



@dataclass(frozen=True)
class JobIngestionConfig:
    keywords: List[str] = field(
        default_factory=lambda: _list_str(
            "INGEST_KEYWORDS",
            ["AI Engineer", "ML Engineer", "Data Scientist", "Python Developer"],
        )
    )
    location: Optional[str] = field(default_factory=lambda: _opt_str("INGEST_LOCATION", None))
    max_job_age_days: int = field(default_factory=lambda: _int("INGEST_MAX_JOB_AGE_DAYS", 7))
    max_jobs_per_keyword: int = field(default_factory=lambda: _int("INGEST_MAX_JOBS_PER_KEYWORD", 50))


@dataclass(frozen=True)
class ResearchConfig:
    batch_size: int = field(default_factory=lambda: _int("RESEARCH_BATCH_SIZE", 10))
    max_prospects_per_company: int = field(default_factory=lambda: _int("RESEARCH_MAX_PROSPECTS", 20))
    max_profiles_to_assess: int = field(default_factory=lambda: _int("RESEARCH_MAX_PROFILES_ASSESS", 8))
    min_company_confidence: float = field(default_factory=lambda: _float("RESEARCH_MIN_COMPANY_CONF", 70.0))
    prospect_keywords: List[str] = field(
        default_factory=lambda: [
            "HR",
            "Talent Acquisition",
            "Recruiter",
            "Hiring Manager",
            "HR Manager",
            "Head of HR",
            "Head of Talent Acquisition",
            "CEO",
            "CTO",
            "Founder",
            "Co-Founder",
            "VP Engineering",
            "Director Engineering",
            "Engineering Manager",
            "Head of Engineering",
        ]
    )


@dataclass(frozen=True)
class CandidateHuntConfig:
    enabled: bool = field(default_factory=lambda: _bool("CANDIDATE_HUNT_ENABLED", True))
    job_batch_size: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_JOB_BATCH_SIZE", 10))
    max_pages_per_query: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_MAX_PAGES_PER_QUERY", 5))
    max_candidates_per_job: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_MAX_CANDIDATES_PER_JOB", 60))
    max_profiles_to_enrich: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_MAX_PROFILES_TO_ENRICH", 20))
    max_query_variants: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_MAX_QUERY_VARIANTS", 8))
    min_card_relevance_score: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_MIN_CARD_RELEVANCE_SCORE", 30))
    profile_retry_limit: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_PROFILE_RETRY_LIMIT", 2))
    navigation_timeout_ms: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_NAV_TIMEOUT_MS", 25000))
    page_settle_ms: int = field(default_factory=lambda: _int("CANDIDATE_HUNT_PAGE_SETTLE_MS", 900))
    polite_delay_sec: float = field(default_factory=lambda: _float("CANDIDATE_HUNT_POLITE_DELAY_SEC", 0.5))
    search_location_fallback: Optional[str] = field(default_factory=lambda: _opt_str("CANDIDATE_HUNT_LOCATION_FALLBACK", None))
    include_company_in_query: bool = field(default_factory=lambda: _bool("CANDIDATE_HUNT_INCLUDE_COMPANY_IN_QUERY", False))
    include_negative_keywords: bool = field(default_factory=lambda: _bool("CANDIDATE_HUNT_INCLUDE_NEGATIVE_KEYWORDS", True))


@dataclass(frozen=True)
class AgentRuntimeConfig:
    default_agent_key: str = field(default_factory=lambda: _str("AGENT_DEFAULT_KEY", "default-staffing"))
    mode: str = field(default_factory=lambda: _str("AGENT_RUNTIME_MODE", "deterministic"))
    autonomous_allow_fallback: bool = field(default_factory=lambda: _bool("AGENT_AUTONOMOUS_ALLOW_FALLBACK", True))
    max_tool_calls_per_run: int = field(default_factory=lambda: _int("AGENT_MAX_TOOL_CALLS_PER_RUN", 50))
    max_run_minutes: int = field(default_factory=lambda: _int("AGENT_MAX_RUN_MINUTES", 90))


@dataclass(frozen=True)
class OutreachConfig:
    recruiter_name: str = field(default_factory=lambda: _str("OUTREACH_RECRUITER_NAME", "Alex"))
    agency_name: str = field(default_factory=lambda: _str("OUTREACH_AGENCY_NAME", "RecruitPro"))
    # Optional override: linkedin_connect | linkedin_message | email
    force_channel: Optional[str] = field(default_factory=lambda: _opt_str("OUTREACH_FORCE_CHANNEL", None))


@dataclass(frozen=True)
class OutreachDispatchConfig:
    # Test mode: send generated outreach to a fixed inbox for validation.
    test_mode_enabled: bool = field(default_factory=lambda: _bool("OUTREACH_TEST_MODE_ENABLED", False))
    test_recipient_email: Optional[str] = field(default_factory=lambda: _opt_str("OUTREACH_TEST_RECIPIENT_EMAIL", None))
    subject_prefix: str = field(default_factory=lambda: _str("OUTREACH_TEST_SUBJECT_PREFIX", "[Outreach-Test]"))

    # SMTP settings
    smtp_host: Optional[str] = field(default_factory=lambda: _opt_str("SMTP_HOST", None))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_username: Optional[str] = field(default_factory=lambda: _opt_str("SMTP_USERNAME", None))
    smtp_password: Optional[str] = field(default_factory=lambda: _opt_str("SMTP_PASSWORD", None))
    smtp_from_email: Optional[str] = field(default_factory=lambda: _opt_str("SMTP_FROM_EMAIL", None))
    smtp_use_tls: bool = field(default_factory=lambda: _bool("SMTP_USE_TLS", True))
    smtp_use_ssl: bool = field(default_factory=lambda: _bool("SMTP_USE_SSL", False))
    smtp_timeout_seconds: int = field(default_factory=lambda: _int("SMTP_TIMEOUT_SEC", 20))

    # Reply-handler handoff notification recipient (reuses the SMTP settings above).
    handoff_manager_email: Optional[str] = field(default_factory=lambda: _opt_str("REPLY_HANDOFF_MANAGER_EMAIL", None))


@dataclass(frozen=True)
class ReplyPolicyConfig:
    """Tunables governing the LinkedIn reply handler's humanized conversational behavior."""
    max_light_questions: int = field(default_factory=lambda: _int("REPLY_MAX_LIGHT_QUESTIONS", 2))
    max_bot_replies: int = field(default_factory=lambda: _int("REPLY_MAX_BOT_REPLIES", 3))

    live_window_minutes: float = field(default_factory=lambda: _float("REPLY_LIVE_WINDOW_MIN", 5.0))
    live_revert_minutes: float = field(default_factory=lambda: _float("REPLY_LIVE_REVERT_MIN", 10.0))
    live_think_delay_min_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_THINK_DELAY_MIN_SEC", 15.0))
    live_think_delay_max_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_THINK_DELAY_MAX_SEC", 60.0))

    typing_ms_per_char: float = field(default_factory=lambda: _float("REPLY_TYPING_MS_PER_CHAR", 170.0))
    typing_jitter_ms: float = field(default_factory=lambda: _float("REPLY_TYPING_JITTER_MS", 45.0))
    typing_punct_pause_ms: float = field(default_factory=lambda: _float("REPLY_TYPING_PUNCT_PAUSE_MS", 180.0))

    max_replies_per_conversation_24h: int = field(default_factory=lambda: _int("REPLY_MAX_PER_CONV_24H", 6))
    min_global_spacing_sec: float = field(default_factory=lambda: _float("REPLY_MIN_GLOBAL_SPACING_SEC", 45.0))

    live_fastfollow_max_iterations: int = field(default_factory=lambda: _int("REPLY_LIVE_FASTFOLLOW_MAX_ITER", 3))
    live_fastfollow_poll_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_FASTFOLLOW_POLL_SEC", 45.0))
    live_fastfollow_max_wall_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_FASTFOLLOW_MAX_WALL_SEC", 300.0))

    max_concurrent_workers: int = field(default_factory=lambda: _int("REPLY_MAX_CONCURRENT_WORKERS", 3))
    qc_max_chars: int = field(default_factory=lambda: _int("REPLY_QC_MAX_CHARS", 320))
    qc_max_regen: int = field(default_factory=lambda: _int("REPLY_QC_MAX_REGEN", 1))

    live_online_grace_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_ONLINE_GRACE_SEC", 150.0))
    live_offline_grace_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_OFFLINE_GRACE_SEC", 75.0))
    live_session_poll_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_SESSION_POLL_SEC", 2.5))
    live_session_silence_end_min: float = field(default_factory=lambda: _float("REPLY_LIVE_SESSION_SILENCE_END_MIN", 4.0))
    live_session_max_messages: int = field(default_factory=lambda: _int("REPLY_LIVE_SESSION_MAX_MESSAGES", 12))
    live_session_max_wall_min: float = field(default_factory=lambda: _float("REPLY_LIVE_SESSION_MAX_WALL_MIN", 15.0))
    live_read_buffer_min_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_READ_BUFFER_MIN_SEC", 5.0))
    live_read_buffer_max_sec: float = field(default_factory=lambda: _float("REPLY_LIVE_READ_BUFFER_MAX_SEC", 15.0))

    scheduler_interval_minutes: int = field(default_factory=lambda: _int("REPLY_CHECK_INTERVAL_MINUTES", 15))


@dataclass(frozen=True)
class LinkedInOutreachConfig:
    """Configuration for the automated LinkedIn outreach sender pipeline."""
    batch_size: int = field(
        default_factory=lambda: _int("LINKEDIN_OUTREACH_BATCH_SIZE", 10)
    )
    max_attempts: int = field(
        default_factory=lambda: _int("LINKEDIN_OUTREACH_MAX_ATTEMPTS", 3)
    )
    delay_min_seconds: float = field(
        default_factory=lambda: _float("LINKEDIN_OUTREACH_DELAY_MIN_SEC", 8.0)
    )
    delay_max_seconds: float = field(
        default_factory=lambda: _float("LINKEDIN_OUTREACH_DELAY_MAX_SEC", 20.0)
    )
    connect_note: Optional[str] = field(
        default_factory=lambda: _opt_str("LINKEDIN_OUTREACH_CONNECT_NOTE", None)
    )


@dataclass(frozen=True)
class RAGConfig:
    chroma_path: str = field(default_factory=lambda: _str("RAG_CHROMA_PATH", "./rag_store"))
    collection_name: str = field(default_factory=lambda: _str("RAG_COLLECTION_NAME", "company_kb"))
    embed_model: str = field(default_factory=lambda: _str("RAG_EMBED_MODEL", "gemini-embedding-001"))
    embed_dims: int = field(default_factory=lambda: _int("RAG_EMBED_DIMS", 768))
    top_k: int = field(default_factory=lambda: _int("RAG_TOP_K", 4))
    target_url: str = field(default_factory=lambda: _str("RAG_TARGET_URL", "https://gnxtsystems.com"))


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    chrome: ChromeCDPConfig = field(default_factory=ChromeCDPConfig)
    naukri: NaukriConfig = field(default_factory=NaukriConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    job_ingestion: JobIngestionConfig = field(default_factory=JobIngestionConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    candidate_hunt: CandidateHuntConfig = field(default_factory=CandidateHuntConfig)
    agent_runtime: AgentRuntimeConfig = field(default_factory=AgentRuntimeConfig)
    outreach: OutreachConfig = field(default_factory=OutreachConfig)
    outreach_dispatch: OutreachDispatchConfig = field(default_factory=OutreachDispatchConfig)
    linkedin_outreach: LinkedInOutreachConfig = field(default_factory=LinkedInOutreachConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    reply_policy: ReplyPolicyConfig = field(default_factory=ReplyPolicyConfig)
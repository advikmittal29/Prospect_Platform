# Prospect Platform — CLAUDE.md

Working directory: `C:\Users\advik\Desktop\IIITD\gNxt\Prospect_Platform`  
Python venv: `.\venv\` (always use `.\venv\Scripts\python.exe` or activate first)  
DB: MySQL via PyMySQL — `mysql+pymysql://root:@localhost/prospect_db`  
LinkedIn browser: real Chrome attached via CDP at `http://127.0.0.1:9223` (port 9223)

---

## What this project does

B2B recruitment automation for a staffing agency. Full pipeline:

1. **Ingest** — scrape Naukri.com for keyword-matched job postings
2. **Research** — find the company on LinkedIn, discover hiring contacts (HR, CTO, etc.)
3. **Intelligence** — LLM-generated dossier + personalised outreach message per prospect
4. **Outreach** — send message via LinkedIn (message or connect) or email
5. **Reply handling** — poll LinkedIn threads, detect prospect replies, send AI-generated follow-ups

---

## Directory map

```
config.py                     Unified app config (env + DB-driven). Single source of truth.
db/
  models.py                   All SQLAlchemy ORM models + session_scope + init_db
  __init__.py                 Re-exports everything from models.py
init_db.py                    One-time DB schema creation script (run once, never again)
scheduler/                    Entry-point scripts (run these directly)
  run_ingest.py               Naukri job ingestion
  run_research.py             LinkedIn company + prospect research
  run_intelligence.py         Dossier + outreach message generation
  run_candidate_hunt.py       LinkedIn candidate search for open jobs
  p_outreach.py               LinkedIn/email outreach sender
  p_reply_handler.py          LinkedIn reply poller + AI responder
jobs/
  naukri_ingest.py            Naukri scraper (Playwright Chromium, headless)
  browser.py                  Playwright browser wrapper for Naukri
research/
  linkedin_browser.py         Chrome CDP wrapper (LinkedInBrowser + ChromeLauncher)
  company_finder.py           LinkedIn company search
  people_finder.py            LinkedIn people search on company page
  profile_assessor.py         Deep profile assessment
  pipeline.py                 Orchestrates company + people research
intelligence/
  dossier_generator.py        LLM dossier + outreach message generator
outreach/
  linkedin_outreach_sender.py First outreach sender (LinkedIn message/connect)
  linkedin_reply_handler.py   Reply detection + AI reply sender  ← active work here
  message_generator.py        Outreach message generation helpers
candidate_hunt/
  pipeline.py                 Candidate hunt orchestrator
  query_builder.py            LLM-backed LinkedIn search query builder
  search_extractor.py         LinkedIn people-search ingestion
  profile_extractor.py        Deep profile extraction
  scoring.py                  JD relevance + job-seeking scoring
agents/
  config_resolver.py          Resolves agent config from DB (agent_definitions table)
  guard.py                    AgentInactiveError guard
  run_tracker.py              AgentRunORM tracker
utils/
  llm_client.py               Unified LLM client (openai|gemini|groq)
  logging.py                  build_logger() helper
  prompt_loader.py            Loads prompt templates from prompts/
app-ui/                       React frontend + FastAPI backend monitoring UI
```

---

## Key DB tables

| Table | Purpose |
|---|---|
| `agent_definitions` | One row per agent (staffing persona). Must exist before running. |
| `agent_profiles` | Agent persona config (recruiter name, ICP rules, etc.) |
| `naukri_jobs` | Raw Naukri job records |
| `company_research` | LinkedIn company discovery + research status |
| `prospects` | One row per person. Holds dossier, outreach message, dispatch status. |
| `candidate_profiles` | LinkedIn candidate cards found for open jobs |
| `linkedin_conversations` | **Reply handler table.** One row per prospect. Stores full thread JSON. |
| `app_settings` | Runtime-configurable non-secret settings (overrides env defaults) |
| `linkedin_credentials` | LinkedIn login credentials (email/password) |

### `linkedin_conversations` schema (most relevant for reply handler)

```
prospect_id           INT      FK to prospects
agent_id              INT
linkedin_profile_url  VARCHAR
conversation_status   VARCHAR  active|closed|not_interested|meeting_booked
lead_stage            VARCHAR  cold|warming|interested|hot|converted|dead
thread_json           TEXT     JSON list of {"role":"us"|"them","text":"...","ts":"ISO8601"}
messages_sent         INT
messages_received     INT
last_checked_utc      DATETIME
last_error            TEXT
error_count           INT
```

`get_thread()` / `append_message()` helpers are on the ORM class itself.

---

## Config system

`config.py` has two layers with this **priority order** (highest → lowest):
1. **`.env` file** — checked first. Env vars always win over DB values.
2. **`app_settings` DB table** — non-secret runtime fallbacks (read fresh each run via `reset_runtime_settings_cache()`). Only used when the env var is not set.
3. **Hardcoded catalog defaults** — last resort if neither env nor DB has a value.

This means: set something in `.env` → it works, regardless of what the DB table says. The DB table serves as defaults that the app UI can tweak at runtime without a deploy.

`AppConfig` is a frozen dataclass that composes all sub-configs. Use it like:
```python
config = AppConfig()
config.outreach.recruiter_name   # OUTREACH_RECRUITER_NAME
config.llm.api_key               # LLM_API_KEY (env only)
config.chrome.url                # CHROME_CDP_URL
```

`my_linkedin_name` is read via `getattr(config, "my_linkedin_name", "")` — comes from `MY_LINKEDIN_NAME` in `.env`.

---

## Key `.env` variables (this machine)

```
DB_URL=mysql+pymysql://root:@localhost/prospect_db?charset=utf8mb4
LLM_API_KEY=<google gemini key>       # Used by dossier generator AND reply handler
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.1-flash-lite       # Active model as of June 2026
CHROME_CDP_URL=http://127.0.0.1:9223
CHROME_USER_DATA_DIR=C:\Users\Public\chrome-cdp-profile
MY_LINKEDIN_NAME=Advik Mittal          # Used to classify "us" vs "them" in threads
OUTREACH_RECRUITER_NAME=<recruiter>
OUTREACH_AGENCY_NAME=<agency>
```

---

## How to run each pipeline

```bash
# Always from the project root with venv active
cd C:\Users\advik\Desktop\IIITD\gNxt\Prospect_Platform
.\venv\Scripts\activate

# One-time DB setup (only ever run once)
python init_db.py

# Daily pipelines (in order)
python scheduler/run_ingest.py
python scheduler/run_research.py
python scheduler/run_intelligence.py
python scheduler/p_outreach.py

# Reply handler — runs every 10-15 min via Task Scheduler
python scheduler/p_reply_handler.py              # all active conversations (parallel tabs)
python scheduler/p_reply_handler.py --agent-id 1
python scheduler/p_reply_handler.py --max-workers 1   # force sequential (debugging)

# Test / debug single prospect reply
python scheduler/p_reply_handler.py --test-prospect-id 1

# Register a conversation manually after sending first message outside the app
python scheduler/p_reply_handler.py --register-prospect-id 1 --first-message "Hi Anoop..."

# Candidate hunt
python scheduler/run_candidate_hunt.py
```

---

## LinkedIn browser — how it works

- **NOT Playwright's own browser.** Uses a real Google Chrome instance already logged into LinkedIn.
- Chrome is launched with `--remote-debugging-port=9223` and a persistent user-data-dir so the LinkedIn session is kept alive.
- `LinkedInBrowser` (in `research/linkedin_browser.py`) attaches via Playwright's CDP connect.
- `ChromeLauncher.launch_if_needed()` starts Chrome if not already running.
- **Pre-condition for any LinkedIn script**: Chrome must be open and logged into LinkedIn. The scheduler scripts call `browser.ensure_logged_in()` automatically.

---

## Reply handler — architecture & known quirks

Entry: `scheduler/p_reply_handler.py` → `LinkedInReplyHandler._process_one(conv)`

### Flow
```
profile page → find "Message" link → click/navigate → overlay or full-page 
→ scrape div.msg-s-event-listitem bubbles → classify sender (us/them) 
→ compare count vs stored thread_json → if new "them" messages:
  → classify intent (4 labels, see below)
  → INTERESTED / caps hit  → silent handoff + manager email (HTML + text), stop
  → NOT_INTERESTED         → one gracious close line, mark not_interested/dead, stop
  → otherwise              → call Gemini → quality gate → reopen thread → send → update DB
```

### Intent classifier (4 labels)
`LIGHT_QUESTION | INTERESTED | NOT_INTERESTED | NOT_INTERESTED_NEUTRAL`
- Keyword fast-paths run before the LLM: `_INTERESTED_KEYWORDS` (pricing/call words) and
  `_NOT_INTERESTED_KEYWORDS` (hard declines). If BOTH match → the LLM decides.
- `NOT_INTERESTED` (hard decline) → `_close_not_interested()`: best-effort short close message,
  then `conversation_status='not_interested'`, `lead_stage='dead'`, close reason stored in
  `handoff_reason`. The poller never touches the row again.
- The bot's ONLY goal is gauging interest. It NEVER proposes calls/meetings/coffee — humans
  take over after handoff. Enforced both in the prompt and the quality gate.

### Reply quality gate (`quality_check_reply`)
Every outbound reply must pass deterministic checks before sending: char cap
(`REPLY_QC_MAX_CHARS`, default 320), max sentences, banned AI-isms ("finds you well",
"just following up", "leverage"…), no meeting proposals, no pricing, no markdown/emoji.
On failure the issues are fed back to the LLM as a critique and the reply is regenerated
(`REPLY_QC_MAX_REGEN` extra attempts). If nothing passes → deferred. **Defer semantics
(QC-fail and safety-cap alike): the inbound message is deliberately NOT persisted** — reply
detection diffs live-vs-stored "them" counts, so persisting it would make it invisible and the
prospect would never get an answer. Unpersisted, the next sweep re-detects it and retries.
Voice/persona rules live in `prompts/reply_voice_rules.txt`.

### Concurrency (multi-tab)
`REPLY_MAX_CONCURRENT_WORKERS` (default 3; `--max-workers N` overrides) runs N worker threads.
Each worker has its OWN `sync_playwright()` instance + CDP connection and drives a dedicated
tab (`LinkedInBrowser.start(new_page=True)`) in the same logged-in Chrome — Playwright's sync
API is single-threaded per instance, so per-thread instances are mandatory. Scraping/LLM work
runs in parallel; actual sends serialize through `_GLOBAL_SEND_LOCK` (account-wide
`REPLY_MIN_GLOBAL_SPACING_SEC` re-checked inside the lock) so tabs never burst-send. Worker
tabs are closed by `stop()`.

### Live sessions (real-time chat with an online prospect)
After every batch reply the worker LINGERS in the open thread instead of navigating away
(`_linger_and_live_session`):
- **Entry**: green presence dot (`_prospect_appears_online`, best-effort/unverified selectors)
  → wait `REPLY_LIVE_ONLINE_GRACE_SEC` (150s); offline/unknown → `REPLY_LIVE_OFFLINE_GRACE_SEC`
  (75s). No reply in the window → leave silently (never nudge); scheduler cadence resumes.
  Presence is only a hint — a reply is the only thing that starts a session.
- **Engaged**: watch the open thread DOM (cheap bubble-count change detector, poll
  `REPLY_LIVE_SESSION_POLL_SEC`), read-buffer 5–15s (`REPLY_LIVE_READ_BUFFER_*`, scaled by
  message length), classify, generate via quality gate, send. Ends after
  `REPLY_LIVE_SESSION_SILENCE_END_MIN` (4 min) silence, presence gone + 60s quiet,
  `REPLY_LIVE_SESSION_MAX_MESSAGES` (12), or `REPLY_LIVE_SESSION_MAX_WALL_MIN` (15 min).
- **Session policy vs batch** (deliberate): INTERESTED → manager email fires immediately
  ([Handoff][LIVE] subject + take-over-now banner) but the bot keeps talking in
  `LIVE_DISCOVERY` mode (roles/headcount/timeline, one question at a time); NOT_INTERESTED →
  no mid-chat close, `LIVE_POLITE` wind-down; both outcomes are applied ONLY after the
  session ends (`_finalize_live_session`). Live sends skip the 45s global spacing (lock still
  serialises typing), are flagged `"live": true` in thread_json, are excluded from the 24h
  batch cap (`count_our_messages_since`), and do NOT increment `bot_reply_count`.
- Each exchange persists incrementally (`_persist_live_exchange`) so the dashboard transcript
  grows in real time. Stats: `live_sessions` / `live_replies` in run metrics.
- Robustness: the session loop runs inside try/finally — EVERY exit (normal, caps, exception)
  finalizes the conversation state. An inbound that never got its reply (QC fail, reply cap,
  crash) is trimmed from the in-memory thread before persisting, so the next sweep re-detects
  and answers it. The `[Handoff][LIVE]` email is guarded by the persisted `handoff_email_sent`
  flag, written the moment the send succeeds — a crashed session can never re-email the manager.

### Manager handoff email
`_send_handoff_email` sends multipart text+HTML: verdict subject
(`[Handoff] INTERESTED LEAD: name @ company`), prospect card, reason, highlighted latest
message, chat-bubble transcript, "Open LinkedIn profile" CTA. Uses SMTP_* settings and
`REPLY_HANDOFF_MANAGER_EMAIL`.

### Dashboard engagement status ("Remark" column)
`/api/prospects` (list + detail) LEFT JOINs `linkedin_conversations` and derives
`engagement_status`: `email_sent | handed_off | not_interested | meeting_booked | closed |
talking | in_process | not_contacted` (see `_derive_engagement_status` in
`app-ui/backend/app/main.py`). The Prospects page renders it as the "Remark" pill column.

### Critical selectors (LinkedIn DOM — as of June 2026)
```python
_MSG_LINK_SELS = [
    "a[href*='/messaging/compose/'][href*='interop=msgOverlay']",  # new conversation
    "a[href*='/messaging/thread/']",                                # existing thread
    "a[href*='/messaging/compose/']",                               # compose fallback
]
_MSG_OVERLAY_SEL  = "div.msg-overlay-conversation-bubble"
_MSG_BUBBLE_SEL   = "div.msg-s-event-listitem"
_MSG_TEXT_SEL     = ".msg-s-event-listitem__body"
_MSG_COMPOSE_SELS = ["div.msg-form__contenteditable[role='textbox']", ...]
_MSG_SEND_BTN_SELS = ["button.msg-form__send-button[type='submit']", ...]
```

**LinkedIn uses hashed/obfuscated CSS class names** (e.g. `_4c6a6fe1 _1ef6ccae`) — do NOT rely on class name matching for anything except the stable `msg-s-*` selectors above. Sender classification uses JS `evaluate` walking up the DOM checking computed styles and aria-labels.

### Sender classification (us vs them)
Uses JS that walks up to 15 ancestor levels checking:
1. Semantic class names (`msg-s-message-list__event--right`, `msg-s-event-listitem--message-from-you`)
2. Aria-label patterns: `"you sent"`, `"your message"`, `"sent by you"`
3. `MY_LINKEDIN_NAME` in aria-label (set in `.env` as `MY_LINKEDIN_NAME=Advik Mittal`)
4. `window.getComputedStyle(node).alignSelf === 'flex-end'`

### Click interception workaround
A `<p>` overlay blocks normal click on the Message link. Both scrape-open (`scrape_thread`) and reply-open (`_reopen_overlay`) use the same three-attempt pattern **in this order**:
1. **Direct URL navigation** to the link's `href` — most reliable, skips click machinery entirely
2. **JS click** (`el.click()` via `evaluate`) — bypasses pointer-event interception
3. **Normal click** — last resort

After each attempt, the code checks `_compose_ready()` (is the compose textbox actually visible?) before moving to the next attempt. A promo-page check (`_is_promo_page`) runs if the compose check fails — LinkedIn sometimes redirects to a Premium upsell page instead of the thread.

**Critical**: do NOT use `force=True` click as a strategy. It fires a DOM event without waiting for navigation and silently "succeeds" even when the compose area never opens. This was the root cause of the original `_reopen_overlay` failure.

### Reply detection logic
Simple count-based diff in `_find_new_their_messages()`:
- Count `"them"` messages in stored `thread_json` → `stored_their_count`
- Count `"them"` messages in live scraped thread → `live_their`
- If `len(live_their) > stored_their_count` → new messages exist

### LLM for reply generation
Uses `utils/llm_client.py` → `llm_complete()` — the same shared client used by the dossier generator.
Provider/model/key come from `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` in `.env`.
Supports `openai`, `gemini`, `groq`. Retry + fallback logic is handled by `llm_complete`.

`utils/llm_client.py` gemini branch uses `google.genai` (v2.10.0), **not** the deprecated `google.generativeai`.

**Config gotcha**: `app_settings` DB table is seeded with OpenAI defaults (`LLM_PROVIDER=openai`, `LLM_MODEL=gpt-4o-mini`). The DB used to take priority over `.env`, meaning a Gemini key in `.env` would be sent to the OpenAI endpoint. This is now fixed — `.env` always wins. If you see a 401 from OpenAI when using a Gemini key, this priority bug has regressed.

---

## Known issues / gotchas

### Empty `thread_json` in `linkedin_conversations`
If `Stored thread has 0 message(s)` appears in logs, the conversation was registered without storing the first outreach message. Fix manually:
```sql
UPDATE linkedin_conversations
SET thread_json = '[{"role":"us","text":"YOUR FIRST MESSAGE","ts":"2026-01-01T00:00:00+00:00"}]'
WHERE prospect_id = 1;
```
Or re-register: `python scheduler/p_reply_handler.py --register-prospect-id 1 --first-message "..."`.
After one successful reply run the DB self-corrects.

### LinkedIn "Message" button selector changes
When a conversation thread already exists, LinkedIn changes the button href from a compose URL to a thread URL (`/messaging/thread/2-xxx/`). Always use `_MSG_LINK_SELS` (a list), never the old single `_MSG_LINK_SEL`.

### `app_settings` table not in `_REQUIRED_TABLES`
`linkedin_conversations` is defined in `db/models.py` but not added to `_REQUIRED_TABLES` set in `init_db()`. If schema validation fails, check that the table exists in MySQL.

### Duplicate ORM columns in `ProspectORM`
`outreach_sent`, `outreach_status`, `outreach_type`, `outreach_error`, `outreach_ts`, `outreach_attempts`, `outreach_last_attempt_ts` appear twice in `db/models.py` (lines ~337 and ~348). SQLAlchemy silently uses the last definition. Don't add more duplicates.

---

## LLM usage across the codebase

| Module | LLM used | How |
|---|---|---|
| `intelligence/dossier_generator.py` | `utils/llm_client.py` (provider from `LLM_PROVIDER`) | Dossier + outreach message |
| `outreach/linkedin_reply_handler.py` | `utils/llm_client.py` (provider from `LLM_PROVIDER`) | AI reply generation |
| `candidate_hunt/query_builder.py` | `utils/llm_client.py` | Query variant generation |
| `candidate_hunt/scoring.py` | `utils/llm_client.py` | JD relevance scoring |

`utils/llm_client.py` supports `openai`, `gemini`, `groq` via `LLM_PROVIDER` env var.

---

## Logging

All modules use `build_logger(name)` from `utils/logging.py`. Log level is `INFO` by default.

Reply handler uses `[SCRAPE]`, `[PROCESS]`, `[REOPEN]` prefixes on `INFO` logs — read these to trace exactly where the handler stops.

---

## When making changes

- **DB schema changes**: edit `db/models.py` AND `_REQUIRED_TABLES` set AND `init_db.py`. Never auto-migrate — run `python init_db.py` manually after schema changes.
- **New config knobs**: add to `_SETTING_CATALOG` in `config.py` AND add a typed field to the relevant `*Config` dataclass.
- **New LinkedIn selectors**: test in browser DevTools first. LinkedIn obfuscates class names — prefer `aria-label`, `href` patterns, or structural DOM signals over class names.
- **Reply handler tested with**: `python scheduler/p_reply_handler.py --test-prospect-id 1`

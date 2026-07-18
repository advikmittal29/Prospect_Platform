# Prospect Platform

A production-grade, scheduled recruitment intelligence platform that automates:
1. **Job ingestion**  scrapes Naukri.com for keyword-matched jobs
2. **Company + people research**  finds companies on LinkedIn, discovers hiring contacts
3. **Prospect dossiers**  LLM-generated decision-ready intelligence profiles
4. **Outreach messages**  personalised LinkedIn/email messages per prospect

---

## Architecture Overview
- `config.py`: unified app configuration (env driven)
- `db/models.py`: SQLAlchemy models + DB session helpers
- `jobs/`: Naukri browser + ingestion
- `research/`: LinkedIn company/people/profile research pipeline
- `candidate_hunt/`: LinkedIn candidate-hunting pipeline
- `intelligence/`: dossier generation
- `outreach/`: personalized outreach generation
- `scheduler/`: script entry points for each pipeline
- `app-ui/`: monitoring and control UI (frontend + backend API)

**Data flow**
1. `run_ingest.py` -> `naukri_jobs`
2. `run_research.py` -> `company_research` + `prospects`
3. `run_candidate_hunt.py` -> `candidate_profiles`
4. `run_intelligence.py` -> dossier + outreach fields on `prospects`

---

## Setup

### 1. Prerequisites

- Python 3.11+
- SQL Server (local or remote) with ODBC Driver 17 or 18
- Google Chrome installed (for LinkedIn research)
- OpenAI API key

### 2. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env  set DB_URL, LLM_API_KEY, CHROME_EXE, CHROME_USER_DATA_DIR, etc.
```

### 4. Create the database

Create a database named `ProspectDB` (or whatever you set in `DB_URL`) in SQL Server.
Tables are created automatically on first run.

### 5. Seed search keywords

```bash
python setup_keywords.py seed          # seeds from INGEST_KEYWORDS in .env
python setup_keywords.py list          # verify
python setup_keywords.py add "DevOps Engineer" --location "Bangalore" --max-jobs 40
```

---

## Running the pipelines

### Job Ingestion (Naukri)
```bash
python scheduler/run_ingest.py
```
Scrapes Naukri for all active keywords. Saves jobs and creates company stubs for research.

### Research Pipeline (LinkedIn)
```bash
python scheduler/run_research.py
```
**Requires Chrome open with LinkedIn logged in** (or Chrome will be launched using `CHROME_USER_DATA_DIR` which must already be signed into LinkedIn).

Processes pending companies: finds LinkedIn URL, discovers people, deep-assesses top profiles.

### Intelligence + Outreach
```bash
python scheduler/run_intelligence.py
```
Requires `LLM_API_KEY`. Generates dossiers and outreach messages for all assessed prospects.

---

## Windows Task Scheduler Setup

Create three tasks in Task Scheduler. Example for ingestion:

| Field | Value |
|---|---|
| Program | `C:\Python311\python.exe` |
| Arguments | `scheduler/run_ingest.py` |
| Start in | `C:\path\to\your\project` |
| Trigger | Daily, e.g. 07:00 |
| Run whether logged on or not | Yes |

Recommended schedule:
- **run_ingest**  every day at 07:00
- **run_research**  every day at 08:00 (after ingestion)
- **run_intelligence**  every day at 10:00 (after research)

---

## Configuration Reference

All settings are in `.env`. See `.env.example` for all variables with descriptions.

Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `DB_URL` | Windows Auth to localhost/ProspectDB | SQLAlchemy connection string |
| `LLM_API_KEY` |  | OpenAI secret key | 
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI model to use |
| `INGEST_KEYWORDS` | `AI Engineer,...` | Comma-separated Naukri search keywords |
| `INGEST_LOCATION` | _(empty)_ | Default location filter for Naukri |
| `CHROME_CDP_URL` | `http://127.0.0.1:9223` | CDP endpoint for LinkedIn Chrome |
| `CHROME_USER_DATA_DIR` | Windows temp path | Chrome profile with LinkedIn session |
| `RESEARCH_BATCH_SIZE` | `10` | Companies processed per research run |
| `BROWSER_HEADLESS` | `true` | Run Naukri browser headless |

---

## LinkedIn Requirements

The research pipeline **requires a real LinkedIn account** logged into Chrome. It attaches to a running Chrome via CDP rather than logging in programmatically, which is both more reliable and less likely to trigger LinkedIn security checks.

**First-time setup:**
1. Launch Chrome with the configured `CHROME_USER_DATA_DIR`
2. Log into LinkedIn manually
3. Close Chrome  the session is saved to the profile directory
4. Future runs will reuse the saved session automatically

---

## Database Tables

| Table | Purpose |
|---|---|
| `search_keywords` | Managed list of Naukri keywords to ingest |
| `naukri_jobs` | Raw job records from Naukri |
| `company_research` | One row per company; tracks LinkedIn discovery and research status |
| `prospects` | One row per person; full assessment scores, dossier JSON, outreach message |

---

## Extending

- **Add a new job source**: Implement a new `*_ingest.py` in `jobs/` following the same pattern as `naukri_ingest.py`
- **Add Anthropic as LLM**: Extend `LLMClient` in `dossier_generator.py` with an `anthropic` branch
- **Export to CRM**: Query `ProspectORM` where `outreach_message IS NOT NULL` and push via API
- **Add email channel**: Set `LLM_MODEL=gpt-4o` and extend `_pick_channel()` in `message_generator.py`


---

## Candidate Hunt Service (New)

This project now includes a separate LinkedIn candidate-hunting layer:

- Scheduler entry: `scheduler/run_candidate_hunt.py`
- Core pipeline: `candidate_hunt/pipeline.py`
- Search query generation: `candidate_hunt/query_builder.py`
- LinkedIn people-search ingestion: `candidate_hunt/search_extractor.py`
- Profile intelligence extraction: `candidate_hunt/profile_extractor.py`
- Evidence-backed scoring: `candidate_hunt/scoring.py`

### What it does

1. Reads jobs from `naukri_jobs`.
2. Builds role-keyword query variants from job context (LLM + deterministic fallback).
3. Searches LinkedIn People results and ingests minimal candidate cards.
4. Upserts candidates into `candidate_profiles` using canonical LinkedIn profile URL dedupe per job.
5. Visits selected profiles for deeper extraction.
6. Produces evidence-backed job-seeking + JD relevance assessments.

### New table

- `candidate_profiles`: staged candidate discovery/enrichment/scoring records.

### New job status fields on `naukri_jobs`

- `candidate_hunt_status`
- `candidate_hunt_attempts`
- `candidate_hunt_failure_reason`
- `candidate_hunted_at_utc`

### Run manually

```bash
python scheduler/run_candidate_hunt.py
```

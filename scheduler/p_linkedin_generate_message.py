"""
Scheduler entry point: LinkedIn ad-hoc outreach message draft.

Scrapes a single LinkedIn profile (no DB read/write of prospects — this is
for a URL that may not be a tracked prospect at all) and runs it through the
exact same two-stage LLM pipeline the regular pipeline uses (dossier ->
outreach message, via intelligence/dossier_generator.py and
outreach/message_generator.py) so an ad-hoc "LinkedIn" tab send gets a
message in the same voice/quality as every other prospect.

Prints a single marker line followed by a JSON result on stdout; the backend
extracts that from the captured subprocess log.

Usage:
    python scheduler/p_linkedin_generate_message.py --url <LINKEDIN_URL> [--agent-id N]

Prerequisites: Chrome must be open and logged into LinkedIn (same as every
other LinkedIn script), OR CDP Chrome will be launched automatically.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from agents import AgentConfigResolver
from config import AppConfig, DatabaseConfig, reset_runtime_settings_cache
from db import init_db
from intelligence.dossier_generator import LLMClient, _build_dossier_prompt
from intelligence.schemas import OutreachAngle, ProspectDossier, ProspectSignal
from outreach.message_generator import _build_outreach_prompt
from research.linkedin_browser import ChromeLauncher, LinkedInBrowser
from research.profile_assessor import LinkedInProfileAssessor, ProfileAssessment
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.scheduler.linkedin_generate", level=logging.INFO)

RESULT_MARKER = "===LINKEDIN_GENERATE_RESULT==="


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an ad-hoc LinkedIn outreach message draft.")
    parser.add_argument("--url", type=str, required=True, help="LinkedIn profile URL to draft a message for.")
    parser.add_argument("--agent-id", type=int, default=None)
    parser.add_argument("--agent-key", type=str, default=None)
    return parser.parse_args()


def _prospect_stub(profile: ProfileAssessment) -> SimpleNamespace:
    """Duck-typed stand-in for ProspectORM — _build_dossier_prompt/_build_outreach_prompt only read attributes."""
    return SimpleNamespace(
        name=profile.name,
        linkedin_profile_url=profile.profile_url,
        headline=profile.headline,
        location=profile.location,
        connection_degree=profile.connection_degree,
        current_title=profile.current_title_topcard or profile.current_title_experience,
        current_company=profile.current_company_topcard or profile.current_company_experience,
        tenure_hint=profile.tenure_hint,
        role_bucket=profile.role_bucket,
        profile_summary_text=profile.profile_summary_text,
        experiences=profile.experiences,
        recent_posts=profile.recent_posts,
        recent_posts_json=json.dumps(profile.recent_posts or []),
        search_confidence=0,
        company_match_confidence=profile.company_match_confidence,
        outreach_feasibility_score=profile.outreach_feasibility_score,
        contact_relevance_score=profile.contact_relevance_score,
        contact_relevance_bucket=profile.contact_relevance_bucket,
        message_available=profile.message_available,
        connect_available=profile.connect_available,
        contact_info_available=profile.contact_info_available,
        about_text=profile.about_text,
        assessment_reasons=profile.reasons,
        assessment_warnings=profile.warnings,
    )


def _company_stub(profile: ProfileAssessment) -> SimpleNamespace:
    """
    The prompt's "company" concept is the company the prospect currently
    works at (that's who we'd be pitching candidates/services to) — there's
    no Naukri-job-derived company_research row for an ad-hoc URL.
    """
    company_name = (
        profile.current_company_topcard
        or profile.current_company_experience
        or "their company"
    )
    return SimpleNamespace(
        company_name=company_name,
        linkedin_url=None,
        industry="unknown",
        employee_range="unknown",
        location=profile.location or "unknown",
        tagline=None,
    )


def _parse_dossier_json(raw_json: str, profile_url: str, name: Optional[str]) -> ProspectDossier:
    data = json.loads(raw_json)

    signals = []
    for s in data.get("signals", []):
        try:
            signals.append(ProspectSignal(**s))
        except Exception:
            pass

    angles = []
    for a in data.get("outreach_angles", []):
        try:
            angles.append(OutreachAngle(**a))
        except Exception:
            pass

    return ProspectDossier(
        profile_url=profile_url,
        name=name,
        role_summary=data.get("role_summary", ""),
        company_context=data.get("company_context", ""),
        relevance_verdict=data.get("relevance_verdict", ""),
        relevance_bucket=data.get("relevance_bucket", "moderate"),
        confidence_score=int(data.get("confidence_score", 50)),
        signals=signals,
        outreach_angles=angles,
        recommended_action=data.get("recommended_action", "message"),
        priority_rank=int(data.get("priority_rank", 1)),
        reasoning=data.get("reasoning", ""),
        caveats=data.get("caveats", []),
    )


def main() -> int:
    args = _parse_args()
    logger.info("=" * 60)
    logger.info("LINKEDIN AD-HOC MESSAGE GENERATION START — %s", args.url)
    logger.info("=" * 60)

    browser: LinkedInBrowser | None = None
    try:
        init_db(DatabaseConfig())
        reset_runtime_settings_cache()
        config = AppConfig()
        agent = AgentConfigResolver(config).resolve(agent_id=args.agent_id, agent_key=args.agent_key)
        prompt_context = agent.prompt_context

        launcher = ChromeLauncher(config.chrome)
        launcher.launch_if_needed()
        browser = LinkedInBrowser(config.chrome)
        browser.start()
        browser.ensure_logged_in()

        assessor = LinkedInProfileAssessor(
            browser.page,
            auth_handler=browser.ensure_logged_in,
            llm_config=config.llm,
            prompt_context=prompt_context,
        )
        profile = assessor.assess(profile_url=args.url, target_company_name="")
        if not profile.name and not profile.headline and not profile.about_text:
            raise RuntimeError(
                "Could not read this profile (it may not exist, may be blocked, or the page didn't load)."
            )
        logger.info("Scraped profile: name=%s headline=%s", profile.name, profile.headline)

        llm = LLMClient(config.llm)
        prospect = _prospect_stub(profile)
        company = _company_stub(profile)

        dossier_prompt = _build_dossier_prompt(prospect, company, prompt_context=prompt_context)
        dossier_system = render_prompt("dossier_system", agent_context=prompt_context)
        dossier_raw = llm.chat(dossier_system, dossier_prompt)
        dossier = _parse_dossier_json(dossier_raw, profile.profile_url, profile.name)
        logger.info("Dossier generated: bucket=%s angles=%d", dossier.relevance_bucket, len(dossier.outreach_angles))

        channel = "linkedin_message"
        outreach_prompt = _build_outreach_prompt(
            prospect, company, dossier, channel,
            prompt_context=prompt_context,
            recruiter_name=config.outreach.recruiter_name,
            agency_name=config.outreach.agency_name,
        )
        outreach_system = render_prompt("outreach_system", agent_context=prompt_context)
        outreach_raw = llm.chat(outreach_system, outreach_prompt)
        outreach_data = json.loads(outreach_raw)
        message_body = (outreach_data.get("message_body") or "").strip()
        if not message_body:
            raise RuntimeError("LLM did not return a usable message body.")

        logger.info("Message generated (%d words).", len(message_body.split()))
        print(RESULT_MARKER)
        print(json.dumps({
            "message": message_body,
            "name": profile.name,
            "headline": profile.headline,
            "current_company": prospect.current_company,
        }))

        logger.info("LINKEDIN AD-HOC MESSAGE GENERATION COMPLETE")
        return 0

    except Exception:
        logger.error("LINKEDIN AD-HOC MESSAGE GENERATION FAILED\n%s", traceback.format_exc())
        return 1

    finally:
        if browser is not None:
            try:
                browser.stop()
            except Exception:
                logger.warning("Browser stop raised an exception.", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())

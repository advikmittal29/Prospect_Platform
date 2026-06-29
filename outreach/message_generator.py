"""
Outreach message generator.

Reads completed dossiers (dossier_status='completed', outreach_message IS NULL)
for prime/strong/moderate prospects and generates personalised outreach messages
via the LLM. Persists messages back to ProspectORM.
"""
from __future__ import annotations

import json
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

from config import AppConfig
from db import CompanyResearchORM, ProspectORM, resolve_agent_id, session_scope
from intelligence.dossier_generator import LLMClient
from intelligence.schemas import OutreachMessage, ProspectDossier
from utils.logging import build_logger
from utils.prompt_loader import render_prompt

logger = build_logger("prospect.outreach.generator")


def _format_recent_posts_for_outreach(recent_posts_json: Optional[str]) -> str:
    """
    Convert the stored recent_posts_json (list of structured post blocks) into
    a clean section for LLM outreach context. Returns 'n/a' if nothing usable.
    """
    if not recent_posts_json:
        return "n/a"
    try:
        posts = json.loads(recent_posts_json)
    except Exception:
        return "n/a"
    if not isinstance(posts, list) or not posts:
        return "n/a"

    # posts are already formatted blocks (POST N / Time / Recency / Type / Text)
    # Pass them through as-is, separated by ---
    blocks = [str(p).strip() for p in posts if str(p).strip()]
    if not blocks:
        return "n/a"
    return "\n\n---\n\n".join(blocks)


def _build_outreach_prompt(
    prospect: ProspectORM,
    company: CompanyResearchORM,
    dossier: ProspectDossier,
    channel: str,
    prompt_context: Optional[Dict[str, Any]] = None,
    recruiter_name: str = "Alex",
    agency_name: str = "RecruitPro",
) -> str:
    angle_text = ""
    if dossier.outreach_angles:
        angle_text = "Suggested angle: " + dossier.outreach_angles[0].angle

    signal_text = ""
    if dossier.signals:
        top = dossier.signals[0]
        signal_text = f"{top.signal} | {top.interpretation}"

    return render_prompt(
        "outreach_user",
        agent_context=prompt_context,
        seller_name=recruiter_name or "Alex",
        business_name=agency_name or "RecruitPro",
        channel=channel or "linkedin_connect",
        prospect_name=prospect.name or "unknown",
        prospect_title=prospect.current_title or prospect.headline or "unknown",
        company_name=company.company_name or "unknown",
        company_industry=company.industry or "unknown",
        company_size=company.employee_range or "unknown",
        prospect_location=prospect.location or "unknown",
        connection_degree=prospect.connection_degree or "unknown",
        role_bucket=prospect.role_bucket or "unknown",
        role_summary=dossier.role_summary or "n/a",
        relevance_bucket=dossier.relevance_bucket or "unknown",
        relevance_score=dossier.confidence_score,
        relevance_verdict=dossier.relevance_verdict or "n/a",
        key_signal=signal_text or "n/a",
        suggested_angle=angle_text or "n/a",
        about_snippet=(prospect.about_text or "n/a")[:400],
        recent_posts_context=_format_recent_posts_for_outreach(prospect.recent_posts_json),
    )


def _pick_channel(prospect: ProspectORM, dossier: ProspectDossier) -> str:
    """Determine best outreach channel based on availability."""
    degree = (prospect.connection_degree or "").replace("·", " ").replace("  ", " ").strip().lower()
    if prospect.connect_available and degree in ("", "3rd", "3rd 3rd"):
        return "linkedin_connect"
    if prospect.message_available:
        return "linkedin_message"
    return "linkedin_connect"


def _resolve_channel_override(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    norm = str(value).strip().lower()
    allowed = {"linkedin_connect", "linkedin_message", "email"}
    return norm if norm in allowed else None


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class OutreachGenerator:
    """
    Generates personalized outreach messages for all prospects that have
    a completed dossier but no outreach message yet.
    """

    ELIGIBLE_BUCKETS = {"prime", "strong", "moderate"}

    def __init__(
        self,
        config: AppConfig,
        *,
        agent_id: Optional[int] = None,
        agent_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._cfg = config
        self._agent_id = resolve_agent_id(
            agent_id=agent_id,
            default_agent_key=config.agent_runtime.default_agent_key,
        )
        self._prompt_context = dict(agent_context or {})
        self._llm = LLMClient(config.llm)

    def run(
        self,
        recruiter_name: Optional[str] = None,
        agency_name: Optional[str] = None,
    ) -> dict:
        recruiter_name = (recruiter_name or self._cfg.outreach.recruiter_name).strip()
        agency_name = (agency_name or self._cfg.outreach.agency_name).strip()
        forced_channel = _resolve_channel_override(self._cfg.outreach.force_channel)
        if self._cfg.outreach.force_channel and not forced_channel:
            logger.warning(
                "Invalid OUTREACH_FORCE_CHANNEL=%r. Falling back to auto channel selection.",
                self._cfg.outreach.force_channel,
            )

        stats = {
            "processed": 0,
            "generated": 0,
            "skipped": 0,
            "errors": 0,
            "email_sent": 0,
            "email_failed": 0,
            "email_skipped": 0,
            "channel_forced_count": 0,
            "channel_auto_count": 0,
        }

        prospects = self._load_pending()
        logger.info("Outreach generation: %d prospects to process.", len(prospects))

        company_cache: Dict[int, CompanyResearchORM] = {}

        for prospect in prospects:
            stats["processed"] += 1
            try:
                bucket = (prospect.contact_relevance_bucket or "").lower()
                if bucket not in self.ELIGIBLE_BUCKETS or not prospect.dossier_json:
                    self._mark_no_message(prospect.id, "ineligible_bucket_or_no_dossier")
                    stats["skipped"] += 1
                    continue

                company = self._get_company(prospect.company_research_id, company_cache)
                if company is None:
                    stats["skipped"] += 1
                    continue

                dossier = self._parse_dossier(prospect.dossier_json)
                if dossier is None:
                    stats["errors"] += 1
                    continue

                if forced_channel:
                    channel = forced_channel
                    stats["channel_forced_count"] += 1
                else:
                    channel = _pick_channel(prospect, dossier)
                    stats["channel_auto_count"] += 1
                message = self._generate(prospect, company, dossier, channel, recruiter_name, agency_name)

                if message is None:
                    stats["errors"] += 1
                    continue

                self._save_message(prospect.id, message)
                stats["generated"] += 1
                logger.info(
                    "  Outreach for %s via %s (%d words)",
                    prospect.name or prospect.linkedin_profile_url,
                    message.channel,
                    message.word_count,
                )
                sent = self._send_test_email_if_enabled(
                    prospect=prospect,
                    company=company,
                    message=message,
                    recruiter_name=recruiter_name,
                    agency_name=agency_name,
                )
                sent_flag, sent_error, sent_target = sent
                if sent_flag is True:
                    stats["email_sent"] += 1
                    self._mark_dispatch_state(
                        prospect_id=prospect.id,
                        status="sent",
                        channel="email_test",
                        target=sent_target,
                        error=None,
                        increment_attempt=True,
                        mark_sent=True,
                    )
                elif sent_flag is False:
                    stats["email_failed"] += 1
                    self._mark_dispatch_state(
                        prospect_id=prospect.id,
                        status="failed",
                        channel="email_test",
                        target=sent_target,
                        error=sent_error or "email_dispatch_failed",
                        increment_attempt=True,
                        mark_sent=False,
                    )
                else:
                    stats["email_skipped"] += 1
                    self._mark_dispatch_state(
                        prospect_id=prospect.id,
                        status="ready_manual",
                        channel=message.channel,
                        target="linkedin" if message.channel.startswith("linkedin") else None,
                        error=None,
                        increment_attempt=False,
                        mark_sent=False,
                    )

            except Exception as exc:
                logger.error("Error generating outreach for prospect %d: %s", prospect.id, exc, exc_info=True)
                stats["errors"] += 1

        logger.info("Outreach generation complete. Stats: %s", stats)
        return stats

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    def _generate(
        self,
        prospect: ProspectORM,
        company: CompanyResearchORM,
        dossier: ProspectDossier,
        channel: str,
        recruiter_name: str,
        agency_name: str,
    ) -> Optional[OutreachMessage]:
        prompt = _build_outreach_prompt(
            prospect,
            company,
            dossier,
            channel,
            self._prompt_context,
            recruiter_name,
            agency_name,
        )
        system_prompt = render_prompt(
            "outreach_system",
            agent_context=self._prompt_context,
        )
        try:
            raw_json = self._llm.chat(system_prompt, prompt)
        except Exception as exc:
            logger.error("LLM call failed for prospect %d: %s", prospect.id, exc)
            return None

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON from LLM for prospect %d: %s", prospect.id, exc)
            return None

        # Enforce connect note length limit
        body = data.get("message_body", "")
        if data.get("channel") == "linkedin_connect" and len(body) > 300:
            body = body[:297] + "..."

        try:
            msg = OutreachMessage(
                profile_url=prospect.linkedin_profile_url,
                name=prospect.name,
                channel=data.get("channel", channel),
                subject=data.get("subject"),
                message_body=body,
                word_count=len(body.split()),
                tone=data.get("tone", "professional"),
                personalization_hooks=data.get("personalization_hooks", []),
                suggested_followup=data.get("suggested_followup"),
            )
            return msg
        except Exception as exc:
            logger.error("Outreach message validation failed for prospect %d: %s", prospect.id, exc)
            return None

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_pending(self) -> List[ProspectORM]:
        with session_scope() as session:
            rows = (
                session.query(ProspectORM)
                .filter(
                    ProspectORM.dossier_status == "completed",
                    ProspectORM.outreach_message.is_(None),
                    ProspectORM.agent_id == self._agent_id,
                    (ProspectORM.outreach_dispatch_status.is_(None))
                    | (ProspectORM.outreach_dispatch_status != "sent"),
                )
                .order_by(ProspectORM.contact_relevance_score.desc())
                .all()
            )
            session.expunge_all()
            return rows

    def _get_company(
        self, company_id: int, cache: Dict[int, CompanyResearchORM]
    ) -> Optional[CompanyResearchORM]:
        if company_id in cache:
            return cache[company_id]
        with session_scope() as session:
            row = (
                session.query(CompanyResearchORM)
                .filter_by(id=company_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if row:
                session.expunge(row)
                cache[company_id] = row
            return row

    def _parse_dossier(self, dossier_json: str) -> Optional[ProspectDossier]:
        try:
            return ProspectDossier.model_validate_json(dossier_json)
        except Exception as exc:
            logger.warning("Could not parse dossier JSON: %s", exc)
            return None

    def _save_message(self, prospect_id: int, message: OutreachMessage) -> None:
        with session_scope() as session:
            row = (
                session.query(ProspectORM)
                .filter_by(id=prospect_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if not row:
                return
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            row.outreach_message = message.model_dump_json()
            row.outreach_generated_at_utc = now
            row.outreach_dispatch_status = "generated"
            row.outreach_dispatch_channel = message.channel
            row.outreach_dispatch_target = None
            row.outreach_dispatch_error = None
            row.outreach_last_dispatch_at_utc = None
            row.outreach_sent_at_utc = None

    def _mark_no_message(self, prospect_id: int, reason: str) -> None:
        # We don't update the DB here  just log. Outreach can be reattempted.
        logger.debug("Skipping outreach for prospect %d: %s", prospect_id, reason)

    # ------------------------------------------------------------------
    # Test-mode dispatch
    # ------------------------------------------------------------------

    def _send_test_email_if_enabled(
        self,
        *,
        prospect: ProspectORM,
        company: CompanyResearchORM,
        message: OutreachMessage,
        recruiter_name: str,
        agency_name: str,
    ) -> Tuple[Optional[bool], Optional[str], Optional[str]]:
        cfg = self._cfg.outreach_dispatch
        if not cfg.test_mode_enabled:
            return None, None, None
        if not cfg.test_recipient_email:
            err = "OUTREACH_TEST_MODE_ENABLED is true but OUTREACH_TEST_RECIPIENT_EMAIL is empty."
            logger.warning(err)
            return False, err, None
        if not cfg.smtp_host:
            err = "OUTREACH_TEST_MODE_ENABLED is true but SMTP_HOST is empty."
            logger.warning(err)
            return False, err, cfg.test_recipient_email

        sender = cfg.smtp_from_email or cfg.smtp_username or "noreply@localhost"
        subject = (
            f"{cfg.subject_prefix} {message.channel} | "
            f"{prospect.name or 'Unknown'} @ {company.company_name or 'Unknown Company'}"
        )
        body = "\n".join(
            [
                "Test outreach dispatch",
                "",
                f"Prospect: {prospect.name or 'Unknown'}",
                f"Company: {company.company_name or 'Unknown'}",
                f"Profile URL: {prospect.linkedin_profile_url or 'N/A'}",
                f"Channel: {message.channel}",
                f"Tone: {message.tone}",
                f"Word count: {message.word_count}",
                f"Recruiter: {recruiter_name}",
                f"Agency: {agency_name}",
                "",
                f"Subject: {message.subject or '(none)'}",
                "",
                "Message body:",
                message.message_body or "",
                "",
                "Suggested follow-up:",
                message.suggested_followup or "(none)",
            ]
        )

        email = EmailMessage()
        email["From"] = sender
        email["To"] = cfg.test_recipient_email
        email["Subject"] = subject
        email.set_content(body)

        try:
            if cfg.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    cfg.smtp_host,
                    cfg.smtp_port,
                    timeout=cfg.smtp_timeout_seconds,
                ) as smtp:
                    if cfg.smtp_username:
                        smtp.login(cfg.smtp_username, cfg.smtp_password or "")
                    smtp.send_message(email)
            else:
                with smtplib.SMTP(
                    cfg.smtp_host,
                    cfg.smtp_port,
                    timeout=cfg.smtp_timeout_seconds,
                ) as smtp:
                    smtp.ehlo()
                    if cfg.smtp_use_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    if cfg.smtp_username:
                        smtp.login(cfg.smtp_username, cfg.smtp_password or "")
                    smtp.send_message(email)

            logger.info("  Test outreach email sent to %s for prospect=%s", cfg.test_recipient_email, prospect.id)
            return True, None, cfg.test_recipient_email
        except Exception as exc:
            err = str(exc)
            logger.error(
                "  Failed to send test outreach email (prospect=%s, to=%s): %s",
                prospect.id,
                cfg.test_recipient_email,
                exc,
            )
            return False, err, cfg.test_recipient_email

    def _mark_dispatch_state(
        self,
        *,
        prospect_id: int,
        status: str,
        channel: Optional[str],
        target: Optional[str],
        error: Optional[str],
        increment_attempt: bool,
        mark_sent: bool,
    ) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with session_scope() as session:
            row = (
                session.query(ProspectORM)
                .filter_by(id=prospect_id, agent_id=self._agent_id)
                .one_or_none()
            )
            if not row:
                return
            row.outreach_dispatch_status = (status or "not_sent")[:50]
            if channel:
                row.outreach_dispatch_channel = channel[:50]
            row.outreach_dispatch_target = (target[:500] if target else None)
            if increment_attempt:
                row.outreach_dispatch_attempts = int(row.outreach_dispatch_attempts or 0) + 1
                row.outreach_last_dispatch_at_utc = now
            if error:
                row.outreach_dispatch_error = error[:2000]
            else:
                row.outreach_dispatch_error = None
            row.outreach_sent_at_utc = now if mark_sent else None

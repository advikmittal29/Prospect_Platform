"""
Naukri job ingestion pipeline.

Responsibilities:
- Fetch job listing pages for each keyword from SearchKeywordORM
- Parse detail pages (JSON-LD + DOM fallback)
- Deduplicate against DB
- Upsert NaukriJobORM rows
- Flag new unique company names for downstream research
- Create CompanyResearchORM stubs for newly seen companies
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import AppConfig
from db import (
    CompanyResearchORM,
    NaukriJobORM,
    SearchKeywordORM,
    resolve_agent_id,
    session_scope,
)
from jobs.browser import NaukriBrowser, PageHandle
from utils.logging import build_logger

logger = build_logger("prospect.jobs.ingest")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean_html(html: Optional[str]) -> Optional[str]:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    text = soup.get_text("\n", strip=True)
    text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _norm_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip() or None
    if isinstance(value, (list, tuple, set)):
        parts = [p for item in value if (p := _norm_text(item))]
        return ", ".join(dict.fromkeys(parts)) or None
    if isinstance(value, dict):
        for key in ("name", "value", "text", "title", "label"):
            if v := _norm_text(value.get(key)):
                return v
        return None
    return re.sub(r"\s+", " ", str(value)).strip() or None


def _norm_skills(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in re.split(r"[,\n|;]+", value) if t.strip()]
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            out.extend(_norm_skills(item) if not isinstance(item, str)
                       else [item.strip()] if item.strip() else [])
        return out
    v = _norm_text(value)
    return _norm_skills(v) if v else []


def _norm_company_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return re.sub(r"[,\s]+", "", text).lower() or None


def _normalize_title_company_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\u00a0", " ")
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ,")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qsl(parsed.query, keep_blank_values=True)
    keep = {"jobAge", "k", "q", "experience", "location", "l", "pageNo"}
    qs = [(k, v) for k, v in qs if k in keep]
    clean = parsed._replace(fragment="", query=urlencode(qs))
    return urlunparse(clean)


def _find_jobposting(data: object) -> dict:
    if isinstance(data, dict):
        node_type = data.get("@type", "")
        types = [node_type] if isinstance(node_type, str) else list(node_type)
        if any(str(t).lower() == "jobposting" for t in types):
            return data
        for key in ("@graph", "graph", "mainEntity", "jobPosting"):
            if key in data:
                found = _find_jobposting(data[key])
                if found:
                    return found
        for v in data.values():
            if isinstance(v, (dict, list)):
                found = _find_jobposting(v)
                if found:
                    return found
        return {}
    if isinstance(data, (list, tuple)):
        for item in data:
            found = _find_jobposting(item)
            if found:
                return found
    return {}


# ---------------------------------------------------------------------------
# Pydantic job record
# ---------------------------------------------------------------------------

class JobRecord(BaseModel):
    source: str = "naukri"
    search_keyword: str
    search_location: Optional[str] = None

    job_id: Optional[str] = None
    title: Optional[str] = None
    company_name: Optional[str] = None
    posted_date: Optional[str] = None
    posted_relative: Optional[str] = None
    valid_through: Optional[str] = None

    experience_text: Optional[str] = None
    salary_text: Optional[str] = None
    location_text: Optional[str] = None
    employment_type: Optional[str] = None
    industry: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    role_category: Optional[str] = None
    education: Optional[str] = None

    skills: List[str] = Field(default_factory=list)
    job_description_text: Optional[str] = None
    ai_summary: Optional[str] = None

    openings: Optional[str] = None
    applicants: Optional[str] = None
    company_rating: Optional[str] = None
    company_review_count: Optional[str] = None

    job_url: str
    canonical_job_url: str

    extraction_confidence: int = 0
    extraction_notes: List[str] = Field(default_factory=list)
    fetched_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("skills")
    @classmethod
    def _dedup_skills(cls, v: List[str]) -> List[str]:
        seen: Set[str] = set()
        out = []
        for item in v or []:
            s = re.sub(r"\s+", " ", str(item)).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
        return out


# ---------------------------------------------------------------------------
# Detail page parser
# ---------------------------------------------------------------------------

class NaukriDetailParser:
    def __init__(self, cfg_naukri):
        self._cfg = cfg_naukri

    def parse(self, handle: PageHandle, detail_url: str, search_keyword: str,
              search_location: Optional[str]) -> Optional[JobRecord]:
        html = handle.full_html()

        json_ld = self._extract_json_ld(handle)
        if not json_ld:
            json_ld = self._extract_json_ld_from_html(html)

        dom_live = self._extract_dom(handle)
        dom_html = self._extract_dom_from_html(html)
        dom_data = self._merge_dom_sources(dom_live, dom_html)

        data = self._merge_sources(
            detail_url=detail_url,
            search_keyword=search_keyword,
            search_location=search_location,
            json_ld=json_ld,
            dom_data=dom_data,
        )

        if not data.get("title") and not data.get("company_name"):
            logger.debug("Skipping %s  no title or company extracted.", detail_url)
            return None

        return JobRecord(**data)

    # ------ JSON-LD ------

    def _extract_json_ld(self, handle: PageHandle) -> dict:
        try:
            scripts = handle.page.locator(self._cfg.json_ld_selector)
            for i in range(scripts.count()):
                try:
                    raw = scripts.nth(i).inner_text(timeout=2000)
                    data = json.loads(raw)
                    found = _find_jobposting(data)
                    if found:
                        return found
                except Exception:
                    pass
        except Exception:
            pass
        return {}

    def _extract_json_ld_from_html(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.select(self._cfg.json_ld_selector)
        for script in scripts:
            raw = script.string or script.get_text() or ""
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
                found = _find_jobposting(data)
                if found:
                    return found
            except Exception:
                continue
        return {}

    # ------ DOM fallback ------

    def _extract_dom(self, handle: PageHandle) -> dict:
        data: Dict[str, object] = {}
        cfg = self._cfg

        data["title"] = handle.text(cfg.job_title_selector)
        data["company_name"] = handle.text(cfg.company_name_selector)
        data["skills"] = handle.texts(cfg.skills_selector)

        education_selector = getattr(
            cfg,
            "education_selector",
            "[class*='education'] [class*='details']",
        )
        data["education"] = handle.text(education_selector)

        desc_html = handle.inner_html(cfg.job_desc_selector)
        data["job_description_text"] = _clean_html(desc_html) if desc_html else None

        # Experience / salary / location
        try:
            data["experience_text"] = handle.page.locator("[class*='jhc__exp'] span").first.inner_text(timeout=1500).strip()
        except Exception:
            data["experience_text"] = None
        try:
            data["salary_text"] = handle.page.locator("[class*='jhc__salary'] span").first.inner_text(timeout=1500).strip()
        except Exception:
            data["salary_text"] = None
        try:
            data["location_text"] = handle.page.locator("[class*='jhc__location']").first.inner_text(timeout=1500).strip()
        except Exception:
            data["location_text"] = None

        # Rating / reviews
        try:
            data["company_rating"] = handle.page.locator("[class*='amb-rating']").first.inner_text(timeout=1200).strip()
        except Exception:
            data["company_rating"] = None
        try:
            data["company_review_count"] = handle.page.locator("[class*='amb-reviews']").first.inner_text(timeout=1200).strip()
        except Exception:
            data["company_review_count"] = None

        # Stats: posted/openings/applicants (label + value rows)
        data["posted_relative"] = None
        data["openings"] = None
        data["applicants"] = None
        stats = handle.page.locator(f"{cfg.stats_container_selector} [class*='stat']")
        for i in range(stats.count()):
            try:
                row = stats.nth(i)
                label = row.locator("label").first.inner_text(timeout=1000).strip().rstrip(":").lower()
                value = row.locator("span").nth(1).inner_text(timeout=1000).strip()
                if not value:
                    continue
                if label == "posted":
                    data["posted_relative"] = value
                elif label == "openings":
                    data["openings"] = value
                elif label == "applicants":
                    data["applicants"] = value
            except Exception:
                pass

        # Structured details rows
        label_map = {
            "role": "role",
            "industry type": "industry",
            "department": "department",
            "employment type": "employment_type",
            "role category": "role_category",
        }
        try:
            rows = handle.page.locator(cfg.detail_row_selector)
            for i in range(min(rows.count(), 20)):
                try:
                    row = rows.nth(i)
                    label = row.locator("label").first.inner_text(timeout=1000).strip().rstrip(":").lower()
                    spans = row.locator("span")
                    texts: List[str] = []
                    for j in range(spans.count()):
                        t = spans.nth(j).inner_text(timeout=700).strip()
                        if t and t != ",":
                            texts.append(t)
                    value = ", ".join(dict.fromkeys(texts))
                    key = label_map.get(label)
                    if key and value:
                        data[key] = value
                except Exception:
                    pass
        except Exception:
            pass

        # Fallback parsing from unstructured stats text
        if not data.get("experience_text") or not data.get("salary_text"):
            try:
                stat_text = handle.page.locator(cfg.stats_container_selector).first.inner_text(timeout=2000)
                lines = [ln.strip() for ln in stat_text.splitlines() if ln.strip()]
                for ln in lines:
                    low = ln.lower()
                    if not data.get("experience_text") and re.search(r"\d+\s*(yr|year|yrs|month|months)", low):
                        data["experience_text"] = ln
                    if not data.get("salary_text") and re.search(r"\b(rs\.?|lpa|lakh|per\s+annum|pa)\b", low):
                        data["salary_text"] = ln
            except Exception:
                pass

        return data

    def _extract_dom_from_html(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        data: Dict[str, object] = {}
        cfg = self._cfg

        def _txt(selector: str) -> Optional[str]:
            node = soup.select_one(selector)
            if node is None:
                return None
            val = node.get_text(" ", strip=True)
            return val or None

        data["title"] = _txt(cfg.job_title_selector)
        data["company_name"] = _txt(cfg.company_name_selector)

        desc_node = soup.select_one(cfg.job_desc_selector)
        data["job_description_text"] = _clean_html(desc_node.decode_contents()) if desc_node else None

        data["skills"] = [
            node.get_text(" ", strip=True)
            for node in soup.select(cfg.skills_selector)
            if node.get_text(" ", strip=True)
        ]
        education_selector = getattr(
            cfg,
            "education_selector",
            "[class*='education'] [class*='details']",
        )
        data["education"] = _txt(education_selector)

        data["experience_text"] = _txt("[class*='jhc__exp'] span")
        data["salary_text"] = _txt("[class*='jhc__salary'] span")
        data["location_text"] = _txt("[class*='jhc__location']")
        data["company_rating"] = _txt("[class*='amb-rating']")
        data["company_review_count"] = _txt("[class*='amb-reviews']")

        data["posted_relative"] = None
        data["openings"] = None
        data["applicants"] = None
        for row in soup.select(f"{cfg.stats_container_selector} [class*='stat']"):
            label_node = row.select_one("label")
            if label_node is None:
                continue
            label = label_node.get_text(" ", strip=True).rstrip(":").lower()
            spans = row.select("span")
            value = spans[1].get_text(" ", strip=True) if len(spans) > 1 else None
            if not value:
                continue
            if label == "posted":
                data["posted_relative"] = value
            elif label == "openings":
                data["openings"] = value
            elif label == "applicants":
                data["applicants"] = value

        label_map = {
            "role": "role",
            "industry type": "industry",
            "department": "department",
            "employment type": "employment_type",
            "role category": "role_category",
        }
        for row in soup.select(cfg.detail_row_selector):
            label_node = row.select_one("label")
            if label_node is None:
                continue
            label = label_node.get_text(" ", strip=True).rstrip(":").lower()
            texts: List[str] = []
            for node in row.select("span"):
                text = node.get_text(" ", strip=True)
                if text and text != ",":
                    texts.append(text)
            value = ", ".join(dict.fromkeys(texts))
            key = label_map.get(label)
            if key and value:
                data[key] = value

        return data

    def _merge_dom_sources(self, primary: dict, secondary: dict) -> dict:
        merged: Dict[str, object] = {}
        keys = set(primary.keys()) | set(secondary.keys())
        for key in keys:
            p = primary.get(key)
            s = secondary.get(key)
            if isinstance(p, list) or isinstance(s, list):
                out: List[str] = []
                for item in (p or []):
                    if item:
                        out.append(str(item))
                for item in (s or []):
                    if item:
                        out.append(str(item))
                merged[key] = list(dict.fromkeys(out))
                continue
            if p not in (None, "", []):
                merged[key] = p
            else:
                merged[key] = s
        return merged

    def _merge_sources(
        self,
        detail_url: str,
        search_keyword: str,
        search_location: Optional[str],
        json_ld: dict,
        dom_data: dict,
    ) -> dict:
        notes: List[str] = []
        confidence = 0

        title = _norm_text(json_ld.get("title")) or _norm_text(dom_data.get("title"))
        if json_ld.get("title"):
            confidence += 20
            notes.append("title_from_jsonld")
        elif dom_data.get("title"):
            confidence += 10
            notes.append("title_from_dom")

        company_name = (
            _norm_text((json_ld.get("hiringOrganization") or {}).get("name"))
            or _norm_text(dom_data.get("company_name"))
        )
        if (json_ld.get("hiringOrganization") or {}).get("name"):
            confidence += 20
            notes.append("company_from_jsonld")
        elif dom_data.get("company_name"):
            confidence += 10
            notes.append("company_from_dom")

        title = _normalize_title_company_text(title)
        company_name = _normalize_title_company_text(company_name)

        json_desc = json_ld.get("description")
        desc_text = _clean_html(json_desc if isinstance(json_desc, str) else _norm_text(json_desc))
        job_description_text = desc_text or _norm_text(dom_data.get("job_description_text"))
        if json_desc:
            confidence += 20
            notes.append("description_from_jsonld")
        elif dom_data.get("job_description_text"):
            confidence += 10
            notes.append("description_from_dom")

        skills = _norm_skills(json_ld.get("skills") or dom_data.get("skills") or [])
        if json_ld.get("skills"):
            confidence += 15
            notes.append("skills_from_jsonld")
        elif dom_data.get("skills"):
            confidence += 8
            notes.append("skills_from_dom")

        experience_text = _norm_text(dom_data.get("experience_text"))
        exp_req = json_ld.get("experienceRequirements")
        if not experience_text and isinstance(exp_req, dict):
            months = exp_req.get("monthsOfExperience")
            if months:
                experience_text = f"{months} months"

        return {
            "source": "naukri",
            "search_keyword": search_keyword,
            "search_location": search_location,
            "job_id": self._extract_identifier(json_ld),
            "title": title,
            "company_name": company_name,
            "posted_date": self._extract_date(json_ld.get("datePosted")),
            "posted_relative": _norm_text(dom_data.get("posted_relative")),
            "valid_through": self._extract_date(json_ld.get("validThrough")),
            "experience_text": experience_text,
            "salary_text": _norm_text(dom_data.get("salary_text")) or self._extract_salary(json_ld),
            "location_text": _norm_text(dom_data.get("location_text")) or self._extract_location(json_ld),
            "employment_type": _norm_text(json_ld.get("employmentType") or dom_data.get("employment_type")),
            "industry": _norm_text(json_ld.get("industry") or dom_data.get("industry")),
            "department": _norm_text(json_ld.get("occupationalCategory") or dom_data.get("department")),
            "role": _norm_text(dom_data.get("role") or json_ld.get("responsibilities")),
            "role_category": _norm_text(dom_data.get("role_category")),
            "education": _norm_text(self._extract_education(json_ld) or dom_data.get("education")),
            "skills": skills,
            "job_description_text": job_description_text,
            "ai_summary": self._simple_summary(title, company_name, job_description_text, skills),
            "openings": _norm_text(dom_data.get("openings")),
            "applicants": _norm_text(dom_data.get("applicants")),
            "company_rating": _norm_text(dom_data.get("company_rating")),
            "company_review_count": _norm_text(dom_data.get("company_review_count")),
            "job_url": detail_url,
            "canonical_job_url": _canonicalize_url(detail_url),
            "extraction_confidence": min(confidence, 100),
            "extraction_notes": notes,
        }

    # ------ field extractors ------

    def _extract_date(self, value: object) -> Optional[str]:
        if not value:
            return None
        s = _norm_text(value)
        if not s:
            return None
        # Try to normalise to YYYY-MM-DD
        match = re.search(r"\d{4}-\d{2}-\d{2}", s)
        return match.group(0) if match else s[:20]

    def _extract_salary(self, json_ld: dict) -> Optional[str]:
        bs = json_ld.get("baseSalary")
        if not isinstance(bs, dict):
            return None
        val = bs.get("value")
        if isinstance(val, dict):
            lo = _norm_text(val.get("minValue"))
            hi = _norm_text(val.get("maxValue"))
            unit = _norm_text(val.get("unitText"))
            amount = _norm_text(val.get("value"))
            if lo and hi:
                return f"{lo}-{hi} {unit}" if unit else f"{lo}-{hi}"
            if amount:
                return f"{amount} {unit}" if unit else amount
            if lo:
                return lo
            if hi:
                return hi
        return _norm_text(val)

    def _extract_identifier(self, json_ld: dict) -> Optional[str]:
        ident = json_ld.get("identifier")
        if isinstance(ident, dict):
            return _norm_text(ident.get("value") or ident.get("name"))
        return _norm_text(ident)

    def _extract_location(self, json_ld: dict) -> Optional[str]:
        loc = json_ld.get("jobLocation")
        if not loc:
            return None
        parts: List[str] = []

        def _collect(item: object) -> None:
            if isinstance(item, dict):
                addr = item.get("address")
                if isinstance(addr, dict):
                    locality = _norm_text(addr.get("addressLocality"))
                    if locality:
                        parts.extend(p.strip() for p in locality.split(",") if p.strip())
                direct = _norm_text(item.get("addressLocality"))
                if direct:
                    parts.extend(p.strip() for p in direct.split(",") if p.strip())
            elif isinstance(item, (list, tuple)):
                for sub in item:
                    _collect(sub)
            else:
                t = _norm_text(item)
                if t:
                    parts.extend(p.strip() for p in t.split(",") if p.strip())

        _collect(loc)
        return ", ".join(dict.fromkeys(parts)) or None

    def _extract_education(self, json_ld: dict) -> Optional[str]:
        q = json_ld.get("qualifications")
        if isinstance(q, dict):
            return _norm_text(q.get("educationalLevel") or q.get("name"))
        return _norm_text(q)

    def _simple_summary(self, title: Optional[str], company: Optional[str],
                        description: Optional[str], skills: List[str]) -> Optional[str]:
        parts: List[str] = []
        if title and company:
            parts.append(f"{title} at {company}.")
        elif title:
            parts.append(f"{title}.")
        if description:
            parts.append(re.sub(r"\s+", " ", description).strip()[:500])
        if skills:
            parts.append("Key skills: " + ", ".join(skills[:10]) + ".")
        return " ".join(parts).strip() or None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _content_hash(job: JobRecord) -> str:
    raw = "||".join([
        str(job.title or ""),
        str(job.company_name or ""),
        str(job.posted_date or ""),
        str(job.location_text or ""),
        str(job.job_description_text or ""),
        ",".join(job.skills),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_business_key_duplicate(session: Session, job: JobRecord, *, agent_id: int) -> bool:
    title_key = _norm_company_key(job.title)
    company_key = _norm_company_key(job.company_name)
    posted = str(job.posted_date).strip() if job.posted_date else None
    if not (title_key and company_key and posted):
        return False

    db_title_key = func.replace(
        func.replace(
            func.lower(func.ltrim(func.rtrim(func.coalesce(NaukriJobORM.title, "")))),
            ",",
            "",
        ),
        " ",
        "",
    )
    db_company_key = func.replace(
        func.replace(
            func.lower(func.ltrim(func.rtrim(func.coalesce(NaukriJobORM.company_name, "")))),
            ",",
            "",
        ),
        " ",
        "",
    )

    existing = (
        session.query(NaukriJobORM.id)
        .filter(
            NaukriJobORM.agent_id == agent_id,
            NaukriJobORM.posted_date == posted,
            db_title_key == title_key,
            db_company_key == company_key,
        )
        .first()
    )
    return existing is not None


def upsert_job(session: Session, job: JobRecord, *, agent_id: int) -> str:
    """
    Upsert by canonical URL.
    Returns one of: inserted | updated | skipped.
    """
    existing = (
        session.query(NaukriJobORM)
        .filter_by(agent_id=agent_id, canonical_job_url=job.canonical_job_url)
        .one_or_none()
    )

    clean_title = _normalize_title_company_text(job.title)
    clean_company_name = _normalize_title_company_text(job.company_name)
    clean_posted_date = str(job.posted_date).strip() if job.posted_date else None

    payload = dict(
        agent_id=agent_id,
        source=job.source,
        search_keyword=job.search_keyword,
        search_location=job.search_location,
        job_id=job.job_id,
        title=clean_title,
        company_name=clean_company_name,
        posted_date=clean_posted_date,
        posted_relative=job.posted_relative,
        valid_through=job.valid_through,
        experience_text=job.experience_text,
        salary_text=job.salary_text,
        location_text=job.location_text,
        employment_type=job.employment_type,
        industry=job.industry,
        department=job.department,
        role=job.role,
        role_category=job.role_category,
        education=job.education,
        skills_json=json.dumps(job.skills, ensure_ascii=False),
        job_description_text=job.job_description_text,
        ai_summary=job.ai_summary,
        openings=job.openings,
        applicants=job.applicants,
        company_rating=job.company_rating,
        company_review_count=job.company_review_count,
        job_url=job.job_url,
        canonical_job_url=job.canonical_job_url,
        extraction_confidence=job.extraction_confidence,
        extraction_notes_json=json.dumps(job.extraction_notes, ensure_ascii=False),
        fetched_at_utc=job.fetched_at_utc.replace(tzinfo=None),
        content_hash=_content_hash(job),
    )

    if existing:
        for k, v in payload.items():
            setattr(existing, k, v)
        return "updated"

    if _is_business_key_duplicate(session, job, agent_id=agent_id):
        logger.info(
            "Business-key duplicate skipped: %s | %s | %s",
            clean_title,
            clean_company_name,
            clean_posted_date,
        )
        return "skipped"

    session.add(NaukriJobORM(**payload))
    return "inserted"


def ensure_company_stub(
    session: Session,
    company_name: str,
    *,
    agent_id: int,
    new_job_inserted: bool = False,
) -> None:
    """
    Ensure a CompanyResearchORM row exists.
    If a truly new job is inserted for an already researched company,
    re-queue company research to refresh insights/prospects.
    """
    if not company_name:
        return

    normalized = _norm_company_key(company_name)
    db_company_key = func.replace(
        func.replace(
            func.lower(func.ltrim(func.rtrim(func.coalesce(CompanyResearchORM.company_name, "")))),
            ",",
            "",
        ),
        " ",
        "",
    )
    row = (
        session.query(CompanyResearchORM)
        .filter(
            CompanyResearchORM.agent_id == agent_id,
            db_company_key == normalized,
        )
        .order_by(CompanyResearchORM.id.asc())
        .first()
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row is None:
        session.add(
            CompanyResearchORM(
                agent_id=agent_id,
                company_name=company_name,
                search_query=company_name,
                research_status="pending",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        logger.debug("Created company stub: %s", company_name)
        return

    # New job for an already researched company should trigger refresh.
    if new_job_inserted and row.research_status in {"completed", "failed", "skipped"}:
        row.research_status = "pending"
        row.failure_reason = None
        row.attempts = 0
        row.updated_at_utc = now
        if not row.search_query:
            row.search_query = row.company_name
        logger.info(
            "Re-queued company research due to new job insertion: %s (company_id=%s)",
            row.company_name,
            row.id,
        )


# ---------------------------------------------------------------------------
# Top-level ingestion runner
# ---------------------------------------------------------------------------

class NaukriIngestionRunner:
    """
    Orchestrates the full ingestion cycle:
      1. Load active keywords from DB
      2. For each keyword: scrape Naukri listing pages
      3. Fetch each detail page, parse, upsert
      4. Create company stubs for downstream research
      5. Update keyword last_run_utc
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        agent_id: Optional[int] = None,
    ) -> None:
        self._cfg = config
        self._agent_id = resolve_agent_id(
            agent_id=agent_id,
            default_agent_key=config.agent_runtime.default_agent_key,
        )
        self._browser = NaukriBrowser(config.browser, config.naukri)
        self._parser = NaukriDetailParser(config.naukri)

    def run(self) -> Dict[str, int]:
        """
        Returns stats dict: {keyword: jobs_saved, ...}
        """
        stats: Dict[str, int] = {}
        self._browser.start()
        try:
            keywords = self._load_keywords()
            if not keywords:
                logger.warning("No active search keywords found in DB.")
                return stats

            for kw_row in keywords:
                count = self._ingest_keyword(kw_row)
                stats[kw_row.keyword] = count
                self._update_keyword_run(kw_row.id)
        finally:
            self._browser.close()

        logger.info("Ingestion complete. Stats: %s", stats)
        return stats

    def _load_keywords(self) -> List[SearchKeywordORM]:
        with session_scope() as session:
            rows = (
                session.query(SearchKeywordORM)
                .filter_by(active=True)
                .all()
            )
            # detach from session
            session.expunge_all()
            return rows

    def _ingest_keyword(self, kw_row: SearchKeywordORM) -> int:
        keyword = kw_row.keyword
        location = kw_row.location
        max_jobs = kw_row.max_jobs
        max_age = kw_row.max_job_age_days

        logger.info("Ingesting keyword='%s' location='%s' max_jobs=%d", keyword, location, max_jobs)

        seen_urls: Set[str] = set()
        saved = 0

        for page_no in range(1, self._cfg.naukri.max_pages + 1):
            if saved >= max_jobs:
                break

            url = self._build_listing_url(keyword, location, max_age, page_no)
            logger.info("  Listing page %d: %s", page_no, url)

            handle = self._browser.safe_visit(url)
            time.sleep(0.5)
            self._browser.intelligent_scroll()

            detail_urls = self._extract_listing_urls(handle)
            if not detail_urls:
                logger.info("  No job cards on page %d. Stopping.", page_no)
                break

            for detail_url in detail_urls:
                if saved >= max_jobs:
                    break
                canon = _canonicalize_url(detail_url)
                if canon in seen_urls:
                    continue
                seen_urls.add(canon)

                try:
                    time.sleep(self._cfg.naukri.detail_page_delay_sec)
                    detail_handle = self._browser.safe_visit(detail_url)
                    self._wait_for_detail_ready(detail_handle)
                    job = self._parser.parse(detail_handle, detail_url, keyword, location)
                    if job is None:
                        continue

                    with session_scope() as session:
                        mode = upsert_job(session, job, agent_id=self._agent_id)
                        if mode != "skipped" and job.company_name:
                            ensure_company_stub(
                                session,
                                job.company_name,
                                agent_id=self._agent_id,
                                new_job_inserted=(mode == "inserted"),
                            )
                        if mode != "skipped":
                            saved += 1
                            logger.debug(
                                "    %s: %s | %s",
                                mode.capitalize(),
                                job.title,
                                job.company_name,
                            )
                except Exception as exc:
                    logger.warning("    Error on %s: %s", detail_url, exc)

        logger.info("  Keyword '%s': %d jobs saved.", keyword, saved)
        return saved

    def _build_listing_url(self, keyword: str, location: Optional[str],
                           max_age: Optional[int], page_no: int) -> str:
        slug = quote_plus(keyword.strip().lower().replace(" ", "-"))
        base = f"{self._cfg.naukri.base_url}/{slug}-jobs"
        parts: List[tuple] = []
        if location:
            parts.append(("location", location))
        if max_age:
            parts.append(("jobAge", str(max_age)))
        if page_no > 1:
            parts.append(("pageNo", str(page_no)))
        if not parts:
            return base
        return base + "?" + "&".join(f"{k}={quote_plus(v)}" for k, v in parts)

    def _extract_listing_urls(self, handle: PageHandle) -> List[str]:
        """Extract detail job URLs from listing page."""
        try:
            hrefs = handle.page.evaluate("""
                () => {
                    const cards = document.querySelectorAll('div.cust-job-tuple a[href*="job-listings"], article a[href*="job-listings"]');
                    return [...new Set([...cards].map(a => a.href).filter(Boolean))];
                }
            """)
            return [h for h in (hrefs or []) if isinstance(h, str) and "naukri.com" in h]
        except Exception as exc:
            logger.warning("Failed to extract listing URLs: %s", exc)
            return []

    def _wait_for_detail_ready(self, handle: PageHandle) -> None:
        selectors = (
            self._cfg.naukri.json_ld_selector,
            self._cfg.naukri.job_title_selector,
            self._cfg.naukri.company_name_selector,
        )
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                for sel in selectors:
                    if handle.page.locator(sel).count() > 0:
                        return
            except Exception:
                pass
            time.sleep(0.25)

    def _update_keyword_run(self, kw_id: int) -> None:
        with session_scope() as session:
            row = session.query(SearchKeywordORM).filter_by(id=kw_id).one_or_none()
            if row:
                row.last_run_utc = datetime.now(timezone.utc).replace(tzinfo=None)



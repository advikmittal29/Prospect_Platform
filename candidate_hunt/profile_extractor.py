from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page

from candidate_hunt.schemas import CandidateProfileIntelligence
from research.linkedin_browser import detect_linkedin_auth_screen
from utils.logging import build_logger

logger = build_logger("prospect.candidate_hunt.profile_extractor")


class LinkedInCandidateProfileExtractor:
    """
    Semantic profile extractor relying on headings/text anchors and role attributes,
    avoiding brittle generated class-name selectors.
    """

    def __init__(self, page: Page, auth_handler: Optional[Callable[[], bool]] = None) -> None:
        self.page = page
        self._auth_handler = auth_handler
        self._navigation_timeout_ms = 25000
        self._page_settle_ms = 900
        self._polite_delay_sec = 0.0

    def configure_timing(
        self,
        *,
        navigation_timeout_ms: int,
        page_settle_ms: int,
        polite_delay_sec: float = 0.0,
    ) -> None:
        self._navigation_timeout_ms = max(5000, int(navigation_timeout_ms))
        self._page_settle_ms = max(100, int(page_settle_ms))
        self._polite_delay_sec = max(0.0, float(polite_delay_sec))

    def extract(self, profile_url: str) -> CandidateProfileIntelligence:
        profile_url = self._canonical_profile(profile_url)
        self._goto_with_auth(profile_url, timeout=self._navigation_timeout_ms)

        raw = self.page.evaluate(
            r"""
            () => {
              function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }
              function linesFrom(el) {
                if (!el) return [];
                return clean(el.innerText || '').split(/\n+/).map(clean).filter(Boolean);
              }

              const root = document.querySelector('main') || document.body;
              const hero = root.querySelector('section') || root;

              const name = clean((root.querySelector('h1') || {}).innerText || '');

              const heroLines = linesFrom(hero).slice(0, 35);
              let headline = null;
              let locationText = null;
              for (const ln of heroLines) {
                if (!headline && ln !== name && ln.length > 8 && ln.length < 180 && !/,/.test(ln)) {
                  headline = ln;
                  continue;
                }
                if (!locationText && /,/.test(ln) && ln.length < 100) {
                  locationText = ln;
                }
              }

              function sectionByHeading(patterns) {
                const regexes = patterns.map((p) => new RegExp(p, 'i'));
                const sections = [...root.querySelectorAll('section')];
                for (const section of sections) {
                  const headingNode = section.querySelector('h2, h3, h1');
                  const heading = clean(headingNode ? headingNode.innerText : '');
                  if (!heading) continue;
                  if (regexes.some((r) => r.test(heading))) return section;
                }
                return null;
              }

              const aboutSection = sectionByHeading(['^about$']);
              const aboutLines = linesFrom(aboutSection).slice(1);
              const aboutText = aboutLines.length ? aboutLines.join(' ') : null;

              function listEntries(section) {
                if (!section) return [];
                const items = [...section.querySelectorAll('li')].slice(0, 20);
                const out = [];
                for (const item of items) {
                  const lines = linesFrom(item).slice(0, 8);
                  if (!lines.length) continue;
                  out.push(lines);
                }
                return out;
              }

              const expSection = sectionByHeading(['experience']);
              const eduSection = sectionByHeading(['education']);
              const skillsSection = sectionByHeading(['skills']);
              const certSection = sectionByHeading(['certification', 'license']);
              const featuredSection = sectionByHeading(['featured']);

              const expRaw = listEntries(expSection);
              const educationRaw = listEntries(eduSection);
              const certRaw = listEntries(certSection);

              const experiences = expRaw.map((entry) => ({
                title: entry[0] || null,
                company: entry[1] || null,
                duration: entry.find((line) => /(present|year|yr|month|mo|\d{4})/i.test(line)) || null,
                snippet: entry.join(' | '),
              }));

              const education = educationRaw.map((entry) => ({
                school: entry[0] || null,
                degree: entry[1] || null,
                duration: entry.find((line) => /(\d{4}|year)/i.test(line)) || null,
                snippet: entry.join(' | '),
              }));

              const certifications = certRaw.map((entry) => ({
                name: entry[0] || null,
                issuer: entry[1] || null,
                date: entry.find((line) => /(\d{4}|year)/i.test(line)) || null,
                snippet: entry.join(' | '),
              }));

              const skills = [];
              if (skillsSection) {
                const skillLines = linesFrom(skillsSection);
                for (const line of skillLines) {
                  if (/^skills?$/i.test(line)) continue;
                  if (/show all/i.test(line)) continue;
                  if (line.length > 2 && line.length <= 60) skills.push(line);
                }
              }

              const allResumeAnchors = [...root.querySelectorAll("a[href]")]
                .filter((a) => {
                  const href = (a.href || '').toLowerCase();
                  const text = clean(a.innerText || '').toLowerCase();
                  return /\.(pdf|doc|docx)(\?|$)/i.test(href) ||
                         /\b(resume|cv|curriculum vitae)\b/i.test(text);
                })
                .slice(0, 10)
                .map((a) => a.href);

              let currentTitle = experiences.length ? experiences[0].title : null;
              let currentCompany = experiences.length ? experiences[0].company : null;
              if (!currentTitle && headline) {
                const parts = headline.split(' at ');
                if (parts.length > 1) {
                  currentTitle = parts[0];
                  currentCompany = parts.slice(1).join(' at ');
                }
              }

              const openToWork = /open to work/i.test(clean(hero.innerText || '')) ||
                                 root.querySelector("img[alt*='open to work' i]") !== null;

              return {
                profile_name: name || null,
                profile_headline: headline || null,
                profile_location: locationText || null,
                profile_about_text: aboutText || null,
                current_title: currentTitle || null,
                current_company: currentCompany || null,
                experiences,
                education,
                skills: [...new Set(skills)].slice(0, 60),
                certifications,
                resume_urls: [...new Set(allResumeAnchors)],
                is_open_to_work: !!openToWork,
              };
            }
            """
        )

        contact_points = self._extract_contact_points()
        details_skills = self._extract_skills_from_details(profile_url)
        details_experience = self._extract_experience_from_details(profile_url)
        details_education = self._extract_education_from_details(profile_url)
        activity = self._extract_recent_activity(profile_url)

        return CandidateProfileIntelligence(
            profile_name=self._clean(raw.get("profile_name")),
            profile_headline=self._clean(raw.get("profile_headline")),
            profile_location=self._clean(raw.get("profile_location")),
            profile_about_text=self._clean(raw.get("profile_about_text")),
            current_title=self._clean(
                (details_experience[0].get("title") if details_experience else None)
                or raw.get("current_title")
            ),
            current_company=self._clean(
                (details_experience[0].get("company") if details_experience else None)
                or raw.get("current_company")
            ),
            experiences=details_experience or self._to_list_of_dicts(raw.get("experiences")),
            education=details_education or self._to_list_of_dicts(raw.get("education")),
            skills=details_skills or self._to_string_list(raw.get("skills")),
            certifications=self._to_list_of_dicts(raw.get("certifications")),
            activity=activity,
            contact_points=contact_points,
            resume_urls=self._to_string_list(raw.get("resume_urls")),
            resume_text=None,
            extraction_stage="profile_extraction",
            extracted_at_utc=datetime.now(timezone.utc),
        )

    def _extract_skills_from_details(self, profile_url: str) -> List[str]:
        skills_url = profile_url.rstrip("/") + "/details/skills/"
        try:
            self._goto_with_auth(skills_url, timeout=self._navigation_timeout_ms)
        except Exception as exc:
            logger.debug("Could not open LinkedIn skills page for %s: %s", profile_url, exc)
            return []

        self._progressive_scroll_for_skill_details()

        raw = self.page.evaluate(
            r"""
            () => {
              function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }
              function isSkillCandidate(value) {
                if (!value) return false;
                if (value.length < 2 || value.length > 90) return false;
                if (/^(skills?|all|industry knowledge|tools\s*&\s*technologies|interpersonal skills|languages)$/i.test(value)) return false;
                if (/^show\s+(all|more)/i.test(value)) return false;
                if (/^\d+\s+experiences?\s+at\b/i.test(value)) return false;
                if (/\bexperiences?\s+at\b/i.test(value)) return false;
                if (/\b(other company|other companies)\b/i.test(value)) return false;
                return true;
              }

              const root = document.querySelector("[data-sdui-screen*='ProfileSkillDetails']") || document;
              const cards = [...root.querySelectorAll("[componentkey^='com.linkedin.sdui.profile.skill(']:not([componentkey$='-divider'])")];
              const out = [];
              const seen = new Set();

              for (const card of cards) {
                const primary = card.querySelector("p");
                const title = clean(primary ? primary.innerText : "");
                if (!isSkillCandidate(title)) continue;
                const key = title.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push(title);
              }

              return out.slice(0, 250);
            }
            """
        )
        return self._to_string_list(raw)

    def _progressive_scroll_for_skill_details(self) -> None:
        # Scroll in short, human-like bursts so LinkedIn lazy-loaded skill rows can render.
        wheel_steps = [340, 460, 620, 520, 760]
        wait_steps_ms = [240, 320, 410, 290]

        prev_height = -1
        prev_skill_count = -1
        stable_rounds = 0

        for _ in range(2):
            for idx in range(40):
                step = wheel_steps[idx % len(wheel_steps)]
                wait_ms = wait_steps_ms[idx % len(wait_steps_ms)]
                try:
                    self.page.mouse.wheel(0, step)
                except Exception:
                    self.page.evaluate(f"window.scrollBy(0, {step});")
                self.page.wait_for_timeout(wait_ms)

                state = self.page.evaluate(
                    r"""
                    () => {
                      const doc = document.documentElement || document.body;
                      const height = Math.max(
                        doc ? doc.scrollHeight || 0 : 0,
                        document.body ? document.body.scrollHeight || 0 : 0
                      );
                      const atBottom = window.scrollY + window.innerHeight >= height - 6;
                      const skillCount = document.querySelectorAll(
                        "[componentkey^='com.linkedin.sdui.profile.skill(']:not([componentkey$='-divider'])"
                      ).length;
                      return { height, atBottom, skillCount };
                    }
                    """
                )

                height = int(state.get("height") or 0)
                skill_count = int(state.get("skillCount") or 0)
                at_bottom = bool(state.get("atBottom"))

                if height <= prev_height and skill_count <= prev_skill_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0

                prev_height = max(prev_height, height)
                prev_skill_count = max(prev_skill_count, skill_count)

                if at_bottom and stable_rounds >= 4:
                    break

            self.page.wait_for_timeout(900)
            self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight);")
            self.page.wait_for_timeout(650)


    def _extract_education_from_details(self, profile_url: str) -> List[Dict[str, str]]:
        edu_url = profile_url.rstrip("/") + "/details/education/"
        try:
            self._goto_with_auth(edu_url, timeout=self._navigation_timeout_ms)
        except Exception as exc:
            logger.debug("Could not open education page for %s: %s", profile_url, exc)
            return []

        self._wait_for_details_page_stable("Education")

        raw = self.page.evaluate(
            r"""
            () => {
            function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }

            const root = document.querySelector("[data-testid*='EducationDetailsSection']") || document;

            const items = [...root.querySelectorAll("[componentkey^='entity-collection-item']")];

            const results = [];

            for (const item of items) {
                const texts = [...item.querySelectorAll("p")]
                .map(el => clean(el.innerText))
                .filter(Boolean);

                if (!texts.length) continue;

                let school = texts[0] || null;
                let degree = texts[1] || null;
                let duration = null;

                for (const t of texts) {
                if (!duration && /(\d{4})/.test(t)) {
                    duration = t;
                }
                }

                results.push({
                school,
                degree,
                duration,
                snippet: texts.join(" | ")
                });
            }

            return results;
            }
            """
        )

        return self._to_list_of_dicts(raw)

    def _extract_experience_from_details(self, profile_url: str) -> List[Dict[str, str]]:
        exp_url = profile_url.rstrip("/") + "/details/experience/"
        try:
            self._goto_with_auth(exp_url, timeout=self._navigation_timeout_ms)
        except Exception as exc:
            logger.debug("Could not open experience page for %s: %s", profile_url, exc)
            return []

        self._wait_for_details_page_stable("Experience")

        raw = self.page.evaluate(
            r"""
            () => {
            function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }

            const root = document.querySelector("[data-testid*='ExperienceDetailsSection']") || document;

            const items = [...root.querySelectorAll("[componentkey^='entity-collection-item']")];

            const results = [];

            for (const item of items) {
                const texts = [...item.querySelectorAll("p")]
                .map(el => clean(el.innerText))
                .filter(Boolean);

                if (!texts.length) continue;

                let title = texts[0] || null;
                let company = null;
                let duration = null;
                let location = null;

                for (const t of texts) {
                if (!company && /·/.test(t)) {
                    company = t.split("·")[0].trim();
                }
                if (!duration && /(\d{4}|present)/i.test(t)) {
                    duration = t;
                }
                if (!location && /(india|remote|on-site|hybrid)/i.test(t)) {
                    location = t;
                }
                }

                results.push({
                title,
                company,
                duration,
                location,
                snippet: texts.join(" | ")
                });
            }

            return results;
            }
            """
        )

        return self._to_list_of_dicts(raw)

    def _extract_contact_points(self) -> List[Dict[str, str]]:
        contact_points: List[Dict[str, str]] = []
        try:
            trigger = self.page.locator("a:has-text('Contact info'), button:has-text('Contact info')")
            if trigger.count() == 0:
                return []
            trigger.first.click(timeout=4000)
            self.page.wait_for_selector("[role='dialog']", timeout=5000)

            raw = self.page.evaluate(
                r"""
                () => {
                  function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }
                  const dialog = document.querySelector("[role='dialog']");
                  if (!dialog) return [];

                  const rows = [];
                  const links = [...dialog.querySelectorAll('a[href]')];
                  for (const link of links) {
                    const label = clean(link.innerText || link.getAttribute('aria-label') || '');
                    const href = clean(link.href || link.getAttribute('href') || '');
                    if (!href) continue;
                    rows.push({ type: label || 'link', value: href });
                  }

                  const texts = clean(dialog.innerText || '').split(/\n+/).map(clean).filter(Boolean);
                  for (const line of texts) {
                    if (/@/.test(line) || /\+\d/.test(line) || /github|portfolio|website|blog|twitter|x\.com/i.test(line)) {
                      rows.push({ type: 'text', value: line });
                    }
                  }

                  return rows;
                }
                """
            )
            for item in raw or []:
                value = self._clean(item.get("value"))
                if not value:
                    continue
                contact_points.append(
                    {
                        "type": self._clean(item.get("type")) or "link",
                        "value": value,
                    }
                )
        except Exception:
            pass
        finally:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)
            except Exception:
                pass

        deduped: List[Dict[str, str]] = []
        seen = set()
        for item in contact_points:
            key = (item.get("type", ""), item.get("value", "").lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:25]

    def _wait_for_details_page_stable(self, section_keyword: str) -> None:
        """
        Wait until LinkedIn details page (experience/education) is fully rendered.
        Uses DOM growth + item count stabilization.
        """
        prev_count = -1
        stable_rounds = 0

        for _ in range(40):
            state = self.page.evaluate(
                r"""
                () => {
                const items = document.querySelectorAll("[componentkey^='entity-collection-item']");
                const height = document.body.scrollHeight || 0;
                return { count: items.length, height };
                }
                """
            )

            count = int(state.get("count") or 0)

            if count == prev_count:
                stable_rounds += 1
            else:
                stable_rounds = 0

            prev_count = count

            if stable_rounds >= 4 and count > 0:
                break

            try:
                self.page.mouse.wheel(0, 800)
            except Exception:
                self.page.evaluate("window.scrollBy(0, 800)")

            self.page.wait_for_timeout(400)

        self.page.wait_for_timeout(800)

    def _extract_recent_activity(self, profile_url: str) -> List[Dict[str, str]]:
        activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
        try:
            self._goto_with_auth(activity_url, timeout=self._navigation_timeout_ms)
        except Exception as exc:
            logger.debug("Recent activity fetch failed for %s: %s", profile_url, exc)
            return []

        raw = self.page.evaluate(
            r"""
            () => {
              function clean(v) { return (v || '').replace(/\s+/g, ' ').trim(); }
              const nodes = [...document.querySelectorAll("article, [role='article']")].slice(0, 8);
              const out = [];
              for (const node of nodes) {
                const text = clean(node.innerText || '');
                if (!text || text.length < 20) continue;
                const snippet = text.slice(0, 700);
                const timeEl = node.querySelector("time") || node.querySelector("span[aria-label*='ago' i]");
                const timeText = clean(timeEl ? timeEl.innerText : '');
                out.push({ snippet, timestamp: timeText || null });
              }
              return out;
            }
            """
        )

        items: List[Dict[str, str]] = []
        for entry in raw or []:
            snippet = self._clean(entry.get("snippet"))
            if not snippet:
                continue
            items.append(
                {
                    "snippet": snippet,
                    "timestamp": self._clean(entry.get("timestamp")) or "",
                }
            )
        return items[:6]

    def _goto_with_auth(self, url: str, timeout: int) -> None:
        for attempt in range(1, 3):
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            self._settle()
            is_auth, reason = detect_linkedin_auth_screen(self.page)
            if not is_auth:
                return
            if not self._auth_handler:
                raise RuntimeError(f"LinkedIn auth required while opening {url}: {reason}")
            logger.warning(
                "Auth wall detected while opening %s (attempt %d): %s",
                url,
                attempt,
                reason,
            )
            if not self._auth_handler():
                raise RuntimeError(f"LinkedIn login failed for auth wall: {reason}")
        raise RuntimeError(f"Could not pass auth wall for {url} after login retries.")

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        self.page.wait_for_timeout(self._page_settle_ms)
        if self._polite_delay_sec > 0:
            self.page.wait_for_timeout(int(self._polite_delay_sec * 1000))

    def _canonical_profile(self, url: str) -> str:
        p = urlparse(url)
        path = p.path.rstrip("/") + "/"
        return urlunparse((p.scheme or "https", p.netloc or "www.linkedin.com", path, "", "", ""))

    def _clean(self, value: object) -> Optional[str]:
        if value is None:
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text or None

    def _to_string_list(self, value: object) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        seen = set()
        for item in value:
            clean = self._clean(item)
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
        return out

    def _to_list_of_dicts(self, value: object) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            clean_item: Dict[str, str] = {}
            for key, raw in item.items():
                clean = self._clean(raw)
                if clean:
                    clean_item[str(key)] = clean
            if clean_item:
                out.append(clean_item)
        return out

"""Pure business rules: posting identity, de-duplication, deterministic filters, search plan,
notify/tailor thresholds, profile freshness and resume reuse."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import (
    JobLead,
    JobMatch,
    JobPosting,
    Profile,
    ResumeVersion,
    SearchPlan,
    SearchQuery,
    StoredProfile,
)

# --- Normalisation ---------------------------------------------------------------------------------

_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh",
    "ag", "bv", "nv", "srl", "sl", "sa", "sas", "sac", "sae", "ltda", "spa", "cv", "de", "the",
}
_TITLE_ABBREVIATIONS = {
    "sr": "senior", "snr": "senior", "jr": "junior", "jnr": "junior", "ssr": "semisenior", "semi": "semi",
    "dev": "developer", "devs": "developer", "eng": "engineer", "engr": "engineer", "mgr": "manager",
    "swe": "software engineer", "sde": "software engineer", "fullstack": "full stack", "backend": "back end",
    "frontend": "front end", "ii": "2", "iii": "3", "desarrollador": "developer", "ingeniero": "engineer",
}
_TITLE_NOISE = {
    "remote", "remoto", "remota", "hybrid", "hibrido", "hibrida", "onsite", "presencial", "wfh", "latam",
    "anywhere", "urgent", "urgente", "hiring", "f", "m", "d", "x", "w", "h",
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _ascii(text)).strip()


def normalize_company(name: str) -> str:
    words = _norm(re.sub(r"[.,]", "", name)).split()  # "S.A.S." -> "sas"
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def normalize_title(title: str) -> str:
    text = re.sub(r"[(\[{][^)\]}]*[)\]}]", " ", _ascii(title))  # "(m/f/d)", "[Remote]"
    words = []
    for word in _norm(text).replace("on site", "onsite").split():
        word = _TITLE_ABBREVIATIONS.get(word, word)
        words += [w for w in word.split() if w not in _TITLE_NOISE]
    return " ".join(words)


# --- Identity and duplicates ---------------------------------------------------------------------


def _key(source: str, external_id: str, url: str, fallback: str) -> str:
    if external_id:
        basis = f"{_norm(source)}:{external_id.strip()}"
    elif url:
        basis = re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")
    else:
        basis = fallback
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def job_key(job: JobPosting) -> str:
    """Stable id: source id when present, else canonical url, else company+title."""
    return _key(job.source, job.external_id, job.url, duplicate_signature(job) or _norm(job.description[:200]))


def lead_key(lead: JobLead) -> str:
    """Same key the posting will get once fetched, so seen ids are skipped before fetching details."""
    return _key(lead.source, lead.external_id, lead.url, "")


def duplicate_signature(job: JobPosting) -> str:
    """Identifies the same role reposted under a new id or found on another board."""
    company, words = normalize_company(job.company), sorted(set(normalize_title(job.title).split()))
    if not company or not words:
        return ""  # unknown company or title: cannot tell duplicates apart
    return f"{company}|{' '.join(words)}"  # word order does not matter


# --- Search plan ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchPreferences:
    locations: tuple[str, ...] = ("Remote",)
    extra_keywords: tuple[str, ...] = ()
    max_roles_from_profile: int = 3
    max_queries: int = 8
    posted_within_days: int = 1
    work_types: tuple[str, ...] = ()
    experience_levels: tuple[str, ...] = ()
    max_details_per_run: int = 20
    max_jobs_per_run: int = 25

    def plan_for(self, profile: Profile) -> SearchPlan:
        """Keyword x location queries, most specific first: configured keywords, target roles, profile keywords."""
        keywords, seen = [], set()
        for kw in [*self.extra_keywords, *profile.target_roles[: self.max_roles_from_profile], *profile.search_keywords]:
            if kw.strip() and (norm := _norm(kw)) not in seen:
                seen.add(norm)
                keywords.append(kw.strip())
        locations = list(self.locations or profile.locations) or [None]
        queries = [SearchQuery(keywords=k, location=loc) for k in keywords for loc in locations]
        return SearchPlan(
            queries=queries[: self.max_queries],
            posted_within_days=self.posted_within_days,
            work_types=list(self.work_types),
            experience_levels=list(self.experience_levels),
        )


# --- Deterministic filters -----------------------------------------------------------------------


@dataclass(frozen=True)
class JobFilter:
    """Rules applied to every fetched posting before any LLM sees it. Returns a reason when it must be dropped."""

    exclude_companies: tuple[str, ...] = ()
    exclude_title_keywords: tuple[str, ...] = ()
    posted_within_days: int | None = None
    work_types: tuple[str, ...] = ()  # allowed: remote, hybrid, on_site (empty = any)
    _companies: frozenset[str] = field(init=False, repr=False)
    _title_words: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_companies", frozenset(normalize_company(c) for c in self.exclude_companies))
        object.__setattr__(self, "_title_words", tuple(normalize_title(k) for k in self.exclude_title_keywords if k.strip()))

    def rejection(self, job: JobPosting, now: datetime | None = None) -> str | None:
        if self._companies and normalize_company(job.company) in self._companies:
            return "empresa_excluida"
        title = f" {normalize_title(job.title)} "
        if any(f" {w} " in title for w in self._title_words):
            return "palabra_excluida_en_titulo"
        if self.work_types and job.remote != "unknown":
            allowed = {w.replace("-", "_") for w in self.work_types}
            if {"remote": "remote", "hybrid": "hybrid", "onsite": "on_site"}[job.remote] not in allowed:
                return "modalidad_no_deseada"
        if self.posted_within_days is not None and (posted := _parse_date(job.posted_at)):
            now = now or datetime.now(timezone.utc)
            # One extra day of slack: sources report coarse ages ("1 day ago").
            if now - posted > timedelta(days=self.posted_within_days + 1):
                return "publicacion_antigua"
        if not job.description.strip():
            return "sin_descripcion"
        return None


def _parse_date(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- Matching thresholds --------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchingPolicy:
    min_score_to_notify: int = 70
    min_score_to_tailor: int = 70

    def should_notify(self, match: JobMatch) -> bool:
        return match.score >= self.min_score_to_notify

    def should_tailor(self, match: JobMatch) -> bool:
        return self.should_notify(match) and match.resume_undersells and match.score >= self.min_score_to_tailor


# --- Profile and resume versions --------------------------------------------------------------------


def repos_fingerprint(repo_heads: list[str]) -> str:
    """Identifies the state of the candidate's repositories (url + latest commit of each)."""
    h = hashlib.sha256()
    for head in sorted(repo_heads):
        h.update(head.encode())
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class ProfileRefreshPolicy:
    """When the (LLM-inferred, token-costly) profile must be rebuilt.

    - The base resume changed: right away; it is the main source and rarely changes.
    - Repositories changed: at most every ``refresh_days`` (commits happen all the time).
    - Nothing changed: never. A rebuild can always be forced by hand.
    """

    refresh_days: int = 30

    def repos_check_due(self, stored: StoredProfile, now: datetime) -> bool:
        last_check = stored.repos_checked_at or stored.built_at
        return now - last_check >= timedelta(days=self.refresh_days)

    def rebuild_reason(
        self, stored: StoredProfile | None, resume_fp: str, repos_fp: str | None, now: datetime
    ) -> str | None:
        """Why the profile must be rebuilt, or None. ``repos_fp`` is None when repos were not checked."""
        if stored is None:
            return "no hay perfil guardado"
        if stored.resume_fingerprint != resume_fp:
            return "la hoja de vida base cambió"
        if repos_fp is not None and stored.repos_fingerprint != repos_fp and self.repos_check_due(stored, now):
            return f"los repositorios cambiaron y pasaron {self.refresh_days} días o más"
        return None


def resume_fingerprint(resume_text: str) -> str:
    """Identifies the base resume a tailored version was derived from."""
    return hashlib.sha256(resume_text.strip().encode()).hexdigest()[:16]


@dataclass(frozen=True)
class ReusePolicy:
    """Which stored versions may be offered for reuse.

    Only versions derived from the current base resume qualify: if the base changed (new job, new
    skills, corrected data), older versions could carry outdated information.
    """

    enabled: bool = True
    max_candidates: int = 20

    def candidates(self, versions: list[ResumeVersion], base_fingerprint: str) -> list[ResumeVersion]:
        if not self.enabled:
            return []
        current = [v for v in versions if v.base_fingerprint == base_fingerprint]
        return sorted(current, key=lambda v: v.created_at, reverse=True)[: self.max_candidates]

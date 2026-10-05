"""Pure business rules: profile freshness and changes, search plan compilation.

Normalisation lives in job_contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from job_contracts import CollectorPlan, SearchPlan, SearchPreferences, SearchQuery
from job_contracts.normalize import normalize_keyword as _norm

from .models import Profile, StoredProfile

# --- Profile freshness --------------------------------------------------------------------


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


_LEVELS = {"basic": "básico", "intermediate": "intermedio", "advanced": "avanzado", "expert": "experto"}


def _added(before: list[str], after: list[str]) -> list[str]:
    known = {_norm(item) for item in before}
    return [item for item in after if _norm(item) not in known]


def profile_changes(previous: Profile | None, current: Profile) -> list[str]:
    """What a rebuilt profile brings compared with the previous one, one line per change (Spanish)."""
    if previous is None:
        return []
    changes: list[str] = []
    if previous.headline != current.headline:
        changes.append(f"Nuevo titular: {current.headline}")
    if previous.seniority != current.seniority:
        changes.append(f"Seniority: {previous.seniority} → {current.seniority}")
    if previous.years_of_experience != current.years_of_experience:
        changes.append(f"Años de experiencia: {previous.years_of_experience:g} → {current.years_of_experience:g}")

    old_skills = {_norm(s.name): s for s in previous.skills}
    new_skills = [s for s in current.skills if _norm(s.name) not in old_skills]
    if new_skills:
        changes.append("Habilidades nuevas: " + ", ".join(f"{s.name} ({_LEVELS[s.level]})" for s in new_skills))
    levels = [
        f"{s.name} ({_LEVELS[old.level]} → {_LEVELS[s.level]})"
        for s in current.skills
        if (old := old_skills.get(_norm(s.name))) and old.level != s.level
    ]
    if levels:
        changes.append("Cambio de nivel: " + ", ".join(levels))
    current_skills = {_norm(s.name) for s in current.skills}
    if removed := [s.name for s in previous.skills if _norm(s.name) not in current_skills]:
        changes.append("Habilidades que ya no aparecen: " + ", ".join(removed))

    for label, before, after in (
        ("Cargos objetivo nuevos", previous.target_roles, current.target_roles),
        ("Dominios nuevos", previous.domains, current.domains),
        ("Idiomas nuevos", previous.languages, current.languages),
        ("Proyectos destacados nuevos", previous.notable_projects, current.notable_projects),
        ("Fortalezas nuevas que tu CV no muestra", previous.strengths_missing_from_resume,
         current.strengths_missing_from_resume),
        ("Palabras clave de búsqueda nuevas", previous.search_keywords, current.search_keywords),
    ):
        if added := _added(before, after):
            changes.append(f"{label}: " + ", ".join(added))
    return changes


# --- Search plan compilation -----------------------------------------------------------------


@dataclass(frozen=True)
class SearchBudgets:
    max_roles_from_profile: int = 3
    max_queries: int = 8
    max_details_per_run: int = 20


@dataclass(frozen=True)
class SearchPlanBuild:
    plan: CollectorPlan | None  # None when there is no keyword at all
    total_queries: int  # before the max_queries cut


def profile_fingerprint(profile: Profile | None) -> str:
    """SHA-256 of the profile a plan was compiled from; empty when there was none."""
    if profile is None:
        return ""
    return hashlib.sha256(json.dumps(profile.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def _search_keywords(preferences: SearchPreferences, profile: Profile | None, budgets: SearchBudgets) -> list[str]:
    candidates = list(preferences.keywords_include)
    if preferences.use_profile_keywords and profile is not None:
        candidates += profile.target_roles[: budgets.max_roles_from_profile] + profile.search_keywords
    excluded = {_norm(word) for word in preferences.keywords_exclude}
    seen: set[str] = set()
    keywords = []
    for candidate in candidates:
        key = _norm(candidate)
        if key and key not in excluded and key not in seen:
            seen.add(key)
            keywords.append(candidate.strip())
    return keywords


def build_search_plan(
    preferences: SearchPreferences, profile: Profile | None, budgets: SearchBudgets, now: datetime
) -> SearchPlanBuild:
    """Compile the collector plan from the stored preferences and the profile, without any LLM."""
    keywords = _search_keywords(preferences, profile, budgets)
    if not keywords:
        return SearchPlanBuild(plan=None, total_queries=0)
    locations: list[str | None] = list(preferences.locations)
    if not locations and profile is not None:
        locations = [place.strip() for place in profile.locations if place.strip()]
    if not locations:
        locations = [None]
    queries = [SearchQuery(keywords=keyword, location=place) for keyword in keywords for place in locations]
    plan = CollectorPlan(
        search=SearchPlan(
            queries=queries[: budgets.max_queries],
            posted_within_days=preferences.posted_within_days,
            work_types=list(preferences.work_types),
            experience_levels=list(preferences.experience_levels),
        ),
        max_details_per_run=budgets.max_details_per_run,
        exclude_companies=list(preferences.exclude_companies),
        exclude_title_keywords=list(preferences.exclude_title_keywords),
        preferences_version=preferences.version,
        profile_fingerprint=profile_fingerprint(profile),
        built_at=now,
    )
    return SearchPlanBuild(plan=plan, total_queries=len(queries))

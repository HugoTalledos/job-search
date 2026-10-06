"""Validated candidate facts and pure projections.

Language names use explicit Spanish/English aliases. Free denied claims match
normalized whole phrases only: paraphrases and contextual negation require review.
Revocations are resolved against immutable correction IDs by the persistence store.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from job_agent.domain.models import Profile


def _normalize(text: str) -> str:
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                            if not unicodedata.combining(c)).split())


_LANGUAGE_ALIASES = {
    'english': ('english', 'ingles', 'en'),
    'spanish': ('spanish', 'espanol', 'castellano', 'es'),
    'french': ('french', 'frances', 'fr'),
    'german': ('german', 'aleman', 'de'),
    'portuguese': ('portuguese', 'portugues', 'pt'),
    'italian': ('italian', 'italiano', 'it'),
    'mandarin': ('mandarin', 'mandarin chinese', 'chino mandarin'),
    'japanese': ('japanese', 'japones', 'ja'),
}
_LEVELS = {'A1', 'A2', 'B1', 'B2', 'C1', 'C2', 'native', 'basic', 'intermediate', 'advanced', 'fluent'}
_SKILL_LEVELS = {'basic', 'intermediate', 'advanced', 'expert'}
_SENIORITIES = {'intern', 'junior', 'mid', 'senior', 'staff', 'principal', 'manager', 'director'}


def _language(subject: str) -> str:
    normalized = _normalize(subject)
    for canonical, aliases in _LANGUAGE_ALIASES.items():
        if normalized in aliases:
            return canonical
    raise ValueError('Language must have an unambiguous supported name')


def _contains(text: str, term: str) -> bool:
    return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text))


def _language_entry(entry: str, subject: str) -> bool:
    text = _normalize(entry)
    # Two-letter ISO codes identify inputs, not arbitrary words in CV prose.
    return any(re.match(r'^' + re.escape(alias) + r'(?!\w)', text)
               for alias in _LANGUAGE_ALIASES[subject])


class FactOperation(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['remove_language', 'set_language', 'remove_skill', 'set_skill_level',
                  'set_seniority', 'set_years_of_experience', 'deny_claim', 'revoke']
    subject: str
    value: str | float | None = None

    def __init__(self, kind=None, subject=None, value=None, **data):
        if kind is not None:
            data['kind'] = kind
        if subject is not None:
            data['subject'] = subject
        data['value'] = value
        super().__init__(**data)

    @field_validator('value', mode='before')
    @classmethod
    def reject_boolean_value(cls, value):
        if isinstance(value, bool):
            raise ValueError('Boolean values are not candidate fact values')
        return value

    @model_validator(mode='after')
    def validate_fact(self):
        if self.kind in {'remove_skill', 'set_skill_level'} and re.search(r'[\r\n]', self.subject):
            raise ValueError('Skill subject must identify one unambiguous name')
        subject = self.subject.strip()
        if not subject:
            raise ValueError('A fact subject is required')
        if self.kind in {'remove_language', 'set_language'}:
            subject = _language(subject)
        elif self.kind != 'revoke':
            subject = _normalize(subject)
        if self.kind in {'remove_skill', 'set_skill_level'}:
            # One normalized name per operation; multi-target instructions need
            # separate operations after the interpreter resolves their intent.
            if re.search(r'\b(?:or|and|o|y)\b|[,;/|&\n]', subject):
                raise ValueError('Skill subject must identify one unambiguous name')
        if self.kind in {'remove_language', 'remove_skill', 'deny_claim', 'revoke'}:
            if self.value is not None:
                raise ValueError('This operation cannot carry a value')
        elif self.kind == 'set_language':
            if self.value not in _LEVELS:
                raise ValueError('Unsupported language level')
        elif self.kind == 'set_skill_level':
            if self.value not in _SKILL_LEVELS:
                raise ValueError('Unsupported skill level')
        elif self.kind == 'set_seniority':
            if subject != 'seniority' or self.value not in _SENIORITIES:
                raise ValueError('Invalid seniority correction')
        elif self.kind == 'set_years_of_experience':
            if subject != 'years_of_experience' or not isinstance(self.value, (int, float)) or not math.isfinite(self.value) or self.value < 0:
                raise ValueError('Experience must be a finite nonnegative number')
        if self.kind == 'deny_claim':
            for canonical, aliases in _LANGUAGE_ALIASES.items():
                if subject in aliases:
                    subject = canonical
                    break
        object.__setattr__(self, 'subject', subject)
        return self


class CorrectionSet(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0, strict=True)
    operations: list[FactOperation]
    active_ids: list[str] = Field(default_factory=list)


def apply_fact_operations(base: Profile, operations: list[FactOperation]) -> Profile:
    """Apply active operations in order without changing the inferred base."""
    result = base.model_copy(deep=True)
    for operation in operations:
        kind, subject, value = operation.kind, operation.subject, operation.value
        if kind == 'revoke':
            raise ValueError('Revocations must be resolved by the correction store')
        if kind in {'remove_language', 'set_language'}:
            result.languages = [entry for entry in result.languages if not _language_entry(entry, subject)]
            if kind == 'set_language':
                result.languages.append(f'{subject.title()} ({value})')
        elif kind == 'remove_skill':
            result.skills = [skill for skill in result.skills if _normalize(skill.name) != subject]
        elif kind == 'set_skill_level':
            matches = [skill for skill in result.skills if _normalize(skill.name) == subject]
            if len(matches) != 1:
                raise ValueError('Skill level requires one existing skill')
            matches[0].level = value
        elif kind == 'set_seniority':
            result.seniority = value
        elif kind == 'set_years_of_experience':
            result.years_of_experience = value
    return Profile.model_validate(result.model_dump())


def contradictions(markdown: str, operations: list[FactOperation]) -> list[str]:
    """Return canonical subjects for explicit denied or replaced CV claims.

    This deterministic guard is conservative; it does not infer paraphrases,
    experience totals or seniority from prose. Human review remains necessary.
    """
    text = _normalize(markdown)
    active: dict[tuple[str, str], FactOperation] = {}
    for operation in operations:
        if operation.kind == 'revoke':
            raise ValueError('Revocations must be resolved by the correction store')
        group = ('language' if operation.kind in {'remove_language', 'set_language'} else
                 'skill' if operation.kind in {'remove_skill', 'set_skill_level'} else operation.kind)
        active[group, operation.subject] = operation
    found = []
    for operation in active.values():
        subject = operation.subject
        aliases = (tuple(alias for alias in _LANGUAGE_ALIASES[subject] if len(alias) > 2)
                   if subject in _LANGUAGE_ALIASES else (subject,))
        if operation.kind in {'remove_language', 'remove_skill', 'deny_claim'}:
            if any(_contains(text, alias) for alias in aliases):
                found.append(subject)
        elif operation.kind in {'set_language', 'set_skill_level'}:
            # A substitution is contradictory only when a different explicit level
            # accompanies the same named fact in its CV segment.
            levels = _LEVELS if operation.kind == 'set_language' else _SKILL_LEVELS
            segments = []
            for line in markdown.splitlines():
                # Table cells belong to one fact row (name | level). Other
                # pipe-separated lists contain independent fact segments.
                if line.strip().startswith('|') and line.strip().endswith('|'):
                    segments.append(line)
                else:
                    segments.extend(re.split(r'[;,|]', line))
            for segment in segments:
                normalized = _normalize(segment)
                if any(_contains(normalized, alias) for alias in aliases) and any(
                    level != operation.value and _contains(normalized, _normalize(level)) for level in levels
                ):
                    found.append(subject)
                    break
    return list(dict.fromkeys(found))

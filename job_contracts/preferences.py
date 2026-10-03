"""Search preferences stored at ``settings/search_preferences``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .normalize import normalize_company, normalize_keyword

WorkType = Literal["remote", "hybrid", "on_site"]
ExperienceLevel = Literal["internship", "entry", "associate", "mid_senior", "director", "executive"]


def _canonical(items: list[str], key) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        text = item.strip()
        k = key(text)
        if text and k not in seen:
            seen.add(k)
            result.append(text)
    return result


class SearchPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keywords_include: list[str] = []
    keywords_exclude: list[str] = []
    use_profile_keywords: bool = True
    locations: list[str] = []
    posted_within_days: int = Field(default=2, ge=1, le=30)
    work_types: list[WorkType] = []
    experience_levels: list[ExperienceLevel] = []
    exclude_companies: list[str] = []
    exclude_title_keywords: list[str] = []
    version: int = Field(default=0, ge=0)
    updated_at: datetime | None = None

    @field_validator("work_types", "experience_levels", mode="before")
    @classmethod
    def _clean_enum_items(cls, value):
        if not isinstance(value, list):
            return value
        cleaned = [v.strip().lower() if isinstance(v, str) else v for v in value]
        return [v for v in cleaned if v != ""]

    @model_validator(mode="after")
    def _canonicalise(self) -> SearchPreferences:
        for name in ("keywords_include", "keywords_exclude", "locations", "exclude_title_keywords"):
            setattr(self, name, _canonical(getattr(self, name), normalize_keyword))
        self.work_types = list(dict.fromkeys(self.work_types))
        self.experience_levels = list(dict.fromkeys(self.experience_levels))
        self.exclude_companies = _canonical(self.exclude_companies, normalize_company)
        return self

    def content_equals(self, other: SearchPreferences) -> bool:
        ignored = {"version", "updated_at"}
        return self.model_dump(exclude=ignored) == other.model_dump(exclude=ignored)

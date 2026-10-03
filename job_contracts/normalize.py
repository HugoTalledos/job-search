"""Text normalisation shared by the collector and the agent."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Literal

from .models import JobPosting

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


def normalize_keyword(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _ascii(text)).strip()


def normalize_company(name: str) -> str:
    words = normalize_keyword(re.sub(r"[.,]", "", name)).split()  # "S.A.S." -> "sas"
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    while words and words[0] == "the":
        words.pop(0)
    return " ".join(words)


def normalize_title(title: str) -> str:
    text = re.sub(r"[(\[{][^)\]}]*[)\]}]", " ", _ascii(title))  # "(m/f/d)", "[Remote]"
    words = []
    for word in normalize_keyword(text).replace("on site", "onsite").split():
        word = _TITLE_ABBREVIATIONS.get(word, word)
        words += [w for w in word.split() if w not in _TITLE_NOISE]
    return " ".join(words)


def exclusion_reason(
    job: JobPosting, exclude_companies: Sequence[str], exclude_title_keywords: Sequence[str]
) -> Literal["empresa_excluida", "palabra_excluida_en_titulo"] | None:
    """Why a posting must be dropped, or None. Titles match whole normalised words."""
    company = normalize_company(job.company)
    if company and any(company == normalize_company(name) for name in exclude_companies):
        return "empresa_excluida"
    title = f" {normalize_title(job.title)} "
    for word in exclude_title_keywords:
        if (w := normalize_keyword(word)) and f" {w} " in title:
            return "palabra_excluida_en_titulo"
    return None

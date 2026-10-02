"""Deterministic parser for the job-posting text returned by the LinkedIn MCP server's ``get_job_details``.

The server returns the page's visible text (``sections.job_posting``). Its header usually reads:

    <Company>
    <Job title>
    <Location> · <age, e.g. "3 days ago"> · <applicants>
    <workplace type, e.g. "Remote">  <employment type> ...
    About the job
    <description>

Parsing is best-effort: anything not found is left empty (filters skip unknown values, and the matcher's
reading of the posting is used for display only).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ...domain.models import JobPosting

DESCRIPTION_HEADINGS = ("About the job", "Acerca del empleo", "Acerca de este empleo", "Sobre el empleo")

_NOISE = {
    "share", "compartir", "show more options", "mostrar más opciones", "save", "guardar", "saved", "guardado",
    "apply", "solicitar", "easy apply", "solicitud sencilla", "promoted", "promocionado", "follow", "seguir",
    "message", "enviar mensaje", "see more", "ver más", "show more", "mostrar más", "…", "...", "more", "más",
    "use ai to assess how you fit", "show match details", "tailor my resume", "help me stand out",
}
_NOISE_PREFIXES = ("promoted by", "promocionado por", "responses managed", "respuestas gestionadas", "reposted")

_UNITS = {
    "minute": 1 / 1440, "minuto": 1 / 1440, "hour": 1 / 24, "hora": 1 / 24, "day": 1, "día": 1, "dia": 1,
    "week": 7, "semana": 7, "month": 30, "mes": 30,
}
_AGE_EN = re.compile(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I)
_AGE_ES = re.compile(r"hace\s+(\d+)\s+(minuto|hora|d[ií]a|semana|mes)(?:es|s)?", re.I)
_META_HINTS = re.compile(r"applicant|solicitud|clicked apply|personas hicieron clic|ago\b|hace\s+\d", re.I)

_WORKPLACE = (
    ("hybrid", re.compile(r"\b(hybrid|h[ií]brido)\b", re.I)),
    ("remote", re.compile(r"\b(remote|remoto|en remoto)\b", re.I)),
    ("onsite", re.compile(r"\b(on-site|onsite|presencial|en las instalaciones)\b", re.I)),
)


def _split(text: str) -> tuple[list[str], str]:
    for heading in DESCRIPTION_HEADINGS:
        idx = text.find(heading)
        if idx != -1:
            return text[:idx].splitlines(), text[idx + len(heading):].strip()
    return text.splitlines()[:25], text.strip()


def _is_noise(line: str) -> bool:
    low = line.lower()
    return low in _NOISE or low.startswith(_NOISE_PREFIXES)


def _age(line: str, now: datetime) -> str:
    match = _AGE_EN.search(line) or _AGE_ES.search(line)
    if not match:
        return ""
    unit = match.group(2).lower()
    days = int(match.group(1)) * _UNITS.get(unit, _UNITS.get(unit.replace("í", "i"), 1))
    return (now - timedelta(days=days)).isoformat()


def parse_job_posting(job_id: str, url: str, text: str, now: datetime | None = None) -> JobPosting:
    now = now or datetime.now(timezone.utc)
    raw_header, description = _split(text)
    header = [line.strip() for line in raw_header if line.strip() and not _is_noise(line.strip())]

    meta_idx = next(
        (i for i, line in enumerate(header) if " · " in line and _META_HINTS.search(line)),
        None,
    )
    title = company = location = posted_at = ""
    if meta_idx is not None:
        meta = header[meta_idx]
        location = meta.split(" · ")[0].strip()
        posted_at = _age(meta, now)
        if meta_idx >= 1:
            title = header[meta_idx - 1]
        if meta_idx >= 2:
            company = header[0] if header[0] != title else header[meta_idx - 2]

    header_text = "\n".join(header)
    remote = next((kind for kind, pattern in _WORKPLACE if pattern.search(header_text)), "unknown")

    return JobPosting(
        source="linkedin",
        external_id=job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        remote=remote,
        posted_at=posted_at,
        description=description,
    )

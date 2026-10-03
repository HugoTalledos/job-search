"""Structured edits to search preferences and their deterministic validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from job_contracts import SearchPreferences
from job_contracts.normalize import normalize_company, normalize_keyword
from pydantic import BaseModel, Field, ValidationError

PreferenceField = Literal[
    "keywords_include", "keywords_exclude", "use_profile_keywords", "locations", "posted_within_days",
    "work_types", "experience_levels", "exclude_companies", "exclude_title_keywords", "none",
]
LIST_FIELDS = {
    "keywords_include", "keywords_exclude", "locations", "work_types", "experience_levels",
    "exclude_companies", "exclude_title_keywords",
}

NO_CHANGES = "No encontré cambios para aplicar."


class PreferenceOperation(BaseModel):
    action: Literal["add", "remove", "set", "unclear"]
    field: PreferenceField = Field(description="'none' only for unclear")
    values: list[str] = Field(
        description="Items to add/remove/set; for posted_within_days one number, for use_profile_keywords 'true' or 'false'"
    )
    explanation: str = Field(description="Short Spanish note; required for unclear")


class PreferenceEdit(BaseModel):
    operations: list[PreferenceOperation]


@dataclass(frozen=True)
class EditOutcome:
    preferences: SearchPreferences | None  # None when nothing valid changed
    problems: list[str]  # Spanish, safe to show


def _key(field: str):
    return normalize_company if field == "exclude_companies" else normalize_keyword


def _parse_bool(text: str) -> bool | None:
    value = text.strip().lower()
    if value in ("true", "sí", "si", "yes"):
        return True
    if value in ("false", "no"):
        return False
    return None


def apply_operations(current: SearchPreferences, edit: PreferenceEdit) -> EditOutcome:
    data = current.model_dump()
    problems: list[str] = []
    invalid = False

    def reject(message: str) -> None:
        nonlocal invalid
        invalid = True
        problems.append(message)

    for operation in edit.operations:
        action, field, values = operation.action, operation.field, operation.values
        if action == "unclear":
            problems.append(operation.explanation.strip() or "No entendí la petición.")
        elif field == "none":
            reject("Operación sin campo válido.")
        elif action in ("add", "remove"):
            if field not in LIST_FIELDS:
                reject(f"No puedo usar «{action}» en {LABELS[field]}.")
            elif action == "add":
                data[field] = [*data[field], *values]
            else:
                drop = {_key(field)(v.strip()) for v in values}
                data[field] = [x for x in data[field] if _key(field)(x) not in drop]
        elif field in LIST_FIELDS:
            data[field] = list(values)
        elif len(values) != 1:
            reject(f"{LABELS[field]} necesita un solo valor.")
        elif field == "posted_within_days":
            try:
                data[field] = int(values[0].strip())
            except ValueError:
                reject("Los días desde la publicación deben ser un número.")
        else:  # use_profile_keywords
            flag = _parse_bool(values[0])
            if flag is None:
                reject("Usar keywords del perfil debe ser sí o no.")
            else:
                data[field] = flag

    if invalid:
        return EditOutcome(None, problems)
    try:
        updated = SearchPreferences.model_validate(data)
    except ValidationError:
        problems.append("Algún valor no es válido para las preferencias.")
        return EditOutcome(None, problems)
    if updated.content_equals(current):
        return EditOutcome(None, [*problems, NO_CHANGES])
    return EditOutcome(updated, problems)


LABELS = {
    "keywords_include": "Palabras clave",
    "keywords_exclude": "Palabras clave excluidas",
    "use_profile_keywords": "Usar keywords del perfil",
    "locations": "Ubicaciones",
    "posted_within_days": "Días desde la publicación",
    "work_types": "Modalidades",
    "experience_levels": "Niveles de experiencia",
    "exclude_companies": "Empresas excluidas",
    "exclude_title_keywords": "Palabras excluidas del título",
}


def preference_diff(old: SearchPreferences, new: SearchPreferences) -> list[str]:
    lines: list[str] = []
    for field, label in LABELS.items():
        before, after = getattr(old, field), getattr(new, field)
        if before == after:
            continue
        if field in LIST_FIELDS:
            key = _key(field)
            old_keys, new_keys = {key(x) for x in before}, {key(x) for x in after}
            added = [x for x in after if key(x) not in old_keys]
            removed = [x for x in before if key(x) not in new_keys]
            if added:
                lines.append(f"➕ {label}: {', '.join(added)}")
            if removed:
                lines.append(f"➖ {label}: {', '.join(removed)}")
        elif field == "use_profile_keywords":
            lines.append(f"🔁 {label}: {'sí' if after else 'no'}")
        else:
            lines.append(f"🗓 {label}: {after}")
    return lines

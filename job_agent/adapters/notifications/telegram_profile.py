"""ProfileReporter port on Telegram, plus the bot setup (webhook and command menu)."""

from __future__ import annotations

import html

import httpx

from ...domain.models import Profile
from .telegram_notifier import TELEGRAM_LIMIT, TelegramNotifier

_LEVEL_ORDER = {"expert": 0, "advanced": 1, "intermediate": 2, "basic": 3}

BOT_COMMANDS = [
    {"command": "build_profile", "description": "Construir mi perfil profesional"},
    {"command": "resend_pending", "description": "Reenviar propuestas pendientes"},
    {"command": "ajustar_cv", "description": "Responde a una oferta para recibir un CV ajustado"},
    {"command": "preferencias", "description": "Ver o cambiar el tipo de ofertas que busco"},
]


def _bullets(items: list[str], limit: int) -> list[str]:
    lines = [f"• {html.escape(item)}" for item in items[:limit]]
    if len(items) > limit:
        lines.append(f"• … y {len(items) - limit} más")
    return lines


def format_profile_message(profile: Profile, changes: list[str], first_build: bool) -> str:
    e = html.escape
    skills = sorted(profile.skills, key=lambda s: _LEVEL_ORDER[s.level])
    lines = [
        "✅ <b>Tu perfil profesional está listo</b>",
        "",
        f"<b>{e(profile.headline)}</b>",
        f"🎓 Seniority: {e(profile.seniority)} · {profile.years_of_experience:g} años de experiencia",
        f"🎯 Cargos objetivo: {e(', '.join(profile.target_roles[:5]))}",
        f"🛠 Habilidades principales: {e(', '.join(s.name for s in skills[:8]))}",
    ]
    if profile.domains:
        lines.append(f"🏢 Dominios: {e(', '.join(profile.domains[:5]))}")
    lines.append("")
    if first_build:
        lines.append("<b>Es tu primer perfil.</b> Lo que tus repositorios muestran y tu CV no:")
        lines += _bullets(profile.strengths_missing_from_resume, 6) or ["• Nada adicional"]
    elif changes:
        lines.append("<b>Novedades frente al perfil anterior:</b>")
        lines += _bullets(changes, 12)
    else:
        lines.append("Sin cambios frente al perfil anterior.")
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


class TelegramProfileReporter:
    def __init__(self, telegram: TelegramNotifier) -> None:
        self.telegram = telegram

    def built(self, profile: Profile, changes: list[str], first_build: bool) -> None:
        self.telegram.send_text(format_profile_message(profile, changes, first_build))

    def failed(self) -> None:
        self.telegram.send_text("❌ No pude construir tu perfil profesional. Revisa los registros del servicio.")


def register_webhook(token: str, url: str, secret: str, client: httpx.Client | None = None) -> None:
    """Point the bot at ``url`` (Telegram will send ``secret`` in every call) and publish its commands."""
    http = client or httpx.Client(timeout=30)
    for method, payload in (
        ("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"],
                        "drop_pending_updates": True}),
        ("setMyCommands", {"commands": BOT_COMMANDS}),
    ):
        response = http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
        if response.status_code != 200 or response.json().get("ok") is not True:
            raise RuntimeError(f"Telegram {method} failed (HTTP {response.status_code})")

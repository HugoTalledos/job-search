# job-search — agente de búsqueda de empleo

Agente que, **3 veces al día**, busca ofertas de trabajo, filtra las que mejor encajan con tu perfil
y, cuando una oferta encaja pero tu hoja de vida no te hace justicia, **crea una versión ajustada
de tu CV** para esa oferta. Después te envía una notificación por **Telegram** con la oferta, por qué
encaja y qué cambió en el CV (con el PDF adjunto).

```
             ┌──────────────── GitHub Actions (cron 3×/día) ─────────────────┐
resume/      │                                                                │
base.md ───► │ 1. Perfil   CV + repos git ──Claude──► data/profile.json       │
repos git ─► │             (solo se rehace si cambian o pasan N días)        │
             │ 2. Búsqueda agente Claude + herramientas MCP (LinkedIn, …)     │
             │             + búsqueda web opcional ──► ofertas nuevas         │
             │ 3. Afinidad cada oferta vs perfil + CV ──► score 0-100        │
             │ 4. Ajuste   si encaja y el CV la "subvende" ──► CV a medida   │
             │             output/AAAA-MM-DD/<empresa>-<cargo>/resume.{md,pdf}│
             │ 5. Aviso    Telegram: oferta + motivos + cambios + PDF         │
             │ 6. Estado   data/state.json, data/matches.jsonl → commit       │
             └────────────────────────────────────────────────────────────────┘
```

## Cómo funciona

| Paso | Archivo | Detalle |
|---|---|---|
| Perfil | `job_agent/profile.py`, `repos.py` | Clona (superficialmente) tus repos de GitHub — o cualquier remoto git — y extrae lenguajes, árbol, README y manifiestos (`package.json`, `pyproject.toml`, …). Claude infiere roles objetivo, seniority, habilidades con evidencia y lo que tus repos demuestran pero tu CV no menciona. Se persiste en `data/profile.json` con una huella del CV + commits; solo se recalcula si algo cambia o cada `profile_refresh_days`. |
| Búsqueda | `job_agent/sources/search_agent.py` | Un agente Claude recibe **todas las herramientas de todos los servidores MCP** de `config.yaml` (LinkedIn de entrada) y, si lo activas, la búsqueda web de Anthropic. Busca combinando tus roles/palabras clave con tus ubicaciones, trae el detalle de las ofertas prometedoras y las entrega validadas por la herramienta `submit_jobs`. Agregar una fuente = agregar un servidor MCP al YAML; no hay que tocar código. |
| Afinidad | `job_agent/matcher.py` | Puntaje 0-100, motivos, brechas y `resume_undersells` (encajas, pero tu CV actual no lo muestra). |
| CV a medida | `job_agent/tailor.py`, `resume.py` | Reescribe resumen, orden y viñetas con el vocabulario de la oferta y agrega proyectos de tus repos. **Regla dura: solo usa hechos de tu CV o de tus repos**; nunca inventa empresas, fechas, títulos ni métricas. Cada cambio queda justificado en `README.md` dentro de la carpeta de la oferta. |
| Aviso | `job_agent/notifier.py` | Telegram (mensaje + PDF). Sin credenciales, imprime el mensaje en el log. |
| Estado | `job_agent/state.py` | Evita repetir ofertas (por id, url o empresa+cargo para reposts). Historial en `data/matches.jsonl`. |

Modelo: `claude-opus-5-5` con pensamiento adaptativo, salidas estructuradas (Pydantic) y
*fallback* del lado del servidor (`fallbacks: "default"`) por si el modelo declina una petición.
Puedes cambiarlo con la variable `JOB_AGENT_MODEL`.

## Puesta en marcha

1. **Repositorio privado.** El agente guarda tu perfil, tus CVs ajustados y el historial en este
   repo; mantenlo privado.
2. **Tu hoja de vida**: reemplaza `resume/base.md` (Markdown recomendado; también acepta `.pdf`
   o `.txt` cambiando `resume_path`).
3. **`config.yaml`**: usuario de GitHub, repos extra, ubicaciones, empresas a excluir, umbrales.
4. **Bot de Telegram**: habla con [@BotFather](https://t.me/BotFather) → `/newbot` → token.
   Escríbele algo a tu bot y abre `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.
5. **Sesión de LinkedIn MCP** ([stickerdaniel/linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server)).
   Este servidor usa una sesión de navegador, no una API key. En tu máquina:
   ```bash
   uvx mcp-server-linkedin@latest --login     # inicia sesión; queda en ~/.linkedin-mcp
   tar -czf - -C ~ .linkedin-mcp | base64 -w0 > linkedin_session.b64
   ```
   Guarda el contenido como secret `LINKEDIN_SESSION_B64`. Cuando la sesión expire, repite el paso.
6. **Secrets** del repo (Settings → Secrets and variables → Actions):

   | Secret | Obligatorio | Uso |
   |---|---|---|
   | `ANTHROPIC_API_KEY` | sí | Claude |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | sí | notificaciones |
   | `LINKEDIN_SESSION_B64` | para LinkedIn | sesión del MCP |
   | `REPO_READ_TOKEN` | no | PAT de solo lectura si quieres que analice repos privados |

7. Ejecuta el workflow **job-search** a mano (Actions → Run workflow, con `dry_run` la primera vez).
   Después corre solo a las 7:07, 12:07 y 17:07 hora Colombia (ajusta el cron en
   `.github/workflows/job-search.yml` si estás en otra zona).

## Uso local

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=...            # y opcionalmente TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
python -m job_agent profile --force    # inferir y ver tu perfil
python -m job_agent run --dry-run      # un ciclo completo sin enviar notificaciones
python -m job_agent test-notify        # probar Telegram
pytest                                  # tests (no requieren API key)
```

## Agregar más fuentes

Cualquier servidor MCP de empleo funciona; el agente descubre sus herramientas solo:

```yaml
search:
  mcp_servers:
    - name: linkedin
      command: uvx
      args: ["mcp-server-linkedin@latest"]
    - name: mi-portal
      command: npx
      args: ["-y", "algún-mcp-de-empleos"]
      env: { API_KEY: "${MI_PORTAL_API_KEY}" }   # pásalo como secret en el workflow
  web_search: true                               # búsqueda web restringida a web_search_domains
```

## Notas

- Automatizar LinkedIn con una sesión de navegador puede ir contra sus términos de uso; úsalo con
  moderación (3 corridas/día, `max_jobs_per_run` acotado).
- Costo aproximado por corrida: una sesión de búsqueda + un puntaje por oferta nueva + un CV por
  oferta que lo amerite. Ajusta `max_jobs_per_run` y los umbrales para controlarlo.
- Revisa siempre el CV ajustado antes de enviarlo: el agente justifica cada cambio, pero tú decides.

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

| Paso | Caso de uso / puerto | Detalle |
|---|---|---|
| Perfil | `EnsureProfile` · `ResumeSource`, `CodeRepositoryReader`, `ProfileInferer`, `ProfileStore` | Clona (superficialmente) tus repos de GitHub — o cualquier remoto git — y extrae lenguajes, árbol, README y manifiestos. Claude infiere roles objetivo, seniority, habilidades con evidencia y lo que tus repos demuestran pero tu CV no menciona. Se persiste en `data/profile.json` con una huella del CV + commits; solo se recalcula si algo cambia o cada `profile_refresh_days`. |
| Búsqueda | `RunSearchCycle` · `JobSource` (uno por fuente) | Cada servidor MCP de `config.yaml` (LinkedIn de entrada) es un `JobSource` independiente: un agente Claude recibe las herramientas de ese servidor, busca con tus roles/palabras clave/ubicaciones y entrega ofertas validadas mediante `submit_jobs`. La búsqueda web de Anthropic es otro `JobSource`. Si una fuente falla, las demás siguen. |
| Afinidad | `JobMatcher` | Puntaje 0-100, motivos, brechas y `resume_undersells` (encajas, pero tu CV actual no lo muestra). |
| CV a medida | `ResumeTailor`, `ApplicationStore` | Reescribe resumen, orden y viñetas con el vocabulario de la oferta y agrega proyectos de tus repos. **Regla dura: solo usa hechos de tu CV o de tus repos**; nunca inventa empresas, fechas, títulos ni métricas. Cada cambio queda justificado en `README.md` dentro de la carpeta de la oferta. |
| Aviso | `Notifier` | Telegram (mensaje + PDF). Con `--dry-run` o sin credenciales, se usa el notificador de consola. |
| Estado | `SeenJobsRepository`, `MatchHistory` | Evita repetir ofertas (por id, url o empresa+cargo para reposts). Historial en `data/matches.jsonl`. |

Las reglas de negocio (umbrales para notificar/ajustar, identidad y duplicados de ofertas, vigencia
del perfil, criterios de búsqueda) viven en `job_agent/domain/policies.py`.

## Arquitectura hexagonal

Cada herramienta que usa el agente (Claude, servidores MCP, git/GitHub, Telegram, sistema de
archivos) es un **adaptador** detrás de un **puerto**. El núcleo no sabe con qué herramienta habla.

```
                 adaptador de entrada                      adaptadores de salida
                ┌────────────────────┐                  ┌──────────────────────────────────┐
 GitHub Actions │ entrypoints/cli.py │                  │ llm/            Claude            │
(cron 3×/día)─►│                    │                  │   ClaudeProfileInferer           │
                └─────────┬──────────┘                  │   ClaudeJobMatcher               │
                          │                             │   ClaudeResumeTailor             │
              ┌───────────▼───────────────┐  puertos    │ job_sources/                      │
              │ application/              │◄───────────►│   McpJobSource  (LinkedIn, …)     │
              │   EnsureProfile           │ (ports.py)  │   WebSearchJobSource              │
              │   RunSearchCycle          │             │ code_repositories/                │
              │ ┌───────────────────────┐ │             │   GitRepositoryReader (GitHub, …) │
              │ │ domain/               │ │             │ resume/   FileResumeSource        │
              │ │  models, policies     │ │             │ notifications/ Telegram, Console  │
              │ └───────────────────────┘ │             │ persistence/   JSON / archivos    │
              └───────────────────────────┘             └──────────────────────────────────┘
                     bootstrap.py (raíz de composición) conecta cada puerto con su adaptador
```

```
job_agent/
├── domain/            # entidades, value objects y reglas puras (sin dependencias externas)
├── application/       # casos de uso + ports.py (interfaces que el núcleo necesita)
├── adapters/          # implementaciones concretas de los puertos, una carpeta por herramienta
│   ├── llm/  job_sources/  code_repositories/  resume/  notifications/  persistence/
├── entrypoints/cli.py # adaptador de entrada (lo invoca el workflow)
├── bootstrap.py       # único lugar que decide qué adaptador implementa cada puerto
└── config.py          # lectura de config.yaml (solo la usa bootstrap)
```

La regla de dependencias se verifica en `tests/test_architecture.py`: `domain` no importa nada del
proyecto ni librerías de infraestructura; `application` solo importa `domain`; los adaptadores no
dependen de `bootstrap`, `config` ni de la CLI. Los casos de uso se prueban con adaptadores en memoria
(`tests/fakes.py`), sin red ni API key.

**Agregar una herramienta nueva** (otra bolsa de empleo con API REST, notificar por correo, guardar en
una base de datos, otro LLM…): implementa el puerto correspondiente en `adapters/<herramienta>/` y
regístralo en `bootstrap.py`. El dominio y los casos de uso no cambian.

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

Cualquier servidor MCP de empleo funciona sin escribir código: cada entrada se convierte en un
`McpJobSource` y el agente descubre sus herramientas solo. Para fuentes que no son MCP, implementa
el puerto `JobSource` (ver arriba).

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

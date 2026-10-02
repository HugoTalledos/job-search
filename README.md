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
             │ 2. Búsqueda LinkedIn vía MCP, sin LLM ──► ids de ofertas      │
             │ 3. Filtros  deterministas: vistas, duplicadas, exclusiones    │
             │ 4. Afinidad Claude: cada oferta que pasó vs perfil + CV       │
             │ 5. Ajuste   si encaja y el CV la "subvende" ──► CV a medida   │
             │             output/AAAA-MM-DD/<empresa>-<cargo>/resume.{md,pdf}│
             │ 6. Aviso    Telegram: oferta + motivos + cambios + PDF         │
             │ 7. Estado   data/state.json, data/matches.jsonl → commit       │
             └────────────────────────────────────────────────────────────────┘
```

## Cómo funciona

| Paso | Caso de uso / puerto | Detalle |
|---|---|---|
| Perfil | `EnsureProfile` · `ResumeSource`, `CodeRepositoryReader`, `ProfileInferer`, `ProfileStore` | Clona (superficialmente) tus repos de GitHub — o cualquier remoto git — y extrae lenguajes, árbol, README y manifiestos. Claude infiere roles objetivo, seniority, habilidades con evidencia y lo que tus repos demuestran pero tu CV no menciona. Se persiste en `data/profile.json` con una huella del CV + commits; solo se recalcula si algo cambia o cada `profile_refresh_days`. |
| Búsqueda y filtros | `RunSearchCycle` · `JobSource`, `SeenJobsRepository` · `JobFilter` | **Sin LLM.** El agente arma las consultas (palabras clave × ubicaciones) y llama directamente a `search_jobs` del servidor MCP de LinkedIn con sus filtros nativos (fecha, modalidad, nivel). Los ids ya procesados se descartan antes de descargar el detalle; luego se aplican los filtros deterministas (empresas y palabras excluidas, modalidad, antigüedad, duplicados por empresa + cargo normalizados). Detalle en [docs/arquitectura.md](docs/arquitectura.md#filtros-en-orden). |
| Afinidad | `JobMatcher` | **Único juicio de Claude sobre la oferta**, solo para las que pasaron los filtros. Puntaje 0-100, motivos, brechas y `resume_undersells` (encajas, pero tu CV actual no lo muestra). |
| CV a medida | `ResumeSelector`, `ResumeTailor`, `ApplicationStore` | Antes de escribir un CV nuevo revisa el catálogo de versiones ya ajustadas (ver [Reutilización](#reutilización-de-hojas-de-vida)): reutiliza, adapta la más parecida o crea una nueva. Al crear o adaptar, reescribe resumen, orden y viñetas con el vocabulario de la oferta y agrega proyectos de tus repos. **Regla dura: solo usa hechos de tu CV o de tus repos**; nunca inventa empresas, fechas, títulos ni métricas. Cada cambio queda justificado en `README.md` dentro de la carpeta de la versión. |
| Aviso | `Notifier` | Telegram (mensaje + PDF). Con `--dry-run` o sin credenciales, se usa el notificador de consola. |
| Estado | `SeenJobsRepository`, `MatchHistory` | Evita repetir ofertas (por id, url o empresa+cargo para reposts). Historial en `data/matches.jsonl`. |

Las reglas de negocio (umbrales para notificar/ajustar, identidad y duplicados de ofertas, vigencia
del perfil, criterios de búsqueda) viven en `job_agent/domain/policies.py`.

## Reutilización de hojas de vida

Cada CV ajustado se guarda en `output/AAAA-MM-DD/<empresa>-<cargo>-<id>/` con `resume.md`/`.html`/`.pdf`,
un `README.md` con los cambios y `version.json`: la ficha del catálogo (para qué oferta se creó, idioma,
qué destaca, de qué versión de tu CV base sale y para qué ofertas se ha usado).

Cuando una oferta nueva amerita un CV ajustado, el agente:

1. Toma las versiones creadas a partir de tu `resume/base.md` **actual** (las 20 más recientes). Si
   cambias tu CV base, las versiones anteriores dejan de ofrecerse, para no enviar datos desactualizados.
2. Si hay alguna, Claude compara la oferta con ese catálogo y decide:
   - **reutilizar** una versión que ya muestra los requisitos clave y está en el idioma de la oferta;
     no se genera nada nuevo y se registra el uso en `version.json`;
   - **adaptar** la más cercana con cambios mínimos, que se guarda como versión nueva con
     `adapted_from` apuntando a la original;
   - **crear** una nueva desde tu CV base.
3. La notificación dice cuál de los tres casos ocurrió y, al reutilizar o adaptar, cuál versión usó.

Se configura en `config.yaml` → `resume_reuse` (`enabled`, `max_candidates`). Como estas versiones
viven en el repo, mantenlo **privado**.

## Arquitectura hexagonal

> Diagramas de componentes y de secuencia en [`docs/arquitectura.md`](docs/arquitectura.md).

Cada herramienta que usa el agente (Claude, servidores MCP, git/GitHub, Telegram, sistema de
archivos) es un **adaptador** detrás de un **puerto**. El núcleo no sabe con qué herramienta habla.

```
                 adaptador de entrada                      adaptadores de salida
                ┌────────────────────┐                  ┌──────────────────────────────────┐
 GitHub Actions │ entrypoints/cli.py │                  │ llm/            Claude            │
(cron 3×/día)─►│                    │                  │   ClaudeProfileInferer           │
                └─────────┬──────────┘                  │   ClaudeJobMatcher               │
                          │                             │   ClaudeResumeTailor / Selector  │
              ┌───────────▼───────────────┐  puertos    │ job_sources/                      │
              │ application/              │◄───────────►│   LinkedInMcpJobSource (sin LLM)  │
              │   EnsureProfile           │ (ports.py)  │                                   │
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

Las fuentes son deterministas: no usan Claude para buscar. Para agregar otra bolsa de empleo (otra API,
otro servidor MCP), implementa el puerto `JobSource` en `adapters/job_sources/`:

1. Ejecuta las consultas del `SearchPlan` contra la fuente (aplicando sus filtros nativos si los tiene).
2. Llama a `admit(JobLead(...))` por cada id encontrado y pide el detalle solo de los admitidos, hasta
   `max_details`.
3. Devuelve `JobPosting` con los campos que la fuente provea.

Luego regístrala en `bootstrap.py` y agrega su sección en `search.sources` de `config.yaml`. Los filtros
deterministas, la deduplicación y el puntaje de Claude se aplican igual a todas las fuentes.

## Notas

- Automatizar LinkedIn con una sesión de navegador puede ir contra sus términos de uso; úsalo con
  moderación (3 corridas/día, `max_jobs_per_run` acotado).
- Costo aproximado por corrida: una sesión de búsqueda + un puntaje por oferta nueva + un CV por
  oferta que lo amerite. Ajusta `max_jobs_per_run` y los umbrales para controlarlo.
- Revisa siempre el CV ajustado antes de enviarlo: el agente justifica cada cambio, pero tú decides.

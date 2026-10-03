# job-search — agente de búsqueda de empleo

## Webhook de entrada para la inferencia

`job_agent` expone `POST /webhooks/inference` para iniciar la evaluación de ofertas en segundo
plano. Una petición autorizada recibe `200` sin cuerpo cuando se programa el trabajo; ese código no
confirma que la evaluación haya terminado. Una API key ausente o incorrecta recibe `401` sin iniciar
trabajo.

Configura `FIRESTORE_PROJECT_ID`, las credenciales de Google, `JOB_AGENT_WEBHOOK_API_KEY` y
`OPENROUTER_API_KEY` en el entorno (o en `.env` para una ejecución local). Instala las dependencias
de `requirements.txt` y arranca el servicio desde la raíz del repositorio:

```bash
.venv/bin/python -m uvicorn job_agent.webhook:app --host 127.0.0.1 --port 8000
```

Para enviar una notificación local:

```bash
curl -i -X POST http://127.0.0.1:8000/webhooks/inference \
  -H "X-API-Key: <tu-api-key>"
```

Sustituye `<tu-api-key>` por la clave configurada. La respuesta correcta es `HTTP/1.1 200 OK`
con cuerpo vacío. La clave no debe incluirse en la URL.

Antes de notificar, guarda el perfil profesional en el documento Firestore `profiles/current`, con
los campos de `Profile` (`full_name`, `headline`, `seniority`, `years_of_experience`, `summary`,
`target_roles`, `search_keywords`, `skills`, `domains`, `languages`, `locations`,
`notable_projects` y `strengths_missing_from_resume`). Cada elemento de `skills` tiene `name`,
`level` y `evidence`. El servicio solo lee ese documento; no genera ni actualiza el perfil.

Por cada documento `job_postings/{id}` con `status: "PENDING"`, el servicio compara el campo `job`
con el perfil mediante Jev (`typesafe/jev-1.13`). También acepta como pendiente un documento antiguo
sin `status`. Al obtener un resultado, actualiza el mismo documento con `status: "EVALUATED"`,
`score` (entero de 0 a 100), `confidence` (0 a 1), `score_model` y `evaluated_at`. Conserva el campo
`job`. Una oferta que falla sigue pendiente; vuelve a notificar el webhook para reintentarla. Las
ofertas evaluadas no se puntúan de nuevo. Las ejecuciones se serializan dentro de cada instancia del
servicio. El trabajo vive en el proceso: si este termina durante una evaluación, no hay recuperación
automática ni cola persistente; vuelve a notificar para procesar los pendientes.

## Buscador local con Firestore

El componente [`local_collector/`](local_collector/) corre en tu Mac, usa tu sesión de
LinkedIn mediante el MCP y guarda ofertas completas en Cloud Firestore. La evaluación con Jev ya está
disponible en el webhook; los avisos por Telegram y la generación de CV son etapas posteriores. El comando
anterior `run` sigue disponible durante la transición. `job_contracts/` contiene los modelos de los
documentos compartidos; el buscador tiene entrada, configuración y entorno Python propios.

Para preparar el buscador:

1. Crea un proyecto Firebase con una base de [Cloud Firestore](https://firebase.google.com/docs/firestore/quickstart).
2. Instala solo las dependencias del buscador con `scripts/macos/setup_collector.sh`. Conserva tu `.env` si ya existe; agrega
   `FIRESTORE_PROJECT_ID` y `GOOGLE_APPLICATION_CREDENTIALS` (ruta absoluta a un JSON de cuenta de
   servicio guardado fuera del repositorio). El buscador usa estas credenciales para acceder a Firestore.
3. Genera el plan inicial a partir del perfil local existente y `config.yaml`:
   ```bash
   .venv/bin/python -m job_agent seed-search-plan
   ```
   Si aún no existe `data/profile.json`, créalo primero con `.venv/bin/python -m job_agent profile --force`.
   Publicar el plan es una preparación única; cada corrida normal solo lo lee de Firestore.
4. Ejecuta una búsqueda manual para comprobar la conexión y ver el resultado en `logs/collector-AAAA-MM-DD.log`:
   ```bash
   scripts/macos/run_collector.sh
   ```
   También puedes invocarlo con `.venv-collector/bin/python -m local_collector`.
5. Programa las tres búsquedas diarias (08:00, 14:00 y 22:00 por defecto):
   ```bash
   python3 scripts/macos/install_schedule.py --component collector
   ```

La programación anterior se administra por separado con `--component legacy` (valor por defecto).
Si ambas están activas, cada corrida espera a que termine la otra antes de usar LinkedIn. El buscador nuevo no envía notificaciones
por sí mismo; el webhook inicia la evaluación cuando recibe una petición.

```text
local_collector/          # búsqueda local, LinkedIn MCP, Firestore y CLI
job_contracts/            # esquemas compartidos de SearchPlan y JobPosting
job_agent/                # agente anterior; luego evolucionará al servicio remoto
local_collector/requirements.txt
scripts/macos/setup_collector.sh
scripts/macos/run_collector.sh
```

La corrida programada importa `local_collector` y `job_contracts`, sin importar `job_agent` ni sus
dependencias de LLM, Telegram o PDF. `job_agent collect` se conserva como puente de compatibilidad y
`job_agent seed-search-plan` prepara el plan inicial a partir del perfil local existente.

## Flujo anterior (`job_agent run`)

Agente que corre en tu Mac **3 veces al día**, busca ofertas de trabajo, filtra las que mejor encajan con tu perfil
y, cuando una oferta encaja pero tu hoja de vida no te hace justicia, **crea una versión ajustada
de tu CV** para esa oferta. Después te envía una notificación por **Telegram** con un resumen de la oferta
(cargo, empresa, lugar, fuente, afinidad, nivel de inglés y salario si la publicación los indica) y si la
hoja de vida se reescribió, se reutilizó o no se pudo generar (con el PDF adjunto).

```
             ┌───────────────────── tu Mac (launchd 3×/día) ─────────────────────┐
resume/      │                                                                │
base.md ───► │ 1. Perfil   CV + repos git ──Claude──► data/profile.json       │
repos git ─► │             (solo se rehace si cambian o pasan N días)        │
             │ 2. Búsqueda LinkedIn vía MCP, sin LLM ──► ids de ofertas      │
             │ 3. Filtros  deterministas: vistas, duplicadas, exclusiones    │
             │ 4. Afinidad Claude: cada oferta que pasó vs perfil + CV       │
             │ 5. Ajuste   si encaja y el CV la "subvende" ──► CV a medida   │
             │             output/AAAA-MM-DD/<empresa>-<cargo>/resume.{md,pdf}│
             │ 6. Aviso    Telegram: resumen de la oferta + estado CV + PDF   │
             │ 7. Estado   data/ y output/ en tu Mac (nunca en el repositorio)  │
             └────────────────────────────────────────────────────────────────┘
```

## Cómo funciona

| Paso | Caso de uso / puerto | Detalle |
|---|---|---|
| Perfil | `EnsureProfile` · `ResumeSource`, `CodeRepositoryReader`, `ProfileInferer`, `ProfileStore` | Clona (superficialmente) tus repos de GitHub — o cualquier remoto git — y extrae lenguajes, árbol, README y manifiestos. Claude infiere roles objetivo, seniority, habilidades con evidencia y lo que tus repos demuestran pero tu CV no menciona. Se guarda en `data/profile.json`. Como es la llamada más costosa, solo se rehace si cambias `resume/base.md` (de inmediato) o si cambiaron tus repos y pasaron `profile_refresh_days` días (30 por defecto); si nada cambió, nunca. Para forzarlo: `profile --force` o `run --refresh-profile`. |
| Búsqueda y filtros | `RunSearchCycle` · `JobSource`, `SeenJobsRepository` · `JobFilter` | **Sin LLM.** El agente arma las consultas (palabras clave × ubicaciones) y llama directamente a `search_jobs` del servidor MCP de LinkedIn con sus filtros nativos (fecha, modalidad, nivel). Los ids ya procesados se descartan antes de descargar el detalle; luego se aplican los filtros deterministas (empresas y palabras excluidas, modalidad, antigüedad, duplicados por empresa + cargo normalizados). Detalle en [docs/arquitectura.md](docs/arquitectura.md#filtros-en-orden). |
| Afinidad | `JobMatcher` | **Único juicio de Claude sobre la oferta**, solo para las que pasaron los filtros. Puntaje 0-100, motivos, brechas y `resume_undersells` (encajas, pero tu CV actual no lo muestra). |
| CV a medida | `ResumeSelector`, `ResumeTailor`, `ApplicationStore` | Antes de escribir un CV nuevo revisa el catálogo de versiones ya ajustadas (ver [Reutilización](#reutilización-de-hojas-de-vida)): reutiliza, adapta la más parecida o crea una nueva. Al crear o adaptar, reescribe resumen, orden y viñetas con el vocabulario de la oferta y agrega proyectos de tus repos. **Regla dura: solo usa hechos de tu CV o de tus repos**; nunca inventa empresas, fechas, títulos ni métricas. Cada cambio queda justificado en `README.md` dentro de la carpeta de la versión. |
| Aviso | `Notifier` | Telegram (mensaje + PDF). Con `--dry-run` o sin credenciales, se usa el notificador de consola. |
| Estado | `SeenJobsRepository`, `MatchHistory` | Evita repetir ofertas (por id, url o empresa+cargo para reposts). Historial en `data/matches.jsonl`. |

Las reglas de negocio (umbrales para notificar/ajustar, identidad y duplicados de ofertas, vigencia
del perfil, criterios de búsqueda) viven en `job_agent/domain/policies.py`.

## Proveedor del modelo (Anthropic u OpenRouter)

Las tareas que usan un LLM (inferir el perfil, puntuar ofertas, elegir una hoja de vida para reutilizar y
ajustarla) comparten los mismos prompts y piden una respuesta con esquema fijo; el proveedor solo cambia
el transporte. Se elige en `config.yaml`:

```yaml
llm:
  provider: openrouter                 # o anthropic (por defecto, claude-opus-5-5)
  model: anthropic/claude-sonnet-4.5   # id exacto de openrouter.ai/models
  models:                              # opcional: un modelo por tarea (profile, match, select, tailor)
    match: google/gemini-2.5-flash
```

y la clave en `.env` (`OPENROUTER_API_KEY` o `ANTHROPIC_API_KEY`). Con OpenRouter el agente pide salida
estructurada (`response_format: json_schema`) y que solo se use un proveedor que la soporte; si el modelo
no la soporta, describe el esquema en el prompt. En ambos casos valida la respuesta y, si no es válida,
reintenta una vez indicando el error. Los modelos pequeños o gratuitos pueden fallar en esa validación o
dar puntajes y hojas de vida de peor calidad.

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

Se configura en `config.yaml` → `resume_reuse` (`enabled`, `max_candidates`). El catálogo vive solo en
tu Mac, en `storage.output_dir` (por defecto `output/`, ignorado por git); haz copia de seguridad de esa
carpeta si quieres conservarlo. La notificación indica la carpeta de cada versión.

## Arquitectura hexagonal

> Diagramas de componentes y de secuencia en [`docs/arquitectura.md`](docs/arquitectura.md).

Cada herramienta que usa el agente (Claude, servidores MCP, git/GitHub, Telegram, sistema de
archivos) es un **adaptador** detrás de un **puerto**. El núcleo no sabe con qué herramienta habla.

```
                 adaptador de entrada                      adaptadores de salida
                ┌────────────────────┐                  ┌──────────────────────────────────┐
 launchd (Mac)  │ entrypoints/cli.py │                  │ llm/  Anthropic u OpenRouter      │
(cron 3×/día)─►│                    │                  │   LlmProfileInferer               │
                └─────────┬──────────┘                  │   LlmJobMatcher                  │
                          │                             │   LlmResumeTailor / Selector     │
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

## Puesta en marcha (macOS)

Correrlo en tu Mac usa tu misma sesión, IP y perfil de navegador de LinkedIn, lo que reduce el riesgo de
verificaciones de seguridad. Lo pesado (el LLM) corre en los servidores del proveedor; en tu Mac solo corre un Chromium oculto unos minutos por corrida (~0,5-1 GB de RAM).

1. **Clona el repo fuera de Documentos, Escritorio, Descargas o iCloud** (macOS bloquea ahí las tareas
   programadas), por ejemplo en `~/dev/job-search`, y entra en la carpeta.
2. **Preparación** (Python 3.10+, `uv`, `pango` para los PDF, dependencias y `.env`):
   ```bash
   scripts/macos/setup.sh
   ```
3. **Completa `.env`** (el script lo crea copiando `example.env`, que explica cada variable) con la clave del LLM (`ANTHROPIC_API_KEY` u `OPENROUTER_API_KEY`, según
   `llm.provider`), `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`. El perfil, las ofertas vistas, el
   historial y las hojas de vida quedan en tu Mac (`storage` en `config.yaml`); nada de eso se sube al
   repositorio.
4. **Sesión de LinkedIn** (una vez; repítelo si LinkedIn cierra la sesión):
   ```bash
   uvx mcp-server-linkedin@latest --login
   ```
5. **Prueba** sin enviar notificaciones y revisa el log:
   ```bash
   scripts/macos/run_local.sh --dry-run
   tail -100 logs/run-$(date +%Y-%m-%d).log
   ```
6. **Programa las 3 corridas diarias** (por defecto 08:00, 14:00 y 22:00; cámbialas con `--times`):
   ```bash
   python3 scripts/macos/install_schedule.py
   ```
   Si el Mac está suspendido a esa hora, la corrida se ejecuta al despertar; si está apagado, se pierde
   (por eso `posted_within_days` está en 2). Correr ya: `launchctl kickstart gui/$(id -u)/<label>`
   (el instalador te muestra el comando exacto). Quitarlo: `--uninstall`.

## Desarrollo y pruebas

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp example.env .env                     # y completa las claves (ver comentarios en example.env)
python -m job_agent profile --force    # inferir y ver tu perfil
python -m job_agent run --dry-run      # un ciclo completo sin enviar notificaciones
python -m job_agent test-notify        # probar Telegram
pytest                                  # tests (no requieren API key)
```

## Agregar más fuentes

Las fuentes son deterministas: no usan el LLM para buscar. Para agregar otra bolsa de empleo (otra API,
otro servidor MCP), implementa el puerto `JobSource` en `adapters/job_sources/`:

1. Ejecuta las consultas del `SearchPlan` contra la fuente (aplicando sus filtros nativos si los tiene).
2. Llama a `admit(JobLead(...))` por cada id encontrado y pide el detalle solo de los admitidos, hasta
   `max_details`.
3. Devuelve `JobPosting` con los campos que la fuente provea.

Luego regístrala en `bootstrap.py` y agrega su sección en `search.sources` de `config.yaml`. Los filtros
deterministas, la deduplicación y el puntaje del LLM se aplican igual a todas las fuentes.

## Notas

- Automatizar LinkedIn con una sesión de navegador puede ir contra sus términos de uso; úsalo con
  moderación (3 corridas/día, `max_jobs_per_run` acotado).
- Costo aproximado por corrida: una sesión de búsqueda + un puntaje por oferta nueva + un CV por
  oferta que lo amerite. Ajusta `max_jobs_per_run` y los umbrales para controlarlo.
- Revisa siempre el CV ajustado antes de enviarlo: el agente justifica cada cambio, pero tú decides.

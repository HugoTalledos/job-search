# job-search — agente de búsqueda de empleo

## Webhook de entrada para la inferencia

`job_agent` expone `POST /webhooks/inference` para iniciar la evaluación y recomendación de ofertas en segundo
plano. Una petición autorizada recibe `200` sin cuerpo cuando se programa el trabajo; ese código no
confirma que la evaluación haya terminado. Una API key ausente o incorrecta recibe `401` sin iniciar
trabajo.

Configura `FIRESTORE_PROJECT_ID`, las credenciales de Google, `JOB_AGENT_WEBHOOK_API_KEY`,
`OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET` y
`FIREBASE_STORAGE_BUCKET` en el entorno (o en `.env` para una ejecución local). El servicio no arranca
si falta alguna; el bucket se explica en [CV a medida desde Telegram](#cv-a-medida-desde-telegram-ajustar_cv).
Instala las dependencias
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
`level` y `evidence`. Puedes crearlo (o rehacerlo) desde Telegram con `/build-profile`; ver abajo.

Por cada documento `job_postings/{id}` con `status: "PENDING"`, el servicio primero enriquece la
oferta a partir de su `description`. Guarda `required_language`, `salary_range` (vacíos si no hay
datos claros) y `enriched_at` en el mismo documento. Este paso usa Jev para elegir entre fragmentos
literales de la descripción y se ejecuta por separado del motor de score. Si `enriched_at` ya existe,
reutiliza el enriquecimiento. Luego compara `job` con el perfil mediante Jev (`typesafe/jev-1.13`)
y guarda `score` (entero de 0 a 100), `confidence` (0 a 1), `score_model` y `evaluated_at`.
También acepta como pendiente un documento antiguo sin `status`.

Si el score queda bajo `matching.min_score_to_notify` de `config.yaml` (70 por defecto), cambia el
estado a `EVALUATED`. Si alcanza el umbral, cambia a `PENDING_NOTIFICATION` y envía un mensaje a
Telegram con cargo, empresa, ubicación, enlace y score; incluye idioma y rango salarial solo cuando
están disponibles. Cuando Telegram confirma el envío, cambia a `NOTIFIED` y guarda `notified_at`.
Un fallo de enriquecimiento o score deja la oferta `PENDING`, lista para procesarse en una futura
notificación normal al webhook. Un fallo de Telegram deja `PENDING_NOTIFICATION`: este webhook no
reintenta esos envíos, que quedan para otro mecanismo. Conserva `job` y los metadatos del buscador.
Las ejecuciones se serializan dentro de cada instancia del servicio; no hay coordinación entre
instancias ni cola persistente. El `200` del webhook no confirma la entrega del mensaje.

## Control desde Telegram

El mismo servicio expone `POST /webhooks/telegram`, al que Telegram envía los mensajes que le escribes
a tu bot. Solo acepta llamadas con el encabezado `X-Telegram-Bot-Api-Secret-Token` igual a
`TELEGRAM_WEBHOOK_SECRET` (1 a 256 caracteres `A-Z`, `a-z`, `0-9`, `_` o `-`; otro valor responde
`401`) y solo atiende mensajes del chat `TELEGRAM_CHAT_ID`; el resto se ignora con `200` para que
Telegram no los reenvíe.

Para conectar el bot, publica el servicio en una URL HTTPS (Telegram no llama a HTTP) y ejecuta una vez:

```bash
.venv/bin/python -m job_agent set-telegram-webhook https://<tu-servicio>
```

Esto registra `https://<tu-servicio>/webhooks/telegram` con el secreto y publica el menú de comandos
del bot. Comandos disponibles:

| Comando | Qué hace |
|---|---|
| `/build-profile` (o `/build_profile`, el que aparece en el menú) | Responde de inmediato «Voy a construir tu nuevo perfil profesional» y, en segundo plano, lee `resume_path` y los repositorios públicos de `github_user` (más `repositories`) de `config.yaml`, infiere el perfil con el modelo de `llm` (tarea `profile`) y lo guarda en `profiles/current`. Al terminar te envía un resumen: titular, seniority, cargos objetivo, habilidades principales y las novedades frente al perfil anterior (habilidades nuevas o con otro nivel, cargos, dominios, fortalezas que tu CV no muestra…). Si falla, te avisa. |
| `/resend_pending` | Responde inmediatamente «Estoy buscando propuestas que hayan quedado pendientes de notificar». En segundo plano, busca ofertas con estado `PENDING_NOTIFICATION` y reenvía la notificación usando la puntuación y el enriquecimiento guardados. Marca `NOTIFIED` cada envío confirmado; los fallidos conservan `PENDING_NOTIFICATION`. Al terminar informa cuántas se notificaron y cuántas fallaron. |
| `/preferencias` | Sin texto, muestra tus preferencias de búsqueda guardadas y cuántas búsquedas tiene el plan vigente (sin usar el LLM). Con texto (`/preferencias quiero Go y sin Acme`), responde «Revisando tus preferencias…», interpreta la petición con el LLM (tarea `preferences`), y muestra el cambio y las primeras búsquedas con los botones Aplicar y Cancelar. Nada cambia hasta pulsar Aplicar; al aplicar se guardan `settings/search_preferences` (versión + 1) y el plan `settings/search_plan` que lee el buscador. Las propuestas caducan a las 24 horas. |
| `/ajustar_cv` (como respuesta a un mensaje de oferta del bot) | Responde de inmediato «Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar.» y, en segundo plano, genera un CV ajustado a esa oferta y te lo envía en PDF junto con un resumen de encaje y brechas. Ver la sección siguiente. |

Si el webhook ya estaba registrado, vuelve a ejecutar `set-telegram-webhook` para actualizar el menú de comandos y los tipos de actualización (`message` y `callback_query`, necesario para los botones de `/preferencias`). Dentro de una instancia, el reenvío espera a que termine una evaluación en curso para evitar notificaciones duplicadas.

Cualquier otro mensaje recibe la lista de comandos. Si escribes `/build-profile` mientras ya se está
construyendo un perfil, el bot te lo indica y no inicia otro. Igual que la evaluación, las
construcciones se serializan dentro de cada instancia del servicio, sin cola persistente. El servicio
necesita `git` instalado para leer los repositorios.

### CV a medida desde Telegram (`/ajustar_cv`)

Cuando una oferta te interesa, **responde al mensaje de esa oferta** (mantén pulsado → Responder) con
`/ajustar_cv`. El bot solo atiende el comando en el chat privado con `TELEGRAM_CHAT_ID` (el chat y el
remitente deben ser ese mismo id; en grupos se ignora) y solo si respondes a un mensaje enviado por el bot.

- **Identificación de la oferta.** Busca primero `telegram_offer_messages/{chat_id}_{message_id}` del
  mensaje respondido. Para mensajes enviados antes de existir ese registro, usa únicamente el enlace
  «Ver publicación» del mensaje (entidad `text_link`) si coincide con exactamente un documento
  `job_postings` por `job.url`. Nunca deduce la oferta del título ni del texto. Si no hay una
  identificación única, el bot lo explica y no genera nada; un `/ajustar_cv` sin respuesta recibe
  instrucciones de uso.
- **Confirmación.** Antes de responder, lee el CV base (`resume_path`), `profiles/current` y la oferta
  completa, y reclama la versión en Firestore. Si falta alguno de ellos, el bot avisa al momento y no
  programa trabajo. Si la misma versión ya se está generando, te lo indica sin iniciar otra.
- **Generación en segundo plano.** Analiza requisitos con el modelo de `llm` (tarea `match`), ajusta el
  CV (tarea `tailor`), lo renderiza a PDF y sube `cv.pdf`, `resume.md` y `README.md` (análisis,
  evidencia, brechas, cambios y huellas de las entradas) a `gs://<bucket>/cvs/{posting_id}/{version_id}/…`.
  Luego te envía el resumen y el PDF como respuesta al mensaje de la oferta. Si algo falla, recibes un
  aviso genérico (sin contenido del CV ni de la oferta) y puedes repetir `/ajustar_cv`.
- **Versiones y seguimiento.** `application_tracking/{posting_id}` registra la oferta (etapa `CV_READY`
  cuando hay artefactos completos; **no** significa que te postulaste) y
  `application_tracking/{posting_id}/versions/{version_id}` guarda cada versión con sus estados de
  generación (`PROCESSING`, `READY`, `FAILED`) y entrega (`PENDING`, `SENT`, `FAILED`). Repetir el
  comando sin cambios en el CV base, el perfil o la oferta reenvía el PDF ya generado sin volver a llamar
  al LLM; si cambia alguna entrada, se crea una versión nueva y se conserva la anterior. El reclamo
  expira a los 30 minutos, de modo que una ejecución interrumpida se puede retomar.

**Configuración previa al despliegue:**

1. **Plan Blaze y bucket.** Cloud Storage for Firebase requiere el plan Blaze. Crea (o usa) un bucket
   **privado** del proyecto, por ejemplo `<proyecto>.firebasestorage.app`, con la prevención de acceso
   público activada (`gcloud storage buckets update gs://<bucket> --public-access-prevention`). Los
   archivos no se publican con URL; solo se guardan rutas `gs://` en Firestore.
2. **`FIREBASE_STORAGE_BUCKET`.** Nombre del bucket sin `gs://` ni barras. Es obligatorio para
   `job_agent.webhook:app`; el buscador local no lo usa.
3. **Permisos (IAM).** La cuenta de servicio del agente (la de Cloud Run o la de
   `GOOGLE_APPLICATION_CREDENTIALS`) ya necesita Firestore; además debe crear y leer objetos del bucket,
   por ejemplo con `roles/storage.objectUser` sobre ese bucket:
   `gcloud storage buckets add-iam-policy-binding gs://<bucket> --member=serviceAccount:<cuenta> --role=roles/storage.objectUser`.
4. **PDF.** El renderizado usa WeasyPrint (`requirements.txt`), que necesita las bibliotecas del sistema
   de Pango (por ejemplo `libpango-1.0-0` y `libpangoft2-1.0-0` en Debian/Ubuntu, `pango` en macOS) en la
   imagen del servicio. Sin ellas la generación falla y el bot avisa del fallo.
5. **CV base y modelo.** El archivo `resume_path` de `config.yaml` debe estar disponible para el
   servicio, y la clave del proveedor de `llm` (tareas `match` y `tailor`) configurada.
6. **Menú del bot.** Vuelve a ejecutar `.venv/bin/python -m job_agent set-telegram-webhook https://<tu-servicio>`
   para que `/ajustar_cv` aparezca en el menú de comandos.

La generación no usa el bloqueo de la evaluación de ofertas: no la detiene ni cambia el estado
`NOTIFIED` de la propuesta. No se garantiza una entrega exactamente única si Telegram acepta un envío
y Firestore falla antes de registrar su confirmación.

## Buscador local con Firestore

El componente [`local_collector/`](local_collector/) corre en tu Mac, usa tu sesión de
LinkedIn mediante el MCP y guarda ofertas completas en Cloud Firestore. La evaluación con Jev y los
avisos por Telegram están disponibles en el webhook; la generación de CV es una etapa posterior. `job_contracts/` contiene los modelos de los
documentos compartidos; el buscador tiene entrada, configuración y entorno Python propios.

Para preparar el buscador:

1. Crea un proyecto Firebase con una base de [Cloud Firestore](https://firebase.google.com/docs/firestore/quickstart).
2. Instala solo las dependencias del buscador con `scripts/macos/setup_collector.sh`. Conserva tu `.env` si ya existe; agrega
   `FIRESTORE_PROJECT_ID` y `GOOGLE_APPLICATION_CREDENTIALS` (ruta absoluta a un JSON de cuenta de
   servicio guardado fuera del repositorio). El buscador usa estas credenciales para acceder a Firestore.
3. Ejecuta una búsqueda manual para comprobar la conexión y ver el resultado en `logs/collector-AAAA-MM-DD.log`:
   ```bash
   scripts/macos/run_collector.sh
   ```
   También puedes invocarlo con `.venv-collector/bin/python -m local_collector`.
4. Programa las tres búsquedas diarias (08:00, 14:00 y 22:00 por defecto):
   ```bash
   python3 scripts/macos/install_schedule.py --component collector
   ```

Las preferencias de búsqueda (palabras clave, ubicaciones, antigüedad, modalidad, nivel y exclusiones)
viven en Firestore, no en `config.yaml`, y se editan con `/preferencias`. El buscador lee el plan
`settings/search_plan` y descarta antes de guardar las ofertas de empresas o con títulos excluidos
(el informe de cada corrida cuenta cuántas excluyó), de modo que no llegan a `job_postings`.

### Migrar desde el `config.yaml` anterior

Haz esto una sola vez, en este orden:

```bash
git show 38f1609:config.yaml > /tmp/config-legacy.yaml
.venv/bin/python -m job_agent seed-search-preferences --from-config /tmp/config-legacy.yaml
```

1. Lo anterior publica tus preferencias antiguas y su plan en Firestore (con `--force` reemplaza las
   existentes; sin él no toca unas ya guardadas).
2. Despliega la nueva versión del servicio.
3. Ejecuta `.venv/bin/python -m job_agent set-telegram-webhook https://<tu-servicio>` para registrar los
   nuevos tipos de actualización y el menú con `/preferencias`.

Si `config.yaml` conserva claves antiguas bajo `search:`, se ignoran con una advertencia.

El buscador no envía notificaciones por sí mismo; el webhook inicia la evaluación y el envío cuando recibe una petición.

Si instalaste antes el job de launchd del flujo anterior (`job_agent run`), desinstálalo:

```bash
python3 scripts/macos/install_schedule.py --label com.<usuario>.job-search --uninstall
```

(`<usuario>` es tu usuario de macOS en minúsculas y sin símbolos; la etiqueta anterior era
`com.<usuario>.job-search`. Si prefieres hacerlo a mano:
`launchctl bootout gui/$(id -u)/com.<usuario>.job-search` y borra
`~/Library/LaunchAgents/com.<usuario>.job-search.plist`.)

```text
local_collector/          # búsqueda local, LinkedIn MCP, Firestore y CLI
job_contracts/            # esquemas compartidos de SearchPlan y JobPosting
job_agent/                # servicio remoto (webhook, puntaje Jev, Telegram, CV)
local_collector/requirements.txt
scripts/macos/setup_collector.sh
scripts/macos/run_collector.sh
```

La corrida programada importa `local_collector` y `job_contracts`, sin importar `job_agent` ni sus
dependencias de LLM, Telegram o PDF.

## Cómo funciona

| Paso | Caso de uso / puerto | Detalle |
|---|---|---|
| Perfil | `EnsureProfile` · `ResumeSource`, `CodeRepositoryReader`, `ProfileInferer`, `ProfileStore` | Clona (superficialmente) tus repos de GitHub — o cualquier remoto git — y extrae lenguajes, árbol, README y manifiestos. Claude infiere roles objetivo, seniority, habilidades con evidencia y lo que tus repos demuestran pero tu CV no menciona. Solo se rehace si cambias `resume/base.md` (de inmediato) o si cambiaron tus repos y pasaron `profile_refresh_days` días (30 por defecto). Para forzarlo: `/build_profile` en Telegram. |
| Afinidad | `JobMatcher` | Puntaje 0-100, motivos, brechas y `resume_undersells` para cada oferta evaluada. |
| CV a medida | `ResumeTailor` | Reescribe resumen, orden y viñetas con el vocabulario de la oferta. **Regla dura: solo usa hechos de tu CV o de tus repos**; nunca inventa empresas, fechas, títulos ni métricas. |

Las reglas de negocio (identidad y duplicados de ofertas, vigencia del perfil) viven en `job_agent/domain/policies.py`.

## Proveedor del modelo (Anthropic u OpenRouter)

Las tareas que usan un LLM (inferir el perfil, puntuar ofertas, ajustar la hoja de vida) comparten los mismos prompts y piden una respuesta con esquema fijo; el proveedor solo cambia
el transporte. Se elige en `config.yaml`:

```yaml
llm:
  provider: openrouter                 # o anthropic (por defecto, claude-opus-5-5)
  model: anthropic/claude-sonnet-4.5   # id exacto de openrouter.ai/models
  models:                              # opcional: un modelo por tarea (profile, match, tailor, preferences)
    match: google/gemini-2.5-flash
```

y la clave en `.env` (`OPENROUTER_API_KEY` o `ANTHROPIC_API_KEY`). Con OpenRouter el agente pide salida
estructurada (`response_format: json_schema`) y que solo se use un proveedor que la soporte; si el modelo
no la soporta, describe el esquema en el prompt. En ambos casos valida la respuesta y, si no es válida,
reintenta una vez indicando el error. Los modelos pequeños o gratuitos pueden fallar en esa validación o
dar puntajes y hojas de vida de peor calidad.

## Arquitectura hexagonal

> Diagramas de flujo de datos y de secuencia en [`docs/arquitectura.md`](docs/arquitectura.md).

Cada herramienta que usa el agente (Claude, servidores MCP, git/GitHub, Telegram, sistema de
archivos) es un **adaptador** detrás de un **puerto**. El núcleo no sabe con qué herramienta habla.

```
                 adaptador de entrada                      adaptadores de salida
                ┌────────────────────┐                  ┌──────────────────────────────────┐
 Cloud Run      │ entrypoints/       │                  │ llm/  Anthropic u OpenRouter      │
(webhook)─────►│                    │                  │   LlmProfileInferer               │
                └─────────┬──────────┘                  │   LlmJobMatcher                  │
                          │                             │   LlmResumeTailor                │
              ┌───────────▼───────────────┐  puertos    │                                   │
              │ application/              │◄───────────►│                                   │
              │   EnsureProfile           │ (ports.py)  │                                   │
              │   GenerateTailoredCv      │             │ code_repositories/                │
              │ ┌───────────────────────┐ │             │   GitRepositoryReader (GitHub, …) │
              │ │ domain/               │ │             │ resume/   FileResumeSource        │
              │ │  models, policies     │ │             │ notifications/ Telegram          │
              │ └───────────────────────┘ │             │ persistence/   Firestore          │
              └───────────────────────────┘             └──────────────────────────────────┘
                     bootstrap.py (raíz de composición) conecta cada puerto con su adaptador
```

```
job_agent/
├── domain/            # entidades, value objects y reglas puras (sin dependencias externas)
├── application/       # casos de uso + ports.py (interfaces que el núcleo necesita)
├── adapters/          # implementaciones concretas de los puertos, una carpeta por herramienta
│   ├── llm/  code_repositories/  resume/  notifications/  persistence/
├── entrypoints/cli.py # adaptador de entrada (registro del webhook de Telegram)
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

El buscador local se instala como se describe arriba (`scripts/macos/setup_collector.sh` y
`scripts/macos/install_schedule.py`). Correr en tu Mac usa tu misma sesión, IP y perfil de navegador de
LinkedIn, lo que reduce el riesgo de verificaciones de seguridad.

1. **Clona el repo fuera de Documentos, Escritorio, Descargas o iCloud** (macOS bloquea ahí las tareas
   programadas), por ejemplo en `~/dev/job-search`.
2. **Sesión de LinkedIn** (una vez; repítelo si LinkedIn cierra la sesión):
   ```bash
   uvx mcp-server-linkedin@latest --login
   ```
3. Quitar la programación: `python3 scripts/macos/install_schedule.py --uninstall`.

## Desarrollo y pruebas

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp example.env .env                     # y completa las claves (ver comentarios en example.env)
pytest                                  # tests (no requieren API key)
```

## Notas

- Automatizar LinkedIn con una sesión de navegador puede ir contra sus términos de uso; úsalo con
  moderación (3 corridas/día).

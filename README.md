# job-search — agente de búsqueda de empleo

## Webhook de entrada para la inferencia

`job_agent` expone `POST /webhooks/inference` para iniciar la evaluación y recomendación de ofertas en segundo
plano. Una petición autorizada recibe `200` sin cuerpo cuando se programa el trabajo; ese código no
confirma que la evaluación haya terminado. Una API key ausente o incorrecta recibe `401` sin iniciar
trabajo.

Configura `FIRESTORE_PROJECT_ID`, las credenciales de Google, `JOB_AGENT_WEBHOOK_API_KEY`,
`OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET` y
`FIREBASE_STORAGE_BUCKET` en el entorno (o en `.env` para una ejecución local). El servicio no arranca
si falta alguna; el bucket se explica en [CV a medida desde Telegram](#cv-a-medida-desde-telegram-botón-ajustar-cv).
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
Telegram con cargo, empresa, ubicación, enlace y score, y el botón «📄 Ajustar CV»; incluye idioma y
rango salarial solo cuando están disponibles. Cuando Telegram confirma el envío, cambia a `NOTIFIED` y guarda `notified_at`.
Un fallo de enriquecimiento o score deja la oferta `PENDING`, lista para procesarse en una futura
notificación normal al webhook. Un fallo de Telegram deja `PENDING_NOTIFICATION`: este webhook no
reintenta esos envíos, que quedan para otro mecanismo. Conserva `job` y los metadatos del buscador.
Las ejecuciones se serializan dentro de cada instancia del servicio; no hay coordinación entre
instancias ni cola persistente. El `200` del webhook no confirma la entrega del mensaje.

Al terminar cada corrida, el bot resume cuántas ofertas se notificaron. Si no notificó ninguna,
indica cuántas ya se habían notificado en búsquedas anteriores, cuántas se excluyeron por preferencias,
cuántas quedaron bajo el umbral de afinidad con el perfil y cuántas fallaron. Una oferta conocida que
todavía está `PENDING` no se cuenta como descartada: puede evaluarse en esa misma corrida.

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
| Botón «📄 Ajustar CV» (bajo cada oferta) | Prepara un borrador con resumen y Markdown completo para revisión. El PDF solo se genera al pulsar «✅ Aprobar y generar PDF». Ver la sección siguiente. Para ofertas antiguas sin botón, responde a su mensaje con `/ajustar_cv` (ya no aparece en el menú). |

El PDF del CV ajustado lleva el botón «✅ Apliqué». Al pulsarlo, el bot registra una copia de la oferta
y la fecha en `applied_proposals/{posting_id}` y confirma la marca por Telegram. La marca se crea una
sola vez por oferta, aunque vuelvas a pulsar el botón o generes otro CV. Generar el PDF no implica
haber aplicado; la marca solo se guarda al pulsar el botón. Los PDF enviados antes de esta versión no
tienen el botón.

Si el webhook ya estaba registrado, vuelve a ejecutar `set-telegram-webhook` para actualizar el menú de comandos y los tipos de actualización (`message` y `callback_query`, necesario para los botones de `/preferencias`). Dentro de una instancia, el reenvío espera a que termine una evaluación en curso para evitar notificaciones duplicadas.

Cualquier otro mensaje recibe la lista de comandos. Si escribes `/build-profile` mientras ya se está
construyendo un perfil, el bot te lo indica y no inicia otro. Igual que la evaluación, las
construcciones se serializan dentro de cada instancia del servicio, sin cola persistente. El servicio
necesita `git` instalado para leer los repositorios.

### CV a medida desde Telegram (botón «Ajustar CV»)

Cuando una oferta te interesa, **pulsa «📄 Ajustar CV»** bajo su mensaje. Telegram muestra un aviso breve
(«Ajustando tu CV…», «Ya estoy ajustando este CV» o «No pude iniciar el ajuste») y el bot te escribe el
detalle en el chat. Puedes volver a pulsarlo para pedir el CV otra vez. El bot solo atiende el botón en el
chat privado con `TELEGRAM_CHAT_ID` (el chat y quien lo pulsa deben ser ese mismo id; en otro caso responde
«Acción no válida» sin generar nada).

Las ofertas enviadas antes de existir el botón no lo tienen: para ellas, **responde al mensaje de la
oferta** (mantén pulsado → Responder) con `/ajustar_cv`. El comando sigue funcionando, solo en el mismo chat
privado y solo si respondes a un mensaje enviado por el bot, pero ya no aparece en el menú.

- **Identificación de la oferta.** Busca primero `telegram_offer_messages/{chat_id}_{message_id}` del
  mensaje de la oferta. Para mensajes enviados antes de existir ese registro, usa únicamente el enlace
  «Ver publicación» del mensaje (entidad `text_link`) si coincide con exactamente un documento
  `job_postings` por `job.url`. Nunca deduce la oferta del título ni del texto. Si no hay una
  identificación única, el bot lo explica y no genera nada; un `/ajustar_cv` sin respuesta recibe
  instrucciones de uso.
- **Vista previa.** El bot analiza la oferta (`match`) y ajusta el CV (`tailor`), guarda una revisión
  inmutable y entrega `cv-borrador.md` con el texto completo y un resumen. Lee el documento completo;
  el resumen puede omitir errores. Todavía no se crea ni envía un PDF.
- **Corrección factual.** Responde al mensaje o documento de la revisión: «No hablo inglés; elimina
  English B2». El bot propone el fragmento anterior y el nuevo, e indica el cambio del perfil para
  futuros CV. Pulsa «✅ Confirmar» para guardarlo o «❌ Rechazar» para descartarlo. Una instrucción
  ambigua pide precisión y no cambia datos. El hecho confirmado sigue vigente aunque después canceles
  el borrador, ejecutes `/build-profile` o solicites el CV de otra oferta. El perfil efectivo usado por
  scoring, preferencias y futuros CV incorpora las correcciones.
- **Corrección de estilo.** Responde, por ejemplo: «Cambia “Desarrollo soluciones” por “Desarrollo
  servicios backend”». La propuesta indica «Solo cambia esta versión del CV»; confirmar no modifica
  el perfil. Corregir aplica cambios puntuales al Markdown guardado, sin repetir el ajuste completo.
- **Confirmación y aprobación.** Confirmar una corrección publica otra revisión y su documento
  completo. Revisa esa versión y pulsa «✅ Aprobar y generar PDF». Un botón de aprobación anterior
  avisa que el borrador cambió. «❌ Cancelar» detiene ese borrador. El PDF se renderiza desde el Markdown
  exacto de la revisión aprobada; el análisis se actualiza frente a los hechos confirmados.
- **PDF ya entregado.** Pulsa «✏️ Corregir CV» bajo el PDF para abrir otro borrador desde su Markdown
  guardado. En un PDF antiguo sin botón, responde al PDF con `/corregir_cv`. Confirma la corrección y
  aprueba la nueva revisión; la entrega anterior permanece como versión histórica. El botón
  «✅ Apliqué» sigue registrando la postulación explícitamente.
- **Revocar un dato confirmado.** Abre un borrador (o corrige un PDF) y responde, por ejemplo:
  «Revoco la corrección anterior de inglés; sí hablo inglés B2. Cambia “Idiomas: español” por
  “Idiomas: español e inglés B2”». Revisa la propuesta factual antes de confirmar: la revocación debe
  identificar una corrección activa sin ambigüedad. El historial se conserva y el perfil se recalcula
  desde su inferencia base con las correcciones que sigan activas. Si falta un fragmento concreto o
  no se identifica la corrección, el bot pide precisión.
- **Versiones y reintentos.** Los borradores y propuestas viven en `cv_reviews`; cada revisión tiene
  Markdown privado e inmutable. Las correcciones globales tienen historial en `profile_corrections`
  y una proyección efectiva en `profiles/current`; `resume/base.md` no se edita desde Telegram.
  `application_tracking/{posting_id}/versions/{version_id}` conserva los artefactos y recibos de
  entrega. Si falla el renderizado o envío, vuelve a aprobar la misma revisión: se reutiliza el
  Markdown aprobado, sin ajustar otra vez con el modelo. Una corrección factual cambia la versión de
  los CV futuros. Si falla la recompilación del plan de búsqueda, el bot lo indica y la corrección
  permanece guardada.

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
6. **Recuperación de PDFs antiguos.** Configura un índice de **campo único con alcance de grupo de
   colecciones** para el grupo `versions`, campo `pdf_message_id` (orden ascendente). La búsqueda
   usa `collection_group("versions")` y filtra por ese recibo; el índice automático con alcance de
   colección no basta. Si falta, `/corregir_cv` explica qué índice debe configurar el administrador
   y permite reintentar sin alterar el PDF ni el estado guardado.
7. **Menú del bot.** Vuelve a ejecutar `.venv/bin/python -m job_agent set-telegram-webhook https://<tu-servicio>`
   para actualizar el menú de comandos (`/ajustar_cv` ya no aparece; las ofertas nuevas traen el botón).

La generación no usa el bloqueo de la evaluación de ofertas: no la detiene ni cambia el estado
`NOTIFIED` de la propuesta. No se garantiza una entrega exactamente única si Telegram acepta un envío
y Firestore falla antes de registrar su confirmación.

## Buscador local con Firestore

El componente [`local_collector/`](local_collector/) corre en tu Mac, usa tu sesión de
LinkedIn mediante el MCP y guarda ofertas completas en Cloud Firestore. Al terminar cada corrida avisa al
webhook del servicio, que evalúa las ofertas con Jev y envía los avisos por Telegram; la generación de
CV es una etapa posterior. `job_contracts/` contiene los modelos de los
documentos compartidos; el buscador tiene entrada, configuración y entorno Python propios.

Para preparar el buscador:

1. Crea un proyecto Firebase con una base de [Cloud Firestore](https://firebase.google.com/docs/firestore/quickstart).
2. Instala solo las dependencias del buscador con `scripts/macos/setup_collector.sh`. Conserva tu `.env` si ya existe; agrega
   `FIRESTORE_PROJECT_ID` y `GOOGLE_APPLICATION_CREDENTIALS` (ruta absoluta a un JSON de cuenta de
   servicio guardado fuera del repositorio). El buscador usa estas credenciales para acceder a Firestore.
   Para que cada corrida pida la evaluación al servicio, agrega también `JOB_AGENT_URL` y
   `JOB_AGENT_WEBHOOK_API_KEY` (ver más abajo).
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

Al terminar cada corrida, el buscador llama a `POST <JOB_AGENT_URL>/webhooks/inference` con el
encabezado `X-API-Key: <JOB_AGENT_WEBHOOK_API_KEY>`, de modo que las ofertas nuevas se evalúan y se
notifican por Telegram sin pasos manuales. Lo hace aunque no haya ofertas nuevas, para que el servicio
reintente las que quedaron `PENDING` en corridas anteriores. Agrega ambas variables al `.env` del Mac
(`JOB_AGENT_URL` es la URL base HTTPS del servicio; HTTP solo se acepta en `localhost`). Si
`JOB_AGENT_URL` está vacía, el buscador solo guarda las ofertas y lo advierte en el log. Si la llamada
falla (servicio caído, `401` por clave incorrecta…), las ofertas quedan guardadas, el error aparece en
`logs/collector-AAAA-MM-DD.log` y la corrida termina con código 1; la siguiente corrida vuelve a pedir
la evaluación. Un `200` solo confirma que el servicio programó el trabajo, no que haya terminado.

Con el servicio configurado, Telegram avisa al comenzar la búsqueda, al comenzar la evaluación y al
terminarla. El cierre indica cuántas ofertas se evaluaron y notificaron, incluso cuando no había ninguna
pendiente. También avisa si la búsqueda terminó con errores o si una etapa falló; los detalles quedan en
los registros del buscador o del servicio.

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

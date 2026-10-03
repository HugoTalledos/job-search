# Preferencias de búsqueda editables desde Telegram

## Objetivo

El candidato cambia el tipo de ofertas que recibe escribiéndole al bot en lenguaje natural, sin editar `config.yaml` ni ejecutar comandos en el Mac. Por ejemplo: `/preferencias quiero backend senior en Python, remoto o España, nada de bancos`. El bot interpreta el mensaje y muestra los cambios. Solo los aplica cuando el candidato los confirma con un botón. Al aplicarlos, actualiza el plan que el colector local consulta en su siguiente corrida.

Las preferencias que influyen en la búsqueda dejan `config.yaml` y pasan a Firestore como única fuente de verdad. El bot de Telegram las edita y el colector solo lee el plan ya calculado.

## Alcance

**Migra a Firestore** lo que define qué se busca y qué se descarta:

- palabras clave extra (hoy `extra_keywords`),
- ubicaciones, días desde la publicación, modalidad (`work_types`) y seniority (`experience_levels`),
- empresas excluidas y palabras excluidas en el título.

**Permanece en `config.yaml`**, porque es infraestructura o presupuesto y no se edita por chat:

- `search.sources.linkedin` (servidor MCP del Mac),
- `search.max_queries`, `search.max_roles_from_profile` y `search.max_details_per_run`,
- `matching.min_score_to_notify`,
- `llm`, `resume_path`, `github_*` y `repositories`.

**Se retira** el flujo anterior `job_agent run` (ver "Retiro del flujo anterior").

**Fuera de alcance:** cambiar el puntaje de afinidad según las preferencias, editar presupuestos o umbrales por chat, y aplicar exclusiones en el evaluador del webhook.

## Datos en Firestore

### `settings/search_preferences` (nuevo, fuente de verdad)

| Campo | Tipo | Significado |
|---|---|---|
| `keywords_include` | lista de texto | Keywords que siempre se buscan, antes que las del perfil. |
| `keywords_exclude` | lista de texto | Keywords que no se buscan aunque vengan del perfil. La comparación está normalizada. |
| `use_profile_keywords` | bool (por defecto `true`) | Si es `false`, el perfil no aporta keywords ("solo quiero X"). |
| `locations` | lista de texto | Ubicaciones de búsqueda. Si está vacía, se usan las del perfil; si tampoco hay, se busca sin ubicación. |
| `posted_within_days` | entero 1–30 | Antigüedad máxima de la publicación. |
| `work_types` | subconjunto de `remote`, `hybrid`, `on_site` | Vacío significa cualquiera. |
| `experience_levels` | subconjunto de `internship`, `entry`, `associate`, `mid_senior`, `director`, `executive` | Vacío significa cualquiera. |
| `exclude_companies` | lista de texto | Se comparan normalizadas: "Acme Inc." equivale a "ACME". |
| `exclude_title_keywords` | lista de texto | Se descarta la oferta si alguna aparece en el título. |
| `version` | entero | Aumenta en 1 con cada cambio aplicado. |
| `updated_at` | marca de tiempo | Hora del último cambio. |

Un modelo Pydantic compartido en `job_contracts` valida el documento. Los valores fuera de los conjuntos permitidos se rechazan, y las listas se guardan sin duplicados normalizados y sin entradas vacías.

### `settings/search_plan` (existe, pasa a ser derivado)

Conserva la forma actual de `CollectorPlan`: consultas, días, `work_types`, `experience_levels` y `max_details_per_run`. Se agregan estos campos con valores por defecto, para que un plan antiguo siga siendo válido:

- `exclude_companies` y `exclude_title_keywords`,
- `preferences_version`, la versión de preferencias de la que salió,
- `profile_fingerprint`, el SHA-256 del perfil usado (vacío si no había perfil),
- `built_at`.

Nadie edita este documento a mano. Solo lo escribe la compilación descrita más abajo.

### `search_preference_drafts/{draft_id}` (nuevo)

Un cambio propuesto que espera confirmación. Guarda:

- `chat_id`,
- `base_version`, la versión de preferencias sobre la que se interpretó,
- las preferencias resultantes ya validadas,
- el texto del diff mostrado,
- `status`: `PENDING`, `APPLIED`, `CANCELLED` o `EXPIRED`,
- `created_at` y `expires_at` (24 h).

El texto libre del candidato no se guarda.

## Compilación del plan

La función pura `build_search_plan(preferences, profile | None, budgets) -> CollectorPlan` reemplaza a `SearchPreferences.plan_for` y no usa LLM.

1. **Keywords**, en este orden: `keywords_include`; después, si `use_profile_keywords` es verdadero, los primeros `max_roles_from_profile` cargos objetivo del perfil y sus `search_keywords`. Se descartan las vacías, las que coinciden con `keywords_exclude` y los duplicados normalizados. Se conserva el primer orden de aparición.
2. **Ubicaciones:** `locations` de las preferencias; si está vacía, las del perfil; si tampoco hay, una consulta sin ubicación.
3. **Consultas:** keywords × ubicaciones, en ese orden, recortadas a `max_queries`.
4. Copia días, modalidad, seniority y exclusiones. Toma `max_details_per_run` de `config.yaml` y añade `preferences_version`, `profile_fingerprint` y `built_at`.

Para las mismas entradas, el resultado es idéntico salvo `built_at`.

**Cuándo se recalcula:**

- al aplicar un borrador de preferencias,
- al terminar con éxito `/build_profile`,
- con el comando de migración.

**Sin perfil o sin keywords:** si no existe `profiles/current` y las preferencias no tienen `keywords_include`, no hay ninguna keyword. En ese caso las preferencias se guardan igual, el plan **no** se reemplaza y el bot explica que se generará cuando exista el perfil o cuando agregues keywords. Nunca se publica un plan con cero consultas.

## Flujo en Telegram

Todos los comandos de esta sección exigen el secreto del webhook y un chat privado cuyo `chat.id` y `from.id` coincidan con `TELEGRAM_CHAT_ID`, igual que `/ajustar_cv`. Los botones validan el chat y el usuario del `callback_query` de la misma forma.

### `/preferencias` sin texto: consulta determinista

Es una consulta puramente determinista: no llama al LLM, no crea borradores y no programa trabajo en segundo plano. Lee `settings/search_preferences` y `settings/search_plan` y responde en la misma petición con:

- las preferencias en formato legible,
- el número de búsquedas del plan vigente,
- la versión de preferencias de la que salió el plan, para notar si está desfasado.

Si no hay preferencias, indica que debe ejecutarse la migración.

### `/preferencias <texto>`: propuesta de cambio

1. Responde de inmediato `Revisando tus preferencias…` y continúa en segundo plano, como los comandos actuales.
2. **Interpretación:** el LLM recibe las preferencias actuales y el texto, y devuelve una lista de operaciones con un esquema estricto. Las operaciones son:
   - `add` y `remove` sobre las listas,
   - `set` sobre `locations`, `posted_within_days`, `work_types`, `experience_levels` y `use_profile_keywords`,
   - `unclear`, con una explicación.

   El LLM no devuelve el documento completo, así que lo que el candidato no menciona no se toca.
3. **Validación determinista:** se aplican las operaciones sobre una copia de las preferencias actuales y se valida el resultado con el modelo de `job_contracts`.
   - Si hay valores inválidos, solo `unclear` o ningún cambio efectivo, el bot dice qué no entendió y no crea borrador.
   - "Nada de bancos" puede traducirse en `exclude_title_keywords` y no en una lista de empresas que el LLM invente. El prompt prohíbe inventar nombres de empresas que el candidato no escribió.
4. **Vista previa:** guarda el borrador y envía un mensaje con:
   - el diff por campo (`➕` agregado, `➖` quitado, valor nuevo para los campos simples),
   - un resumen del plan resultante, calculado con el perfil actual: número de búsquedas y las primeras consultas,
   - un aviso si `max_queries` recortó consultas,
   - los botones inline **✅ Aplicar** y **❌ Cancelar**, cuyo `callback_data` lleva el `draft_id`.

### Botones

- **Aplicar:** en una transacción de Firestore, exige que el borrador esté `PENDING`, no haya caducado y que la `version` actual de las preferencias siga siendo `base_version`. Si se cumple, escribe las preferencias con `version + 1`, el plan recompilado y el borrador como `APPLIED`. Después edita el mensaje a `✅ Aplicado: N búsquedas`.
  - Si cambió la versión, el borrador pasa a `EXPIRED` y el bot pide repetir el pedido.
  - Si no hay keywords, se aplica la regla de "Sin perfil o sin keywords".
- **Cancelar:** marca el borrador `CANCELLED` y edita el mensaje. No cambia las preferencias.
- **Repeticiones:** pulsar de nuevo un borrador ya resuelto o caducado responde con su estado y no hace nada. Telegram exige `answerCallbackQuery` en cada pulsación.

### Registro en Telegram

- `setWebhook` pasa a `allowed_updates: ["message", "callback_query"]`.
- `setMyCommands` incluye `preferencias`.
- El README indica cómo volver a registrar el webhook y el menú.

## Colector local

- Lee `settings/search_plan` igual que hoy y no lee el perfil ni las preferencias.
- Después de descargar el detalle de cada oferta y antes de guardarla en `job_postings`, descarta las que coinciden con `exclude_companies` o `exclude_title_keywords`. Usa la misma normalización que `JobFilter`, movida a `job_contracts` para compartirla sin depender de `job_agent`.
- Las ofertas descartadas no se guardan y se cuentan en `CollectionReport.excluded`.

## Retiro del flujo anterior

El flujo anterior `job_agent run` es un ciclo único en el Mac que busca, filtra, puntúa, ajusta CVs en carpetas locales y notifica. Ya lo reemplazan el colector, el webhook y `/ajustar_cv`, y no debe mantenerse vivo para esta funcionalidad. Se retira **antes** de migrar las preferencias:

- **CLI:** se eliminan los comandos `run`, `profile`, `test-notify`, `collect` (el colector tiene su propia CLI) y `seed-search-plan`. Queda `set-telegram-webhook` y se agrega `seed-search-preferences`.
- **Programación:** se eliminan `scripts/macos/run_local.sh` y el componente `legacy` de `install_schedule.py`; `--component collector` pasa a ser el único y su valor por defecto. El README explica cómo desinstalar la tarea antigua de launchd si estaba instalada (`launchctl bootout` de la etiqueta `.job-search`).
- **Código exclusivo del flujo anterior:** `RunSearchCycle`, `build_container` y lo que solo él usa (fuentes de empleo del agente, almacenes JSON locales de estado, historial y catálogo de CVs, selector de reutilización, notificador de consola, formato de alertas, `JobFilter`, `ReusePolicy`, `MatchingPolicy` y sus modelos). Se conserva lo que usa el webhook: `build_llm`, `EnsureProfile`, `BuildProfessionalProfile`, matcher, tailor, renderizadores, `TelegramNotifier.send_text` y la normalización de nombres.
- **`config.yaml`:** se eliminan `storage`, `resume_reuse`, `matching.min_score_to_tailor` y `search.max_jobs_per_run`, que solo usaba el flujo anterior.

## Migración

- **Comando de migración:** `job_agent seed-search-preferences [--from-config RUTA] [--force]` lee las claves de búsqueda de un `config.yaml` antiguo (por defecto el actual) y crea `settings/search_preferences` con `version = 1`. Si el documento ya existe, no hace nada salvo con `--force`. Después compila el plan con el perfil de `profiles/current`.
- **`config.yaml`:** se eliminan las claves migradas y `Config` deja de declararlas. Si aparecen en un `config.yaml` antiguo, se ignoran con una advertencia que remite al comando de migración. `search.sources.linkedin` se queda, porque lo lee el colector.
- **Ubicaciones del perfil:** al reconstruir el perfil, `EnsureProfile` toma las ubicaciones preferidas de `settings/search_preferences` en ese momento, en lugar de `config.yaml`.

## Errores y privacidad

- Los fallos del LLM, de Firestore o de Telegram durante la interpretación responden un mensaje genérico y no crean borrador.
- Aplicar es atómico: las preferencias y el plan se escriben en la misma transacción, o no se escribe nada.
- Si `/build_profile` termina pero falla la recompilación del plan, el perfil queda guardado y el bot avisa que el plan no se actualizó.
- Los logs no incluyen el texto libre del candidato ni la respuesta del LLM; solo el tipo de excepción y los identificadores.

## Pruebas

Fakes de Firestore (con transacciones), LLM y Telegram; ninguna prueba requiere servicios reales.

- `build_search_plan`: orden de keywords, `keywords_exclude`, `use_profile_keywords = false`, respaldo de ubicaciones, recorte por `max_queries`, determinismo y caso sin keywords.
- Interpretación: aplicación de cada operación, rechazo de valores fuera de los enumerados, `unclear` y "sin cambios" sin borrador.
- `/preferencias` sin texto no llama al LLM ni crea borrador.
- Aplicar:
  - en una transacción;
  - pulsar dos veces aplica una sola vez;
  - una versión cambiada expira el borrador;
  - un borrador caducado no aplica;
  - Cancelar no cambia nada;
  - una pulsación de otro chat o usuario se ignora.
- `/build_profile` recompila el plan; si la recompilación falla, el perfil se conserva.
- Colector: descarta por empresa y por título normalizados, cuenta `excluded` y acepta un plan antiguo sin campos de exclusión.
- Migración: copia los valores de `config.yaml`, no sobrescribe sin `--force` y compila el plan.
- Retiro: la CLI ya no ofrece `run`, `profile`, `test-notify`, `collect` ni `seed-search-plan`; el webhook arranca y sus pruebas pasan sin el código retirado; las pruebas de arquitectura siguen pasando.
- Registro: `allowed_updates` incluye `callback_query` y el menú incluye `preferencias`.

## Criterios de aceptación

1. `/preferencias` sin texto muestra las preferencias y el resumen del plan sin llamar al LLM.
2. `/preferencias <texto>` muestra un diff y no cambia nada hasta pulsar **Aplicar**.
3. Tras **Aplicar**, la siguiente corrida del colector usa las nuevas consultas y descarta las empresas y títulos excluidos.
4. `/build_profile` deja un plan coherente con las preferencias vigentes y el perfil nuevo.
5. `config.yaml` ya no contiene preferencias de búsqueda, y ningún comando sobrescribe por sí solo las preferencias guardadas en Firestore.
6. El flujo anterior `job_agent run` ya no existe, y la única tarea programada en el Mac es el colector.

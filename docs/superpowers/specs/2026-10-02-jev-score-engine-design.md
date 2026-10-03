# Motor de afinidad con Jev

## Objetivo y alcance

La notificación `POST /webhooks/inference` inicia en segundo plano la evaluación de las ofertas reunidas por `local_collector`. El servicio lee un perfil profesional ya publicado en Firestore, puntúa las ofertas pendientes con Jev 1.13 y guarda cada resultado en el documento original de la oferta. El webhook conserva la autenticación `X-API-Key` y responde `200` con cuerpo vacío al programar el trabajo. Esta etapa no crea ni modifica el perfil, no genera explicaciones, no notifica por Telegram y no prepara CV.

## Datos compartidos

El perfil se lee del documento `profiles/current`, cuyos campos corresponden directamente al modelo `job_agent.domain.models.Profile`. Otra parte del sistema lo publicará; el motor solo valida y consume sus datos. Si falta o no cumple el esquema, la corrida en segundo plano termina con un error registrado y no cambia las ofertas.

Cada `job_postings/{job_key}` conserva los campos actuales (`source`, `external_id`, `job`, `ingested_at`) y añade `status`. `local_collector` escribe `status: "PENDING"` al crear una oferta nueva. Los documentos anteriores sin `status` se interpretan como `PENDING`. El lector conserva el ID real de cada documento para actualizarlo después. Un estado desconocido se registra y se deja intacto. El motor lee la colección y puntúa solo las ofertas pendientes; no vuelve a puntuar las `EVALUATED` si cambia el perfil.

Después de una puntuación válida, el motor actualiza únicamente ese documento con `status: "EVALUATED"`, `score` (entero de 0 a 100), `confidence` (número de 0 a 1), `score_model: "typesafe/jev-1.13"` y `evaluated_at` (fecha de servidor en UTC). Conserva la oferta completa y los metadatos del buscador. Si falla la puntuación o la escritura de una oferta, esta permanece pendiente para otra notificación; el error se registra y las demás ofertas continúan. No hay colección separada de scores ni de corridas en esta etapa.

## Herramienta de score

El motor expone una interfaz independiente de HTTP y Firestore: recibe un `Profile` y una `JobPosting` y devuelve `score` y `confidence`. Un caso de uso coordina la lectura del perfil y las ofertas, invoca esa herramienta por cada oferta pendiente y guarda el resultado. El modelo del motor se configura por separado del `llm` anterior y queda fijado en esta etapa a `typesafe/jev-1.13`. Usa `OPENROUTER_API_KEY` del lado del servidor; no carga CV, repositorios ni modelos del flujo anterior.

El adaptador de Jev llama `POST https://openrouter.ai/api/alpha/decisions` con el perfil y una oferta en `state` y una pregunta de tipo `score`. La escala tiene cinco niveles: (0) cargo ajeno al perfil o requisito indispensable incumplido; (1) pocos requisitos coincidentes y varias brechas esenciales; (2) encaje con el cargo y algunos requisitos, pero con una brecha importante; (3) mayoría de requisitos esenciales cumplidos y solo brechas menores; (4) requisitos esenciales prácticamente completos con evidencia sólida en el perfil. La posición devuelta por Jev (0 a 4) se multiplica por 25 y se redondea a un entero de 0 a 100. Se valida el tipo de respuesta y el rango de `score` y `confidence`; una respuesta inválida no marca la oferta como evaluada. Jev no devuelve justificaciones de texto libre, por lo que esta herramienta no promete razones ni brechas.

Esta elección usa la operación Score específica de Jev. La alternativa de usar el adaptador actual de chat no sirve para el contrato tipado de Decisions; una cola y un worker persistentes se dejan para una etapa de despliegue que lo requiera. Referencias: [tutorial de Jev](https://openrouter.ai/docs/guides/community/jev-tutorial), [Score de TypeSafe](https://docs.typesafe.ai/primitives/score) y [API Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request).

## Ejecución y errores

Tras validar `X-API-Key`, el endpoint programa una tarea en segundo plano dentro del proceso y responde `200` sin esperar las llamadas a Jev. Una petición no autorizada no programa nada. Una colección vacía o sin ofertas pendientes no llama a Jev. La tarea carga el perfil antes de puntuar ofertas. Los fallos de Firestore al leer perfil o enumerar ofertas abortan la corrida y se registran sin exponer secretos ni descripciones completas en el log. Un documento de oferta inválido se deja intacto y se registra; las demás ofertas continúan. Los fallos de puntuación o escritura de una oferta también se registran sin detener las demás.

Una interrupción del proceso puede cortar la tarea. Las ofertas que no llegaron a `EVALUATED` siguen pendientes y se intentan en una nueva notificación. Dos notificaciones simultáneas podrían causar llamadas duplicadas a Jev; el servicio evita el solapamiento dentro de una instancia con un bloqueo en memoria. La coordinación entre múltiples instancias y la reanudación automática quedan fuera de esta etapa. Por ello, el `200` confirma que la tarea se programó en la instancia, no que todas las ofertas se hayan evaluado.

## Comprobación

Pruebas con dobles de Firestore y HTTP comprueban la lectura y validación del perfil, `PENDING` para ofertas nuevas y documentos antiguos sin estado, exclusión de `EVALUATED`, actualización del documento original, reintento tras fallos por oferta, ausencia de llamadas a Jev sin pendientes y respuesta inmediata vacía del webhook. Las pruebas del adaptador de Jev usan un transporte HTTP simulado para verificar URL, modelo, autenticación, `state`, pregunta Score, normalización y rechazo de respuestas inválidas. No requieren llamadas reales ni claves de producción.

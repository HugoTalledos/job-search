# Enriquecimiento y notificación de ofertas

## Objetivo y alcance

Cada llamada autorizada a `POST /webhooks/inference` inicia en segundo plano el procesamiento de ofertas `PENDING`. El orquestador enriquece cada oferta con idioma requerido y rango salarial cuando la descripción los expresa, obtiene su score de afinidad con el perfil profesional y envía una recomendación real a Telegram por cada oferta cuyo score alcanza el umbral configurado. El envío es un puerto del caso de uso; Telegram es un adaptador. El webhook sigue respondiendo `200` vacío al programar el trabajo, sin esperar la evaluación. Esta etapa no genera CV ni añade un mecanismo de reintentos de notificación.

## Flujo y límites de los componentes

El enriquecedor es una herramienta independiente del motor de score. Recibe `JobPosting` y devuelve `required_language` y `salary_range`, ambos opcionales. Primero identifica en `description` fragmentos literales candidatos para idioma y salario; luego consulta Jev mediante preguntas tipadas de selección para decidir cuáles representan requisitos o condiciones de *esa* oferta, con una opción `none`. Solo devuelve fragmentos presentes en la descripción. La ausencia de candidatos o una selección `none` produce campos vacíos; no se inventan niveles, monedas ni cifras. La extracción puede omitir formatos no reconocidos, lo que es preferible a mostrar datos incorrectos. El enriquecedor usa `typesafe/jev-1.13` en una llamada distinta a la del score, de modo que ambos contratos y pruebas permanecen independientes.

El orquestador carga el perfil existente de `profiles/current` y las ofertas `PENDING` de `job_postings`. Para cada oferta, si no tiene `enriched_at`, llama al enriquecedor y guarda sus campos opcionales más `enriched_at` en el mismo documento antes de pedir el score. Si el enriquecimiento ya está guardado, lo reutiliza. El motor actual de Jev conserva la entrada `Profile` + `JobPosting` y la salida `ScoreResult`; no asume responsabilidades de extracción ni envío. El umbral se toma de `matching.min_score_to_notify` en `config.yaml`, cuyo valor predeterminado es 70 y debe estar entre 0 y 100.

## Estados y persistencia

Las transiciones son:

| Estado de origen | Condición | Estado de destino |
| --- | --- | --- |
| `PENDING` (también documento antiguo sin `status`) | Enriquecimiento y score válidos, score bajo el umbral | `EVALUATED` |
| `PENDING` | Enriquecimiento y score válidos, score igual o superior al umbral | `PENDING_NOTIFICATION` |
| `PENDING_NOTIFICATION` recién producido en esa corrida | Telegram confirma el envío | `NOTIFIED` |

El score se guarda antes de intentar Telegram en el documento original: `score`, `confidence`, `score_model` y `evaluated_at`. Las ofertas bajo el umbral conservan `EVALUATED`. Las superiores conservan `PENDING_NOTIFICATION` si Telegram falla. `NOTIFIED` registra `notified_at` con fecha de servidor. Se preservan `job`, `source`, `external_id` y `ingested_at`. Los documentos `PENDING_NOTIFICATION` existentes al comenzar una corrida no se leen ni se envían; otro mecanismo gestionará sus reintentos. Una futura llamada normal al webhook sí vuelve a procesar ofertas que permanecieron `PENDING` por fallos de enriquecimiento o score, reutilizando cualquier enriquecimiento ya guardado.

El almacenamiento expone operaciones separadas para guardar el enriquecimiento, guardar el score y confirmar el envío. Si falla la escritura del enriquecimiento o del score, no se continúa hacia la siguiente etapa de esa oferta. Si Telegram confirma el envío pero falla la escritura de `NOTIFIED`, la oferta queda `PENDING_NOTIFICATION` y el futuro mecanismo de reintentos podría duplicar el mensaje; no se garantiza entrega exactamente una vez. No se crea una colección nueva de resultados o notificaciones.

## Notificación

El puerto de notificación recibe la oferta y los datos ya persistidos de evaluación, sin conocer HTTP ni Telegram. El adaptador de Telegram envía un mensaje por oferta con cargo, empresa, ubicación, enlace y score. Agrega idioma requerido y rango salarial solo cuando el enriquecedor los encontró. Escapa el contenido de la oferta para el formato de Telegram y respeta el límite de longitud. La respuesta de Telegram debe confirmar éxito; un error HTTP, una respuesta `ok: false` o un problema de red se propagan como fallo de esa oferta, sin registrar el token, el cuerpo completo de la oferta ni la URL que contiene el token.

El servicio requiere `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` al arrancar, además de las claves y credenciales que ya necesita. No sustituye silenciosamente Telegram por consola. La configuración de logs del cliente HTTP evita escribir la URL del Bot API con el token.

## Errores y concurrencia

Un fallo de lectura del perfil o de la colección aborta la corrida en segundo plano y se registra sin datos sensibles. Los errores por oferta se registran con el ID de documento y el tipo de error, y el orquestador continúa con las demás. El bloqueo por instancia del webhook sigue evitando dos corridas simultáneas en el mismo proceso. No hay coordinación entre instancias, cola persistente ni reintento automático de `PENDING_NOTIFICATION` en esta entrega.

## Comprobación

Pruebas del caso de uso con puertos falsos cubren el orden enriquecer → guardar enriquecimiento → score → guardar score → notificar → confirmar; el umbral inclusivo; omisión de datos ausentes; reutilización del enriquecimiento; fallo de cada etapa y continuidad de otras ofertas; y ausencia de envío para ofertas por debajo del umbral o ya `PENDING_NOTIFICATION`. Pruebas del adaptador de enriquecimiento con HTTP simulado verifican candidatos literales, selección `none`, rechazo de selecciones ajenas a la descripción y errores de Jev. Pruebas del adaptador de Telegram con HTTP simulado verifican mensaje, escape, campos opcionales, confirmación de éxito y fallos sin exposición del token. Pruebas de Firestore comprueban los campos, fechas y transiciones sobre el documento original. La suite no requiere llamadas reales a Jev ni Telegram.

## Referencia de Jev

La extracción acotada por candidatos sigue el patrón de [OpenRouter: Extraction without guessing](https://openrouter.ai/labs/jev/extract). Jev devuelve decisiones tipadas sobre criterios definidos por el código, según la [guía oficial de Jev](https://openrouter.ai/blog/tutorials/how-to-use-jev/).

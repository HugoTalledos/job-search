# CV a medida solicitado desde Telegram

## Objetivo

El candidato decide qué oferta le interesa después de recibirla por Telegram. Responde **al mensaje de esa oferta** con `/ajustar_cv`. El bot confirma la solicitud de inmediato y prepara en segundo plano un CV específico para la propuesta. Al terminar envía el PDF y un resumen en español de por qué encaja y qué brechas encontró. Conserva los artefactos y un registro separado para seguir más adelante qué ofertas recibieron un CV; generar el CV **no** significa que el candidato ya se postuló.

Este flujo corre en el servicio `job_agent.webhook:app`, que ya comparte Firestore con el evaluador de ofertas. Reutiliza los puertos y adaptadores actuales de lectura del CV base, análisis de encaje, ajuste con LLM y renderizado. El flujo local anterior (`job_agent run`) conserva su catálogo de archivos; no se traslada ni se modifica para esta entrega.

## Alternativa elegida

Un caso de uso nuevo, `GenerateTailoredCv`, coordina la solicitud y depende de puertos para leer CV, perfil y oferta, analizar requisitos, ajustar, renderizar, guardar artefactos y notificar. La entrada de Telegram identifica la oferta y encola el trabajo; no contiene lógica de generación. Se prefiere esta separación frente a incorporar el proceso en el webhook o adaptar el ciclo local, porque el webhook necesita persistencia y reintentos propios.

## Identificación y autorización de la oferta

1. `TelegramOfferNotifier` obtiene el `message_id` de cada `sendMessage` exitoso. Antes de marcar la oferta como `NOTIFIED`, guarda `telegram_offer_messages/{chat_id}_{message_id}` con la referencia a `job_postings/{id}` y `sent_at`. Cada reenvío crea su propia asociación, pues una oferta puede tener varios mensajes.
2. El webhook acepta `/ajustar_cv` únicamente como texto de una respuesta de Telegram (`reply_to_message`) a un mensaje de oferta del bot. Se conserva la validación del secreto del webhook y se exige un chat privado cuyo `chat.id` y `from.id` correspondan al `TELEGRAM_CHAT_ID` configurado. No se aceptan solicitudes en grupos ni comandos sueltos sin respuesta a una oferta.
3. Busca primero la asociación por `(chat_id, reply_to_message.message_id)`. Para mensajes enviados antes de introducir este registro, puede tomar **solo** el enlace de la publicación presente en las entidades del mensaje original y usarlo si identifica exactamente un documento `job_postings`. No se infiere la oferta a partir del título o del texto libre. Si no hay una identificación única, responde con una explicación breve y no genera nada.
4. Registra o recupera la solicitud antes de confirmar por chat: `Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar.` Los updates repetidos y las respuestas repetidas con las mismas entradas no inician dos generaciones simultáneas.

## Generación y reglas de contenido

El caso de uso lee, en este orden, el CV base indicado por `resume_path`, `profiles/current` y la oferta completa de `job_postings/{id}`. Si falta alguno o la descripción de la oferta está vacía, informa el fallo sin crear un PDF engañoso. El perfil guardado se usa como evidencia de habilidades demostradas por repositorios; no se reconstruye en cada solicitud.

El análisis estructurado de la propuesta distingue habilidades y requisitos imprescindibles de los deseables, y para cada uno señala evidencia en el CV o perfil, o lo marca como brecha. Se reutilizan `LlmJobMatcher` y su resultado de razones, gaps y foco de ajuste, ampliando el esquema o un adaptador específico para exponer los requisitos de forma explícita. El puntaje Jev ya guardado para la notificación no sustituye este análisis detallado. No se rechaza la generación por un puntaje bajo: la decisión de preparar el CV es del candidato.

`LlmResumeTailor` recibe la oferta, el análisis, el perfil y el CV base. Mantiene las reglas actuales: no inventar empleadores, cargos, fechas, títulos, métricas ni habilidades sin evidencia, y conservar los empleos y fechas del CV original. El resumen al candidato explica tanto las coincidencias como las brechas reales; una brecha no se convierte en una habilidad declarada en el PDF. El candidato revisa el documento antes de usarlo para postularse; el bot no presenta postulaciones automáticamente.

El PDF es obligatorio en este flujo. El renderizador convierte el Markdown generado a PDF y comprueba que el archivo exista y no esté vacío. Si falla la conversión, la solicitud queda fallida y el bot informa el error; no envía Markdown como sustituto del PDF.

## Artefactos y seguimiento

Se usa un bucket **privado** de Cloud Storage for Firebase, configurado mediante `FIREBASE_STORAGE_BUCKET` y las credenciales de servicio del agente. Cada versión tiene una ruta estable `cvs/{posting_id}/{version_id}/` con `cv.pdf`, `resume.md` y `README.md`. El README resume la propuesta, los requisitos cubiertos con su evidencia, brechas, motivos de encaje, cambios realizados, fecha y huellas del CV base y perfil. Las rutas `gs://` se guardan en Firestore; no se publica el CV con una URL permanente.

`application_tracking/{posting_id}` es el registro de la oferta de interés. Contiene la referencia a la propuesta, la fecha de primera solicitud, la última versión y una etapa inicial `CV_READY` cuando hay artefactos completos. Esta etapa no equivale a `APPLIED` y permite añadir seguimiento de postulaciones en una entrega posterior. `application_tracking/{posting_id}/versions/{version_id}` conserva por versión: huellas del CV base, perfil y descripción, análisis de requisitos, razones y brechas, rutas de artefactos, fechas, estado de generación (`PROCESSING`, `READY`, `FAILED`) y estado de entrega por Telegram (`PENDING`, `SENT`, `FAILED`).

El `version_id` se deriva de la oferta y las huellas de las tres entradas. Si el candidato repite `/ajustar_cv` sin cambios, una versión `READY` se vuelve a enviar sin repetir llamadas al LLM; una versión en proceso se informa como tal y una fallida puede reintentarse. Si cambian el CV base, el perfil o la oferta, se crea otra versión y se conserva el historial. Firestore reclama el trabajo de manera atómica para impedir dos generaciones de la misma versión entre instancias. Una ejecución interrumpida deja un registro recuperable mediante un plazo de expiración del reclamo; una solicitud posterior puede retomarlo.

Los artefactos se suben antes de marcar la versión como `READY`. La entrega a Telegram es un paso separado: un fallo de `sendDocument` no obliga a regenerar el CV y se puede reintentar. El bot envía un texto conciso con motivos de encaje y gaps y adjunta `cv.pdf` con `sendDocument`; el texto y el archivo hacen referencia a la misma oferta. Guarda el resultado de entrega y los identificadores de los mensajes cuando Telegram los confirma. No se promete entrega exactamente una vez si Telegram acepta un envío y Firestore falla antes de registrar esa confirmación.

## Fallos, límites y configuración

El webhook responde rápido y ejecuta la generación en segundo plano, como los comandos actuales. La solicitud queda persistida antes de responder para que una interrupción del proceso no la haga invisible. Cada fallo se comunica al chat sin incluir texto del CV, descripción privada ni claves en los logs. La generación se serializa por versión, no bloquea la evaluación de otras ofertas y no modifica el estado `NOTIFIED` de la propuesta.

El bucket debe existir antes del despliegue y la identidad del servicio necesita permiso de escritura y lectura sobre él. Firebase documenta que Cloud Storage for Firebase usa un bucket de Google Cloud Storage y requiere el plan Blaze: [Admin Cloud Storage API](https://firebase.google.com/docs/storage/admin/start?hl=es-419). La integración Python puede usar la [librería oficial de Cloud Storage](https://docs.cloud.google.com/storage/docs/samples/storage-upload-file) con las credenciales de aplicación ya usadas para Firestore. La configuración y el README explicarán la variable del bucket, los permisos y la necesidad de volver a publicar el menú del bot si se añade el comando allí.

## Comprobación de aceptación

- Una respuesta válida con `/ajustar_cv` identifica la oferta exacta, confirma la solicitud y devuelve el webhook sin esperar el análisis ni el PDF.
- Un comando sin respuesta, un mensaje de otro chat o remitente, y una respuesta a un mensaje no identificable no generan CV.
- El análisis usa CV base, perfil y descripción completa; registra requisitos cubiertos y brechas, y el PDF no afirma requisitos sin evidencia.
- Una generación completa guarda PDF, Markdown, README y metadatos de seguimiento; el bot envía el PDF y el resumen de encaje y gaps.
- Repetir la solicitud con entradas iguales reutiliza el PDF; cambiar una entrada crea una versión nueva sin borrar la anterior.
- Fallos de LLM, renderizado, Storage o Telegram dejan estados observables y permiten reintento sin marcar una postulación como realizada.
- Las pruebas de unidad usan adaptadores falsos; las de integración verifican el webhook y la persistencia sin llamar a Telegram, al LLM o a Firebase reales.

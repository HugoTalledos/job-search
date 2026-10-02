# Webhook de entrada para la inferencia remota

## Propósito y alcance

Un cliente podrá notificar a `job_agent` mediante HTTP para iniciar, en una entrega posterior, la evaluación de las ofertas reunidas por `local_collector`. En esta primera entrega el servicio recibe la notificación y consulta las ofertas de Cloud Firestore. No ejecuta inferencia, no entrega ofertas al cliente y no crea evaluaciones, postulaciones, notificaciones ni CV.

El contrato público es `POST /webhooks/inference`, sin cuerpo obligatorio. Una lectura completa y correcta de Firestore responde `200 OK` con cuerpo vacío. Por ahora este código representa el punto de inicio futuro; el `200` no afirma que se haya ejecutado una inferencia. Una colección vacía también produce `200`. El cliente puede repetir la petición: esta etapa no modifica datos ni guarda estado de procesamiento.

## Decisión de diseño

Se evaluaron dos formas de preparar el punto de entrada. La primera consulta Firestore dentro de la petición y confirma con `200` después de que la lectura termine. La segunda confirma de inmediato y deja la lectura para un proceso en segundo plano. Se elige la primera porque comprueba la conexión y el esquema reales sin introducir una cola o un trabajador que todavía no tiene trabajo de inferencia. Cuando exista ese trabajo, se podrá conectar después del caso de uso de lectura y revisar entonces el contrato de ejecución asíncrona.

## Componentes y datos

- El adaptador HTTP de `job_agent/entrypoints` valida un token Bearer compartido, configurado mediante `JOB_AGENT_WEBHOOK_TOKEN`. El servicio no arranca si falta el token. Una credencial ausente o incorrecta recibe `401` y no consulta Firestore. El token no se registra en logs.
- Un caso de uso en `job_agent/application` solicita las ofertas a un puerto de solo lectura. No depende de HTTP, Firestore, LLM ni de `local_collector`.
- Un adaptador de persistencia de `job_agent` lee `job_postings` con el SDK de Firestore y valida el campo `job` de cada documento con `job_contracts.JobPosting`. El identificador del documento puede acompañar al dato dentro del servicio para su uso futuro, pero no se devuelve al cliente. No se actualiza ninguna oferta.
- La composición del servicio crea solo el cliente de Firestore y el caso de uso de lectura. No construye los modelos, el perfil, LinkedIn, Telegram ni el flujo anterior de `RunSearchCycle`.

Se reutilizan `FIRESTORE_PROJECT_ID` y las credenciales de servicio de Google (`GOOGLE_APPLICATION_CREDENTIALS` localmente o credenciales de aplicación en el entorno remoto). La configuración y el arranque HTTP quedan documentados en el README. La elección del proveedor de alojamiento y su despliegue quedan fuera de esta entrega.

## Errores y observabilidad

Una petición con método distinto no ejecuta el caso de uso. Si Firestore falla o un documento no cumple el esquema compartido, el endpoint devuelve `500` sin incluir detalles de la oferta ni credenciales en la respuesta. El error se registra en el servidor para diagnóstico. El `200` solo se envía después de iterar todas las ofertas disponibles; la primera versión no introduce paginación, filtros ni marca de ofertas procesadas.

## Comprobación

Las pruebas del caso de uso usan un repositorio en memoria para comprobar la lectura completa y la colección vacía. Las pruebas HTTP usan un repositorio falso para comprobar `200` sin cuerpo, autenticación y error de lectura. Las pruebas del adaptador comprueban la ruta `job_postings`, la validación de `job` y la propagación de fallos sin necesitar credenciales reales. Una prueba de composición comprueba que el arranque HTTP no inicializa el agente anterior ni proveedores de LLM.

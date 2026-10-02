# Buscador local de LinkedIn

## Objetivo y alcance

El monorepo tendrá un buscador que corre en el Mac tres veces al día. Usa la sesión local de LinkedIn mediante el MCP actual y guarda ofertas completas en Cloud Firestore. No evalúa afinidad, no envía mensajes de Telegram y no genera hojas de vida. El agente remoto será responsable de esas tareas en una etapa posterior. Esta especificación cubre el buscador y los documentos de Firestore que necesita.

El buscador y el agente remoto se instalarán por separado. No se implementará un protocolo de compatibilidad entre versiones ni una API de ingestión: ambos componentes accederán directamente al mismo proyecto de Firestore. No hay migraciones SQL; la preparación inicial crea el documento del plan.

## Datos compartidos

Se usará Cloud Firestore en un proyecto de Firebase. Dos rutas de documentos bastan para este componente:

- `settings/search_plan`: un documento con el `SearchPlan` existente, el límite de detalles por corrida y su fecha de actualización. El agente remoto publicará el plan cuando se implemente. Para poner en marcha el buscador antes, un comando de preparación leerá `data/profile.json` y las preferencias actuales de `config.yaml`, calculará el plan con `SearchPreferences.plan_for` y guardará ese documento. Es una operación inicial, no parte de cada corrida. El buscador solo lee este documento.
- `job_postings/{job_key}`: un documento por oferta con `source`, `external_id`, la oferta completa y `ingested_at`. `job_key` es el identificador estable existente, derivado de fuente e ID externo. El buscador crea el documento solo si no existe. La descripción completa queda disponible para que el agente remoto la evalúe. Las evaluaciones, notificaciones y solicitudes de CV irán en colecciones propias.

Los documentos existentes se consultan en bloque por sus referencias tras `search_jobs` y antes de `get_job_details`, sin consultar la colección completa. Una oferta cuyo detalle falla no se inserta y podrá intentarse en otra corrida. La clave `source + external_id` es suficiente para el conector de LinkedIn actual, que siempre obtiene un ID; los duplicados por empresa y cargo quedan para el agente remoto, después de disponer del detalle. Si una oferta excede el límite de tamaño de documento de Firestore, se registra el error sin truncar silenciosamente la descripción.

No se reescriben documentos ya conocidos en cada búsqueda, para evitar escrituras innecesarias. Firestore ofrece una cuota gratuita de lecturas, escrituras y almacenamiento; se revisará el consumo real antes de ampliar la frecuencia o el número de ofertas. Referencias: [cuotas y límite de documento](https://firebase.google.com/docs/firestore/quotas) y [lecturas por lotes del cliente Python](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.client.Client).

El buscador usará credenciales de servicio mediante Application Default Credentials, guardadas fuera del repositorio. El SDK de servidor se autoriza mediante IAM; las reglas de seguridad de clientes de Firebase no restringen este acceso. El buscador no necesita claves del LLM, Telegram o S3. El bucket S3 pertenece al flujo remoto de CV y no interviene aquí.

## Corrida local

1. `launchd` inicia el comando del buscador a las horas ya configuradas. Se conserva el bloqueo que evita corridas solapadas.
2. El buscador lee `settings/search_plan`. Si falta el documento o Firestore no responde, termina con error visible en el log; no inicia una búsqueda sin plan.
3. Reutiliza `LinkedInMcpJobSource` y el parseo actual. Después de `search_jobs`, lee en bloque las referencias de los IDs conocidos y descarga detalles solo de IDs nuevos, hasta el límite del plan.
4. Crea cada documento de oferta en `job_postings` sin sobrescribir uno existente. Una creación repetida se trata como oferta ya conocida. Registra número de IDs encontrados, conocidos, detalles descargados, ofertas insertadas y errores. La escritura se confirma antes de reportarla como completada.
5. Si un detalle falla, continúa con los demás. Si falla Firestore, la corrida termina con error; la siguiente corrida volverá a encontrar las ofertas que sigan publicadas. No se añade una cola local persistente en esta primera versión.

La programación conserva `posted_within_days: 2`, que hoy cubre una corrida perdida, aunque no garantiza recuperar una oferta retirada mientras la base está indisponible. El comando admite una ejecución manual para diagnóstico. El perfil y el CV base ya no se leen en este proceso: el plan llega preparado desde la base.

## Organización del monorepo

El código de búsqueda y los modelos `JobLead`, `JobPosting` y `SearchPlan` existentes se reutilizarán. Se añadirá un caso de uso de recolección local, un adaptador Firestore para leer el plan y guardar ofertas, y una entrada CLI específica. El agente remoto podrá reutilizar los modelos de dominio cuando se implemente; este diseño no exige dividir el repositorio en paquetes instalables ahora. El script y la configuración de `launchd` apuntarán al nuevo comando. Las tareas actuales de evaluación y CV se retirarán del camino programado local cuando la ruta remota esté lista.

## Comprobación y límite de entrega

El diseño se comprueba con adaptadores en memoria para verificar que los IDs conocidos no consumen el cupo de detalles, que una corrida repetida no duplica ofertas, que los fallos de detalle no detienen el resto y que un fallo de Firestore se informa. El adaptador se comprueba con el emulador de Firestore cuando esté disponible. No se requiere una cuenta de LinkedIn ni credenciales de producción para estas comprobaciones.

Esta etapa entrega el buscador local y su esquema. La publicación automática del plan por el agente remoto, el procesamiento de ofertas, Telegram y el almacenamiento de CV en S3 pertenecen a la siguiente etapa.

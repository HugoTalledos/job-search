# Buscador local de LinkedIn

## Objetivo y alcance

El monorepo tendrá un buscador que corre en el Mac tres veces al día. Usa la sesión local de LinkedIn mediante el MCP actual y guarda ofertas completas en una base de datos externa. No evalúa afinidad, no envía mensajes de Telegram y no genera hojas de vida. El agente remoto será responsable de esas tareas en una etapa posterior. Esta especificación cubre el buscador y el contrato mínimo de base de datos que necesita.

El buscador y el agente remoto se instalarán por separado. No se implementará un protocolo de compatibilidad entre versiones ni una API de ingestión: ambos componentes accederán directamente a la misma base. Las migraciones se ejecutarán explícitamente durante la preparación de la base, nunca al iniciar una corrida del buscador.

## Datos compartidos

Se usará PostgreSQL como base externa inicial. Dos tablas bastan para este componente:

- `search_plan`: una fila activa con el `SearchPlan` existente serializado como JSON, el límite de detalles por corrida y su fecha de actualización. El agente remoto publicará el plan cuando se implemente. Para poner en marcha el buscador antes, un comando de preparación leerá `data/profile.json` y las preferencias actuales de `config.yaml`, calculará el plan con `SearchPreferences.plan_for` y guardará esa fila. Es una operación inicial, no parte de cada corrida. El buscador solo lee esta tabla.
- `job_postings`: clave única `(source, external_id)`, oferta completa serializada como JSON y marcas de primera y última recepción. El buscador inserta las ofertas obtenidas; un conflicto de clave actualiza `last_seen` y, si corresponde, el texto de la oferta. La descripción completa queda disponible para que el agente remoto la evalúe. El agente remoto solo leerá esta tabla; sus evaluaciones, notificaciones y solicitudes de CV irán en tablas propias.

La lista de IDs existentes se consulta en bloque tras `search_jobs` y antes de `get_job_details`. Una oferta cuyo detalle falla no se inserta y podrá intentarse en otra corrida. La clave `source + external_id` es suficiente para el conector de LinkedIn actual, que siempre obtiene un ID; los duplicados por empresa y cargo quedan para el agente remoto, después de disponer del detalle.

El buscador tendrá credenciales con permisos de lectura sobre `search_plan` y lectura/escritura limitada a `job_postings`. No necesita claves del LLM, Telegram o S3. El bucket S3 pertenece al flujo remoto de CV y no interviene aquí.

## Corrida local

1. `launchd` inicia el comando del buscador a las horas ya configuradas. Se conserva el bloqueo que evita corridas solapadas.
2. El buscador lee el plan activo y los límites de la base. Si falta el plan o la base no responde, termina con error visible en el log; no inicia una búsqueda sin plan.
3. Reutiliza `LinkedInMcpJobSource` y el parseo actual. Después de `search_jobs`, consulta en bloque los IDs conocidos y descarga detalles solo de IDs nuevos, hasta el límite del plan.
4. Inserta cada oferta completa en `job_postings` con una operación idempotente. Registra número de IDs encontrados, conocidos, detalles descargados, ofertas insertadas y errores. La inserción se confirma antes de reportarla como completada.
5. Si un detalle falla, continúa con los demás. Si falla la escritura en la base, la corrida termina con error; la siguiente corrida volverá a encontrar las ofertas que sigan publicadas. No se añade una cola local persistente en esta primera versión.

La programación conserva `posted_within_days: 2`, que hoy cubre una corrida perdida, aunque no garantiza recuperar una oferta retirada mientras la base está indisponible. El comando admite una ejecución manual para diagnóstico. El perfil y el CV base ya no se leen en este proceso: el plan llega preparado desde la base.

## Organización del monorepo

El código de búsqueda y los modelos `JobLead`, `JobPosting` y `SearchPlan` existentes se reutilizarán. Se añadirá un caso de uso de recolección local, un adaptador PostgreSQL para leer el plan y guardar ofertas, y una entrada CLI específica. El agente remoto podrá reutilizar los modelos de dominio cuando se implemente; este diseño no exige dividir el repositorio en paquetes instalables ahora. El script y la configuración de `launchd` apuntarán al nuevo comando. Las tareas actuales de evaluación y CV se retirarán del camino programado local cuando la ruta remota esté lista.

## Comprobación y límite de entrega

El diseño se comprueba con adaptadores en memoria para verificar que los IDs conocidos no consumen el cupo de detalles, que una corrida repetida no duplica ofertas, que los fallos de detalle no detienen el resto y que un fallo de base se informa. La consulta e inserción PostgreSQL se comprueban con una base de prueba cuando esté disponible. No se requiere una cuenta de LinkedIn ni credenciales de producción para estas comprobaciones.

Esta etapa entrega el buscador local y su esquema. La publicación automática del plan por el agente remoto, el procesamiento de ofertas, Telegram y el almacenamiento de CV en S3 pertenecen a la siguiente etapa.

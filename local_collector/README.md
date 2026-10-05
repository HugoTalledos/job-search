# Buscador local

Este componente consulta LinkedIn mediante la sesión MCP del Mac y crea ofertas nuevas en Cloud
Firestore. Solo depende de `job_contracts`, el SDK de Firestore, MCP, httpx, PyYAML y Pydantic. No importa
`job_agent` ni realiza evaluación, notificaciones o generación de CV: al terminar cada corrida llama a
`POST <JOB_AGENT_URL>/webhooks/inference` (encabezado `X-API-Key: <JOB_AGENT_WEBHOOK_API_KEY>`) para que
el servicio remoto evalúe y notifique. Sin `JOB_AGENT_URL` solo guarda las ofertas; un fallo de esa
llamada se registra como error de la corrida (código de salida 1) sin perder las ofertas guardadas.
Cuando el webhook está configurado, el buscador también avisa al servicio al iniciar la búsqueda y si
encuentra errores. El servicio envía por Telegram estos estados y el resultado final de la evaluación,
incluso cuando no hay ofertas pendientes. La llamada de inferencia incluye los conteos de ofertas
conocidas ya notificadas y de ofertas excluidas para explicar por qué no se enviaron propuestas.

Desde la raíz del repositorio:

```bash
scripts/macos/setup_collector.sh
scripts/macos/run_collector.sh
python3 scripts/macos/install_schedule.py --component collector
```

La CLI directa es `.venv-collector/bin/python -m local_collector [--config ruta.yaml]`. Lee el
servidor MCP de `search.sources.linkedin` en `config.yaml`, las credenciales de `.env` y el plan de
`settings/search_plan` en Firestore. Escribe ofertas nuevas en `job_postings/{job_key}`. Requiere
`FIRESTORE_PROJECT_ID` y `GOOGLE_APPLICATION_CREDENTIALS`.

El plan lo escribe el servicio remoto a partir de las preferencias que editas con `/preferencias` en
Telegram; no se configura en `config.yaml`. El buscador descarta, antes de guardarlas, las ofertas cuya
empresa o título coincide con las exclusiones del plan (`CollectionReport.excluded` cuenta cuántas) y
nunca las escribe en `job_postings`.

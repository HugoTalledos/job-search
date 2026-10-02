# Buscador local

Este componente consulta LinkedIn mediante la sesión MCP del Mac y crea ofertas nuevas en Cloud
Firestore. Solo depende de `job_contracts`, el SDK de Firestore, MCP, PyYAML y Pydantic. No importa
`job_agent` ni realiza evaluación, notificaciones o generación de CV.

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

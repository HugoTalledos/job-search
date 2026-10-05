# Registro de postulaciones desde Telegram — plan de implementación

**Objetivo:** Vincular un botón del PDF del CV con una oferta y registrar la marca «Apliqué» en Firestore una sola vez.

**Arquitectura:** La entrega del PDF agrega `callback_data` con el identificador estable de la oferta. El webhook de Telegram valida el callback y delega la escritura a un adaptador Firestore que guarda una copia de la oferta en `applied_proposals/{posting_id}` de forma transaccional.

**Tecnología:** Python, FastAPI, httpx, Cloud Firestore, pytest.

**Spec:** `docs/superpowers/specs/2026-10-05-applied-proposals-telegram-design.md`

## Pasos

- [x] Probar y agregar el botón «✅ Apliqué» al `sendDocument` del CV, con un identificador válido y breve.
- [x] Probar y construir el adaptador Firestore: oferta existente, duplicado, oferta ausente y concurrencia.
- [x] Probar y conectar el callback en Telegram: chat autorizado, dato válido, resultado nuevo, repetido y fallo.
- [x] Conectar el adaptador en `build_webhook_app`, actualizar la documentación y verificar la suite completa.

# Registro de postulaciones desde Telegram

## Objetivo

Permitir marcar como aplicada una oferta desde el PDF del CV ajustado y conservar las ofertas marcadas en una colección independiente para funciones futuras.

## Flujo

- Cada PDF enviado por `TelegramCvDelivery` lleva un botón «✅ Apliqué» con `callback_data` que contiene el identificador de la oferta.
- El webhook acepta el botón solo desde el chat privado configurado y del usuario configurado. Responde al callback tras guardar; confirma si la oferta acaba de marcarse o si ya estaba marcada.
- Un botón antiguo o una oferta ausente produce una respuesta clara sin crear datos. Una falla de Firestore informa que no se pudo registrar y permite reintentar.
- La generación y entrega del CV no marcan por sí mismas una postulación.

## Datos

`applied_proposals/{posting_id}` contiene `posting_ref`, una copia validada de `job` y `applied_at` con hora del servidor. El identificador por oferta y una escritura transaccional hacen idempotentes las pulsaciones repetidas, incluso si se generaron varios CV para la misma oferta.

## Límites y pruebas

El botón solo aparece en PDF enviados con la nueva versión. El bot no verifica una postulación externa; guarda la declaración del usuario. Probar el `reply_markup` real de Telegram, la autenticación y validación del callback, la persistencia idempotente, el caso de oferta ausente y la entrega/reintento del CV.

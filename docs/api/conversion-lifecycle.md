# Ciclo de vida de una conversión

```text
POST /v1/conversions            (archivo multipart)
POST /v1/conversions/from-url   (URL pública)
        |
        | 202 Accepted  { id, status: "queued", status_url }
        v
GET {status_url}  --- polling --->  queued -> running -> succeeded
        |                                             |
        |                                             +--> GET {result_url}   200  cuerpo Markdown
        |                                                                     (consume el resultado)
        |                                             |
        |                                             +--> fallo: failed  (error_code)
        |
        +--> resultado no descargado y TTL vencido: expired
```

## 1. Envío: `202 Accepted`

Ambos `POST` devuelven exactamente el mismo cuerpo, el modelo `ConversionAcceptedResponse`:

```json
{
  "id": "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
  "status": "queued",
  "status_url": "/v1/conversions/9f8e7d6c5b4a39281706f5e4d3c2b1a0"
}
```

- `status` siempre es `queued` en esta respuesta; el valor es constante en el contrato.
- `status_url` es un **path relativo**. Únelo con la base URL; no lo resuelvas contra otro origen.
- `Location` no se devuelve. El identificador viaja en el cuerpo y en `status_url`.
- Hay un `id` nuevo por solicitud. No hay idempotencia: reenviar el mismo archivo crea otra conversión y
  consume otra vez la tasa de solicitudes y, cuando termine bien, otra conversión de cuota.

`202` significa **aceptado y en cola**, no “convertido” ni “formato validado”. Lo que ocurre después:

- En la carga por archivo, el archivo ya está escrito en el almacenamiento temporal y contabilizado en
  bytes de entrada.
- En la variante por URL, todavía no se descargó nada: la descarga ocurre en el trabajador, respetando los
  límites de destino y de tamaño.
- La validación de formato (allowlist de extensiones, cabecera PDF, contenedores ZIP/Office/EPUB) se ejecuta
  en el trabajador, **después** del `202`. Un archivo con extensión no admitida o con contenido
  inconsistente produce `202` y luego un trabajo `failed` con `unsupported_format`,
  `file_type_mismatch`, `invalid_file_content` o `archive_limits_exceeded`.

Si el encolado falla, no hay `202`: la solicitud responde `429 queue_full` o `503 queue_unavailable`, y la
conversión queda registrada internamente como `failed`. Como el cliente no recibe un `id` en ese caso, no
hay nada que consultar; hay que reenviar más tarde. Ver [`errors.md`](./errors.md).

## 2. Seguimiento: `GET {status_url}`

`200 OK` con el modelo `ConversionStatusResponse`. **Siempre** trae las ocho claves, incluidas las que
valen `null`; ninguna es opcional:

```json
{
  "id": "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
  "status": "succeeded",
  "error_code": null,
  "created_at": "2026-11-04T17:03:21.114820Z",
  "started_at": "2026-11-04T17:03:21.902117Z",
  "completed_at": "2026-11-04T17:03:24.558041Z",
  "result_expires_at": "2026-11-05T17:03:24.558041Z",
  "result_url": "/v1/conversions/9f8e7d6c5b4a39281706f5e4d3c2b1a0/result"
}
```

| Campo | Significado |
|---|---|
| `id` | Identificador de la conversión, 32 hex. |
| `status` | `queued`, `running`, `succeeded`, `failed`, `expired` o `cancelled` (reservado, nunca emitido). |
| `error_code` | Código de fallo; `null` mientras no haya fallo. Catálogo en [`errors.md`](./errors.md). |
| `created_at` | Aceptación de la solicitud. |
| `started_at` | Inicio del procesamiento; `null` si nunca llegó a ejecutarse. |
| `completed_at` | Fin del intento (éxito o fallo); `null` mientras está en curso. |
| `result_expires_at` | Caducidad del resultado: `completed_at + 1440 min` cuando hay éxito; `null` en otros estados. |
| `result_url` | Path relativo del resultado, o `null`. |

`result_url` es no nulo **si y solo si** `status = succeeded` y el resultado no se ha descargado todavía. No
se comprueba la caducidad al construir el campo, así que puede aparecer no nulo en la ventana entre el
vencimiento y la limpieza (ver más abajo).

### Qué hacer con cada estado

| `status` | `result_url` | ¿Terminal? | Acción |
|---|---|---|---|
| `queued` | `null` | No | Seguir consultando. El trabajo espera turno en la cola. |
| `running` | `null` | No | Seguir consultando. Hay una conversión en curso como máximo. |
| `succeeded` | path | No | Descargar **una vez** el `result_url` (ver [`markdown-result.md`](./markdown-result.md)). |
| `succeeded` | `null` | Sí (para el cliente) | El resultado ya se descargó o se está descargando. No reintentar. |
| `failed` | `null` | Sí | Leer `error_code`. Un reintento exige un `POST` nuevo y suele consumir cuota. |
| `expired` | `null` | Sí | El resultado no se descargó dentro de las 24 h y se borró. Hay que volver a convertir. |
| `cancelled` | — | — | Valor reservado del enum: el servicio no lo emite y no existe endpoint de cancelación. |

### Frecuencia de polling

La API no devuelve `Retry-After` ni comunica un intervalo recomendado; tampoco hay webhooks ni SSE. El
ritmo lo decide el cliente. Sugerencia coherente con los tiempos del servidor:

- Empieza en 1 s, con un crecimiento suave hasta 10–15 s.
- El tiempo máximo de conversión es de 900 s: si a los ~15 min el estado sigue en `queued` o `running`, algo
  está mal en tu lado del cómputo (o el trabajo se interrumpió y ya se habrá marcado `failed`).
- No consultes en bucle cerrado: cada cuenta tiene un límite de solicitudes por minuto que **sí** cuenta
  los `POST`; el polling de estado y la descarga no consumen ese contador. No hay un límite de polling
  documentado, pero tampoco hay razón para consultar más de una vez por segundo.
- Cada consulta devuelve el estado actual; no hay historial de transiciones.

## 3. Descarga: `GET {result_url}`

`200` con el Markdown como cuerpo crudo. La descarga **consume** el resultado: tras completarse se borra y
`result_url` pasa a `null`. Detalles de forma y cabeceras en
[`markdown-result.md`](./markdown-result.md).

Resultados posibles del `GET` de descarga:

| HTTP | `detail` | Cuándo |
|---|---|---|
| `200` | — (cuerpo Markdown) | `succeeded`, sin descarga previa, sin reclamo en curso, `result_expires_at > ahora` y archivo presente. Consume. |
| `401` | `invalid_api_key` | Clave ausente, inválida, revocada, cuenta suspendida o sin verificar. No consume. |
| `404` | `conversion_not_found` | El `id` no existe **o pertenece a otra cuenta**. No consume. |
| `409` | `conversion_not_ready` | `status` distinto de `succeeded`: incluye `queued`, `running`, `failed` y `expired`. No consume. |
| `410` | `result_unavailable` | `succeeded` pero ya descargado, con reclamo en curso, con TTL vencido, o con el archivo ausente. No consume. |
| `405` | — (`Allow: GET`) | `HEAD`, `POST`, etc. No consume. |

Puntos que suelen sorprender:

- **`409` no siempre significa “espera”.** También es la respuesta para un trabajo `failed` o `expired`.
  Mira el `status` antes de decidir si reintentar.
- **`410` cubre cuatro causas**: consumido, reclamado por otra descarga en curso, TTL vencido o archivo
  ausente. No las distingue. En los cuatro casos el resultado es irrecuperable desde la API.
- **`410` tiene una ventana previa a `expired`.** La caducidad se aplica de forma perezosa: el trabajador
  limpia resultados vencidos como máximo una vez por minuto. En ese intervalo, `status`
  sigue siendo `succeeded` y `result_url` sigue no nulo, pero el `GET` ya devuelve `410`. Si recibes `410`
  con estado `succeeded`, no es una inconsistencia: el resultado ya no está disponible. Deja de consultar.
- **`404` protege la propiedad.** Una cuenta no puede distinguir “no existe” de “es de otra cuenta”: ambas
  responden `404 conversion_not_found`. No intentes enumerar identificadores.

## 4. Un solo uso y reintentos

La semántica exacta, ya descrita desde el punto de vista del cuerpo en
[`markdown-result.md`](./markdown-result.md):

1. El `GET` reclama la conversión (`download_started_at`) antes de abrir el archivo.
2. Al terminar de escribir el cuerpo, marca `downloaded_at` y borra el archivo.
3. Si la escritura no termina, libera el reclamo inmediatamente: el resultado queda disponible otra vez.
4. Si el proceso muere con un reclamo tomado, el reclamo se libera tras **1 hora** (barrido periódico).
5. Si el archivo no existe cuando se reclama, la conversión pasa a `expired` con `error_code =
   result_missing` y el cliente recibe `410`.

Reglas de reintento:

- Un reintento de descarga solo es seguro si la transferencia se cortó **antes** de que la API terminara de
  escribir el cuerpo. Un `410` nunca se recupera reintentando.
- Prohibido el reintento especulativo (bibliotecas HTTP que repiten `GET` automáticamente, proxies,
  antivirus, *crawlers*). Puede consumir el resultado dos veces o dejar la segunda petición en `410`.
- La API no tiene endpoint de acuse de recibo (`ack`) ni confirmación de guardado: si necesitas garantía de
  entrega en tu aplicación, guarda el archivo y verifica su tamaño/hash en tu lado antes de dar por buena
  la operación.

## 5. Cuándo desaparece un resultado

- **Éxito**: `result_expires_at = completed_at + 1440 min` (24 h). Si nadie lo descarga, el barrido lo
  borra y el estado pasa a `expired` con `error_code = result_expired`.
- **Descarga completa**: se borra inmediatamente; el estado sigue siendo `succeeded` con `result_url=null`.
- **Fallo**: no hay resultado; el archivo de entrada se elimina al terminar el intento.
- **Entrada**: se elimina al terminar el intento, con éxito o sin él.

El almacenamiento temporal tiene además un tope agregado de 2 GiB para resultados pendientes de descarga.
Si un resultado nuevo excede ese tope, el trabajo falla con `temporary_storage_limit` (no se sirve a medias
ni se recorta).

## 6. Interrupciones y reinicios

- Si el trabajador se reinicia, los trabajos `queued`/`running` que ya no están en la cola se marcan
  `failed` con `error_code = worker_interrupted`. El cliente lo ve como un fallo terminal y debe reenviar.
- Si el cliente Redis no está disponible al arrancar la API, las solicitudes de conversión responden
  `503 queue_unavailable`. Si Redis falla en caliente, la comprobación de tasa puede terminar en `500`; en
  cualquiera de los dos casos no hay encolado y la solicitud debe repetirse más tarde.
- Un trabajo `failed` nunca se reintenta en el servidor. El reintento es siempre una solicitud nueva, con
  `id` nuevo.

## 7. Contabilidad de un intento

Aunque el contrato de consumo se detalla en [`limits-and-formats.md`](./limits-and-formats.md), el
integrador necesita saber qué cuesta un fallo:

- Un trabajo `failed` **no** consume conversiones ni páginas del plan.
- Un trabajo `failed` **sí** consume los bytes de entrada efectivamente procesados y la solicitud
  autenticada del minuto correspondiente.
- Un trabajo que se descarga o expira cuenta como conversión y páginas (el estado `expired` cuenta como
  conversión exitosa a efectos de cuota; el resultado solo no está disponible).
- OCR consume unidades solo por las llamadas realmente enviadas al proveedor externo, incluso si el intento
  termina fallando.

## 8. Lo que no existe en este ciclo

- Sin webhooks ni callbacks: el servidor nunca notifica.
- Sin SSE ni *long polling*: `GET` de estado devuelve inmediatamente.
- Sin cancelación: no hay `DELETE /v1/conversions/{id}` ni `POST .../cancel`; `cancelled` es un valor
  reservado del enum.
- Sin listado de conversiones en la superficie de integración: solo puedes consultar un `id` que ya tienes.
- Sin prioridad, *fast lane* ni estimación de tiempo de espera.
- Sin `Retry-After` ni `X-RateLimit-*` en las respuestas `429`.

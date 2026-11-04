# Errores

Hay **dos** formatos de cuerpo de error en la superficie de integración, y no están unificados. Tu cliente
debe tolerar ambos. Además, el gateway puede devolver HTML o texto plano antes de que la petición llegue a
la API.

Ningún error incluye trazas, nombres de archivo, URLs, contenido del documento ni claves. Todos los
mensajes son códigos estables en inglés.

## Sobre 1 — `detail` como cadena

Es la forma de todos los errores controlados: autenticación, cuotas, límites y estado del resultado.

```json
{ "detail": "conversion_not_found" }
```

`detail` es un **código estable**, no una frase para el usuario final. Traduce tú; no compares contra
textos libres ni contra el código HTTP solo.

## Sobre 2 — `detail` como array de validación

Lo produce la validación de esquema del framework (por ejemplo, un `url` demasiado corto o un tipo
incorrecto) **antes** de ejecutar el manejador. Es el formato estándar de Pydantic:

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "url"],
      "msg": "String should have at least 8 characters",
      "input": "nope",
      "ctx": { "min_length": 8 }
    }
  ]
}
```

- `type` y `ctx` los define el framework; `msg` está en inglés y puede cambiar entre versiones. No los uses
  para lógica de negocio.
- `input` reproduce el valor recibido. **No registres este cuerpo en logs** si el campo pudiera contener
  datos sensibles: en la práctica solo aparece para `url` y `ocr`.
- Este sobre **solo** aparece en `POST /v1/conversions/from-url` (validación del cuerpo JSON). Los `422` de
  la carga multipart (`file_required`, `unexpected_form_field`, `invalid_ocr_flag`) y los de URL
  (`invalid_url`, `port_not_allowed`, `destination_not_public`) usan el sobre 1.

Ejemplos en [`examples/errors/`](./examples/errors).

## Superficie y errores del gateway

Antes de la API hay un gateway HTTP. Los errores que genera él **no** son JSON:

| HTTP | Origen | Cuerpo | ¿`X-Request-ID`? |
|---|---|---|---|
| `413` | Gateway, cuando el cuerpo de la petición supera 101 MiB | HTML de error del gateway | No |
| `502` | Gateway, si la API no responde | HTML | No |
| `504` | Gateway, si la API no responde en ~930 s | HTML | No |
| `500` | API, excepción no controlada | texto plano `Internal Server Error` | No (la cabecera se añade solo si la respuesta se genera normalmente) |

El `413` de la API (`file_too_large`) sí es JSON y llega con `X-Request-ID`; se produce cuando el cuerpo
cabe en el gateway pero excede el límite de la cuenta o el global. Es decir: hay **dos** `413` distintos,
con cuerpos distintos.

Regla: decide por código HTTP y, si el `Content-Type` no es JSON, no intentes parsear el cuerpo.

## Cabecera de correlación

Toda respuesta controlada de la API incluye `X-Request-ID`. Si envías uno válido (1–64 caracteres de
`[A-Za-z0-9._-]`), se respeta y se devuelve; si no, se genera. Las `500` no controladas y los errores del
gateway pueden no traerla. Úsalo al pedir soporte junto con el `id` de la conversación. No hay endpoint para
consultar trazas por `X-Request-ID`.

No existe `Retry-After` ni familia `X-RateLimit-*`: la API no comunica cuándo reintentar ni cuánta cuota
queda.

## Códigos síncronos

### `POST /v1/conversions` (carga multipart)

El orden de comprobación es el de la tabla; el primer fallo gana.

| # | HTTP | `detail` | Significado | ¿Reintentar? |
|---|---|---|---|---|
| 1 | `401` | `invalid_api_key` | Clave ausente, inválida, revocada, o cuenta suspendida/sin verificar. | No: corrige la credencial. |
| 2 | `403` | `plan_not_configured` | La cuenta no tiene plan activo asignado (los administradores están exentos). | No: contacta con administración. |
| 3 | `429` | `rate_limit_exceeded` | Se superó el máximo de solicitudes por minuto de la cuenta. | Sí, esperando al siguiente minuto (no hay `Retry-After`). |
| 4 | `429` | `monthly_conversion_quota_exceeded` | Cuota mensual de conversiones agotada. | No hasta el nuevo periodo o cambio de plan. |
| 5 | `429` | `monthly_page_quota_exceeded` | Cuota mensual de páginas/unidades agotada. | No hasta el nuevo periodo o cambio de plan. |
| 6 | `429` | `monthly_input_bytes_quota_exceeded` | Cuota mensual de bytes de entrada agotada. | No hasta el nuevo periodo o cambio de plan. |
| 7 | `411` | `content_length_required` | La petición no trae `Content-Length` (por ejemplo, `Transfer-Encoding: chunked`). | No: envía el tamaño. |
| 8 | `400` | `invalid_content_length` | `Content-Length` no numérico. | No: corrige el cliente. |
| 9 | `413` | `file_too_large` | El `Content-Length` declarado excede el límite por archivo (con 1 MiB de margen para el sobre multipart). | No: reduce el archivo o el plan. |
| 10 | `415` | `multipart_form_required` | `Content-Type` no es `multipart/form-data`. | No. |
| 11 | `400` | `invalid_multipart_form` | Cuerpo multipart mal formado, o más de un archivo. | No. |
| 12 | `422` | `file_required` | No hay parte `file` utilizable. | No. |
| 13 | `422` | `unexpected_form_field` | Hay campos además de `file` y `ocr`. | No. |
| 14 | `422` | `invalid_ocr_flag` | `ocr` no es `true`/`false`/`1`/`0`. | No. |
| 15 | `403` | `ocr_not_in_plan` | Se pidió OCR y el plan no lo habilita. | No. |
| 16 | `503` | `ocr_provider_not_configured` | Se pidió OCR y no hay proveedor configurado. | No. |
| 17 | `429` | `monthly_ocr_quota_exceeded` | Cuota mensual de unidades OCR ya consumida. | No hasta el nuevo periodo. |
| 18 | `413` | `file_too_large` | Los bytes reales escritos superan el límite por archivo (el declarado cabía). | No. |
| 19 | `429` | `monthly_input_bytes_quota_exceeded` | Tras escribir el archivo, la cuenta superó el límite mensual de bytes. | No. |
| 20 | `429` | `queue_full` | La cola de pendientes está llena (5 por defecto). | Sí, con *backoff*. |
| 21 | `503` | `queue_unavailable` | Redis no está disponible. | Sí, con *backoff*. |

En los casos 19, 20 y 21 la API ya había creado internamente el registro de conversión y lo deja en
`failed`; como la respuesta no incluye `id`, no hay nada que consultar.

### `POST /v1/conversions/from-url`

| # | HTTP | `detail` | Significado | ¿Reintentar? |
|---|---|---|---|---|
| 0 | `422` | *(array de validación)* | Falta `url`, mide menos de 8 o más de 4096 caracteres, o `ocr` no es booleano. | No. |
| 1 | `401` | `invalid_api_key` | Igual que en la carga por archivo. | No. |
| 2 | `403` | `plan_not_configured` | Igual que en la carga por archivo. | No. |
| 3 | `429` | `rate_limit_exceeded` | Igual que en la carga por archivo. | Sí, tras esperar. |
| 4 | `403` | `ocr_not_in_plan` | OCR pedido sin habilitación de plan. | No. |
| 5 | `503` | `ocr_provider_not_configured` | OCR pedido sin proveedor configurado. | No. |
| 6 | `429` | `monthly_ocr_quota_exceeded` | Cuota OCR agotada. | No hasta el nuevo periodo. |
| 7 | `422` | `invalid_url` | Esquema distinto de `http`/`https`, sin host, con usuario/contraseña o con `#fragmento`. | No. |
| 8 | `422` | `port_not_allowed` | Puerto explícito distinto del estándar (`80` para `http`, `443` para `https`). | No. |
| 9 | `422` | `destination_not_public` | La URL es una IP literal no global (privada, loopback, link-local, metadatos). | No. |
| 10 | `429` | `monthly_conversion_quota_exceeded` | Igual que en la carga por archivo. | No. |
| 11 | `429` | `monthly_page_quota_exceeded` | Igual que en la carga por archivo. | No. |
| 12 | `429` | `monthly_input_bytes_quota_exceeded` | Igual que en la carga por archivo. | No. |
| 13 | `429` | `queue_full` | Cola llena. | Sí, con *backoff*. |
| 14 | `503` | `queue_unavailable` | Redis no disponible. | Sí, con *backoff*. |

Todo lo que depende del contenido remoto —resolución DNS, TLS, redirecciones, tamaño— ocurre **después**
del `202` y aparece como `error_code` del trabajo, no aquí.

### `GET /v1/conversions/{id}` y `GET .../result`

| HTTP | `detail` | Significado | ¿Reintentar? |
|---|---|---|---|
| `401` | `invalid_api_key` | Credencial inválida. | No. |
| `404` | `conversion_not_found` | El `id` no existe o pertenece a otra cuenta. | No. |
| `409` | `conversion_not_ready` | En el resultado: `status` distinto de `succeeded` (`queued`, `running`, `failed`, `expired`). | Solo si el estado es no terminal. |
| `410` | `result_unavailable` | `succeeded` pero consumido, reclamado, vencido o con archivo ausente. | No. |
| `405` | — (`Allow: GET`) | Método no permitido, incluido `HEAD`. | No. |

El `422` que el snapshot OpenAPI declara para estos dos `GET` no es alcanzable: el único parámetro es un
`conversion_id` de tipo cadena y no hay query string ni cuerpo.

### Salud (`/health/*`)

No requieren autenticación y no forman parte del flujo de conversión, pero sí del contrato de operación.

| Ruta | `200` | `503` |
|---|---|---|
| `GET /health/live` | `{"status":"ok"}` | — (si el proceso responde, está vivo) |
| `GET /health/ready` | `{"status":"ready"}` | `dependencies_unavailable` (PostgreSQL o Redis) o `worker_unavailable` (sin latido del trabajador) |

El `503` usa el sobre 1. `ready` comprueba base de datos, Redis y el latido del trabajador; un `200` aquí
significa que el servicio puede aceptar conversiones, no que el trabajador esté libre.


## Códigos de trabajo (`error_code`)

Aparecen en `GET /v1/conversions/{id}` cuando `status = failed` (o `expired`, en los dos últimos casos).
“Reintentar” significa siempre **un `POST` nuevo**, con un `id` nuevo: el servidor no reintenta trabajos y
no hay endpoint de reintento.

### Entrada y formato

| `error_code` | Significado | ¿Reintentar? |
|---|---|---|
| `unsupported_format` | La extensión no está en la allowlist, o el conversor no dispone de ese formato. | No, salvo que cambies el archivo o su extensión real. |
| `file_type_mismatch` | El contenido no corresponde a la extensión declarada (PDF sin cabecera `%PDF-`, ZIP genérico con extensión `.docx`, texto que no es texto…). | No: corrige el archivo. |
| `invalid_file_content` | Contenido no clasificable, contenedor corrupto o ilegible. | No. |
| `archive_limits_exceeded` | ZIP/Office/EPUB supera 5.000 entradas o 128 MiB expandidos declarados. | No: reduce el contenedor. |
| `file_too_large` | El archivo (o la descarga) excede el límite por archivo de la cuenta. | No: reduce el tamaño o cambia de plan. |
| `input_missing` | La entrada desapareció del almacenamiento antes de procesarse. Fallo de infraestructura. | Sí, una vez. |

### Plan y cuotas

| `error_code` | Significado | ¿Reintentar? |
|---|---|---|
| `plan_not_configured` | La cuenta perdió el plan activo entre la aceptación y la ejecución. | No: contacta con administración. |
| `monthly_conversion_quota_exceeded` | Cuota de conversiones agotada al ejecutar. | No hasta el nuevo periodo. |
| `monthly_page_quota_exceeded` | Cuota de páginas/unidades agotada (incluye el caso de que el documento real consuma más de lo previsto). | No hasta el nuevo periodo. |
| `monthly_input_bytes_quota_exceeded` | La descarga o el archivo habrían superado la cuota mensual de bytes. | No hasta el nuevo periodo. |
| `monthly_ocr_quota_exceeded` | Se agotaron las unidades OCR durante la conversión. | No hasta el nuevo periodo. |
| `ocr_not_in_plan` | OCR pedido y no habilitado en el plan activo al ejecutar. | No. |

### URL remota

| `error_code` | Significado | ¿Reintentar? |
|---|---|---|
| `invalid_url` | URL inválida al validarla de nuevo (por ejemplo, tras una redirección). | No. |
| `port_not_allowed` | Redirección a un puerto no estándar. | No. |
| `destination_not_public` | Redirección a una IP literal no global. | No. |
| `https_downgrade` | Una URL `https` redirige a `http`. | No. |
| `too_many_redirects` | Más de 5 redirecciones. | No: usa la URL final. |
| `invalid_redirect` | Redirección sin cabecera `Location`. | No. |
| `remote_http_error` | El servidor remoto respondió un código distinto de `2xx`. | Depende del remoto; no reintentes en bucle. |
| `remote_content_encoding_not_supported` | El remoto envía `Content-Encoding` distinto de `identity`. | No: sirve el archivo sin comprimir. |
| `remote_fetch_failed` | Fallo de DNS, conexión, TLS, tiempo de espera (60 s total) o destino resuelto a una IP no pública. | Sí, con *backoff*, si el origen es inestable. |

> Matiz verificado: si el **hostname** resuelve a direcciones no públicas, el bloqueo lo aplica el
> resolutor y el error llega como `remote_fetch_failed` (la librería HTTP envuelve el fallo del
> resolutor). `destination_not_public` aparece cuando el destino ya es una IP literal no global, o cuando
> una redirección apunta a una. Ambos casos están bloqueados; cambia el código reportado.

### Conversión y recursos

| `error_code` | Significado | ¿Reintentar? |
|---|---|---|
| `conversion_failed` | Fallo no clasificado del conversor. Es el código por defecto. | Sí, una vez; si persiste, reporta con el `id`. |
| `conversion_timeout` | Se superaron 900 s de conversión. | No con el mismo documento; reduce tamaño/complejidad. |
| `resource_limit_exceeded` | El subproceso superó 512 MiB de RSS y fue terminado. | No con el mismo documento en el plan actual. |
| `result_too_large` | El Markdown generado supera 50 MiB. | No: el resultado no se sirve recortado. |
| `temporary_storage_limit` | El almacenamiento temporal agregado de resultados pendientes (2 GiB) está lleno. | Sí, más tarde, cuando otros resultados se descarguen o expiren. |

### Infraestructura y estado

| `error_code` | Estado | Significado | ¿Reintentar? |
|---|---|---|---|
| `worker_interrupted` | `failed` | El trabajador se reinició y el trabajo no pudo retomarse. | Sí. |
| `queue_full` | `failed` (normalmente sin `id` para el cliente) | La cola rechazó el trabajo. | Sí, con *backoff*. |
| `queue_unavailable` | `failed` (normalmente sin `id` para el cliente) | Redis no disponible al encolar. | Sí, con *backoff*. |
| `result_expired` | `expired` | El resultado no se descargó en 24 h y se borró. | Sí: nueva conversión. |
| `result_missing` | `expired` | El resultado se había registrado pero el archivo ya no estaba al descargarlo. | Sí: nueva conversión. |

## Política de reintento recomendada

| Situación | Acción |
|---|---|
| `401`, `403`, `411`, `413`, `415`, `422`, `404`, `409` con estado terminal, `410` | No reintentar. Corregir la petición o la credencial, o crear una conversión nueva. |
| `429 rate_limit_exceeded` | Esperar al siguiente minuto, luego una vez. Sin `Retry-After`, usa 60 s. |
| `429 queue_full`, `503 queue_unavailable`, `502`, `504` | Reintentar con *backoff* exponencial y jitter (por ejemplo 2 s, 8 s, 30 s). |
| `500` | Reintentar una vez con *backoff*; si persiste, reporta el `id` (y el `X-Request-ID` si vino). |
| `error_code` de entrada (`unsupported_format`, `file_type_mismatch`, `file_too_large`…) | No reintentar el mismo documento. |
| `conversion_failed`, `input_missing`, `worker_interrupted` | Reintentar una vez con un `POST` nuevo. |
| `remote_fetch_failed`, `remote_http_error`, `temporary_storage_limit` | Reintentar con *backoff*; verificar el origen. |

Cada reintento es una conversión nueva: cuenta para el límite por minuto y para los bytes procesados, y
consume cuota de conversión si termina bien. No hay deduplicación.

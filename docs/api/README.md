# Contrato de integración — API de conversiones MarkItDown

Paquete de integración para consumidores de la API de conversiones. Todo lo que hay aquí se deriva del
código de `packages/markitdown-api/` en el commit `a026ffa` ("Documenta y tipa la API de conversiones"),
que está en la rama `HP240RG10-dev`. Si el `/openapi.json` del servidor aún no coincide con este snapshot,
significa que ese commit todavía no se ha desplegado allí: este paquete describe el contrato del código, no
el estado del último despliegue.

- Versión de la API (`info.version`): `0.1.0`.
- Especificación: OpenAPI `3.1.0`.
- Snapshot filtrado: [`openapi.consumer.json`](./openapi.consumer.json).

## Base URL

```text
https://mdservice.innersoft.com.mx
```

Un único origen sirve el portal web y la API. Las rutas de la API empiezan por `/v1/`; las de salud, por
`/health/`. No hay hosts separados para producción y pruebas: no existe entorno de sandbox público
(ver [`sandbox.md`](./sandbox.md)).

Todas las rutas de este contrato son relativas a esa base. Las rutas que devuelve la API
(`status_url`, `result_url`) son **relativas** (solo path, sin host): únelas con la base, no las uses
tal cual.

## Autenticación

Cabecera obligatoria en las cuatro operaciones de conversión:

```http
X-API-Key: <tu-clave>
```

- La clave va **solo** en la cabecera. No se acepta en query string, ni en el cuerpo, ni como cookie.
- Las claves se almacenan como HMAC-SHA256 con un *pepper* del servidor: no se pueden recuperar después
  de crearlas. La API solo muestra la clave completa en la respuesta que la crea.
- La clave no tiene scopes, permisos por endpoint ni caducidad propia. Autoriza exactamente el conjunto de
  operaciones de conversión de la cuenta propietaria. Se revoca explícitamente.
- Una cuenta admite como máximo 20 claves activas.
- Una clave pertenece a una cuenta. No hay claves compartidas entre cuentas ni claves de organización.
- No se aceptan tokens `Authorization: Bearer` en los endpoints de conversión. Las sesiones web (cookie +
  CSRF) y los tokens Bearer existen, pero son superficie del portal, no de este contrato.

Respuestas de autenticación fallida: `401` con cuerpo `{"detail":"invalid_api_key"}`. Se devuelve el mismo
código tanto si la clave falta, como si es inválida, está revocada, o la cuenta está suspendida o sin
verificar. No hay información adicional que distinga esos casos.

### Cómo se obtiene una clave

1. Un administrador invita la cuenta desde el portal (`/users`); la invitación llega por correo y caduca
   en 7 días.
2. La persona invitada establece su contraseña en `/accept-invitation`.
3. Con la sesión web iniciada, se crea una clave en `/profile` (`POST /v1/api-keys`). La respuesta incluye
   la clave completa una sola vez; después solo se conserva el prefijo.

El registro público está desactivado (`PUBLIC_SIGNUP_ENABLED=false`). Sin invitación no hay cuenta.

## Superficie de integración frente a superficie del portal

| Superficie | Rutas | Autenticación | ¿Contrato público? |
|---|---|---|---|
| **Integración (consumidores)** | `POST /v1/conversions`, `POST /v1/conversions/from-url`, `GET /v1/conversions/{id}`, `GET /v1/conversions/{id}/result` | `X-API-Key` | Sí. Documentado aquí. |
| Catálogo informativo | `GET /v1/plans` | ninguna | Sí, solo lectura e informativo. |
| Operación | `GET /health/live`, `GET /health/ready` | ninguna | Sí, para monitorización. |
| Portal web | `/v1/web-auth/*`, `/v1/account/*`, `/v1/api-keys`, `/v1/usage` | cookie `HttpOnly` + `X-CSRF-Token`, o Bearer | No. Uso interno del portal; puede cambiar sin aviso en este contrato. |
| Administración | `/v1/admin/*` | sesión con `role=admin` | No. Nunca forma parte del contrato de integración. |

`openapi.consumer.json` contiene únicamente la primera, segunda y tercera fila. Las rutas `/v1/admin/*`,
`/v1/web-auth/*`, `/v1/account/*`, `/v1/api-keys`, `/v1/usage` y las páginas HTML de correo
(`/verify`, `/reset-password`) están excluidas del snapshot a propósito.

## Quickstart

Sustituye los valores de entorno por los tuyos. No escribas claves en el repositorio, en scripts
compartidos ni en el historial de shell.

```bash
export MARKITDOWN_BASE_URL="https://mdservice.innersoft.com.mx"
export MARKITDOWN_API_KEY="<clave-de-tu-cuenta>"
```

1. Enviar un archivo y quedarse con el `id`:

```bash
curl -sS -X POST "$MARKITDOWN_BASE_URL/v1/conversions" \
  -H "X-API-Key: $MARKITDOWN_API_KEY" \
  -F "file=@informe.pdf" \
  -o accepted.json

# accepted.json -> {"id":"...","status":"queued","status_url":"/v1/conversions/..."}
```

2. Consultar el estado hasta que sea terminal (`succeeded`, `failed` o `expired`):

```bash
CONVERSION_ID="<id-de-accepted.json>"

curl -sS "$MARKITDOWN_BASE_URL/v1/conversions/$CONVERSION_ID" \
  -H "X-API-Key: $MARKITDOWN_API_KEY"
```

3. Descargar el resultado **una sola vez** a un archivo, sin `HEAD` y sin preview:

```bash
curl -sS -D result.headers \
  "$MARKITDOWN_BASE_URL/v1/conversions/$CONVERSION_ID/result" \
  -H "X-API-Key: $MARKITDOWN_API_KEY" \
  -o converted.md

grep -i '^content-type' result.headers   # text/markdown; charset=utf-8
```

La descarga consume el resultado: si el comando se repite, la API responde `410`. Ver
[`markdown-result.md`](./markdown-result.md) antes de integrar la descarga.

Alternativa por URL pública (mismo `202`, misma cola, mismo límite de bytes):

```bash
curl -sS -X POST "$MARKITDOWN_BASE_URL/v1/conversions/from-url" \
  -H "X-API-Key: $MARKITDOWN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/informe.pdf","ocr":false}'
```

## Documentos

| Documento | Responde |
|---|---|
| [`markdown-result.md`](./markdown-result.md) | Qué devuelve exactamente `GET .../result`: cuerpo Markdown crudo, cabeceras, límites de forma, descarga de un solo uso. |
| [`conversion-lifecycle.md`](./conversion-lifecycle.md) | `202` → polling de `status_url` → descarga; estados, `result_url`, `404`/`409`/`410`, reintentos. |
| [`errors.md`](./errors.md) | Los dos sobres de error reales y el catálogo de códigos síncronos y de trabajo, con “¿reintentar?”. |
| [`limits-and-formats.md`](./limits-and-formats.md) | Límites de entrada, salida, cola, tiempos y cuotas; allowlist real de extensiones. |
| [`sandbox.md`](./sandbox.md) | Acceso bajo solicitud; no hay cuenta demo ni clave compartida. |
| [`examples/`](./examples) | Respuestas reales de ejemplo, sanitizadas. |
| [`openapi.consumer.json`](./openapi.consumer.json) | Snapshot OpenAPI 3.1 filtrado a la superficie de integración. |

## Convenciones de los datos

- **Identificadores**: 32 caracteres hexadecimales (`[0-9a-f]{32}`). Trátalos como cadenas opacas: no
  inferas significado, orden ni cuenta a partir de ellos.
- **Fechas**: ISO-8601 en UTC. En producción (PostgreSQL) llevan sufijo `Z` y microsegundos, por ejemplo
  `2026-11-04T17:03:21.114820Z`. Parsea con un parser ISO-8601 tolerante: en despliegues de desarrollo con
  SQLite el mismo campo puede llegar sin offset (`2026-11-04T17:03:21.114820`), siempre en UTC.
- **URLs**: `status_url` y `result_url` son paths relativos.
- **Tamaños**: `input_bytes` es la cuenta exacta de bytes; si necesitas MB decimales, divide entre
  `1_000_000` (no entre `1024²`).
- **Cabecera de correlación**: las respuestas de la API (incluidos los `4xx` controlados) incluyen
  `X-Request-ID`. Si envías `X-Request-ID` con 1–64 caracteres de `[A-Za-z0-9._-]`, se respeta; en caso
  contrario se genera uno. Las `500` no controladas y los errores propios del gateway pueden no traerla.
  Úsala para soporte; no sustituye al `id` de la conversión.

## Ejemplos

Todos los ejemplos son sintéticos: identificadores inventados, sin claves, sin correos, sin dominios
internos, sin nombres de clientes y sin contenido de documentos reales. Los cuerpos JSON coinciden con
los modelos `ConversionAcceptedResponse` y `ConversionStatusResponse` y con los literales `detail` y
`error_code` del código.

| Archivo | Qué ilustra |
|---|---|
| [`examples/accepted-202.json`](./examples/accepted-202.json) | Respuesta `202` de ambos `POST`. |
| [`examples/status-succeeded.json`](./examples/status-succeeded.json) | Estado terminal con resultado disponible. |
| [`examples/status-failed.json`](./examples/status-failed.json) | Estado terminal sin resultado. |
| [`examples/status-expired.json`](./examples/status-expired.json) | Resultado caducado antes de descargarse. |
| [`examples/result.md`](./examples/result.md) | Cuerpo completo de una descarga `200`. |
| [`examples/errors/`](./examples/errors) | Sobres de error reales. |

Los tres ejemplos de estado tienen exactamente las ocho claves del modelo, incluidas las que valen
`null`. No hay claves opcionales ni campos extra.

## Qué no está soportado

Se documenta explícitamente porque un integrador puede asumirlo por costumbre:

- **No** hay webhooks ni notificaciones *push*: el avance se obtiene por polling.
- **No** hay SSE ni streaming de progreso.
- **No** hay idempotencia: cada `POST` crea una conversión nueva, aunque el archivo o la URL sean
  idénticos. No existe cabecera `Idempotency-Key`.
- **No** hay cancelación: `cancelled` existe en el enum del modelo de estado pero el servicio no lo emite
  nunca (valor reservado).
- **No** hay `HEAD` en la descarga (`405`, `Allow: GET`). El `GET` consume el resultado, así que `HEAD`,
  *prefetch* y *previews* están prohibidos por diseño.
- **No** hay `Range`, `ETag`, `If-None-Match` ni `Content-Encoding` en la descarga.
- **No** hay CORS: no se emiten cabeceras `Access-Control-*` y no se atiende `OPTIONS` como preflight.
  Consumir la API desde JavaScript de navegador en otro origen no está soportado.
- **No** hay `Retry-After` ni cabeceras `X-RateLimit-*` en los `429`.
- **No** hay lotes: un archivo por solicitud, siempre.
- **No** hay endpoint para listar conversiones ni para consultar el resultado en JSON. El histórico de
  consumo (`GET /v1/usage`) es superficie del portal, no de este contrato.
- **No** hay entorno de pruebas separado ni respuestas pregrabadas.

## Cómo se generó `openapi.consumer.json`

El snapshot se genera una vez desde el código y se versiona. No se regenera en CI. Contiene solo
`/v1/conversions*`, `/v1/plans` y `/health/*`, más los `components` alcanzables desde esas operaciones.

Desde la raíz del repositorio, con el paquete instalable (`python -m pip install -e "packages/markitdown-api"`):

```bash
PYTHONPATH=packages/markitdown-api/src python - <<'PY'
import json
from markitdown_api.main import app

COMMIT = "a026ffa"                     # actualízalo al regenerar
KEEP_EXACT = {"/v1/plans"}
KEEP_PREFIXES = ("/v1/conversions", "/health/")

spec = app.openapi()
paths = {
    path: item
    for path, item in spec["paths"].items()
    if path in KEEP_EXACT or path.startswith(KEEP_PREFIXES)
}

def collect(node, found):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            collect(value, found)
    elif isinstance(node, list):
        for value in node:
            collect(value, found)
    return found

schemas = spec["components"]["schemas"]
used = set()
for item in paths.values():
    collect(item, used)
pending = list(used)
while pending:                          # cierre transitivo de $ref
    name = pending.pop()
    before = set(used)
    collect(schemas[name], used)
    pending.extend(used - before)

security = {}
for item in paths.values():
    for operation in item.values():
        for requirement in operation.get("security") or []:
            for name in requirement:
                security[name] = spec["components"]["securitySchemes"][name]

snapshot = {
    "openapi": spec["openapi"],
    "info": {
        "title": spec["info"]["title"],
        "version": spec["info"]["version"],
        "x-snapshot": {"commit": COMMIT, "generated-by": "docs/api/README.md"},
    },
    "paths": paths,
    "components": {
        "schemas": {name: schemas[name] for name in sorted(used)},
        "securitySchemes": security,
    },
}

with open("docs/api/openapi.consumer.json", "w", encoding="utf-8", newline="\n") as handle:
    handle.write(json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
PY
```

En Windows (PowerShell) el heredoc no existe: guarda el bloque en un archivo `.py` fuera del repositorio y
ejecútalo con `python archivo.py` desde la raíz, con `PYTHONPATH` apuntando a `packages/markitdown-api/src`.

El snapshot omite `servers` a propósito: la base URL vive en este README y no se duplica en un archivo que
se regenera desde código.

### Diferencias conocidas entre el snapshot y el comportamiento real

El snapshot es fiel al código, pero el código no describe todos los resultados posibles:

- Los `POST` declaran solo `202`. Los `4xx`/`5xx` reales están documentados en [`errors.md`](./errors.md).
- `GET /v1/conversions/{id}` y `GET .../result` declaran un `422` que en la práctica no se alcanza: el
  único parámetro es un `conversion_id` de tipo cadena y no hay query string.
- `GET /v1/plans` y `/health/*` aparecen con `schema: {}` porque sus manejadores no declaran
  `response_model`.
- El ejemplo de `ConversionStatusResponse` incrustado en el esquema sale sin `error_code`, porque FastAPI
  serializa los ejemplos del esquema excluyendo valores `null`. La respuesta real **siempre** trae las
  ocho claves; usa [`examples/status-succeeded.json`](./examples/status-succeeded.json) como referencia.
- `GET .../result` se declara como `text/markdown`, que es correcto, pero el `Content-Type` real incluye
  el parámetro `; charset=utf-8`.

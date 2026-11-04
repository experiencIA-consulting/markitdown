# El resultado: Markdown crudo en el cuerpo HTTP

Esta es la pieza que más se malinterpreta: `GET /v1/conversions/{id}/result` **no** devuelve JSON. No hay
envoltorio, ni campo `markdown`, ni `content`, ni base64, ni metadatos. El cuerpo de la respuesta **es** el
documento convertido, byte a byte.

## Qué devuelve exactamente

Respuesta correcta: `200 OK`.

```http
HTTP/1.1 200 OK
content-type: text/markdown; charset=utf-8
content-disposition: attachment; filename="converted.md"
x-request-id: 3b0d9f4c1a2e4f6b8c0d1e2f3a4b5c6d
x-content-type-options: nosniff
referrer-policy: no-referrer
x-frame-options: DENY
```

La respuesta se envía en *streaming*. Sobre HTTP/1.1 eso implica transferencia por bloques; sobre HTTP/2 no
hay cabecera de longitud y el cuerpo llega como un flujo de tramas. En ningún caso el servidor declara el
tamaño por adelantado.

| Aspecto | Valor |
|---|---|
| `Content-Type` | `text/markdown; charset=utf-8` (con el parámetro `charset`, tal cual) |
| `Content-Disposition` | `attachment; filename="converted.md"` — nombre fijo, no el nombre del archivo original |
| Cuerpo | Contenido Markdown codificado en UTF-8 **sin BOM**, tal como se almacenó |
| `Content-Length` | Ausente. La respuesta se envía en *streaming* por bloques (chunked) |
| `Content-Encoding` | Ausente. No hay gzip, brotli ni deflate |
| `ETag` / `Last-Modified` | Ausentes |
| `Range` / `Accept-Ranges` | No soportados: enviar `Range` no produce `206`, se ignora y se devuelve el cuerpo completo |
| Assets auxiliares | Ninguno. La API no sirve imágenes, adjuntos, CSS ni archivos aparte |
| Firmas / integridad | No hay cabecera de hash ni checksum |

Consecuencias prácticas:

- Lee el cuerpo hasta EOF. No esperes `Content-Length` ni calcules el tamaño antes de descargar; el
  resultado puede llegar en varios bloques.
- No intentes reanudar ni pedir un rango: un `Range` se ignora y el servidor vuelve a enviar todo. Y como
  el `GET` consume el resultado, un segundo intento no es posible.
- No asumas compresión: si tu cliente envía `Accept-Encoding`, la respuesta no viene comprimida.
- El cuerpo siempre es UTF-8 válido. No apliques detección de codificación ni conversiones; decodifica
  como UTF-8.

### Contraejemplo: lo que NO vas a recibir

```json
{ "id": "...", "markdown": "# Título", "status": "succeeded" }
```

```json
{ "content": "# Título", "format": "markdown", "schema": "gfm/1" }
```

Ninguna de esas dos formas existe. Si tu cliente hace `response.json()`, fallará o interpretará el
Markdown como texto no-JSON. Guarda el cuerpo como bytes y escríbelo en un archivo `.md`.

## El Markdown no tiene esquema fijo

El contenido depende del formato de origen y del conversor que lo procesó. La API garantiza *un documento
Markdown*, no una estructura concreta:

- No hay esquema, versión de esquema ni `front-matter` YAML. La primera línea del cuerpo ya es contenido.
- No hay envoltorio de metadatos: ni nombre de archivo, ni MIME de origen, ni número de páginas, ni motor
  usado. Esos datos existen como métricas de consumo, no en el resultado (ver
  [`limits-and-formats.md`](./limits-and-formats.md)).
- Los encabezados, listas, tablas, bloques de código y enlaces aparecen **solo si el conversor los
  produce**. Una hoja de cálculo puede salir como una o varias tablas GFM; un TXT puede salir como texto
  plano sin ninguna marca.
- No hay separadores de página ni marcas de salto de página. Un PDF de 30 páginas es un único documento
  Markdown continuo.
- El orden de lectura de documentos maquetados es una aproximación heurística del conversor, no una
  reconstrucción del diseño.
- Si el conversor emite referencias a imágenes, son texto dentro del Markdown; este servicio no sirve esos
  archivos, así que un visor puede mostrarlas rotas. No dependas de ellas.
- Los saltos de línea no se normalizan por la API; los conversores actuales emiten `\n` (LF).

### El cuerpo puede ser vacío

Un `200` con cuerpo de 0 bytes es un resultado **válido**, no un error de transporte. Ocurre, por ejemplo,
con un archivo de texto vacío, o con un PDF escaneado sin capa de texto cuando OCR no está activo. El
trabajo figura como `succeeded`, el resultado se descarga una vez y queda consumido.

Regla de integración: trata `200` como éxito aunque el cuerpo esté vacío, y decide en tu producto qué
hacer con un Markdown vacío (avisar, marcar para revisión, reintentar con OCR). No reintentes la descarga:
el resultado ya se consumió y el segundo `GET` devuelve `410`.

## PDF: estructura, no fidelidad

Los PDF sin OCR se procesan con PyMuPDF4LLM (`use_ocr=False`) y, si falla o devuelve contenido vacío, con
el conversor PDF de MarkItDown. Eso preserva mejor tablas y espaciado que una extracción de texto simple,
pero **no** es una reproducción visual del documento:

- No se garantiza fidelidad 1:1 con el diseño: columnas, cajas, pies de página, encabezados repetidos y
  elementos decorativos pueden reordenarse, compactarse o desaparecer.
- No se garantiza que el Markdown sea visualmente idéntico al PDF ni que un humano lo reconozca como la
  misma página.
- Sí se espera que el contenido textual y las filas de las tablas estén presentes. Si detectas pérdida de
  datos —no de formato—, es un caso para reportar con el `id` de la conversión.
- Un PDF compuesto solo por imágenes, sin capa de texto, produce cuerpo vacío o casi vacío salvo que se
  solicite OCR.
- OCR es opt-in por conversión (`ocr=true`), requiere que el plan lo habilite y que el proveedor esté
  configurado. Cuando se usa, las imágenes de las páginas se envían a Azure OpenAI; sin él, la conversión
  falla con `ocr_provider_not_configured` (`503`) al solicitarla.

Nada de esto promete “todos los formatos”: la allowlist de [`limits-and-formats.md`](./limits-and-formats.md)
es una validación de entrada, no una garantía de calidad de conversión.

## La descarga es de un solo uso

El `GET` del resultado **consume** el recurso. La secuencia real, en el código:

1. La API reclama la conversión con un `UPDATE` condicional: `status=succeeded`, sin descarga previa, sin
   reclamo en curso y `result_expires_at > ahora`. Marca `download_started_at`.
2. Transmite el archivo y, al terminar de escribir el último bloque, marca `downloaded_at` y **borra** el
   resultado del almacenamiento.
3. Si la transferencia no llega a completarse (desconexión antes de que la API termine de escribir), el
   reclamo se libera y el resultado sigue disponible para reintentar hasta la caducidad.

Por eso:

- `status=succeeded` **puede** venir con `result_url=null`. Significa que el resultado ya se descargó (o se
  está descargando). No es un error y no hay nada que reintentar.
- Un segundo `GET` sobre un resultado ya consumido devuelve `410 result_unavailable`.
- Mientras una descarga está en curso, `status` sigue mostrando `succeeded` **con** `result_url` no nulo: el
  campo no es un candado. Un `GET` concurrente también recibe `410`.
- El servidor no puede saber si tu cliente guardó el archivo en disco. La garantía termina en la escritura
  del último byte hacia la conexión HTTP. Si el resultado ya se consumió, no hay recuperación: hay que
  volver a convertir.

### Matices del gateway

Entre tu cliente y la API está el gateway Nginx del portal, que *bufferea* respuestas por defecto. El
“último byte” que observa la API es el de la conexión API→gateway, no necesariamente el de tu socket. En la
práctica:

- Si la interrupción ocurre antes de que la API termine de escribir al gateway, el reclamo se libera y el
  reintento funciona.
- Si tu cliente se desconecta después de que el gateway recibió el cuerpo completo, la API ya lo
  consideró entregado y el resultado está borrado. El reintento devolverá `410`.
- Por eso el reintento automático de un `GET` de resultado solo es seguro ante fallos de conexión previos
  a la recepción del cuerpo, y debe hacerse **una vez**, no en bucle.

## Prohibido: `HEAD`, *prefetch* y *previews*

El endpoint de descarga solo acepta `GET`. `HEAD` responde `405` con `Allow: GET`, y cualquier otro método
también es rechazado:

```http
HTTP/1.1 405 Method Not Allowed
allow: GET
```

Reglas para el integrador y para las herramientas:

- No uses `curl -I`, `HEAD`, `OPTIONS` ni comprobaciones de existencia antes de descargar.
- No pongas la URL del resultado en un *preview* de Postman/Insomnia, en un visor Markdown del IDE, en un
  panel de navegador o en un proxy que haga peticiones especulativas. Cualquier `GET` que llegue consume.
- No la declares como recurso cacheable ni la pases por un CDN o *crawler*.
- No la registres en logs de acceso de terceros, analítica ni historiales de shell; y no la compartas: quien
  tenga la URL y la clave puede consumirla.
- La URL tampoco es navegable en el navegador: requiere la cabecera `X-API-Key`, que no puede ir en la
  barra de direcciones.

## Cómo descargarlo bien

`curl` (una sola petición, sin `-I`):

```bash
curl -sS --fail-with-body -D headers.txt \
  "$MARKITDOWN_BASE_URL/v1/conversions/$CONVERSION_ID/result" \
  -H "X-API-Key: $MARKITDOWN_API_KEY" \
  -o converted.md
```

`curl` con `--fail-with-body` evita guardar un cuerpo JSON de error como si fuera Markdown: en `4xx`
sigue leyendo el cuerpo pero devuelve código de salida distinto de cero. Aun así, revisa
`headers.txt`/el estado antes de dar el archivo por bueno.

Python, en streaming y sin cargar todo en memoria:

```python
import requests

response = requests.get(
    f"{base_url}/v1/conversions/{conversion_id}/result",
    headers={"X-API-Key": api_key},
    stream=True,
    timeout=(10, 120),
)
if response.status_code != 200:
    # 409: aún no listo; 410: consumido, reclamado o expirado; 404: no existe o no es de tu cuenta.
    raise RuntimeError(f"result not available: {response.status_code} {response.text}")

with open("converted.md", "wb") as output:
    for chunk in response.iter_content(chunk_size=64 * 1024):
        output.write(chunk)
```

Notas:

- Comprueba el `status_code` **antes** de escribir. Un `409`/`410`/`404` trae un cuerpo JSON de error
  pequeño; no lo guardes como `.md` ni lo confundas con un Markdown vacío.
- El tamaño máximo del resultado es 50 MiB (la mitad del límite máximo de entrada). Si tu cliente tiene
  límite de memoria, escribe en disco mientras llega.
- Un `200` con cuerpo vacío es válido; no lo trates como fallo de red.
- El estado y su tabla de decisión están en [`conversion-lifecycle.md`](./conversion-lifecycle.md); los
  códigos de error, en [`errors.md`](./errors.md).

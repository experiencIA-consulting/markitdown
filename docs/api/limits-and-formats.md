# Límites, cuotas y formatos

Valores por defecto del despliegue actual. Los `MAX_*`, `WORKER_*`, `RESULT_TTL_MINUTES`,
`CONVERSION_TIMEOUT_SECONDS`, `RATE_LIMIT_PER_MINUTE` y `QUOTA_TIMEZONE` son configurables por entorno; el
valor efectivo de una cuenta es siempre el **menor** entre el global y el de su plan.

El servicio corre en una VPS pequeña con un solo trabajador. Los límites de abajo describen lo que el
sistema acepta, no una promesa de rendimiento: la conversión es de una en una.

## Entrada

| Límite | Valor por defecto | Notas |
|---|---|---|
| Tamaño máximo por archivo | **100 MiB** (`104857600`) — `MAX_UPLOAD_BYTES` | Tope duro del servicio; el plan puede fijar uno menor. La configuración rechaza valores por encima de 100 MiB. |
| Archivos por solicitud | **1** | No hay lotes. Dos archivos en el multipart → `400 invalid_multipart_form`. |
| Campos de formulario | `file` (obligatorio) y `ocr` (opcional) | Cualquier otro campo → `422 unexpected_form_field`. |
| Parseos multipart simultáneos | **1** por proceso de API | Un segundo `POST` espera turno para leer el cuerpo. |
| Tamaño del cuerpo en el gateway | **101 MiB** | Por encima, el gateway responde `413` **en HTML**, sin llegar a la API. |
| `Content-Length` | Obligatorio | Sin él → `411 content_length_required` (afecta a clientes que usan *chunked*). |
| Entradas por contenedor ZIP/Office/EPUB | **5.000** — `MAX_ARCHIVE_ENTRIES` | Se cuentan las entradas declaradas del ZIP. |
| Bytes expandidos declarados | **128 MiB** — `MAX_ARCHIVE_EXPANDED_BYTES` | Suma de tamaños declarados; no se descomprime para medir. |

El margen entre el límite declarado y el real: la API acepta comprobar la cabecera `Content-Length` con
1 MiB de holgura (para el sobre multipart) y luego aplica el límite exacto al escribir los bytes. Un archivo
de 100,5 MiB con límite de 100 MiB pasa la primera comprobación y falla con `413 file_too_large` al
escribirse.

### Descargas por URL

| Límite | Valor por defecto | Notas |
|---|---|---|
| Esquemas | `http`, `https` | Cualquier otro → `422 invalid_url`. |
| Puertos | Solo los estándar del esquema | `80` para `http`, `443` para `https`; otro puerto explícito → `422 port_not_allowed`. |
| Destinos | Solo IP públicas | Se rechazan loopback, privadas, link-local, reservadas, metadatos de nube y, en general, cualquier dirección no global. |
| Redirecciones | Hasta **5** (6 peticiones) | Más → `too_many_redirects`. |
| Rebaja de esquema | Prohibida | `https` → `http` → `https_downgrade`. |
| Tiempo total de la descarga | **60 s** | Incluye conexión, espera y lectura. |
| Conexión / lectura | 10 s / 15 s por operación | Sin datos durante 15 s → fallo. |
| Codificación de transferencia | Solo `identity` | Se envía `Accept-Encoding: identity`; si el origen comprime → `remote_content_encoding_not_supported`. |
| Bytes descargados | `min(límite por archivo, bytes restantes del mes, 100 MiB)` | Al excederse → `file_too_large` o `monthly_input_bytes_quota_exceeded`, según qué límite se haya tocado. |
| Respuesta del origen | Solo `2xx` | `3xx` se siguen; otro código → `remote_http_error`. |

## Salida y retención

| Límite | Valor por defecto | Notas |
|---|---|---|
| Tamaño máximo del Markdown | **50 MiB** (`52428800`) — `MAX_OUTPUT_BYTES` | Medido en bytes UTF-8. Si se excede, el trabajo falla con `result_too_large`; no se sirve recortado. |
| Resultados pendientes almacenados | **2 GiB** (`2147483648`) — `MAX_STORED_RESULT_BYTES` | Suma de resultados `succeeded`, no descargados y no vencidos. Al superarse, el nuevo trabajo falla con `temporary_storage_limit`. |
| Vida del resultado | **1.440 min (24 h)** — `RESULT_TTL_MINUTES` | Desde `completed_at`; la configuración no admite más de 1.440. |
| Reclamo de descarga abandonado | **1 h** | Si un proceso muere con la descarga reclamada, el resultado se libera tras una hora. |
| Caducidad perezosa | ~60 s de retraso | El barrido corre al terminar cada trabajo y, en reposo, aproximadamente cada minuto. |
| Nombre del archivo entregado | `converted.md` (fijo) | No se expone el nombre original. |
| Codificación del resultado | UTF-8 sin BOM | Sin `Content-Length`; ver [`markdown-result.md`](./markdown-result.md). |

El Markdown se escribe en bloques de 64 KiB y se descarta si excede el tope; aun así, el conversor
materializa el documento completo en memoria antes de esa comprobación. El control efectivo de ese pico es
el límite de memoria del subproceso.

## Cómputo

| Límite | Valor por defecto | Notas |
|---|---|---|
| Trabajadores | 1 | Un contenedor `worker`. |
| Conversiones simultáneas | 1 | Cola FIFO. |
| Trabajos pendientes en cola | **5** — `MAX_PENDING_JOBS` (rango 1–100) | Además del que está en ejecución. Al llenarse → `429 queue_full`. |
| Tiempo máximo de conversión | **900 s** — `CONVERSION_TIMEOUT_SECONDS` | Al excederse → `conversion_timeout` y se termina el subproceso. |
| Memoria del subproceso de conversión | **512 MiB** de RSS — `MAX_CONVERSION_MEMORY_BYTES` | Se mide por sondeo y se termina al exceder → `resource_limit_exceeded`. |
| Memoria del contenedor worker | **768 MiB** — `WORKER_MEMORY_LIMIT` | Respaldo ante picos entre sondeos. |
| CPU del worker | **1** — `WORKER_CPUS` | |
| Memoria del contenedor API | 256 MiB | Por eso la validación de contenido corre en el worker. |
| Tiempo de espera en cola | Sin garantía | Depende del tamaño de los trabajos delante. |

Un documento complejo puede consumir el presupuesto de memoria incluso cuando su tamaño en disco es
pequeño (PDF con muchas fuentes o tablas, por ejemplo). Un `resource_limit_exceeded` debe diagnosticarse
antes de subir límites: el contenedor es de 768 MiB y la VPS, de 2 GB.

## Ritmo de solicitudes

| Límite | Valor por defecto | Notas |
|---|---|---|
| Solicitudes de conversión por minuto | **30** — `RATE_LIMIT_PER_MINUTE`, recortado por el plan | Contador por cuenta en ventanas de un minuto UTC. Al excederse → `429 rate_limit_exceeded`. |
| Cabeceras informativas | Ninguna | No hay `Retry-After` ni `X-RateLimit-*`. |
| Qué cuenta | Solo los dos `POST` de conversión | El polling de estado y la descarga no consumen este contador. |
| Alcance | Por cuenta, no por clave | Dos claves de la misma cuenta comparten el contador. |

## Cuotas del plan

El periodo es el **mes natural en `QUOTA_TIMEZONE`** (`America/Mazatlan` por defecto): empieza el día 1 a
las 00:00 locales y termina el día 1 del mes siguiente a la misma hora. Los instantes se almacenan en UTC y
los límites se calculan con `zoneinfo`, así que el cambio de horario no desplaza el corte.

| Cuota | Cómo se consume |
|---|---|
| Conversiones por mes | Solo conversiones en estado `succeeded` o `expired` (con resultado registrado). Un intento `failed` no descuenta. |
| Páginas (unidades) por mes | Suma de unidades por conversión exitosa: el número real de páginas cuando el conversor lo determina; **1 unidad** para imágenes y para formatos sin paginación fiable. |
| Bytes de entrada por mes | Suma de los bytes **realmente procesados**, incluidas las conversiones que acaban en `failed`. Los rechazos anteriores a escribir la entrada no cuentan. |
| Unidades OCR por mes | Llamadas realmente enviadas al proveedor externo, incluidas las de un intento que luego falla. |
| Solicitudes por minuto | Ver la sección anterior; cuenta cada `POST` autenticado. |

Matices que afectan al integrador:

- Un documento cuya conversión real consume más páginas de las previstas puede hacer que el trabajo termine
  en `failed` con `monthly_page_quota_exceeded` **después** de haberse aceptado con `202`.
- Un resultado que caduca sin descargarse (`expired`) sigue contando como conversión y páginas: la cuota no
  se devuelve.
- Las cuotas se comprueban dos veces: al aceptar (respuesta síncrona `429`) y al ejecutar (trabajo
  `failed`). Un cambio de plan a mitad de mes se aplica de inmediato sobre el consumo ya acumulado.
- Sin plan activo, una cuenta no administradora no puede convertir: `403 plan_not_configured`. Las
  cuotas concretas de cada plan las fija administración; el catálogo `GET /v1/plans` es informativo y no
  hay *checkout*.

## Formatos aceptados

La allowlist es de **validación de entrada**. Se comprueba la extensión declarada (o inferida) contra el
contenido: no todos los formatos que MarkItDown podría convertir están permitidos, y estar en la lista no
garantiza calidad de conversión.

| Familia | Extensiones | Cómo se valida |
|---|---|---|
| Texto y marcado | `.txt` `.text` `.md` `.markdown` `.json` `.jsonl` `.csv` `.html` `.htm` `.xhtml` `.xht` `.xml` `.rss` `.atom` `.ipynb` | Clasificación de contenido: debe identificarse como texto o código. Los alias `.html`/`.htm` y `.jpg`/`.jpeg` se aceptan entre sí. |
| PDF | `.pdf` | Debe contener la cabecera `%PDF-` en los primeros 1.024 bytes. Sin ella → `file_type_mismatch`. Si el archivo tiene cabecera PDF y otra extensión → `file_type_mismatch`. |
| Office Open XML y EPUB | `.docx` `.xlsx` `.pptx` `.epub` | Se abren como ZIP: se valida el número de entradas y los bytes expandidos declarados, y se comprueba la firma interna (`word/document.xml`, `xl/workbook.xml`, `ppt/presentation.xml`, `mimetype = application/epub+zip`). Un ZIP genérico con extensión `.docx` → `file_type_mismatch`. |
| ZIP | `.zip` | Contenedor ZIP válido dentro de los límites. |
| Excel binario | `.xls` | No es ZIP: se valida solo por clasificación de contenido; si no se identifica, `file_type_mismatch`. |
| Imágenes | `.jpg` `.jpeg` `.png` | Clasificación de contenido. |
| Audio | `.wav` `.mp3` `.m4a` | Clasificación de contenido. |
| Vídeo | `.mp4` | Clasificación de contenido. |
| Correo | `.msg` | Clasificación de contenido. |

Reglas adicionales:

- **Sin extensión**: si el contenido se clasifica y la extensión inferida está en la allowlist, se acepta y
  se usa esa extensión. Si no, `unsupported_format`.
- **Archivo vacío (0 bytes)**: solo se acepta con extensión de texto; produce un Markdown vacío. Con
  cualquier otra extensión → `invalid_file_content`.
- **Extensiones fuera de la lista** (`.doc`, `.odt`, `.rtf`, `.xlsb`, `.eml`, `.tiff`, `.avi`…):
  `unsupported_format`, aunque el conversor instalado pudiera manejarlas. La lista se mantiene alineada con
  los conversores y dependencias reales de la imagen.
- **Contenido no clasificable** o contenedor corrupto: `invalid_file_content`.
- Esta validación **no** es un antivirus ni una garantía de que el analizador del formato sea seguro.
  Trata todo documento como entrada no confiable.

### Calidad de conversión

- No hay promesa de “todos los formatos”. La lista anterior es lo que se acepta hoy en este despliegue.
- PDF: se procesa con PyMuPDF4LLM (`use_ocr=False`) y, como reserva, con el conversor PDF de MarkItDown. Se
  preservan mejor las tablas y el espaciado que con extracción de texto simple, pero **no hay fidelidad
  visual 1:1** ni reconstrucción del diseño.
- PDF solo-imagen sin OCR: cuerpo vacío o casi vacío. Es un resultado válido, no un error.
- OCR es opt-in, requiere plan y proveedor configurado; cuando se usa, las imágenes se envían a Azure
  OpenAI.
- `PDF_ENGINE` permite elegir `pymupdf4llm` (por defecto) o `markitdown`; no cambia el contrato HTTP.

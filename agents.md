# Handoff técnico: API REST para MarkItDown

## Objetivo

Este documento es el punto de partida para el siguiente agente. Describe la implementación existente y el estado confirmado del despliegue. No recrear el proyecto ni borrar volúmenes durante tareas de diagnóstico sin autorización explícita.

## Estado de implementación

- Implementación en `packages/markitdown-api/`: FastAPI, worker, cola Redis, PostgreSQL, almacenamiento temporal, Dockerfile, Compose, README y pruebas.
- Implementación en `packages/markitdown-admin/`: SPA React + TypeScript + Vite, Nginx y build de producción.
- **Estado remoto confirmado (2026-11-04):** Dokploy `xperiencIA` / `production`; Compose `markitdown`; ruta `packages/markitdown-api/compose.yaml`; dominio `mdservice.innersoft.com.mx` asignado al gateway `admin-web:80`.
- El último despliegue consultado está en `done`; `api`, `worker`, `postgres`, `redis` y `admin-web` están activos. `https://mdservice.innersoft.com.mx/health/live` y `/health/ready` respondieron `200`.
- El administrador inicial ya fue invitado/creado. No volver a ejecutar `bootstrap-admin` sin comprobar antes si la cuenta ya existe; el comando la promovería y cambiaría su versión de sesión.
- La página `/accept-invitation` fue corregida: el crash inicial `Cannot read properties of null (reading 'role')` ocurría al evaluar rutas admin sin sesión. El login web y la aceptación de invitación ya se validaron.
- Hay un plan `pro_weekly` activo con cuotas provisionales configuradas por el administrador; `Free` no aparece activo en `/v1/plans` al momento de este documento. Los valores de ese plan no son un benchmark aprobado.
- La rama desplegada es `HP240RG10-dev`. Confirmar la rama y el estado en Dokploy antes de cambios posteriores.
- SMTP está configurado en el puerto `465`, que requiere **SSL implícito**. `mail.py` usa `SMTP_SSL` automáticamente para 465 o cuando `SMTP_SSL=true`. `SMTP_STARTTLS` no aplica en ese puerto.
- OCR está disponible explícitamente mediante el plugin MarkItDown + Azure OpenAI si se configuran sus secretos; por defecto no se invoca.
- El despliegue inicial usa una instancia API, un worker y una conversión simultánea. No aumentar concurrencia sin medir la VPS de 2 GB.
- Los PDF sin OCR usan `pymupdf4llm==1.28.2` con `use_ocr=False`; MarkItDown queda como fallback. `PDF_ENGINE` permite cambiar el motor.
- El error `resource_limit_exceeded` observado en un PDF se debió a que PyMuPDF4LLM activaba su OCR interno; se desactivó con `use_ocr=False`. El subproceso sigue limitado a 512 MiB y debe vigilarse.
- El repositorio está indexado en Codebase Memory con el nombre `markitdown`. El artefacto `.codebase-memory/graph.db.zst` está versionado para que otros agentes puedan reutilizar el índice sin reindexar; `artifact.json` indica el commit y los conteos.
- La última suite del paquete API conocida antes del portal/PDF fue `27 passed`; Black/Ruff pasan. El build del frontend (TypeScript + Vite) pasa. No se ha ejecutado la suite completa después de los últimos cambios de portal y PDF.

## Requisitos acordados

- VPS actual: Ubuntu, Dokploy, 2 núcleos, 2 GB de RAM y 50 GB de disco.
- Dokploy gestiona la exposición pública y el enrutamiento del dominio.
- Aceptar archivos y URLs HTTP/HTTPS públicas.
- Tamaño máximo de archivo o descarga: 100 MB.
- Habilitar los formatos disponibles de MarkItDown según sus dependencias; dejar preparados OCR y proveedores externos como Azure.
- OCR y servicios externos deben ser explícitos y estar desactivados por defecto.
- Cuentas individuales por cliente, claves API y medición de consumo.
- No integrar pagos en esta etapa. La unidad facturable se decidirá después de evaluar pruebas.
- Conservar registros de consumo indefinidamente.
- Eliminar el archivo de entrada al finalizar el intento de conversión.
- Conservar el resultado hasta una descarga completa o durante un máximo de 1.440 minutos (24 horas), lo que ocurra primero; eliminarlo después de una descarga completa.
- Empezar con una conversión simultánea y una cola limitada, y ajustar los límites con pruebas de carga.

## Arquitectura implementada

```text
Cliente
  |
  v
Dokploy / Traefik
  |
  v
admin-web (Nginx: SPA + proxy /v1/*, /health/*, /docs)
  |                         |
  v                         v
API REST                 PostgreSQL / Redis / Worker
  |                         |
  +-------- cola -----------+
              |
              v
     Volumen temporal de archivos
```

- **admin-web:** sirve la SPA y actúa como gateway same-origin. Es el único servicio publicado por Dokploy.
- **API:** recibe solicitudes, autentica clientes, valida límites y crea trabajos.
- **Redis:** cola de trabajos y coordinación; no expuesto públicamente.
- **PostgreSQL:** cuentas, claves API protegidas, planes, estados de trabajos y registros de consumo.
- **Worker:** ejecuta la conversión con la API Python de MarkItDown, separado del proceso HTTP.
- **Volumen temporal:** mantiene las entradas mientras están pendientes/en proceso y los resultados hasta su entrega o expiración. Debe tener límites y limpieza automática.
- **Dokploy:** publica `admin-web` en el dominio configurado. API, Redis, PostgreSQL y worker permanecen en la red interna de Docker.

Con 2 GB de RAM, mantener un worker con concurrencia 1. Redis y PostgreSQL se ejecutan en la misma VPS que Dokploy; conversiones complejas pueden agotar memoria. No anunciar capacidad de alta carga hasta medirla. El Compose limita recursos y resultados temporales, pero no elimina esta limitación física.

## Contrato de API actual

- `POST /v1/conversions`: recibe un archivo multipart y responde `202 Accepted` con el ID del trabajo.
- `POST /v1/conversions/from-url`: recibe una URL pública y responde `202 Accepted` con el ID del trabajo.
- `GET /v1/conversions/{id}`: devuelve el estado y errores públicos del trabajo.
- `GET /v1/conversions/{id}/result`: entrega el Markdown solo al propietario del trabajo.
- `GET/PATCH /v1/account/me` y `POST /v1/account/password`: perfil y contraseña del usuario autenticado.
- `POST /v1/web-auth/login`, `/me`, `/csrf`, `/logout`, `/accept-invitation`: sesión web con cookies y CSRF.
- `GET /v1/plans`: catálogo público de planes activos.
- Administración, solo `role=admin`: `/v1/admin/overview`, `/v1/admin/users`, `/v1/admin/users/{id}`, `/v1/admin/plans`, `/v1/admin/plans/{id}`, `/v1/admin/reports/consumption` y `.csv`.
- Endpoints de salud (`live` y `ready`) para operación y despliegue.

`POST /v1/accounts` sigue existiendo, pero el registro público está desactivado por defecto (`PUBLIC_SIGNUP_ENABLED=false`). El flujo normal es invitación desde administración. Las claves API se usan en `X-API-Key`; las sesiones web usan cookies same-origin más `X-CSRF-Token` en mutaciones.

Estados sugeridos: `queued`, `running`, `succeeded`, `failed`, `expired` y `cancelled`.

La descarga se considerará completa cuando el servidor finalice el envío de la respuesta sin desconexión del cliente. Si el cliente se desconecta antes de completar la transferencia, conservar el resultado para un nuevo intento hasta la caducidad. El servidor no puede confirmar que el cliente haya guardado el archivo; si se requiere confirmación de aplicación, diseñar un endpoint de acuse de recibo.

## Procesamiento, límites y capacidad actuales

1. Archivos y descargas HTTP se transmiten a disco temporal; la API exige `Content-Length` para cargas multipart.
2. Límite de entrada: 100 MiB; máximo de 5 trabajos pendientes; un upload multipart procesándose a la vez.
3. Un worker y una conversión simultánea.
4. Límite de salida: 50 MiB por resultado; máximo agregado de 2 GiB en resultados pendientes de descarga; expiración de 1.440 minutos.
5. Timeout de conversión: 900 segundos. La cola llena devuelve un error controlado (`429`/`503`).
6. Los límites están configurados mediante variables de entorno en `packages/markitdown-api/compose.yaml`.
7. Carga por encima de 100 MiB no está soportada; aceptación de un tamaño no garantiza que cualquier formato complejo quepa en la memoria disponible.
8. PDF sin OCR: PyMuPDF4LLM 1.28.2 con `use_ocr=False`; fallback a MarkItDown si devuelve vacío o falla. El OCR real solo se invoca con el plugin MarkItDown + Azure.
9. El subproceso de conversión se limita a `MAX_CONVERSION_MEMORY_BYTES` (512 MiB) y el contenedor worker a 768 MiB. PyMuPDF4LLM aún puede acercarse al límite en documentos complejos; un `resource_limit_exceeded` debe diagnosticarse antes de elevar el límite.

La categoría “todos los formatos” debe validarse contra las capacidades y dependencias reales de MarkItDown. Los extras `[all]`, herramientas del sistema, OCR y ciertas integraciones pueden aumentar el tamaño de la imagen o requerir servicios externos. No asumir que todos los formatos tienen conversión local equivalente.

## Seguridad para archivos y URLs

- Autenticar con claves API enviadas en cabecera; no aceptar claves en query strings. Guardar hashes de claves, mostrarlas al cliente solo al crearlas y permitir revocarlas.
- Limitar tamaño de entrada/descarga, tiempo total, tamaño de salida, frecuencia de solicitudes y trabajos concurrentes por cuenta.
- Usar nombres aleatorios para temporales; nunca construir rutas a partir del nombre enviado por el usuario.
- Validar extensión y contenido; tratar documentos y archivos comprimidos como entrada no confiable.
- Aceptar solo esquemas HTTP/HTTPS para URLs. Rechazar destinos localhost, privados, link-local, reservados y otros rangos no públicos.
- Validar DNS y cada redirección; limitar puertos, redirecciones, tiempo y bytes descargados. Evitar que el servicio pueda acceder a metadatos de nube o servicios internos.
- Siempre que sea posible, descargar el contenido con un componente controlado y entregar los bytes al conversor, en vez de pasar una URL arbitraria directamente al conversor.
- Ejecutar workers sin privilegios y con acceso de red saliente restringido cuando las integraciones habilitadas lo permitan.
- No registrar contenido, Markdown, claves, nombres de archivo ni URLs completas. Usar identificadores de trabajo para correlación.
- Mantener dependencias actualizadas y eliminar temporales también ante fallos, cancelaciones y reinicios.

## Retención y registro de consumo

Los archivos y resultados son efímeros; los metadatos de consumo se conservan indefinidamente, según lo acordado. No almacenar el contenido fuente ni el Markdown en la tabla de consumo.

Campos propuestos por conversión:

- ID de conversión y cuenta propietaria.
- Fecha/hora, tipo de entrada, formato/extensión y estado.
- Bytes exactos de entrada (`input_bytes`); derivar MB como `input_bytes / 1_000_000` y documentar esta convención.
- Número de páginas (`page_count`), nullable cuando el formato o convertidor no lo pueda determinar de forma fiable.
- Longitud del resultado en caracteres (`output_char_count`) y bytes UTF-8 (`output_utf8_bytes`).
- Duración, categoría de error y proveedor externo utilizado, si corresponde.

Vigilar el crecimiento de registros indefinidos, índices, copias de seguridad y capacidad de PostgreSQL. Evitar guardar datos identificables no necesarios. Definir posteriormente cómo se anonimizan o eliminan los vínculos a una cuenta cuando se solicite su cierre.

## Cuentas, claves, planes y facturación

- Implementado: cuenta individual, verificación de correo, login web, recuperación y cambio de contraseña, y gestión de claves API.
- Roles `user`/`admin`, estado activo/suspendido, invitaciones con caducidad, planes asignables y overrides de cuotas por cuenta.
- Las claves API se entregan una sola vez, se almacenan como HMAC y se envían en `X-API-Key`; las sesiones web usan cookies `HttpOnly` y CSRF. Las sesiones Bearer siguen disponibles para clientes API.
- Hay un máximo de 20 claves activas por cuenta. Restablecer o cambiar una contraseña revoca las claves y cambia la versión de sesión.
- `GET /v1/usage` devuelve registros paginados. La administración tiene informes agregados y CSV.
- El plan `pro_weekly` está activo con cuotas provisionales; Free no está activo en `/v1/plans` al escribir esto. No tratar esos valores como benchmark aprobado.
- El proveedor de pagos, países, moneda y unidad facturable siguen pendientes. No hay checkout; la asignación de plan es manual.

## OCR y servicios externos

- Implementado mediante el plugin OCR de MarkItDown con Azure OpenAI/OpenAI-compatible.
- Se habilita por conversión (`ocr=true`) y falla con `ocr_provider_not_configured` si faltan secretos; por defecto está apagado.
- Secretos en Dokploy: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_MODEL`.
- PyMuPDF4LLM se ejecuta con `use_ocr=False`; el OCR de pago solo se usa por la ruta MarkItDown/Azure cuando se solicita explícitamente.
- Habilitar OCR envía imágenes de documentos al endpoint configurado; confirmar consentimiento, coste y privacidad antes de hacerlo por cliente.

## Estado de fases y siguiente trabajo

1. **Implementado:** API versionada, auth, conversión por archivo y URL, cola, worker, límites y retención.
2. **Implementado:** Compose para Dokploy, gateway `admin-web`, Redis/PostgreSQL/worker internos y healthchecks.
3. **Implementado:** portal web, RBAC admin, invitaciones, planes, overrides de cuotas, informes y cambio de contraseña.
4. **Implementado:** Alembic y migraciones `0001_base`/`0002_admin_portal`; el arranque aplica `upgrade head`.
5. **Pendiente antes de escala:** benchmark de formatos/tamaños en la VPS, especialmente PDF con PyMuPDF4LLM; medir RSS, CPU y latencia antes de aumentar concurrencia.
6. **Pendiente de producción:** backups/restauración de PostgreSQL, política de anonimización/cierre de cuenta, abuso de registro/reset, y verificar licencia AGPL/comercial de PyMuPDF/PyMuPDF4LLM.
7. **Pendiente de producto:** definir cuotas definitivas de Free/Pro/Lifetime/Business y activar Free si se usará como plan por defecto.
8. **Pendiente de validación:** probar una conversión real con un usuario no admin, descarga única, expiración a 24 h, OCR y exportación CSV.

## Procedimiento operativo Dokploy

- El Compose debe apuntar a `packages/markitdown-api/compose.yaml` en la raíz del checkout del repositorio `experiencIA-consulting/markitdown`.
- El dominio debe seleccionar `serviceName=admin-web`, puerto interno `80`, path `/`; nunca seleccionar `worker` ni `api`. El gateway sirve la SPA y proxya `/v1/*`, `/health/*`, `/docs` y páginas de correo a `api:8000`.
- Para 404, comparar `compose.getConvertedCompose` con `docker.getConfig` del contenedor vivo: debe tener labels Traefik y `dokploy-network`.
- Después de cambiar dominios/rutas o variables, redeploy Compose. Healthchecks confirmados: `/health/live` y `/health/ready`.
- El flujo normal de alta es invitación desde `/users`, no registro público. El primer admin se crea dentro del contenedor `api` con `python -m markitdown_api.admin_cli bootstrap-admin <email>`. Si SMTP falla, el comando imprime el enlace de invitación.
- SMTP en puerto 465 usa SSL implícito; no activar STARTTLS para ese puerto. El cliente detecta 465 automáticamente y también admite `SMTP_SSL=true`.
- Alembic aplica migraciones al arrancar. No volver a `metadata.create_all` para evolución de esquema.
- El gateway envía `Cache-Control: no-store` para el index y cachea assets con hash de forma inmutable; tras un despliegue, un `Ctrl+F5` debe ser suficiente.
- Errores observados históricamente: Nixpacks no correspondía a este monorepo; `composePath` incorrecto causaba “Compose file not found”; un contenedor vivo sin labels Traefik producía 404; un volumen PostgreSQL persistente puede conservar una contraseña anterior aunque `POSTGRES_PASSWORD` cambie. No borrar volúmenes para resolver auth DB sin confirmar pérdida de datos.

## Criterios de aceptación

- Una cuenta no puede consultar conversiones, resultados, claves ni consumo de otra.
- Un usuario normal recibe `403` en endpoints de administración; las rutas de la SPA no son el control de acceso.
- Claves API se pueden revocar y no aparecen en logs ni respuestas posteriores a su creación.
- Las invitaciones caducan, no se pueden reutilizar y permiten establecer contraseña.
- Archivos y descargas de hasta 100 MB se procesan sin cargarse enteros en memoria.
- URLs a destinos internos/privados, redirecciones peligrosas y descargas excesivas se bloquean.
- La cola y la concurrencia respetan los límites y rechazan carga excesiva de forma controlada.
- El archivo fuente se elimina al terminar; el resultado se elimina tras transferencia completa o al vencer 24 horas; una transferencia interrumpida no consume el resultado.
- Las métricas acordadas se guardan indefinidamente sin almacenar documentos ni Markdown.
- Las cuotas se calculan en `America/Mazatlan`; una conversión fallida no consume conversiones/páginas, pero sí bytes y requests/minuto.
- OCR/Azure no se invocan sin habilitación explícita y credenciales configuradas.
- Dokploy expone únicamente el gateway `admin-web`; API, Redis, PostgreSQL y worker no tienen puertos públicos.
- Los PDF se convierten con PyMuPDF4LLM y todas las páginas/filas del documento están presentes; si se requiere fidelidad literal, se usa el modo que se defina.
- Las pruebas de carga documentan memoria, CPU, disco, tiempos y fallos para decidir si se amplía la VPS o se separan workers.

## Pendientes para una fase posterior

- Elegir proveedor de pagos, países, moneda, planes y unidad facturable.
- Decidir si se requiere acuse explícito del cliente para considerar completa la descarga.
- Confirmar la lista exacta de formatos/extras locales y proveedores OCR/Azure a soportar.
- Establecer política de anonimización de métricas si se cierra una cuenta.

## Mejoras implementadas: validación de formatos y límites de memoria

Implementación en `packages/markitdown-api/`; las validaciones de entrada, límites de memoria y limpieza están desplegadas junto con el portal. Mantener un worker y una conversión simultánea.

### Validación de entradas

- `src/markitdown_api/input_validation.py` clasifica el contenido con Magika y compara el tipo detectado con una lista de extensiones soportadas por la imagen actual. Rechaza formatos no admitidos, discrepancias claras y contenido no clasificable con `unsupported_format`, `file_type_mismatch` o `invalid_file_content`.
- Valida la cabecera PDF, reconoce ZIP/Office Open XML/EPUB y limita contenedores con `MAX_ARCHIVE_ENTRIES` (predeterminado 5.000) y `MAX_ARCHIVE_EXPANDED_BYTES` (128 MiB). Estos límites no equivalen a análisis antivirus.
- Se ejecuta en el subprocess del worker antes de MarkItDown, tanto para archivos cargados como descargados desde URL. Por el contrato asíncrono, la solicitud puede responder `202`; una entrada inválida después queda como trabajo `failed` con su `error_code`. Se hace así para no cargar el modelo Magika en el proceso API limitado a 256 MiB.
- Si el nombre no tiene extensión, se infiere una extensión admitida cuando Magika puede identificarla. Mantener `SUPPORTED_EXTENSIONS` alineado con los conversores/dependencias instalados; no representa automáticamente todos los formatos opcionales de MarkItDown.

### Recursos y resultados

- El worker mide el RSS de su subprocess y lo termina si supera `MAX_CONVERSION_MEMORY_BYTES` (predeterminado 512 MiB), reportando `resource_limit_exceeded`; conserva el timeout existente de 900 segundos. El worker en Compose queda limitado por `WORKER_MEMORY_LIMIT` (768 MiB) y `WORKER_CPUS` (1 CPU). El límite del contenedor es el respaldo ante memoria de procesos descendientes o picos entre sondeos; un OOM que mate el worker completo aún requiere validación operativa.
- Markdown se escribe en bloques UTF-8 de 64 KiB y se descarta si supera `MAX_OUTPUT_BYTES` (50 MiB). Esto evita crear una segunda copia completa codificada y elimina temporales parciales. **MarkItDown todavía materializa el Markdown completo en memoria antes de aplicar el límite de salida**; el límite RSS es el control para ese pico, no streaming de conversión.
- Variables nuevas configurables en Compose y documentadas en `.env.example`: `MAX_CONVERSION_MEMORY_BYTES`, `MAX_ARCHIVE_ENTRIES`, `MAX_ARCHIVE_EXPANDED_BYTES`.

### Verificación y pendientes

- Pruebas locales del paquete API: `27 passed` antes del portal/PDF. Ruff y Black pasan; el YAML de Compose parsea. El build Docker se completó en Dokploy. No se ha ejecutado la suite completa después de los últimos cambios de portal y PDF.
- Antes de escalar: probar formatos/tamaños representativos en la VPS, observar memoria/CPU y confirmar que el OOM mata el subprocess sin reiniciar o reencolar indefinidamente el worker. No aumentar concurrencia hasta medir.
- `resource_limit_exceeded` apareció con PyMuPDF4LLM porque su OCR interno estaba activo; se corrigió con `use_ocr=False`. Si reaparece, revisar RSS antes de subir límites o usar `PDF_ENGINE=markitdown` como mitigación temporal.
- Aceptar hasta 100 MiB de entrada no garantiza conversión para todo documento. La validación de formato tampoco es análisis de malware ni garantía de seguridad del parser.

## Plan de referencia: interfaz de administración y portal de usuario

**Estado:** implementado y desplegado; ver “Implementación del portal y cuotas (estado)” al final. Esta sección conserva el diseño original como referencia.

### Decisiones confirmadas

- La interfaz y la API deben compartir `mdservice.innersoft.com.mx`, con rutas como `/login`, `/dashboard`, `/convert`, `/users`, `/reports/consumption` y `/profile`.
- Backend FastAPI existente; se propone una SPA React + TypeScript + Vite y un gateway Nginx en Dokploy. El gateway sirve la SPA y reenvía `/v1/*` a FastAPI por la red interna; conservar las rutas actuales de API.
- El dominio público se debe dirigir al gateway/web, no al worker. Redis, PostgreSQL y worker siguen privados.
- La zona de reinicio de cuotas mensuales es `America/Mazatlan`. Los informes se presentan agrupados en la zona horaria IANA del navegador.
- Free: máximo 300 conversiones por mes natural y 10 MiB por archivo. Mantener el hard cap global actual de 100 MiB.
- Lotes: máximo inicial de 1 archivo por solicitud; dejar el valor como capacidad editable por plan para una posible ampliación.
- Cuotas requeridas por plan: conversiones, bytes de entrada (MB), páginas y solicitudes por minuto. Los valores de MB/páginas/RPM y los límites de otros planes se decidirán después de benchmark; no inventarlos ni habilitar un plan con una matriz incompleta.
- Conversión fallida: no consume cuota de conversiones ni páginas; sí se contabilizan los bytes realmente procesados y las solicitudes autenticadas por minuto.
- Páginas: mantener `page_count` como nullable cuando el conversor no pueda determinarlo. Para enforcement de cuotas usar una unidad derivada: páginas reales cuando existan; en imagen o formato sin paginación, 1 unidad por archivo convertido.
- OCR se habilita por plan y explícitamente por conversión. Medir cuota OCR por el número real de páginas/imágenes enviadas al proveedor, separado del número de páginas fuente.
- Usar los niveles y prestaciones de FileToMD.ai como referencia para Free, Pro semanal/mensual/anual, Lifetime y Business. Mostrar precios solo como catálogo informativo; no hay checkout ni pagos integrados. Asignación manual de plan por administrador.
- Business será una cuenta individual en v1; no implementar organizaciones ni membresías todavía.

### Arquitectura e identidad

- Añadir `packages/markitdown-admin/` como aplicación SPA. Publicar mediante un servicio web estático/Nginx en Compose/Dokploy. Nginx sirve la SPA y hace proxy de `/v1/*` a `api:8000`; revisar también `/health/*` y `/docs` según la política de exposición.
- Reemplazar el destino público del dominio en Dokploy de `api:8000` por el gateway web. Mantener el API accesible al gateway en la red interna y, si se requiere conservar clientes externos, mantener `/v1/*` disponible a través del mismo dominio.
- Extender `Account` con rol (`user`/`admin`), estado activo/suspendido, perfil y plan. El primer admin se debe provisionar por comando/operación controlada, nunca por registro público ni contraseña por defecto.
- Sesión web con cookie `Secure`, `HttpOnly`, `SameSite` y protección CSRF. Mantener las API keys existentes (`X-API-Key`) exclusivamente para clientes de conversión. La autorización siempre se verifica en backend; ocultar rutas en React no es control de acceso.
- Mantener invitación de usuario vía correo con expiración y flujo para establecer contraseña/verificar cuenta. Log de auditoría admin sin contraseñas, claves, documentos ni contenido Markdown.

### Modelo de planes y cuotas

Crear migraciones versionadas; el arranque actual con `metadata.create_all` no sustituye migraciones de producción. Añadir catálogo editable de planes y asignación manual a cuentas. Campos mínimos por plan:

- Conversión máxima por mes natural.
- Bytes de entrada máximos por mes natural.
- Unidades de página máximas por mes natural.
- Requests por minuto.
- Bytes máximos por archivo, sin superar 100 MiB globales.
- Máximo de archivos por lote (inicia en 1).
- OCR habilitado y unidades OCR por mes.
- Nombre/beneficios/precio mostrado como información, sin iniciar cobros.

Semántica de cuotas:

- Calcular los límites de mes natural en `America/Mazatlan`; guardar todos los instantes en UTC y calcular los límites con `zoneinfo` para respetar cambios de horario.
- Requests/minuto cuenta solicitudes de conversión autenticadas; mantener además límites globales/de abuso de infraestructura.
- Bytes se contabilizan por bytes realmente recibidos/procesados, incluso si el conversor termina fallando. Rechazos previos a aceptar/leer la entrada no deben incrementar MB procesados.
- Conversión/páginas solo se descuentan cuando la conversión termina correctamente. `page_count` almacenado puede quedar nulo; el valor `quota_page_units` aplica el fallback de 1 unidad indicado arriba.
- OCR solo descuenta unidades realmente enviadas al proveedor. Registrar proveedor y unidades, no datos/imágenes del documento.
- El admin puede editar límites; validar que los valores de cuenta no superen límites globales, particularmente tamaño de archivo y recursos de la VPS.

### API administrativa propuesta

- `GET /v1/admin/overview`: totales del periodo y estado operativo.
- `GET /v1/admin/users`: búsqueda, filtros y paginación.
- `POST /v1/admin/users`: crear cuenta invitada con plan/límites iniciales.
- `GET /v1/admin/users/{id}` y `PATCH /v1/admin/users/{id}`: detalle, perfil, estado, plan y límites.
- `GET /v1/admin/plans` y `POST/PATCH /v1/admin/plans`: catálogo y cuotas configurables.
- `GET /v1/admin/reports/consumption`: filtros de periodo, cuenta, estado y origen; agrupaciones `day`, `week`, `month`; aceptar zona IANA del navegador.
- `GET /v1/admin/reports/consumption.csv`: exportación paginada/streaming con límites de rango.
- `GET/PATCH /v1/account/me`: perfil del usuario autenticado. Reutilizar gestión existente de claves API y `GET /v1/usage` donde sea apropiado.

Los informes usarán agregaciones en PostgreSQL sobre Conversion (conteos, bytes/MB decimales, unidades de página, OCR, longitud de salida, estados y duración). Añadir índices y probar costes sobre registros indefinidos; no devolver documentos, URLs completas, claves o Markdown.

### Rutas y experiencia de usuario

- `/login`: login y verificación de estado de cuenta.
- `/dashboard`: resúmenes personales o globales según rol.
- `/convert`: archivo o URL, validación de límites/plan, progreso `queued`/`running`/resultado/error.
- `/users` y `/users/:id`: solo admin; invitación, plan, cuotas, estado y consumo.
- `/reports/consumption`: solo admin; agrupación día/semana/mes y filtros.
- `/profile`: perfil, cambio/recuperación de contraseña, claves propias y uso personal.

El GET de resultado es de descarga única y expira a las 24 horas. La SPA debe recuperar el Markdown una vez, conservarlo solo en memoria durante la vista actual y ofrecer preview, copiar y guardar un Blob local; no volver a pedir el resultado al backend tras consumirlo. Evitar persistir contenido en `localStorage`, analytics o logs.

### Fases para el siguiente agente

1. Confirmar/implementar Alembic y migraciones para rol, estado, Plan, asignación, límites, invitación y auditoría.
2. Añadir RBAC admin, verificación de cuenta activa, invitaciones y endpoints administrativos; probar acceso admin vs usuario en backend.
3. Añadir entitlement/quota service compartido por upload y URL. Aplicar bucket mensual `America/Mazatlan`, requests/minuto, bytes, conversiones, páginas y unidades OCR con las semánticas anteriores.
4. Añadir endpoints agregados/reportes antes de construir gráficas; no implementar agregación pesada en el browser.
5. Crear SPA, login/sesión, shell responsive, Dashboard, Convert, Usuarios, Informes y Perfil.
6. Añadir Nginx/gateway same-origin a Compose y actualizar route/domain de Dokploy al gateway. Verificar labels Traefik, `dokploy-network`, `/health/ready` y proxy `/v1`.
7. Probar límites y rollover mensual con reloj/zona controlables, seguridad de rutas admin, invitaciones, OCR accounting, conversión web, streaming/one-time result, accesibilidad y export CSV.
8. Antes de elevar límites o activar lotes/OCR comercialmente, medir en VPS Ubuntu de 2 CPU/2 GB; el benchmark aún está pendiente.

### Pendiente de producto

- Definir después del benchmark los valores numéricos de MB, páginas y RPM para Pro semanal/mensual/anual, Lifetime y Business; Free ya tiene 300/mes y 10 MiB/archivo.
- Definir límites de conversiones por mes para planes de pago y cupo OCR por plan según coste real de Azure.
- Confirmar si Free adopta solo el máximo mensual de 300 (sin límite diario), según el acuerdo actual.
- Revisar moneda/precios visibles del catálogo; no integrar checkout hasta elegir proveedor de pagos.
- Añadir organizaciones/membresías Business solo en una fase futura, si se solicita.

## Implementación del portal y cuotas (estado)

**Estado:** backend y portal desplegados en la VPS el 2026-11-04. El gateway responde `/login`, `/health/live`, `/health/ready` y `/v1/plans`. El administrador inicial ya fue creado y se validó el acceso web/la aceptación de invitación. Hay un plan `pro_weekly` activo con cuotas provisionales; Free no está activo. Falta validar el flujo completo con un usuario no administrador.

### Backend

- `packages/markitdown-api/` incorpora Alembic (`src/markitdown_api/migrations/`) y aplica `upgrade head` al arrancar. Una base existente sin `alembic_version` se marca como `0001_base` y luego recibe `0002_admin_portal`.
- Nuevos modelos: `Plan`, `PlanAssignment`, `AccountQuotaOverride`, `Invitation`, `AdminAuditEvent`; `Account` añade `role`, `is_active`, `display_name`; `Conversion` añade `quota_page_units` y `ocr_units`.
- `entitlements.py` calcula el mes natural en `America/Mazatlan` (configurable con `QUOTA_TIMEZONE`) y aplica cuotas de conversiones, bytes, páginas, RPM y OCR. Las conversiones fallidas no consumen conversiones/páginas pero sí bytes y requests/minuto. Las páginas reales se usan si existen; si no, 1 unidad por archivo. OCR se cuenta por llamadas reales al proveedor.
- `POST /v1/web-auth/login`, `/me`, `/csrf`, `/logout`, `/accept-invitation`; sesión con cookies `HttpOnly`/`Secure`/`SameSite=strict` y CSRF en mutaciones.
- `GET/PATCH /v1/account/me`, `POST /v1/account/password`; cambio de contraseña revoca API keys e invalida la sesión.
- Admin: `/v1/admin/overview`, `/v1/admin/users` (GET/POST/PATCH/detalle), `/v1/admin/plans` (GET/POST/PATCH), `/v1/admin/reports/consumption` y `.csv`. Todo pasa por `require_admin_account`.
- Invitaciones requieren SMTP y un plan activo. El primer admin se crea con `python -m markitdown_api.admin_cli bootstrap-admin <email>` dentro del contenedor `api`; el enlace caduca en 7 días. Si SMTP falla, el comando imprime el enlace de invitación.
- `GET /v1/plans` devuelve solo planes activos para el catálogo informativo.
- El registro público queda desactivado por defecto (`PUBLIC_SIGNUP_ENABLED=false`). Un usuario sin plan activo recibe `plan_not_configured`; los admins pueden probar con límites globales.
- Los planes sembrados están inactivos. Para activar Free/Pro/etc. hay que completar la matriz de cuotas (MB, páginas, RPM) desde administración; los valores de pago siguen pendientes del benchmark.

### Frontend

- `packages/markitdown-admin/` (React + TypeScript + Vite) con `/login`, `/accept-invitation`, `/dashboard`, `/convert`, `/plans`, `/users`, `/users/:id`, `/reports/consumption`, `/profile`.
- Conversión web con archivo o URL, polling, preview Markdown en memoria, copiar y descarga; no persiste resultados.
- Usuarios: invitación con plan activo, edición de rol/estado, overrides de cuotas por usuario (heredar del plan o valor explícito).
- Informes con agrupación y zona IANA del navegador; export CSV.
- Catálogo `/plans` informativo, sin checkout.
- `packages/markitdown-admin/nginx.conf` evita caché del `index.html` y cachea los assets con hash de forma inmutable.

### Despliegue y verificación

- Compose añade `admin-web` (Nginx) que sirve la SPA y proxya `/v1/*`, `/health/*`, `/docs` y páginas de correo a `api:8000`.
- El dominio de Dokploy apunta a `admin-web:80`; API, worker, PostgreSQL, Redis y `admin-web` están activos y saludables.
- Verificados remotamente `/health/live`, `/health/ready`, `/login`, `/docs` y `/v1/plans`. El build del frontend (TypeScript + Vite) pasa.
- Pendiente operativo: validar el flujo completo con un usuario no administrador, definir si Free será el plan por defecto y probar la descarga única/expiración a 24 h.

### Extracción de PDF

- Los PDF sin OCR usan `pymupdf4llm==1.28.2` (PyMuPDF 1.28.2) como motor principal, con `use_ocr=False`, y conservan MarkItDown como fallback. Esto corrige tablas y espaciado que pdfminer/pdfplumber rompían en documentos con fuentes incrustadas.
- OCR sigue usando el plugin MarkItDown + Azure; PyMuPDF 1.28.2 cumple el rango `<2` del plugin OCR.
- `PDF_ENGINE` permite elegir `pymupdf4llm` (predeterminado) o `markitdown` sin cambiar código.
- Revisar la licencia AGPL/comercial de PyMuPDF/PyMuPDF4LLM antes de una explotación comercial pública.

## Estado actual para el siguiente agente

### Lo que ya está funcionando

- Dominio y gateway: `https://mdservice.innersoft.com.mx` sirve la SPA y proxya la API.
- Administrador inicial creado; login web e invitación validados.
- `pro_weekly` activo con cuotas provisionales: 1.000 conversiones/mes, 1 GiB de entrada/mes, 10.000 páginas/mes, 5 req/min, 100 MiB por archivo, lote 1, OCR deshabilitado. Free está inactivo.
- PDF de prueba `LISTA_PRECIOS_MX_AGO26.pdf`: PyMuPDF4LLM devuelve las 3 páginas y todas las filas (33 precios, 33 bonos, 4 paquetes), pero con formato más compacto y sin encabezados decorativos duplicados. Si el usuario exige fidelidad literal 1:1, hace falta un modo `verbatim`; no confundir formato con pérdida de datos.
- El error `resource_limit_exceeded` de ese PDF se corrigió desactivando el OCR interno de PyMuPDF4LLM. Si vuelve a aparecer, medir RSS antes de subir `MAX_CONVERSION_MEMORY_BYTES`.

### Próximos pasos recomendados

1. Probar una conversión con un usuario no admin asignado a `pro_weekly` y verificar cuotas, descarga única, expiración y consumo.
2. Definir la matriz definitiva de Free y activarlo si será el plan por defecto; hoy los usuarios sin plan reciben `plan_not_configured`.
3. Ejecutar benchmark en la VPS con PDFs/Office de distintos tamaños; documentar RSS, CPU y duración antes de aumentar workers o límites.
4. Configurar backups y probar restauración de PostgreSQL.
5. Decidir si se implementa un modo de salida PDF literal/verbatim y si se acepta la licencia AGPL/comercial de PyMuPDF4LLM.
6. El artefacto `.codebase-memory/graph.db.zst` está versionado; si se reindexa, actualizar y commitear también `graph.db.zst` y `artifact.json` para que otros agentes bootstrapeen desde el índice compartido.
7. No volver a ejecutar `bootstrap-admin` para el mismo correo sin comprobar antes la cuenta; el comando promueve y cambia la sesión.

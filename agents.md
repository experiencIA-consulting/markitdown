# Handoff técnico: API REST para MarkItDown

## Objetivo

Este documento es el punto de partida para el siguiente agente. Describe la implementación existente y el estado confirmado del despliegue. No recrear el proyecto ni borrar volúmenes durante tareas de diagnóstico sin autorización explícita.

## Estado de implementación

- Implementación en `packages/markitdown-api/`: FastAPI, worker, cola Redis, PostgreSQL, almacenamiento temporal, Dockerfile, Compose, README y pruebas.
- **Estado remoto confirmado (2026-11-04):** Dokploy `xperiencIA` / `production`; Compose `markitdown`; ruta `packages/markitdown-api/compose.yaml`; dominio `mdservice.innersoft.com.mx` asignado al gateway `admin-web:80`.
- El último despliegue consultado está en `done`; `api`, `worker`, `postgres` y `redis` están activos. `https://mdservice.innersoft.com.mx/health/live` y `/health/ready` respondieron `200`.
- Se corrigió el `404 page not found`: Dokploy tenía el dominio configurado, pero los contenedores vivos no tenían labels Traefik ni estaban en `dokploy-network`. Se volvió a desplegar; el contenedor `api` ahora sí tiene las reglas y la red del proxy.
- La rama desplegada es `HP240RG10-dev`. Confirmar la rama y el estado en Dokploy antes de cambios posteriores.
- SMTP debe estar configurado si `EMAIL_VERIFICATION_REQUIRED=true` (valor predeterminado). Los pagos no están integrados.
- OCR está disponible explícitamente mediante el plugin MarkItDown + Azure OpenAI si se configuran sus secretos; por defecto no se invoca.
- El despliegue inicial usa una instancia API, un worker y una conversión simultánea. No aumentar concurrencia sin medir la VPS de 2 GB.
- El repositorio está indexado en Codebase Memory con el nombre `markitdown`.
- La suite del paquete API registró `18 passed`; Black/Ruff pasaron. El build de Docker no se pudo probar en el entorno local porque no había daemon Docker; Dokploy sí completó el build remoto.

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
Dominio y proxy gestionados por Dokploy
  |
  v
API REST (solo servicio expuesto)
  |                    \
  v                     v
PostgreSQL             Redis / cola
                         |
                         v
                  Worker MarkItDown
                         |
                         v
             Volumen temporal de archivos
```

- **API:** recibe solicitudes, autentica clientes, valida límites y crea trabajos.
- **Redis:** cola de trabajos y coordinación; no expuesto públicamente.
- **PostgreSQL:** cuentas, claves API protegidas, estados de trabajos y registros de consumo.
- **Worker:** ejecuta la conversión con la API Python de MarkItDown, separado del proceso HTTP.
- **Volumen temporal:** mantiene las entradas mientras están pendientes/en proceso y los resultados hasta su entrega o expiración. Debe tener límites y limpieza automática.
- **Dokploy:** publica el servicio API en el dominio configurado. Redis, PostgreSQL y workers permanecen en la red interna de Docker.

Con 2 GB de RAM, mantener un worker con concurrencia 1. Redis y PostgreSQL se ejecutan en la misma VPS que Dokploy; conversiones complejas pueden agotar memoria. No anunciar capacidad de alta carga hasta medirla. El Compose limita recursos y resultados temporales, pero no elimina esta limitación física.

## Contrato inicial de API

- `POST /v1/conversions`: recibe un archivo multipart y responde `202 Accepted` con el ID del trabajo.
- `POST /v1/conversions/from-url`: recibe una URL pública y responde `202 Accepted` con el ID del trabajo.
- `GET /v1/conversions/{id}`: devuelve el estado y errores públicos del trabajo.
- `GET /v1/conversions/{id}/result`: entrega el Markdown solo al propietario del trabajo.
- Endpoints de cuenta y claves API para registro/autenticación, rotación y revocación.
- Endpoint de consumo para consultar las métricas disponibles.
- Endpoints de salud (`live` y `ready`) para operación y despliegue.

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

## Cuentas, claves y facturación

- Implementado: cuenta individual, verificación de correo, login con token de sesión, recuperación de contraseña y gestión de claves API.
- Las claves API se entregan una sola vez, se almacenan como HMAC y se envían en `X-API-Key`; las sesiones usan `Authorization: Bearer`.
- Hay un máximo de 20 claves activas por cuenta. Restablecer una contraseña revoca las claves y cambia la versión de sesión.
- `GET /v1/usage` devuelve registros paginados; no procesa pagos ni almacena tarjetas.
- El proveedor de pagos, países, moneda y unidad facturable siguen pendientes.

## OCR y servicios externos

- Implementado mediante el plugin OCR de MarkItDown con Azure OpenAI/OpenAI-compatible.
- Se habilita por conversión (`ocr=true`) y falla con `ocr_provider_not_configured` si faltan secretos; por defecto está apagado.
- Secretos en Dokploy: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_MODEL`.
- Habilitar OCR envía imágenes de documentos al endpoint configurado; confirmar consentimiento, coste y privacidad antes de hacerlo por cliente.

## Estado de fases y siguiente trabajo

1. **Implementado:** API versionada, auth, conversión por archivo y URL, cola, worker, límites y retención.
2. **Implementado:** Compose para Dokploy, servicio público `api:8000`, Redis/PostgreSQL/worker internos y healthchecks.
3. **Implementado:** tests funcionales y de conversión; el CI tiene un job `api-tests`.
4. **Pendiente antes de escala:** realizar benchmark con formatos y tamaños representativos en la VPS; no hacerlo desde un entorno local si la instrucción activa del usuario lo prohíbe.
5. **Pendiente de producción:** establecer backups/restauración de PostgreSQL, migraciones versionadas (hoy el arranque usa `metadata.create_all`), políticas de anonimización/cierre de cuenta y abuso de alta frecuencia en registro/reset.
6. **Pendiente posterior:** seleccionar proveedor de pagos y unidad de cobro, sin almacenar datos de tarjeta.

## Procedimiento operativo Dokploy

- El Compose debe apuntar a `packages/markitdown-api/compose.yaml` en la raíz del checkout del repositorio `experiencIA-consulting/markitdown`.
- El dominio debe seleccionar `serviceName=api`, puerto interno `8000`, path `/`; nunca seleccionar `worker`.
- Para 404, comparar `compose.getConvertedCompose` con `docker.getConfig` del contenedor vivo: la conversión debe tener labels Traefik y `dokploy-network`.
- Después de cambiar dominios/rutas, redeploy Compose para recrear contenedores con los labels. Healthchecks confirmados: `/health/live` y `/health/ready`.
- El flujo API usa `POST /v1/accounts` → verificar correo → `POST /v1/auth/token` → `POST /v1/api-keys`; ver ejemplos en `packages/markitdown-api/README.md`.
- Errores observados históricamente: Nixpacks no correspondía a este monorepo; `composePath` incorrecto causaba “Compose file not found”; volumen PostgreSQL persistente puede conservar una contraseña anterior aunque `POSTGRES_PASSWORD` cambie en Dokploy. No borrar volúmenes para resolver auth DB sin confirmar pérdida de datos; sincronizar el rol en PostgreSQL de forma segura.

## Criterios de aceptación

- Una cuenta no puede consultar conversiones ni resultados de otra.
- Claves API se pueden revocar y no aparecen en logs ni respuestas posteriores a su creación.
- Archivos y descargas de hasta 100 MB se procesan sin cargarse enteros en memoria.
- URLs a destinos internos/privados, redirecciones peligrosas y descargas excesivas se bloquean.
- La cola y la concurrencia respetan los límites y rechazan carga excesiva de forma controlada.
- El archivo fuente se elimina al terminar; el resultado se elimina tras transferencia completa o al vencer 24 horas; una transferencia interrumpida no consume el resultado.
- Las métricas acordadas se guardan indefinidamente sin almacenar documentos ni Markdown.
- OCR/Azure no se invocan sin habilitación explícita y credenciales configuradas.
- Dokploy expone únicamente la API; dependencias internas no tienen puertos públicos.
- Las pruebas de carga documentan memoria, CPU, disco, tiempos y fallos para decidir si se amplía la VPS o se separan workers.

## Pendientes para una fase posterior

- Elegir proveedor de pagos, países, moneda, planes y unidad facturable.
- Decidir si se requiere acuse explícito del cliente para considerar completa la descarga.
- Confirmar la lista exacta de formatos/extras locales y proveedores OCR/Azure a soportar.
- Establecer política de anonimización de métricas si se cierra una cuenta.

## Mejoras implementadas: validación de formatos y límites de memoria

Implementación local en `packages/markitdown-api/`; **todavía no desplegada ni medida en la VPS**. Mantener un worker y una conversión simultánea.

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

- Pruebas locales del paquete API: `27 passed`. Ruff y Black pasan; el YAML de Compose parsea. No se probó el build Docker (sin daemon en este entorno), ni se desplegó o ejecutó benchmark en Dokploy/VPS.
- Antes de escalar: probar formatos/tamaños representativos en la VPS, observar memoria/CPU y confirmar que el OOM mata el subprocess sin reiniciar o reencolar indefinidamente el worker. No aumentar concurrencia hasta medir.
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

## Implementaci?n del portal y cuotas (estado)

**Estado:** backend y portal desplegados en la VPS el 2026-11-04. El gateway responde `/login`, `/health/live`, `/health/ready` y `/v1/plans`. Falta provisionar el primer administrador y activar/validar la matriz de cuotas del plan Free.

### Backend

- `packages/markitdown-api/` incorpora Alembic (`src/markitdown_api/migrations/`) y aplica `upgrade head` al arrancar. Una base existente sin `alembic_version` se marca como `0001_base` y luego recibe `0002_admin_portal`.
- Nuevos modelos: `Plan`, `PlanAssignment`, `AccountQuotaOverride`, `Invitation`, `AdminAuditEvent`; `Account` a?ade `role`, `is_active`, `display_name`; `Conversion` a?ade `quota_page_units` y `ocr_units`.
- `entitlements.py` calcula el mes natural en `America/Mazatlan` (configurable con `QUOTA_TIMEZONE`) y aplica cuotas de conversiones, bytes, p?ginas, RPM y OCR. Las conversiones fallidas no consumen conversiones/p?ginas pero s? bytes y requests/minuto. Las p?ginas reales se usan si existen; si no, 1 unidad por archivo. OCR se cuenta por llamadas reales al proveedor.
- `POST /v1/web-auth/login`, `/me`, `/csrf`, `/logout`, `/accept-invitation`; sesi?n con cookies `HttpOnly`/`Secure`/`SameSite=strict` y CSRF en mutaciones.
- `GET/PATCH /v1/account/me`, `POST /v1/account/password`; cambio de contrase?a revoca API keys e invalida la sesi?n.
- Admin: `/v1/admin/overview`, `/v1/admin/users` (GET/POST/PATCH/detalle), `/v1/admin/plans` (GET/POST/PATCH), `/v1/admin/reports/consumption` y `.csv`. Todo pasa por `require_admin_account`.
- Invitaciones requieren SMTP y un plan activo. El primer admin se crea con `python -m markitdown_api.admin_cli bootstrap-admin <email>` dentro del contenedor `api`; el enlace caduca en 7 d?as.
- `GET /v1/plans` devuelve solo planes activos para el cat?logo informativo.
- El registro p?blico queda desactivado por defecto (`PUBLIC_SIGNUP_ENABLED=false`). En este estado, un usuario sin plan activo recibe `plan_not_configured`; los admins pueden probar con l?mites globales.
- Los planes sembrados est?n inactivos. Para activar Free/Pro/etc. hay que completar la matriz de cuotas (MB, p?ginas, RPM) desde administraci?n; los valores de pago siguen pendientes del benchmark.

### Frontend

- `packages/markitdown-admin/` (React + TypeScript + Vite) con `/login`, `/accept-invitation`, `/dashboard`, `/convert`, `/plans`, `/users`, `/users/:id`, `/reports/consumption`, `/profile`.
- Conversi?n web con archivo o URL, polling, preview Markdown en memoria, copiar y descarga; no persiste resultados.
- Usuarios: invitaci?n con plan activo, edici?n de rol/estado, overrides de cuotas por usuario (heredar del plan o valor expl?cito).
- Informes con agrupaci?n y zona IANA del navegador; export CSV.
- Cat?logo `/plans` informativo, sin checkout.

### Despliegue y verificaci?n

- Compose a?ade `admin-web` (Nginx) que sirve la SPA y proxya `/v1/*`, `/health/*`, `/docs` y p?ginas de correo a `api:8000`.
- El dominio de Dokploy ya apunta a `admin-web:80`; API, worker, PostgreSQL, Redis y `admin-web` est?n activos y saludables.
- Verificados remotamente `/health/live`, `/health/ready`, `/login`, `/docs` y `/v1/plans`. El build del frontend (TypeScript + Vite) pasa.
- Pendiente operativo: provisionar el primer administrador, configurar SMTP si no est? listo, completar y activar el plan Free, y probar el flujo completo de invitaci?n y conversi?n con una cuenta real.
